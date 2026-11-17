"""Tests for the desktop app's sidecar (table_extract/server). Run with: python -m unittest discover tests

Most tests replace the run subprocess with FakeProcess. The RealRuns tests start
the real runner against a fake model server on 127.0.0.1, so no model is needed.
"""

import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest import mock

warnings.filterwarnings("ignore", message=r"Using `httpx` with `starlette\.testclient`")

import pypdfium2 as pdfium  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from table_extract import runner  # noqa: E402
from table_extract.__main__ import main as cli_main  # noqa: E402
from table_extract.config import RunConfig  # noqa: E402
from table_extract.prompts import Guide, TriagePrompt  # noqa: E402
from table_extract.schema import BUILTIN_FIELDS  # noqa: E402
from table_extract.server import hardware  # noqa: E402
from table_extract.server.app import Settings, create_app  # noqa: E402
from table_extract.server.runs import RunManager  # noqa: E402
from table_extract.server.security import TOKEN_ENV  # noqa: E402

def setUpModule():
    # Apps built here keep their models, settings and server registry in a scratch folder
    data_dir = tempfile.mkdtemp()
    patcher = mock.patch.dict(os.environ, {"TABLE_EXTRACT_DATA_DIR": data_dir})
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    unittest.addModuleCleanup(shutil.rmtree, data_dir, ignore_errors=True)


TOKEN = "test-token"
PORT = 8765
BASE_URL = f"http://127.0.0.1:{PORT}"
POSIX = os.name == "posix"


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


def make_pdf(path: Path, pages: int = 1) -> Path:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(200, 200)
    pdf.save(str(path))
    return path


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def parse_sse(text: str) -> List[Dict[str, str]]:
    messages = []
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            name, _, value = line.partition(": ")
            fields[name] = value
        if fields:
            messages.append(fields)
    return messages


def event(name: str, **fields) -> str:
    return json.dumps({"time": "2026-10-15T00:00:00+00:00", "event": name, **fields}) + "\n"


class FakeProcess:
    """Stands in for subprocess.Popen: records how it was started and exits when told to."""

    def __init__(self, args, kwargs, stderr=""):
        self.args, self.kwargs = args, kwargs
        self.pid = 9_999_999  # above every system's pid limit
        self.returncode = None
        self.stdin = io.StringIO()
        self.stderr = io.StringIO(stderr)
        self._exited = threading.Event()

    def wait(self, timeout=None):
        if not self._exited.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def exit(self, code: int) -> None:
        self.returncode = code
        self._exited.set()

    def kill(self) -> None:
        self.exit(-9)


class FakePopen:
    def __init__(self):
        self.processes: List[FakeProcess] = []
        self.stderr = ""

    def __call__(self, args, **kwargs):
        process = FakeProcess(args, kwargs, self.stderr)
        self.processes.append(process)
        return process

    @property
    def last(self) -> FakeProcess:
        return self.processes[-1]


