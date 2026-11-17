"""Tests for model management in the sidecar: GGUF headers, memory estimates, the Hub client,
downloads, the llama.cpp runtime manager, the catalog and the llama-server supervisor.

Run with: python -m unittest discover tests

Nothing here reaches the network: a fake Hugging Face / GitHub server and a
fake llama-server (tests/fake_llama_server.py) run on 127.0.0.1.
"""

import hashlib
import io
import json
import os
import shutil
import socket
import struct
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest import mock

warnings.filterwarnings("ignore", message=r"Using `httpx` with `starlette\.testclient`")

import psutil  # noqa: E402
import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from table_extract.server import catalog as catalog_module  # noqa: E402
from table_extract.server import estimate, gguf, runtime  # noqa: E402
from table_extract.server.app import Services, Settings, create_app  # noqa: E402
from table_extract.server.catalog import Catalog  # noqa: E402
from table_extract.server.downloads import Cancelled, DownloadError, FileSpec, JobManager, fetch  # noqa: E402
from table_extract.server.hub import HubClient, group_files, preferred_projector, quant_of  # noqa: E402
from table_extract.server.runs import RunManager  # noqa: E402
from table_extract.server.storage import Offline, Storage  # noqa: E402
from table_extract.llm import ChatClient  # noqa: E402
from table_extract.server.supervisor import ServerSpec, Supervisor, free_port, port_free  # noqa: E402

TOKEN = "test-token"
PORT = 8765
POSIX = os.name == "posix"
MIB = 1024 * 1024
FAKE_SERVER = Path(__file__).with_name("fake_llama_server.py")

# Hyperparameters of the local model files the estimates were checked against
QWEN35_9B = {
    "general.architecture": "qwen35", "qwen35.block_count": 32, "qwen35.embedding_length": 4096,
    "qwen35.attention.head_count": 16, "qwen35.attention.head_count_kv": 4,
    "qwen35.attention.key_length": 256, "qwen35.attention.value_length": 256,
    "qwen35.ssm.conv_kernel": 4, "qwen35.ssm.state_size": 128, "qwen35.ssm.group_count": 16,
    "qwen35.ssm.inner_size": 4096, "qwen35.full_attention_interval": 4, "qwen35.context_length": 262144,
    "tokenizer.ggml.tokens.count": 248320,
}
GEMMA4_12B = {
    "general.architecture": "gemma4", "gemma4.block_count": 48, "gemma4.embedding_length": 3840,
    "gemma4.attention.head_count": 16,
    "gemma4.attention.head_count_kv": [8, 8, 8, 8, 8, 1] * 8,
    "gemma4.attention.key_length": 512, "gemma4.attention.value_length": 512,
    "gemma4.attention.key_length_swa": 256, "gemma4.attention.value_length_swa": 256,
    "gemma4.attention.sliding_window": 1024,
    "gemma4.attention.sliding_window_pattern": [True, True, True, True, True, False] * 8,
    "gemma4.context_length": 262144, "tokenizer.ggml.tokens.count": 262144,
}