class SidecarTest(unittest.TestCase):
    """A sidecar app over a FakePopen, plus a temporary folder with one PDF."""

    def setUp(self):
        self.tmp = temp_dir(self)
        self.pdf = make_pdf(self.tmp / "doc.pdf")
        self.out = self.tmp / "results"
        self.popen = FakePopen()
        self.manager = RunManager(popen=self.popen, cancel_grace=0.3)
        self.settings = Settings(token=TOKEN, port=PORT, poll_interval=0.01)
        self.app = create_app(self.settings, self.manager)
        self.client = TestClient(self.app, base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"})

    def tearDown(self):
        for process in self.popen.processes:
            if process.returncode is None:
                process.exit(-9)

    def config(self, **changes) -> Dict[str, Any]:
        data = {"inputs": [str(self.pdf)], "output_dir": str(self.out)}
        data.update(changes)
        return data

    def start(self, **body) -> Dict[str, Any]:
        response = self.client.post("/run", json={"config": self.config(), **body})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def append_events(self, *lines: str) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        with open(self.out / "events.jsonl", "a", encoding="utf-8") as f:
            f.writelines(lines)


class About(SidecarTest):
    def test_libraries_match_the_sidecar_requirements(self):
        from table_extract.server.about import LIBRARIES
        text = (Path(__file__).parent.parent / "packaging" / "requirements_sidecar.txt").read_text(encoding="utf-8")
        shipped = text.split("# Build only")[0]
        pins = dict(line.split("==") for line in shipped.splitlines() if line and not line.startswith("#"))
        self.assertEqual(sorted(LIBRARIES), sorted(pins))

        about = self.client.get("/about").json()
        libraries = {lib["name"].lower(): lib for lib in about["libraries"]}
        for name, version in pins.items():
            self.assertEqual(libraries[name]["version"], version, name)
            self.assertTrue(libraries[name]["license"], name)
        self.assertEqual(libraries["python"]["license"], "PSF-2.0")
        self.assertEqual(about["llama_cpp"]["license"], "MIT")
        self.assertTrue(about["models"])
        for model in about["models"]:
            self.assertTrue(model["license"] and model["license_url"], model["label"])


class Security(SidecarTest):
    def test_token_required(self):
        bare = TestClient(self.app, base_url=BASE_URL)
        self.assertEqual(bare.get("/health").status_code, 401)
        self.assertEqual(bare.get("/health", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(bare.get("/health", headers={"Authorization": f"Basic {TOKEN}"}).status_code, 401)
        self.assertEqual(bare.get("/nowhere").status_code, 401)  # checked before routing
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_host_checked(self):
        for host in ("evil.example", "evil.example:8765", "127.0.0.1:9999", "[::1]:8765", "127.0.0.1.evil.example"):
            response = self.client.get("/health", headers={"Host": host})
            self.assertEqual(response.status_code, 400, host)
            self.assertEqual(response.json()["error"], "bad_host")
        self.assertEqual(self.client.get("/health", headers={"Host": f"localhost:{PORT}"}).status_code, 200)

    def test_no_cors_headers(self):
        preflight = self.client.options("/run", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization"})
        response = self.client.get("/health", headers={"Origin": "https://evil.example"})
        for r in (preflight, response):
            self.assertFalse([h for h in r.headers if h.lower().startswith("access-control-")], r.headers)

    def test_no_docs_pages(self):
        # Swagger UI would load its scripts from a CDN
        self.assertEqual(self.client.get("/docs").status_code, 404)
        self.assertEqual(self.client.get("/redoc").status_code, 404)


class ConfigEndpoints(SidecarTest):
    def test_defaults(self):
        data = self.client.get("/config/defaults").json()
        self.assertEqual(data["inputs"], [])
        self.assertIsNone(data["output_dir"])
        self.assertEqual(data["triage"]["prompt"]["instructions"], TriagePrompt().instructions)
        self.assertEqual(list(data["extraction"]["prompt"]["field_descriptions"]), BUILTIN_FIELDS)
        self.assertEqual(data["triage"]["dpi"], 150)
        # Filling in the two blanks gives a valid config
        result = self.client.post("/config/validate", json=self.config(**{
            k: v for k, v in data.items() if k not in ("inputs", "output_dir")})).json()
        self.assertTrue(result["valid"], result["errors"])

    def test_schema(self):
        schema = self.client.get("/config/schema").json()
        self.assertIn("inputs", schema["properties"])

    def test_errors_are_friendly(self):
        result = self.client.post("/config/validate", json=self.config(
            triage={"dpi": 5}, server={"url": "http://example.com/v1/chat/completions"})).json()
        self.assertFalse(result["valid"])
        self.assertIsNone(result["config"])
        by_field = {e["field"]: e for e in result["errors"]}
        self.assertEqual(set(by_field), {"triage.dpi", "server"})
        self.assertEqual(by_field["triage.dpi"]["loc"], ["triage", "dpi"])
        self.assertEqual(by_field["triage.dpi"]["type"], "greater_than_equal")
        self.assertTrue(by_field["server"]["message"].startswith("http://example.com/v1/chat/completions is not"))
        self.assertNotIn("input", by_field["server"])  # submitted values are not echoed

        result = self.client.post("/config/validate", json={"inputs": []}).json()
        self.assertEqual({e["field"] for e in result["errors"]}, {"inputs", "output_dir"})

    def test_prompts_are_written_from_the_answers_whatever_else_is_wrong(self):
        result = self.client.post("/config/validate", json={
            "inputs": [], "guide": {"entity": "person"}, "triage": {"prompt": {"raw": "edited, no marker"}}}).json()
        self.assertIn("triage.prompt.raw", {e["field"] for e in result["errors"]})
        self.assertEqual(result["prompts"]["triage"], TriagePrompt().editable(Guide(entity="person")))
        self.assertIn("main person", result["prompts"]["extraction"])

        result = self.client.post("/config/validate", json={"inputs": [], "guide": {"entity": ""}}).json()
        self.assertEqual(result["prompts"], {"triage": None, "extraction": None})

    def test_paths_must_be_absolute(self):
        result = self.client.post("/config/validate", json={"inputs": ["doc.pdf"], "output_dir": "results"}).json()
        self.assertEqual([(e["field"], e["type"]) for e in result["errors"]],
                         [("inputs[0]", "absolute_path"), ("output_dir", "absolute_path")])

    def test_inputs_are_listed(self):
        folder = self.tmp / "folder"
        folder.mkdir()
        make_pdf(folder / "a.pdf")
        make_pdf(folder / "b.pdf")
        result = self.client.post("/config/validate", json=self.config(inputs=[str(folder)])).json()
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual([Path(p).name for p in result["pdfs"]], ["a.pdf", "b.pdf"])

        result = self.client.post("/config/validate", json=self.config(inputs=[str(self.tmp / "missing.pdf")])).json()
        self.assertEqual(result["errors"][0]["field"], "inputs")
        self.assertIn("Input not found", result["errors"][0]["message"])

        empty = self.tmp / "empty"
        empty.mkdir()
        result = self.client.post("/config/validate", json=self.config(inputs=[str(empty)])).json()
        self.assertEqual(result["errors"][0]["type"], "no_pdfs")

    def test_chosen_pages_are_checked(self):
        make_pdf(self.tmp / "long.pdf", pages=3)
        inputs = [str(self.pdf), str(self.tmp / "long.pdf")]
        result = self.client.post("/config/validate", json=self.config(
            inputs=inputs, triage={"pages": {"long.pdf": "2-3", "doc.pdf": "1"}})).json()
        self.assertTrue(result["valid"], result["errors"])

        result = self.client.post("/config/validate", json=self.config(
            inputs=inputs, triage={"pages": {"long.pdf": "2-4", "gone.pdf": "1", "doc.pdf": "1 or 2"}})).json()
        by_document = {e["loc"][2]: e for e in result["errors"]}
        self.assertEqual(set(by_document), {"doc.pdf"})  # the syntax first: the documents are checked after
        self.assertIn("ranges such as", by_document["doc.pdf"]["message"])

        result = self.client.post("/config/validate", json=self.config(
            inputs=inputs, triage={"pages": {"long.pdf": "2-4", "gone.pdf": "1"}})).json()
        by_document = {e["loc"][2]: e for e in result["errors"]}
        self.assertEqual(by_document["long.pdf"]["message"], "This document has 3 pages.")
        self.assertEqual(by_document["gone.pdf"]["type"], "unknown_document")

    def test_output_folder_state(self):
        def state():
            return self.client.post("/config/validate", json=self.config()).json()["output"]["state"]

        self.assertEqual(state(), "new")
        self.out.mkdir()
        config = RunConfig.model_validate(self.config())
        (self.out / "run.json").write_text(json.dumps({"fingerprint": runner.fingerprint(config), "status": "cancelled"}))
        self.assertEqual(state(), "resume")
        (self.out / "run.json").write_text(json.dumps({"fingerprint": "something else"}))
        self.assertEqual(state(), "conflict")
        (self.out / "run.json").write_text(json.dumps({"config": {}}))  # prepared, never started
        self.assertEqual(state(), "new")

        result = self.client.post("/config/validate", json=self.config(output_dir=str(self.pdf))).json()
        self.assertEqual(result["output"]["state"], "invalid")
        self.assertEqual(result["errors"][0]["field"], "output_dir")

    def test_malformed_body(self):
        response = self.client.post("/config/validate", content="{not json",
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["field"], "")


class StartRun(SidecarTest):
    def test_start_writes_run_json_and_launches_runner(self):
        os.environ[TOKEN_ENV] = "should not reach the run"
        try:
            status = self.start()
        finally:
            del os.environ[TOKEN_ENV]
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["output_dir"], str(self.out))
        self.assertEqual(status["pdfs"], 1)

        record = json.loads((self.out / "run.json").read_text())
        self.assertNotIn("fingerprint", record)  # the runner treats it as a new run
        self.assertEqual(record["status"], "starting")
        self.assertEqual(RunConfig.model_validate(record["config"]).inputs, [self.pdf])

        process = self.popen.last
        self.assertEqual(process.args, [sys.executable, "-m", "table_extract", "run",
                                        str(self.out / "run.json"), "--stop-when-stdin-closes"])
        self.assertEqual(process.kwargs["stdin"], subprocess.PIPE)
        if POSIX:
            self.assertTrue(process.kwargs["start_new_session"])
        env = process.kwargs["env"]
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        self.assertNotIn(TOKEN_ENV, env)

    def test_one_run_at_a_time(self):
        self.start()
        response = self.client.post("/run", json={"config": self.config(output_dir=str(self.tmp / "other"))})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "run_active")
        self.popen.last.exit(0)
        wait_for(lambda: self.client.get("/run").json()["state"] == "finished", what="run end")
        self.start()
        self.assertEqual(len(self.popen.processes), 2)

    def test_invalid_config_is_refused(self):
        response = self.client.post("/run", json={"config": self.config(triage={"workers": 0})})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "invalid_config")
        self.assertEqual(response.json()["errors"][0]["field"], "triage.workers")
        self.assertEqual(self.popen.processes, [])

        response = self.client.post("/run", json={"overwrite": True})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"][0]["field"], "config")

    def test_conflict_needs_overwrite(self):
        self.out.mkdir()
        (self.out / "run.json").write_text(json.dumps({"fingerprint": "old", "created": "earlier"}))
        response = self.client.post("/run", json={"config": self.config()})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "output_conflict")
        self.assertEqual(response.json()["output"]["state"], "conflict")
        self.assertEqual(self.popen.processes, [])

        self.start(overwrite=True)
        self.assertEqual(self.popen.last.args[-1], "--overwrite")
        # The old fingerprint stays, so the runner deletes the old results before starting
        self.assertEqual(json.loads((self.out / "run.json").read_text())["fingerprint"], "old")

    def test_resume_keeps_the_record(self):
        self.out.mkdir()
        fp = runner.fingerprint(RunConfig.model_validate(self.config()))
        (self.out / "run.json").write_text(json.dumps({"fingerprint": fp, "created": "earlier", "status": "cancelled"}))
        self.start(overwrite=True)  # nothing to overwrite: same settings
        self.assertNotIn("--overwrite", self.popen.last.args)
        record = json.loads((self.out / "run.json").read_text())
        self.assertEqual((record["fingerprint"], record["created"]), (fp, "earlier"))

    def test_resume_a_run_from_before_the_prefilter_was_removed(self):
        self.out.mkdir()
        old_config = {**json.loads(RunConfig.model_validate(self.config()).model_dump_json()),
                      "prefilter": {"enabled": False, "top_k": 3}}
        (self.out / "run.json").write_text(json.dumps({"fingerprint": "from-an-older-version", "created": "earlier",
                                                        "status": "cancelled", "config": old_config}))
        self.start()
        self.assertNotIn("--overwrite", self.popen.last.args)
        # The runner only sees the new config, so the record carries the new fingerprint
        record = json.loads((self.out / "run.json").read_text())
        fp = runner.fingerprint(RunConfig.model_validate(self.config()))
        self.assertEqual((record["fingerprint"], record["created"]), (fp, "earlier"))
        self.assertNotIn("prefilter", record["config"])


class RunStatusAndEvents(SidecarTest):
    def test_idle(self):
        self.assertEqual(self.client.get("/run").json(), {"state": "idle"})
        response = self.client.get("/run/events")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "no_run")

    def test_progress_and_result(self):
        self.append_events(event("run_finished", status="failed"))  # an earlier run in the same folder
        self.start()
        self.append_events(
            event("run_started", message="Found 1 PDF(s)", files=["doc.pdf"]),
            event("stage_started", message="Triage: 4 page(s)", stage="triage", total=4),
            event("page_done", stage="triage", file="doc.pdf", page=1, done=1, total=4),
            event("warning", message="slow"),
            '{"event": "page_done", "stage": "triage", "do',  # still being written
        )
        status = self.client.get("/run").json()
        progress = status["progress"]
        self.assertEqual((progress["files"], progress["stage"], progress["done"], progress["total"]),
                         (1, "triage", 1, 4))
        self.assertEqual((progress["warnings"], progress["errors"], progress["finished"]), (1, 0, None))
        self.assertEqual(progress["last_message"], "slow")
        self.assertIsNone(status["result"])

        self.append_events('ne": 2, "total": 4}\n',
                           event("run_finished", message="Run done.", status="done", failed_pages=0, outputs={}))
        self.popen.last.exit(0)
        status = wait_for(lambda: (s := self.client.get("/run").json())["state"] == "finished" and s,
                          what="run end")
        self.assertEqual((status["result"], status["exit_code"]), ("done", 0))
        self.assertEqual(status["progress"]["done"], 2)

    def test_crash_without_final_event(self):
        self.popen.stderr = "Traceback (most recent call last):\nImportError: no module\n"
        self.start()
        self.popen.last.exit(1)
        status = wait_for(lambda: (s := self.client.get("/run").json())["state"] == "finished" and s,
                          what="run end")
        self.assertEqual(status["result"], "failed")
        wait_for(lambda: self.client.get("/run").json()["stderr"], what="stderr")
        self.assertEqual(self.client.get("/run").json()["stderr"][-1], "ImportError: no module")

    def test_event_stream(self):
        self.append_events(event("log", message="from an earlier run"))
        self.start()
        self.append_events(event("run_started", files=["doc.pdf"]))

        def finish():
            time.sleep(0.2)
            self.append_events(event("page_done", stage="triage", done=1, total=1),
                               event("run_finished", status="done"))
            self.popen.last.exit(0)

        threading.Thread(target=finish).start()
        response = self.client.get("/run/events")  # returns once the run has exited
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        messages = parse_sse(response.text)
        self.assertEqual([m["event"] for m in messages], ["run_started", "page_done", "run_finished", "end"])
        self.assertEqual(json.loads(messages[1]["data"])["done"], 1)
        self.assertEqual(json.loads(messages[-1]["data"])["result"], "done")
        ids = [int(m["id"]) for m in messages[:-1]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids[-1], (self.out / "events.jsonl").stat().st_size)

        # Reconnecting with the last id seen skips what was already received
        response = self.client.get("/run/events", headers={"Last-Event-ID": messages[0]["id"]})
        self.assertEqual([m["event"] for m in parse_sse(response.text)], ["page_done", "run_finished", "end"])