def wait_for(predicate: Callable[[], Any], timeout: float = 10.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def temp_dir(test: unittest.TestCase) -> Path:
    path = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# GGUF files

def _gguf_string(text: str) -> bytes:
    data = text.encode()
    return struct.pack("<Q", len(data)) + data


def _gguf_value(value: Any) -> bytes:
    if isinstance(value, bool):
        return struct.pack("<I?", 7, value)
    if isinstance(value, int):
        return struct.pack("<II", 4, value)
    if isinstance(value, float):
        return struct.pack("<If", 6, value)
    if isinstance(value, str):
        return struct.pack("<I", 8) + _gguf_string(value)
    if isinstance(value, list):
        kind = 8 if value and isinstance(value[0], str) else 7 if value and isinstance(value[0], bool) else 4
        items = b"".join(_gguf_value(v)[4:] for v in value)
        return struct.pack("<IIQ", 9, kind, len(value)) + items
    raise TypeError(value)


def make_gguf(metadata: Dict[str, Any], vocab: int = 0, tail: bytes = b"") -> bytes:
    """A GGUF header (no tensors): metadata, then a vocabulary of `vocab` tokens."""
    items = [(k, v) for k, v in metadata.items() if not k.startswith("tokenizer.ggml.tokens")]
    if vocab:
        items.append(("tokenizer.ggml.tokens", [f"tok{i}" for i in range(vocab)]))
        items.append(("tokenizer.ggml.model", "gpt2"))
    body = b"".join(_gguf_string(k) + _gguf_value(v) for k, v in items)
    return b"GGUF" + struct.pack("<IQQ", 3, 0, len(items)) + body + tail


# A fake Hugging Face Hub and GitHub release host

class FakeHost:
    """Serves files with range support, Hub API answers and redirects, on 127.0.0.1."""

    def __init__(self):
        self.files: Dict[str, bytes] = {}  # path -> content
        self.json: Dict[str, Any] = {}  # path (with query) -> JSON answer
        self.links: Dict[str, str] = {}  # path -> Link header
        self.no_range: set = set()
        self.fail_after: Dict[str, int] = {}  # path -> bytes sent before the connection drops
        self.requests: List[Dict[str, Any]] = []
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                host.requests.append({"path": self.path, "headers": dict(self.headers)})
                if self.path in host.json or self.path.split("?")[0] in host.json:
                    answer = host.json.get(self.path, host.json.get(self.path.split("?")[0]))
                    data = json.dumps(answer).encode()
                    self.send_response(200)
                    if self.path in host.links:
                        self.send_header("Link", host.links[self.path])
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                path = self.path.split("?")[0]
                if path not in host.files:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = host.files[path]
                start, status = 0, 200
                header = self.headers.get("Range")
                if header and path not in host.no_range:
                    start = int(header.split("=")[1].split("-")[0])
                    end = header.split("-")[1]
                    stop = int(end) + 1 if end else len(data)
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    chunk, status = data[start:stop], 206
                else:
                    chunk = data
                self.send_response(status)
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                limit = host.fail_after.pop(path, None)
                try:
                    self.wfile.write(chunk[:limit] if limit is not None else chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                if limit is not None:
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def add_repo(self, repo: str, revision: str, files: Dict[str, bytes], author: Optional[str] = None,
                 gated: bool = False) -> None:
        self.json[f"/api/models/{repo}"] = {"id": repo, "sha": revision, "gated": gated}
        self.json[f"/api/models/{repo}/tree/{revision}"] = [
            {"type": "file", "path": p, "size": len(d), "lfs": {"oid": sha(d), "size": len(d)}}
            for p, d in files.items()] + [{"type": "directory", "path": "docs"}]
        for path, data in files.items():
            self.files[f"/{repo}/resolve/{revision}/{path}"] = data


def fake_host(test: unittest.TestCase) -> FakeHost:
    host = FakeHost()
    test.addCleanup(host.close)
    return host


class GGUFHeaders(unittest.TestCase):
    def test_reads_metadata_and_stops_at_the_vocabulary(self):
        data = make_gguf({**QWEN35_9B, "general.name": "Qwen3.5-9B"}, vocab=50, tail=b"\0" * 10)
        reads = []

        def read_at(offset, size):
            reads.append((offset, size))
            return data[offset:offset + size]

        metadata = gguf.read_metadata(read_at)
        self.assertEqual(metadata["general.architecture"], "qwen35")
        self.assertEqual(metadata["qwen35.attention.head_count_kv"], 4)
        self.assertEqual(metadata["tokenizer.ggml.tokens.count"], 50)
        self.assertNotIn("tokenizer.ggml.model", metadata)  # stopped at the vocabulary

    def test_large_arrays_are_skipped(self):
        metadata = {"general.architecture": "x", "x.block_count": 2, "x.big": list(range(5000)),
                    "x.names": ["a"] * 5000}
        result = gguf.read_metadata(lambda o, s: make_gguf(metadata)[o:o + s])
        self.assertEqual(result["x.big"], {"array_of": 4, "count": 5000})
        self.assertEqual(result["x.names"], {"array_of": 8, "count": 5000})

    def test_vocabulary_before_hyperparameters_is_skipped(self):
        data = (b"GGUF" + struct.pack("<IQQ", 3, 0, 3)
                + _gguf_string("tokenizer.ggml.tokens") + _gguf_value(["a", "b", "c"])
                + _gguf_string("general.architecture") + _gguf_value("llama")
                + _gguf_string("llama.block_count") + _gguf_value(2))
        result = gguf.read_metadata(lambda o, s: data[o:o + s])
        self.assertEqual(result["tokenizer.ggml.tokens.count"], 3)
        self.assertEqual(result["llama.block_count"], 2)

    def test_not_gguf(self):
        with self.assertRaises(gguf.GGUFError):
            gguf.read_metadata(lambda o, s: b"PK\x03\x04"[o:o + s])
        with self.assertRaises(gguf.GGUFError):
            gguf.read_metadata(lambda o, s: make_gguf(QWEN35_9B)[:40][o:o + s])

    def test_remote_header_with_range_requests(self):
        host = fake_host(self)
        data = make_gguf(GEMMA4_12B, vocab=20, tail=b"\0" * 1000)
        host.files["/m.gguf"] = data
        metadata = gguf.read_url(requests.Session(), host.url + "/m.gguf")
        self.assertEqual(metadata["gemma4.attention.sliding_window"], 1024)
        self.assertTrue(all("Range" in r["headers"] for r in host.requests))

    def test_local_file(self):
        path = temp_dir(self) / "m.gguf"
        path.write_bytes(make_gguf(QWEN35_9B, vocab=5))
        self.assertEqual(gguf.read_file(str(path))["qwen35.block_count"], 32)


class MemoryEstimates(unittest.TestCase):
    MEM_16GB_MAC = {"gpu_bytes": 12124 * MIB - estimate.GPU_MARGIN, "ram_bytes": 16 * 1024 * MIB,
                    "unified_memory": True, "has_gpu": True, "gpu_source": "llama.cpp", "known": True}

    def test_kv_cache_matches_llama_fit_params(self):
        # llama-fit-params b11429 on the local files: context column, in MiB
        qwen, gemma = estimate.hparams(QWEN35_9B), estimate.hparams(GEMMA4_12B)
        self.assertEqual(estimate.kv_cache_bytes(qwen, 16384) // MIB, 562)
        self.assertEqual(estimate.kv_cache_bytes(gemma, 16384) // MIB, 736)
        self.assertEqual(estimate.kv_cache_bytes(gemma, 32768) // MIB, 992)
        # Compute buffers: 533 + 96 MiB for Qwen3.5 at 16k, 559 + 95 MiB for Gemma 4
        self.assertAlmostEqual(estimate.compute_bytes(qwen, 16384) / MIB, 629, delta=629 * 0.02)
        self.assertAlmostEqual(estimate.compute_bytes(gemma, 16384) / MIB, 654, delta=654 * 0.02)

    def test_layer_patterns(self):
        qwen = estimate.hparams(QWEN35_9B)
        self.assertEqual(sum(not r for r in qwen["recurrent_layers"]), 8)
        gemma = estimate.hparams(GEMMA4_12B)
        self.assertEqual(sum(gemma["swa_layers"]), 40)
        # A Gemma 3 file without a per-layer pattern: one full layer in six
        gemma3 = estimate.hparams({"general.architecture": "gemma3", "gemma3.block_count": 12,
                                   "gemma3.embedding_length": 1024, "gemma3.attention.head_count": 8,
                                   "gemma3.attention.sliding_window": 512})
        self.assertEqual(gemma3["swa_layers"], [True] * 5 + [False] + [True] * 5 + [False])
        bert = estimate.hparams({"general.architecture": "bert", "bert.block_count": 24,
                                 "bert.embedding_length": 1024, "bert.attention.head_count": 16,
                                 "bert.attention.causal": False})
        self.assertEqual(estimate.kv_cache_bytes(bert, 8192), 0)

    def test_qwen_q8_fits_on_a_16gb_mac(self):
        result = estimate.assess(9527502048, estimate.hparams(QWEN35_9B), 20480, self.MEM_16GB_MAC,
                                 mmproj_bytes=921705024)
        self.assertEqual(result["verdict"], "gpu")

    def test_verdicts(self):
        gib = 1024 * MIB
        nvidia = {"gpu_bytes": 8 * gib, "ram_bytes": 32 * gib, "unified_memory": False, "has_gpu": True, "known": True}
        self.assertEqual(estimate.verdict(7 * gib, nvidia), "gpu")
        self.assertEqual(estimate.verdict(20 * gib, nvidia), "partial")
        self.assertEqual(estimate.verdict(40 * gib, nvidia), "too_big")
        cpu = {**nvidia, "gpu_bytes": 0, "has_gpu": False}
        self.assertEqual(estimate.verdict(7 * gib, cpu), "cpu")
        unknown = {**cpu, "known": False}
        self.assertEqual(estimate.verdict(7 * gib, unknown), "unknown")
        self.assertEqual(estimate.verdict(30 * gib, unknown), "too_big")

    def test_budget_from_the_hardware_probe(self):
        probe = {"system": {"unified_memory": False}, "memory": {"total_bytes": 32 * 1024 * MIB},
                 "llama_server": {"found": True},
                 "devices": [{"name": "CUDA0", "gpu": True, "memory_total_bytes": 24 * 1024 * MIB},
                             {"name": "BLAS", "gpu": False, "memory_total_bytes": 0}]}
        mem = estimate.budget(probe)
        self.assertEqual(mem["gpu_bytes"], 24 * 1024 * MIB - estimate.GPU_MARGIN)
        # Apple Silicon before llama.cpp is installed: macOS's usual GPU share
        mac = estimate.budget({"system": {"unified_memory": True}, "memory": {"total_bytes": 16 * 1024 * MIB},
                               "llama_server": {"found": False}, "devices": []})
        self.assertEqual(mac["gpu_source"], "estimated")
        self.assertTrue(mac["known"])

    def test_fit_flags_keep_only_what_llama_fit_params_accepts(self):
        flags = ["--reasoning", "off", "-ctk", "q8_0", "--swa-full", "--alias", "x", "-ub", "8192"]
        self.assertEqual(estimate.fit_flags(flags), ["-ctk", "q8_0", "--swa-full", "-ub", "8192"])

    @unittest.skipUnless(POSIX, "uses a shell script as llama-fit-params")
    def test_fit_check(self):
        script = temp_dir(self) / "llama-fit-params"
        script.write_text('#!/bin/sh\ncase "$*" in *-fitp*) echo "MTL0 8045 562 533"; echo "Host 1030 0 96";;\n'
                          '*) echo "-c 20480 -ngl -1";; esac\n')
        script.chmod(0o755)
        devices = [{"name": "MTL0", "gpu": True, "memory_total_bytes": 12124 * MIB}]
        result = estimate.fit_check(str(script), "m.gguf", 20480, devices, mmproj_bytes=921705024)
        self.assertEqual(result["verdict"], "gpu")
        self.assertEqual(result["devices"]["MTL0"]["model"], 8045 * MIB)
        # The projector tips a smaller GPU over: partial
        small = [{"name": "MTL0", "gpu": True, "memory_total_bytes": 10000 * MIB}]
        self.assertEqual(estimate.fit_check(str(script), "m.gguf", 20480, small, 921705024)["verdict"], "partial")


class HubFiles(unittest.TestCase):
    def test_quantization_names(self):
        cases = {
            "Qwen3.5-9B-Q8_0.gguf": "Q8_0", "Qwen3.5-9B-UD-Q4_K_XL.gguf": "UD-Q4_K_XL",
            "gemma-4-12b-it-IQ4_XS.gguf": "IQ4_XS", "model-bf16.gguf": "BF16",
            "Llama-4-Q4_K_M-00001-of-00003.gguf": "Q4_K_M", "mmproj-F16.gguf": "F16",
            "gpt-oss-20b-MXFP4.gguf": "MXFP4",
        }
        for name, quant in cases.items():
            self.assertEqual(quant_of(name), quant, name)

    def test_grouping(self):
        files = [
            {"path": "m-Q8_0.gguf", "size": 8}, {"path": "m-Q4_K_M.gguf", "size": 4},
            {"path": "big/m-BF16-00002-of-00002.gguf", "size": 10}, {"path": "big/m-BF16-00001-of-00002.gguf", "size": 10},
            {"path": "mmproj-BF16.gguf", "size": 2}, {"path": "mmproj-F16.gguf", "size": 2},
            {"path": "README.md", "size": 1},
        ]
        grouped = group_files(files)
        self.assertEqual([q["quant"] for q in grouped["quants"]], ["Q4_K_M", "Q8_0", "BF16"])
        bf16 = grouped["quants"][2]
        self.assertEqual(bf16["size"], 20)
        self.assertEqual(bf16["files"][0]["path"], "big/m-BF16-00001-of-00002.gguf")
        self.assertEqual(preferred_projector(grouped["projectors"])["path"], "mmproj-F16.gguf")

    def test_search_keeps_trusted_vision_repos(self):
        host = fake_host(self)
        page1 = [
            {"id": "unsloth/Good-GGUF", "author": "unsloth", "sha": "a1", "downloads": 10,
             "siblings": [{"rfilename": "Good-Q4_K_M.gguf"}, {"rfilename": "mmproj-F16.gguf"}]},
            {"id": "someone/Remix-GGUF", "author": "someone", "sha": "b2",
             "siblings": [{"rfilename": "Remix-Q4_K_M.gguf"}, {"rfilename": "mmproj-F16.gguf"}]},
            {"id": "unsloth/TextOnly-GGUF", "author": "unsloth", "sha": "c3",
             "siblings": [{"rfilename": "TextOnly-Q4_K_M.gguf"}]},
        ]
        page2 = [{"id": "bartowski/Other-GGUF", "author": "bartowski", "sha": "d4",
                  "siblings": [{"rfilename": "Other-Q8_0.gguf"}, {"rfilename": "mmproj-Other-f16.gguf"}]}]
        host.json["/api/models"] = page1
        next_url = f"{host.url}/api/models/page2"
        host.json["/api/models/page2"] = page2
        hub = HubClient(temp_dir(self), base_url=host.url)
        with mock.patch.object(requests.Response, "links", new_callable=mock.PropertyMock) as links:
            links.side_effect = [{"next": {"url": next_url}}, {}]
            results = hub.search("", trusted_only=True)
        self.assertEqual([r["repo"] for r in results], ["unsloth/Good-GGUF", "bartowski/Other-GGUF"])
        self.assertEqual(results[0]["quants"], ["Q4_K_M"])
        query = host.requests[0]["path"]
        self.assertIn("pipeline_tag=image-text-to-text", query)
        self.assertIn("filter=gguf", query)

        with mock.patch.object(requests.Response, "links", new_callable=mock.PropertyMock, return_value={}):
            everything = hub.search("", trusted_only=False)
        self.assertIn("someone/Remix-GGUF", [r["repo"] for r in everything])

    def test_files_and_header_are_cached(self):
        host = fake_host(self)
        model = make_gguf(QWEN35_9B, vocab=10, tail=b"\0" * 100)
        host.add_repo("org/M-GGUF", "rev1", {"M-Q8_0.gguf": model, "mmproj-F16.gguf": b"proj"})
        hub = HubClient(temp_dir(self), base_url=host.url)
        files = hub.files("org/M-GGUF", "rev1")
        self.assertEqual({f["path"] for f in files}, {"M-Q8_0.gguf", "mmproj-F16.gguf"})
        self.assertEqual(next(f for f in files if f["path"] == "M-Q8_0.gguf")["sha256"], sha(model))
        self.assertEqual(hub.header("org/M-GGUF", "rev1", "M-Q8_0.gguf")["qwen35.block_count"], 32)
        count = len(host.requests)
        hub.files("org/M-GGUF", "rev1")
        hub.header("org/M-GGUF", "rev1", "M-Q8_0.gguf")
        self.assertEqual(len(host.requests), count)

    def test_token_is_sent_to_the_hub(self):
        host = fake_host(self)
        host.add_repo("google/gated", "r", {"m.gguf": b"x"}, gated=True)
        hub = HubClient(temp_dir(self), base_url=host.url)
        with mock.patch.dict(os.environ, {"HF_TOKEN": "hf_secret"}):
            hub.model_info("google/gated")
        self.assertEqual(host.requests[-1]["headers"].get("Authorization"), "Bearer hf_secret")


class Downloads(unittest.TestCase):
    def setUp(self):
        self.host = fake_host(self)
        self.data = os.urandom(3 * 1024 * 1024 + 123)
        self.host.files["/f.bin"] = self.data
        self.dest = temp_dir(self) / "sub" / "f.bin"
        self.session = requests.Session()
        self.done = []

    def spec(self, **kw) -> FileSpec:
        return FileSpec(url=self.host.url + "/f.bin", dest=self.dest, size=len(self.data), sha256=sha(self.data), **kw)

    def test_download_and_verify(self):
        fetch(self.session, self.spec(), threading.Event(), self.done.append)
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertEqual(sum(self.done), len(self.data))
        self.assertFalse(self.dest.with_name("f.bin.part").exists())

    def test_resume_after_an_interrupted_download(self):
        self.host.fail_after["/f.bin"] = 1024 * 1024
        with self.assertRaises(DownloadError):
            fetch(self.session, self.spec(), threading.Event(), self.done.append)
        part = self.dest.with_name("f.bin.part")
        self.assertGreater(part.stat().st_size, 0)
        fetch(self.session, self.spec(), threading.Event(), self.done.append)
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertTrue(self.host.requests[-1]["headers"]["Range"].startswith("bytes="))

    def test_server_without_ranges_starts_over(self):
        self.host.no_range.add("/f.bin")
        part = self.dest.with_name("f.bin.part")
        part.parent.mkdir(parents=True)
        part.write_bytes(b"garbage")
        fetch(self.session, self.spec(), threading.Event(), self.done.append)
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_checksum_mismatch(self):
        spec = self.spec()
        spec.sha256 = "0" * 64
        with self.assertRaisesRegex(DownloadError, "damaged"):
            fetch(self.session, spec, threading.Event(), self.done.append)
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.dest.with_name("f.bin.part").exists())

    def test_cancel_keeps_the_part_file(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            fetch(self.session, self.spec(), cancel, self.done.append)
        self.assertFalse(self.dest.exists())

    def test_jobs(self):
        jobs = JobManager()
        started = threading.Event()

        def work(job, progress):
            progress(5)
            started.set()
            job.cancel.wait(5)
            raise Cancelled()

        job = jobs.submit("model", "t", "label", 10, work)
        self.assertIs(jobs.submit("model", "t", "label", 10, work), job)  # one job per target
        started.wait(5)
        self.assertEqual(jobs.list()[0]["bytes_done"], 5)
        jobs.cancel(job.id)
        wait_for(lambda: job.finished, what="cancelled job")
        self.assertEqual(job.state, "cancelled")

        failed = jobs.submit("model", "u", "label", 1, lambda job, p: (_ for _ in ()).throw(DownloadError("no")))
        wait_for(lambda: failed.finished, what="failed job")
        self.assertEqual((failed.state, failed.error), ("failed", "no"))


def tar_gz(files: Dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@unittest.skipUnless(POSIX, "the fake llama.cpp build is a shell script")
class RuntimeManager(unittest.TestCase):
    def setUp(self):
        self.host = fake_host(self)
        self.storage = Storage(temp_dir(self))
        self.archive = tar_gz({
            "llama-b11429/llama-server": "#!/bin/sh\necho 'version: 0.6.0 (build 11429, commit abc)' >&2\n",
            "llama-b11429/llama-fit-params": "#!/bin/sh\n",
            "llama-b11429/libggml.dylib": "",
        })
        self.host.files["/b11429/llama-test.tar.gz"] = self.archive
        self.runtime = runtime.Runtime(self.storage, release_url=self.host.url + "/{build}/{name}")
        self.runtime.override = None

    def variant(self, digest: Optional[str] = None) -> runtime.Variant:
        return runtime.Variant("test", "Darwin", "arm64", "metal", "Test", (
            runtime.Asset("llama-test.tar.gz", len(self.archive), digest or sha(self.archive)),))

    def install(self, variant):
        jobs = JobManager()
        job = jobs.submit("runtime", "llama.cpp test", "test", variant.size,
                          lambda job, progress: self.runtime.install(variant, job, progress))
        wait_for(lambda: job.finished, what="install")
        return job

    def test_install_verify_and_use(self):
        job = self.install(self.variant())
        self.assertEqual(job.state, "done", job.error)
        record = self.runtime.installed()
        self.assertEqual(record["build"], "b11429")
        server = self.runtime.executable()
        self.assertTrue(server.endswith("llama-server"))
        self.assertTrue(server.startswith(str(self.storage.runtime)))
        self.assertTrue(self.runtime.executable("llama-fit-params").endswith("llama-fit-params"))
        self.assertFalse((self.storage.runtime / "downloads" / "llama-test.tar.gz").exists())
        status = self.runtime.status()
        self.assertEqual(status["source"], "installed")
        self.assertFalse(status["update_available"])

    def test_checksum_mismatch_installs_nothing(self):
        job = self.install(self.variant("0" * 64))
        self.assertEqual(job.state, "failed")
        self.assertIn("damaged", job.error)
        self.assertIsNone(self.runtime.installed())

    def test_unsafe_archive(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            info = tarfile.TarInfo("../escape")
            info.size = 0
            tar.addfile(info, io.BytesIO(b""))
        self.host.files["/b11429/llama-test.tar.gz"] = self.archive = buffer.getvalue()
        job = self.install(self.variant())
        self.assertEqual(job.state, "failed")
        self.assertFalse((self.storage.runtime.parent / "escape").exists())

    def test_override_wins(self):
        rt = runtime.Runtime(self.storage, override=sys.executable)
        self.assertEqual(rt.executable(), sys.executable)

    def test_variant_choice(self):
        with mock.patch("platform.system", return_value="Windows"), mock.patch("platform.machine", return_value="AMD64"):
            self.assertEqual(runtime.recommended_variant(["nvidia"]).id, "win-cuda-x64")
            self.assertEqual(runtime.recommended_variant(["amd"]).id, "win-vulkan-x64")
            self.assertEqual(runtime.recommended_variant([]).id, "win-cpu-x64")
        with mock.patch("platform.system", return_value="Linux"), mock.patch("platform.machine", return_value="x86_64"):
            self.assertEqual(runtime.recommended_variant(["nvidia", "intel"]).id, "linux-cuda-x64")
        with mock.patch("platform.system", return_value="Darwin"), mock.patch("platform.machine", return_value="arm64"):
            self.assertEqual(runtime.recommended_variant(["apple"]).id, "macos-arm64")
        for variant in runtime.VARIANTS.values():
            for asset in variant.assets:
                self.assertRegex(asset.sha256, r"^[0-9a-f]{64}$")
                self.assertIn(runtime.PINNED, asset.name.replace("cudart-llama-bin", f"cudart-llama-{runtime.PINNED}-bin"))


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.host = fake_host(self)
        self.storage = Storage(temp_dir(self))
        self.hub = HubClient(self.storage.cache, base_url=self.host.url)
        self.catalog = Catalog(self.storage, self.hub)
        self.model = make_gguf(QWEN35_9B, vocab=10, tail=b"w" * 5000)
        self.small = make_gguf(QWEN35_9B, vocab=10, tail=b"s" * 2000)
        self.host.add_repo("org/M-GGUF", "rev1", {"M-Q8_0.gguf": self.model, "M-Q4_K_M.gguf": self.small,
                                                   "mmproj-F16.gguf": b"projector"})

    def test_curated_list_is_consistent(self):
        data = catalog_module.curated()
        for entry in data["models"]:
            self.assertRegex(entry["revision"], r"^[0-9a-f]{40}$")
            for f in entry["files"] + ([entry["mmproj"]] if entry.get("mmproj") else []):
                self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(f["size"], 0)
            self.assertEqual(entry["quant"], quant_of(entry["files"][0]["path"]))
            estimate.kv_cache_bytes(entry["hparams"], entry["context"])  # complete enough to estimate

    def test_benchmark_scores(self):
        curated_ids = {entry["id"] for entry in catalog_module.curated()["models"]}
        for summary in (Path(catalog_module.__file__).parent / "benchmarks").glob("*.json"):
            self.assertIn(summary.stem, curated_ids)
            scores = catalog_module.benchmark(summary.stem)
            for stage in ("triage", "extraction"):
                if stage in scores:
                    self.assertTrue(0 <= scores[stage]["f1"] <= 1)
        self.assertIsNone(catalog_module.benchmark("nope"))
        options = self.catalog.curated_options(MemoryEstimates.MEM_16GB_MAC)
        self.assertTrue(all("benchmark" in o for o in options))

    def test_recommendation(self):
        mac16 = MemoryEstimates.MEM_16GB_MAC
        rec = self.catalog.recommendation(mac16)
        self.assertEqual(rec["model"]["id"], "qwen3.5-9b-q8")
        gib = 1024 * MIB
        mac32 = {**mac16, "gpu_bytes": int(32 * gib * 0.74) - estimate.GPU_MARGIN, "ram_bytes": 32 * gib}
        rec = self.catalog.recommendation(mac32)
        self.assertEqual((rec["model"]["id"], rec["model"]["estimate"]["verdict"]), ("qwen3.5-35b-a3b-iq4xs", "gpu"))
        # The smallest curated model (Gemma 4 12B) needs about 9 GB
        mac8 = {**mac16, "gpu_bytes": int(8 * gib * 0.74) - estimate.GPU_MARGIN, "ram_bytes": 8 * gib}
        rec = self.catalog.recommendation(mac8)
        self.assertIsNone(rec["model"])
        self.assertIn("not have enough memory", rec["reason"])
        unknown = {**mac16, "known": False}
        self.assertIsNone(self.catalog.recommendation(unknown)["model"])

    def test_hub_options(self):
        result = self.catalog.hub_options("org/M-GGUF", MemoryEstimates.MEM_16GB_MAC)
        self.assertEqual(result["architecture"], "qwen35")
        self.assertEqual([q["quant"] for q in result["quants"]], ["Q4_K_M", "Q8_0"])
        self.assertEqual(result["quants"][0]["estimate"]["verdict"], "gpu")
        self.assertEqual(result["mmproj"]["path"], "mmproj-F16.gguf")

    def test_repo_without_projector_is_refused(self):
        self.host.add_repo("org/Text-GGUF", "r", {"T-Q4_K_M.gguf": self.small})
        with self.assertRaisesRegex(ValueError, "projector"):
            self.catalog.hub_options("org/Text-GGUF", MemoryEstimates.MEM_16GB_MAC)

    def download(self, spec):
        jobs = JobManager()
        job = jobs.submit("model", spec["id"], spec["label"], self.catalog.download_size(spec),
                          lambda job, progress: self.catalog.download(spec, job, progress))
        wait_for(lambda: job.finished, what="download")
        return job

    def test_download_install_and_remove(self):
        q8 = self.catalog.spec_for(repo="org/M-GGUF", quant="Q8_0")
        self.assertEqual(q8["alias"], "m-q8_0")
        job = self.download(q8)
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(job.bytes_done, len(self.model) + len(b"projector"))
        q4 = self.catalog.spec_for(repo="org/M-GGUF", quant="q4_k_m")
        self.assertEqual(self.download(q4).state, "done")
        installed = {m["id"]: m for m in self.catalog.installed()}
        self.assertEqual(set(installed), {"org/M-GGUF:Q8_0", "org/M-GGUF:Q4_K_M"})
        model = installed["org/M-GGUF:Q8_0"]
        self.assertTrue(model["complete"])
        self.assertEqual(Path(model["model_path"]).read_bytes(), self.model)

        # The projector is shared: removing one quantization keeps it
        self.assertTrue(self.catalog.remove("org/M-GGUF:Q8_0"))
        folder = self.storage.models / "org" / "M-GGUF"
        self.assertFalse((folder / "M-Q8_0.gguf").exists())
        self.assertTrue((folder / "mmproj-F16.gguf").exists())
        self.catalog.remove("org/M-GGUF:Q4_K_M")
        self.assertFalse((folder / "mmproj-F16.gguf").exists())
        self.assertEqual(self.catalog.installed(), [])

    def test_curated_spec(self):
        spec = self.catalog.spec_for(curated_id="qwen3.5-9b-q8")
        self.assertEqual((spec["role"], spec["alias"]), ("vision", "qwen3.5-9b-q8"))
        self.assertIsNotNone(spec["mmproj"])
        with self.assertRaises(ValueError):
            self.catalog.spec_for(curated_id="nope")


def fake_llama_server(folder: Path, behaviour: str = "ok") -> Path:
    script = folder / f"llama-server-{behaviour}"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SERVER}" --behaviour {behaviour} "$@"\n')
    script.chmod(0o755)
    return script


def busy_port() -> socket.socket:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s


def avoid_default_ports(test: unittest.TestCase) -> None:
    """Keep away from 8000, which a developer's own llama-server may be using."""
    patcher = mock.patch.dict("table_extract.server.supervisor.PREFERRED_PORTS", {"vision": free_port()})
    patcher.start()
    test.addCleanup(patcher.stop)


@unittest.skipUnless(POSIX, "the fake llama-server is started through a shell script")
class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.storage = Storage(self.tmp / "data")
        self.model = self.tmp / "vision.gguf"
        self.model.write_bytes(b"GGUF")
        self.server = fake_llama_server(self.tmp)
        self.supervisor = self.make_supervisor(self.server)
        avoid_default_ports(self)

    def make_supervisor(self, server: Path, timeout: float = 20) -> Supervisor:
        supervisor = Supervisor(self.storage, lambda: str(server), load_timeout=timeout)
        self.addCleanup(supervisor.shutdown)
        return supervisor

    def spec(self) -> ServerSpec:
        return ServerSpec(role="vision", model_path=str(self.model), alias="vision-alias", context=4096,
                          flags=["--reasoning", "off"], model_id="org/vision:Q8_0")

    def test_start_ready_and_stop(self):
        server = self.supervisor.start(self.spec())
        wait_for(lambda: server.state != "starting", what="ready")
        self.assertEqual(server.state, "ready", server.log)
        status = self.supervisor.status()
        self.assertEqual(status["servers"]["vision"]["alias"], "vision-alias")
        self.assertEqual(status["config"]["server"]["model"], "vision-alias")
        url = status["servers"]["vision"]["url"]
        # The key is required, and comes from the environment, not the command line
        self.assertEqual(requests.get(url.replace("/v1/chat/completions", "/props")).status_code, 401)
        headers = {"Authorization": f"Bearer {self.supervisor.api_key}"}
        self.assertEqual(requests.get(url.replace("/v1/chat/completions", "/props"), headers=headers).status_code, 200)
        cmdline = psutil.Process(server.process.pid).cmdline()
        self.assertNotIn(self.supervisor.api_key, " ".join(cmdline))
        for flag in ("--offline", "--host", "127.0.0.1", "--no-webui"):
            self.assertIn(flag, cmdline)
        self.assertEqual(json.loads(self.storage.root.joinpath("servers.json").read_text())[0]["pid"], server.process.pid)

        self.supervisor.stop()
        self.assertIsNotNone(server.process.poll())
        self.assertEqual(server.state, "stopped")
        self.assertEqual(json.loads(self.storage.root.joinpath("servers.json").read_text()), [])

    def test_taken_port_falls_back_to_a_free_one(self):
        taken = busy_port()
        self.addCleanup(taken.close)
        with mock.patch.dict("table_extract.server.supervisor.PREFERRED_PORTS", {"vision": taken.getsockname()[1]}):
            server = self.supervisor.start(self.spec())
        self.assertNotEqual(server.port, taken.getsockname()[1])
        wait_for(lambda: server.state == "ready", what="ready")

    def test_failure_is_explained(self):
        supervisor = self.make_supervisor(fake_llama_server(self.tmp, "fail"))
        server = supervisor.start(self.spec())
        wait_for(lambda: server.state == "failed", what="failure")
        self.assertIn("too old for this model", server.error)

    def test_other_program_on_the_port_fails_fast(self):
        supervisor = Supervisor(self.storage, lambda: str(fake_llama_server(self.tmp, "deaf")),
                                load_timeout=60, answer_timeout=0.5)
        self.addCleanup(supervisor.shutdown)
        server = supervisor.start(self.spec())
        wait_for(lambda: server.state == "failed", what="failure")
        self.assertIn(f"Another program is answering on port {server.port}", server.error)
        self.assertIsNotNone(server.process.poll())

    def test_load_timeout(self):
        supervisor = self.make_supervisor(fake_llama_server(self.tmp, "slow"), timeout=0.5)
        server = supervisor.start(self.spec())
        wait_for(lambda: server.state == "failed", what="timeout")
        self.assertIn("did not load", server.error)
        self.assertIsNotNone(server.process.poll())

    def test_leftovers_from_a_killed_sidecar_are_stopped(self):
        server = self.supervisor.start(self.spec())
        wait_for(lambda: server.state == "ready", what="ready")
        pid = server.process.pid
        # A new sidecar on the same data folder, as after a crash
        stopped = Supervisor(self.storage, lambda: str(self.server)).clean_up_leftovers()
        self.assertEqual(stopped, [pid])
        wait_for(lambda: server.process.poll() is not None, what="leftover stopped")

    def test_no_llama_cpp(self):
        supervisor = Supervisor(self.storage, lambda: None)
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            supervisor.start(self.spec())


class PortTest(unittest.TestCase):
    def test_port_answered_on_all_addresses_is_taken(self):
        # On macOS 127.0.0.1 can still be bound then, but connections may not reach llama-server
        s = socket.socket()
        self.addCleanup(s.close)
        s.bind(("0.0.0.0", 0))
        s.listen()
        self.assertFalse(port_free(s.getsockname()[1]))
        self.assertTrue(port_free(free_port()))

    def test_local_model_server_ignores_proxies(self):
        self.assertFalse(ChatClient("http://127.0.0.1:8000/v1/chat/completions", "m").session.trust_env)
        self.assertFalse(ChatClient("http://localhost:8000/v1/chat/completions", "m").session.trust_env)
        self.assertTrue(ChatClient("https://models.example.org/v1/chat/completions", "m").session.trust_env)


class StorageTest(unittest.TestCase):
    def test_settings_and_offline(self):
        storage = Storage(temp_dir(self))
        self.assertFalse(storage.offline)
        storage.update_settings({"offline": True, "unknown": 1})
        self.assertEqual(storage.settings(), {"offline": True})
        with self.assertRaises(Offline):
            storage.require_online()


class Endpoints(unittest.TestCase):
    """The model endpoints over a fake Hub, a fake llama-server and a scratch data folder."""

    def setUp(self):
        self.tmp = temp_dir(self)
        self.host = fake_host(self)
        storage = Storage(self.tmp / "data")
        hub = HubClient(storage.cache, base_url=self.host.url)
        self.llama = fake_llama_server(self.tmp) if POSIX else None
        rt = runtime.Runtime(storage, override=str(self.llama) if self.llama else None)
        self.services = Services(storage=storage, runtime=rt, hub=hub, catalog=Catalog(storage, hub),
                                 jobs=JobManager(), supervisor=Supervisor(storage, rt.executable, load_timeout=20))
        self.addCleanup(self.services.supervisor.shutdown)
        avoid_default_ports(self)
        self.app = create_app(Settings(token=TOKEN, port=PORT), RunManager(), self.services)
        self.client = TestClient(self.app, base_url=f"http://127.0.0.1:{PORT}",
                                 headers={"Authorization": f"Bearer {TOKEN}"})
        self.model = make_gguf(QWEN35_9B, vocab=10, tail=b"w" * 3000)
        self.host.add_repo("org/M-GGUF", "rev1", {"M-Q8_0.gguf": self.model, "mmproj-F16.gguf": b"projector"})

    def download(self, body) -> Dict[str, Any]:
        job = self.client.post("/downloads", json=body)
        self.assertEqual(job.status_code, 202, job.text)
        job_id = job.json()["id"]
        return wait_for(lambda: next((j for j in self.client.get("/downloads").json()["jobs"]
                                      if j["id"] == job_id and j["state"] in ("done", "failed", "cancelled")), None),
                        what="download")

    def test_offline_setting_blocks_the_network(self):
        self.assertEqual(self.client.put("/settings", json={"offline": True}).json()["offline"], True)
        for method, path, body in (("get", "/models/search", None), ("get", "/models/hub?repo=org/M-GGUF", None),
                                   ("post", "/downloads", {"repo": "org/M-GGUF", "quant": "Q8_0"}),
                                   ("post", "/runtime/install", {})):
            response = getattr(self.client, method)(path, **({"json": body} if body is not None else {}))
            self.assertEqual((response.status_code, response.json()["error"]), (403, "offline"), path)
        self.assertEqual(self.host.requests, [])
        self.assertEqual(self.client.put("/settings", json={"offline": False}).json()["offline"], False)

    def test_recommendation_and_hub_listing(self):
        result = self.client.get("/models/recommendation").json()
        self.assertIn("memory", result)
        self.assertEqual(len(result["options"]), len(catalog_module.curated()["models"]))
        listing = self.client.get("/models/hub", params={"repo": "org/M-GGUF"}).json()
        self.assertEqual(listing["quants"][0]["quant"], "Q8_0")
        self.assertEqual(self.client.get("/models/hub", params={"repo": "../etc"}).status_code, 422)

    def test_download_then_delete(self):
        job = self.download({"repo": "org/M-GGUF", "quant": "Q8_0"})
        self.assertEqual(job["state"], "done", job["error"])
        installed = self.client.get("/models/installed").json()
        self.assertEqual([m["id"] for m in installed["models"]], ["org/M-GGUF:Q8_0"])
        self.assertEqual(self.client.delete("/models/installed", params={"id": "org/M-GGUF:Q8_0"}).status_code, 200)
        self.assertEqual(self.client.delete("/models/installed", params={"id": "org/M-GGUF:Q8_0"}).status_code, 404)

    def test_runtime_status(self):
        status = self.client.get("/runtime").json()
        self.assertEqual(status["pinned_build"], runtime.PINNED)
        self.assertIn("recommended_variant", status)
        bad = self.client.post("/runtime/install", json={"variant": "amiga"})
        self.assertEqual(bad.status_code, 400)

    @unittest.skipUnless(POSIX, "the fake llama-server is started through a shell script")
    def test_servers_for_a_downloaded_model(self):
        self.assertEqual(self.download({"repo": "org/M-GGUF", "quant": "Q8_0"})["state"], "done")
        started = self.client.post("/servers/start", json={"model_id": "org/M-GGUF:Q8_0"})
        self.assertEqual(started.status_code, 202, started.text)
        wait_for(lambda: self.client.get("/servers").json()["servers"]["vision"]["state"] == "ready", what="ready")
        status = self.client.get("/servers").json()
        self.assertEqual(status["config"], {"server": {"url": status["servers"]["vision"]["url"], "model": "m-q8_0"}})
        busy = self.client.delete("/models/installed", params={"id": "org/M-GGUF:Q8_0"})
        self.assertEqual(busy.json()["error"], "model_in_use")
        # A run started now gets the servers' key
        self.assertEqual(self.app.state.manager.extra_env(), {"TABLE_EXTRACT_API_KEY": self.services.supervisor.api_key})
        self.assertEqual(self.client.post("/servers/stop").json()["servers"]["vision"]["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