@unittest.skipUnless(POSIX, "process groups and signals are POSIX")
class CancelRun(SidecarTest):
    def setUp(self):
        super().setUp()
        self.signals: List[int] = []
        self.obeys_sigterm = True
        patcher = mock.patch("table_extract.server.runs.os.killpg", side_effect=self.on_signal)
        patcher.start()
        self.addCleanup(patcher.stop)

    def on_signal(self, pgid, signum):
        """What the runner does with the signals sent to its process group."""
        process = self.popen.last
        self.assertEqual(pgid, process.pid)
        self.signals.append(signum)
        if signum == signal.SIGTERM and self.obeys_sigterm:
            self.append_events(event("run_finished", status="cancelled"))
            process.exit(130)
        elif signum == signal.SIGKILL:
            process.exit(-signal.SIGKILL)

    def finished(self) -> Dict[str, Any]:
        return wait_for(lambda: (s := self.client.get("/run").json())["state"] == "finished" and s,
                        what="run end")

    def test_nothing_to_cancel(self):
        response = self.client.post("/run/cancel")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "no_active_run")

    def test_cancel_terminates_the_group(self):
        self.start()
        self.client.post("/run/cancel")
        status = self.finished()
        self.assertEqual((status["result"], status["exit_code"]), ("cancelled", 130))
        time.sleep(0.4)  # past the grace period: no SIGKILL for a run that stopped
        self.assertEqual(self.signals, [signal.SIGTERM])
        self.assertEqual(self.client.post("/run/cancel").status_code, 409)

    def test_cancel_escalates_to_kill(self):
        self.obeys_sigterm = False
        self.start()
        self.assertEqual(self.client.post("/run/cancel").json()["state"], "cancelling")
        self.client.post("/run/cancel")  # a second click sends nothing more
        status = self.finished()
        self.assertEqual(self.signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertEqual(status["result"], "cancelled")  # no run_finished event, but it was asked to stop

    def test_sidecar_exit_stops_the_run(self):
        with TestClient(self.app, base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
            client.post("/run", json={"config": self.config()})
            process = self.popen.last
        self.assertEqual(self.signals, [signal.SIGTERM])
        self.assertEqual(process.returncode, 130)

    def test_shutdown_kills_a_stuck_run(self):
        self.obeys_sigterm = False
        self.start()
        self.manager.shutdown(grace=0.1)
        self.assertEqual(self.signals, [signal.SIGTERM, signal.SIGKILL])


class Hardware(unittest.TestCase):
    LISTING = (
        "ggml_metal_init: found device: Apple M1 Pro\n"
        "Available devices:\n"
        "  BLAS: Accelerate (0 MiB, 0 MiB free)\n"
        "  MTL0: Apple M1 Pro (12124 MiB, 12123 MiB free)\n"
        "  CUDA0: NVIDIA GeForce RTX 4090 (24563 MiB, 23889 MiB free)\n"
        "  Vulkan1: Intel(R) Arc(TM) A770 Graphics (16225 MiB, 15000 MiB free)\n"
    )

    def test_parse_devices(self):
        devices = hardware.parse_devices(self.LISTING)
        self.assertEqual([(d["name"], d["backend"], d["gpu"]) for d in devices], [
            ("BLAS", "BLAS", False), ("MTL0", "MTL", True), ("CUDA0", "CUDA", True), ("Vulkan1", "Vulkan", True)])
        self.assertEqual(devices[3]["description"], "Intel(R) Arc(TM) A770 Graphics")
        self.assertEqual(devices[1]["memory_total_bytes"], 12124 * 1024 * 1024)
        self.assertEqual(devices[1]["memory_free_bytes"], 12123 * 1024 * 1024)

    def test_no_llama_server(self):
        result = hardware.probe(llama_server=str(temp_dir(self) / "llama-server"))
        self.assertFalse(result["llama_server"]["found"])
        self.assertEqual(result["devices"], [])
        self.assertGreater(result["memory"]["total_bytes"], 0)

    @unittest.skipUnless(POSIX, "uses a shell script as llama-server")
    def test_endpoint_runs_llama_server(self):
        folder = temp_dir(self)
        script = folder / "llama-server"
        listing = "\n".join(f"echo '{line}'" for line in self.LISTING.splitlines())
        script.write_text(f"#!/bin/sh\n[ \"$1\" = --list-devices ] || exit 3\n{listing}\n")
        script.chmod(0o755)
        failing = folder / "broken-llama-server"
        failing.write_text("#!/bin/sh\necho 'error: invalid argument: --list-devices' >&2\nexit 1\n")
        failing.chmod(0o755)

        app = create_app(Settings(token=TOKEN, port=PORT, llama_server=str(script)))
        client = TestClient(app, base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"})
        result = client.get("/hardware").json()
        self.assertEqual(result["llama_server"], {"path": str(script), "found": True, "error": None})
        self.assertEqual(len(result["devices"]), 4)

        result = hardware.probe(str(failing))
        self.assertEqual(result["devices"], [])
        self.assertIn("exit code 1", result["llama_server"]["error"])
        self.assertIn("invalid argument", result["llama_server"]["error"])


class RunnerHandOff(unittest.TestCase):
    """What the runner does with the run.json the sidecar writes."""

    def setUp(self):
        self.tmp = temp_dir(self)
        self.out = self.tmp / "out"
        self.out.mkdir()
        url = f"http://127.0.0.1:{closed_port()}/v1/chat/completions"  # fails fast: nothing listens
        self.config = RunConfig(inputs=[make_pdf(self.tmp / "doc.pdf")], output_dir=self.out,
                                server={"url": url})
        handler = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, handler)

    def run_cli(self, record: Dict[str, Any]) -> int:
        (self.out / "run.json").write_text(json.dumps(record))
        with mock.patch("sys.stdout", new=io.TextIOWrapper(io.BytesIO())):
            return cli_main(["run", str(self.out / "run.json")])

    def test_prepared_run_json_is_a_new_run(self):
        code = self.run_cli({"status": "starting", "config": json.loads(self.config.model_dump_json())})
        self.assertEqual(code, 1)  # failed on the missing server, not refused (2)
        record = json.loads((self.out / "run.json").read_text())
        self.assertEqual((record["status"], record["fingerprint"]), ("failed", runner.fingerprint(self.config)))

    def test_run_json_from_before_the_prefilter_was_removed_resumes(self):
        old_config = {**json.loads(self.config.model_dump_json()), "prefilter": {"enabled": False, "top_k": 3}}
        code = self.run_cli({"fingerprint": "from-an-older-version", "config": old_config})
        self.assertEqual(code, 1)  # failed on the missing server, not refused (2)
        old_config["prefilter"]["enabled"] = True  # that run left pages out of triage
        self.assertEqual(self.run_cli({"fingerprint": "from-an-older-version", "config": old_config}), 2)

    def test_other_settings_are_still_refused(self):
        code = self.run_cli({"fingerprint": "other", "config": json.loads(self.config.model_dump_json())})
        self.assertEqual(code, 2)

    def test_sigterm_interrupts_once(self):
        with self.assertRaises(KeyboardInterrupt):
            runner._raise_interrupt(signal.SIGTERM, None)
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)

    def test_lifeline(self):
        code = ("import signal, sys, time\n"
                "from table_extract.lifeline import stop_when_stdin_closes\n"
                "signal.signal(signal.SIGTERM, lambda *a: sys.exit(42))\n"
                "stop_when_stdin_closes(grace=5)\n"
                "print('ready', flush=True)\n"
                "time.sleep(30)\n")
        child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 cwd=Path(__file__).resolve().parents[1], text=True)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        child.stdin.close()  # what happens when the parent dies
        self.assertEqual(child.wait(timeout=10), 42)
        child.stdout.close()


class FakeModelServer(ThreadingHTTPServer):
    """Just enough of llama-server for triage: every page is "false", after `delay` seconds."""

    daemon_threads = True
    delay = 0.0
    slots = 2

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status: int, body: Dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/props":
                return self.reply(200, {"total_slots": self.server.slots})
            self.reply(200, {"data": []}) if self.path == "/v1/models" else self.reply(404, {})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path != "/v1/chat/completions":
                return self.reply(404, {})
            time.sleep(self.server.delay)
            self.reply(200, {"choices": [{"message": {"content": '{"triage_output": "false", "notes": ""}'},
                                          "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    def __init__(self):
        super().__init__(("127.0.0.1", 0), self.Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


class RealRuns(unittest.TestCase):
    """The sidecar running the real runner as a subprocess, against FakeModelServer."""

    def setUp(self):
        self.model = FakeModelServer()
        self.addCleanup(self.model.server_close)
        self.addCleanup(self.model.shutdown)
        self.tmp = temp_dir(self)
        self.out = self.tmp / "out"
        self.manager = RunManager(cancel_grace=10)
        self.addCleanup(self.manager.shutdown)
        app = create_app(Settings(token=TOKEN, port=PORT, poll_interval=0.05), self.manager)
        self.client = TestClient(app, base_url=BASE_URL, headers={"Authorization": f"Bearer {TOKEN}"})

    def config(self, pages: int) -> Dict[str, Any]:
        return {
            "inputs": [str(make_pdf(self.tmp / "doc.pdf", pages))],
            "output_dir": str(self.out),
            "server": {"url": f"http://127.0.0.1:{self.model.server_address[1]}/v1/chat/completions",
                       "model": "fake"},
            "triage": {"dpi": 36},
        }

    def run_to_end(self, config: Dict[str, Any]) -> Dict[str, Any]:
        response = self.client.post("/run", json={"config": config})
        self.assertEqual(response.status_code, 202, response.text)
        return wait_for(lambda: (s := self.client.get("/run").json())["state"] == "finished" and s,
                        timeout=60, what="run end")

    def test_run_and_stream(self):
        status = self.run_to_end(self.config(pages=2))
        self.assertEqual((status["result"], status["exit_code"]), ("done", 0), status)
        record = json.loads((self.out / "run.json").read_text())
        self.assertEqual(record["status"], "done")
        self.assertIsNone(record["config"]["triage"]["workers"])  # automatic, as asked
        self.assertEqual(record["servers"]["triage"]["workers"], 2)  # one per server slot
        names = [m["event"] for m in parse_sse(self.client.get("/run/events").text)]
        self.assertEqual(names[0], "run_started")
        self.assertEqual(names.count("page_done"), 2)
        self.assertEqual(names[-2:], ["run_finished", "end"])

    @unittest.skipUnless(POSIX, "cancel by signal")
    def test_cancel_then_resume(self):
        self.model.delay = 0.3
        config = self.config(pages=30)
        self.assertEqual(self.client.post("/run", json={"config": config}).status_code, 202)
        wait_for(lambda: self.client.get("/run").json()["progress"]["done"] >= 1, timeout=60, what="first page")
        self.client.post("/run/cancel")
        status = wait_for(lambda: (s := self.client.get("/run").json())["state"] == "finished" and s,
                          timeout=9, what="cancel")  # within the grace period: it stopped by itself
        self.assertEqual((status["result"], status["exit_code"]), ("cancelled", 130), status)
        self.assertEqual(json.loads((self.out / "run.json").read_text())["status"], "cancelled")
        done_before = status["progress"]["done"]
        self.assertLess(done_before, 30)

        self.model.delay = 0
        self.assertEqual(self.client.post("/config/validate", json=config).json()["output"]["state"], "resume")
        status = self.run_to_end(config)
        self.assertEqual(status["result"], "done", status)
        names = [m["event"] for m in parse_sse(self.client.get("/run/events").text)]
        self.assertEqual(names.count("page_done"), 30 - done_before)  # only the pages left


if __name__ == "__main__":
    unittest.main()
