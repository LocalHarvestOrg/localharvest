"""Model catalog: the curated list, Hub models with per-quantization estimates, installed models, the recommendation.

A model is a set of GGUF files from one Hugging Face repo at one commit: the
weights (one file, or split parts) and the vision projector. Installed models
live in <data>/models/<owner>/<repo>/, each described by a manifest
<quant>.json written once every file is downloaded and verified:

    {"id": "unsloth/Qwen3.5-9B-GGUF:Q8_0", "alias": "qwen3.5-9b-q8", "repo": ..., "revision": ...,
     "quant": "Q8_0", "files": [{path, size, sha256}], "mmproj": {...}, "context": 20480,
     "server_flags": [...], "hparams": {...}, "role": "vision", "curated": "qwen3.5-9b-q8"}

The model name runs send (config.server.model) is the alias, which llama-server
is started with.
"""

import json
import re
import shutil
import time
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import estimate
from .downloads import DownloadError, FileSpec, Job, check_disk_space, fetch
from .hub import HubClient, group_files, preferred_projector
from .storage import Storage, read_json, write_json

DEFAULT_CONTEXT = 20480  # a 200 DPI page and the prompt (about 6k tokens) plus the largest output budget (12k)


def curated() -> Dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("models.json").read_text(encoding="utf-8"))


def curated_entry(model_id: str) -> Optional[Dict[str, Any]]:
    return next((entry for entry in curated()["models"] if entry["id"] == model_id), None)


def benchmark(curated_id: str) -> Optional[Dict[str, Any]]:
    """A curated model's scores on the oihho benchmark (run outside this repository), None if not scored.

    Per stage: page-level triage, and relationships found by extraction
    (matched on the relation's name), each with the GPU its speed was measured on.
    """
    path = resources.files(__package__).joinpath("benchmarks").joinpath(f"{curated_id}.json")
    if not path.is_file():
        return None
    summary = json.loads(path.read_text(encoding="utf-8"))
    result: Dict[str, Any] = {"scored": summary.get("scored")}
    for stage, sets, metric in (("triage", ("triage", "triage-noprefilter"), "pages_found"),
                                ("extraction", ("extraction",), "relationships_found")):
        scored = next((summary[s] for s in sets if s in summary), None)
        if not scored or metric not in scored:
            continue
        speed = (scored.get("speed") or {}).get(stage) or {}
        gpu = scored.get("gpu") or [d.get("description") for d in (summary.get("hardware") or {}).get("gpu") or []]
        result[stage] = {
            **{k: scored[metric][k] for k in ("precision", "recall", "f1")},
            "documents": scored.get("documents"), "pages": scored.get("pages"),
            "seconds_per_page": speed.get("seconds_per_page"), "gpu": ", ".join(filter(None, gpu)) or None,
        }
    return result if len(result) > 1 else None


def model_id(repo: str, quant: str) -> str:
    return f"{repo}:{quant}"


def default_label(repo: str, quant: str) -> str:
    name = re.sub(r"[-_]GGUF$", "", repo.split("/")[-1], flags=re.IGNORECASE)
    return f"{name} {quant}"


def default_alias(repo: str, quant: str) -> str:
    return re.sub(r"[^a-z0-9._]+", "-", default_label(repo, quant).lower()).strip("-")


class Catalog:
    def __init__(self, storage: Storage, hub: HubClient):
        self.storage = storage
        self.hub = hub

    # Installed models

    def _folder(self, repo: str) -> Path:
        return self.storage.models.joinpath(*repo.split("/"))

    def _manifest_path(self, repo: str, quant: str) -> Path:
        return self._folder(repo) / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', quant)}.json"

    def installed(self) -> List[Dict[str, Any]]:
        models = []
        for manifest in sorted(self.storage.models.glob("*/*/*.json")):
            record = read_json(manifest)
            if not isinstance(record, dict) or "id" not in record:
                continue
            folder = manifest.parent
            files = record["files"] + ([record["mmproj"]] if record.get("mmproj") else [])
            record["complete"] = all((folder / f["path"]).is_file() for f in files)
            record["model_path"] = str(folder / record["files"][0]["path"])
            record["mmproj_path"] = str(folder / record["mmproj"]["path"]) if record.get("mmproj") else None
            record["size"] = sum(f.get("size") or 0 for f in files)
            models.append(record)
        return models

    def get_installed(self, mid: str) -> Optional[Dict[str, Any]]:
        return next((m for m in self.installed() if m["id"] == mid and m["complete"]), None)

    def remove(self, mid: str) -> bool:
        """Delete an installed model's manifest and the files no other installed model uses."""
        models = self.installed()
        target = next((m for m in models if m["id"] == mid), None)
        if target is None:
            return False
        folder = self._folder(target["repo"])
        used_elsewhere = {f["path"] for m in models if m["id"] != mid and m["repo"] == target["repo"]
                          for f in m["files"] + ([m["mmproj"]] if m.get("mmproj") else [])}
        for f in target["files"] + ([target["mmproj"]] if target.get("mmproj") else []):
            if f["path"] not in used_elsewhere:
                (folder / f["path"]).unlink(missing_ok=True)
                (folder / (f["path"] + ".part")).unlink(missing_ok=True)
        self._manifest_path(target["repo"], target["quant"]).unlink(missing_ok=True)
        return True

    # Estimates

    def assess(self, spec: Dict[str, Any], mem: Dict[str, Any]) -> Dict[str, Any]:
        weights = sum(f.get("size") or 0 for f in spec["files"])
        mmproj = (spec.get("mmproj") or {}).get("size") or 0
        return estimate.assess(weights, spec["hparams"], spec.get("context") or DEFAULT_CONTEXT, mem, mmproj)

    def curated_options(self, mem: Dict[str, Any]) -> List[Dict[str, Any]]:
        installed = {m["id"] for m in self.installed() if m["complete"]}
        options = []
        for entry in curated()["models"]:
            mid = model_id(entry["repo"], entry["quant"])
            options.append({
                **{k: v for k, v in entry.items() if k != "hparams"},
                "model_id": mid, "installed": mid in installed,
                "download_bytes": sum(f["size"] for f in entry["files"]) + entry["mmproj"]["size"],
                "estimate": self.assess(entry, mem), "benchmark": benchmark(entry["id"]),
            })
        return options

    def recommendation(self, mem: Dict[str, Any]) -> Dict[str, Any]:
        """One choice for this computer: the first curated model that fits on the GPU, else the best fallback."""
        options = self.curated_options(mem)
        if not mem["known"]:
            return {"model": None, "reason": "llama.cpp is needed to check this computer's graphics memory.",
                    "options": options}
        for wanted in ("gpu", "partial", "cpu"):
            fitting = [o for o in options if o["estimate"]["verdict"] == wanted]
            if fitting:
                # Off the GPU every gigabyte is slow: take the smallest
                choice = fitting[0] if wanted == "gpu" else min(fitting, key=lambda o: o["estimate"]["total_bytes"])
                reasons = {
                    "gpu": "It fits in the memory your graphics processor can use.",
                    "partial": "No model fits entirely in your graphics memory; this one runs partly on "
                               "the processor, which is slower.",
                    "cpu": "No graphics processor was found; the model runs on the processor, which is slow.",
                }
                return {"model": choice, "reason": reasons[wanted], "options": options}
        return {"model": None, "reason": "This computer does not have enough memory for any of the models.",
                "options": options}

    def hub_options(self, repo: str, mem: Dict[str, Any], revision: Optional[str] = None,
                    context: Optional[int] = None) -> Dict[str, Any]:
        """A Hub repo's quantizations, each with its download size and memory verdict."""
        info = self.hub.model_info(repo)
        revision = revision or info["revision"]
        grouped = group_files(self.hub.files(repo, revision))
        projector = preferred_projector(grouped["projectors"])
        if not grouped["quants"]:
            raise ValueError(f"{repo} has no GGUF model files.")
        if projector is None:
            raise ValueError(f"{repo} has no vision projector (mmproj file), so it cannot read pages.")
        # Hyperparameters are the same for every quantization: read the smallest file's header
        hp = estimate.hparams(self.hub.header(repo, revision, grouped["quants"][0]["files"][0]["path"]))
        installed = {m["id"] for m in self.installed() if m["complete"]}
        quants = []
        for q in grouped["quants"]:
            spec = {"files": q["files"], "mmproj": projector, "hparams": hp, "context": context or DEFAULT_CONTEXT}
            mid = model_id(repo, q["quant"])
            quants.append({"quant": q["quant"], "model_id": mid, "installed": mid in installed,
                           "files": q["files"], "download_bytes": q["size"] + (projector.get("size") or 0),
                           "estimate": self.assess(spec, mem)})
        return {"repo": info["repo"], "revision": revision, "gated": info["gated"], "mmproj": projector,
                "architecture": hp["architecture"], "trained_context": hp["context_length"], "quants": quants}

    # Downloads

    def spec_for(self, repo: Optional[str] = None, quant: Optional[str] = None, revision: Optional[str] = None,
                 curated_id: Optional[str] = None, context: Optional[int] = None) -> Dict[str, Any]:
        """Everything needed to download and later serve a model, as its manifest will hold it."""
        if curated_id:
            entry = curated_entry(curated_id)
            if entry is None:
                raise ValueError(f"Unknown model: {curated_id}")
            return {
                "id": model_id(entry["repo"], entry["quant"]), "alias": entry["id"], "label": entry["label"],
                "repo": entry["repo"], "revision": entry["revision"], "quant": entry["quant"],
                "files": entry["files"], "mmproj": entry.get("mmproj"),
                "context": context or entry["context"], "server_flags": entry["server_flags"],
                "hparams": entry["hparams"], "role": "vision",
                "curated": entry["id"],
            }
        if not repo or not quant:
            raise ValueError("Give a curated model, or a repo and a quantization.")
        revision = revision or self.hub.model_info(repo)["revision"]
        grouped = group_files(self.hub.files(repo, revision))
        chosen = next((q for q in grouped["quants"] if q["quant"].upper() == quant.upper()), None)
        if chosen is None:
            raise ValueError(f"{repo} has no {quant} files.")
        projector = preferred_projector(grouped["projectors"])
        if projector is None:
            raise ValueError(f"{repo} has no vision projector (mmproj file), so it cannot read pages.")
        hp = estimate.hparams(self.hub.header(repo, revision, chosen["files"][0]["path"]))
        return {
            "id": model_id(repo, chosen["quant"]), "alias": default_alias(repo, chosen["quant"]),
            "label": default_label(repo, chosen["quant"]), "repo": repo, "revision": revision,
            "quant": chosen["quant"], "files": chosen["files"], "mmproj": projector,
            "context": context or DEFAULT_CONTEXT, "server_flags": ["--reasoning", "off"],
            "hparams": hp, "role": "vision", "curated": None,
        }

    def download_size(self, spec: Dict[str, Any]) -> int:
        files = spec["files"] + ([spec["mmproj"]] if spec.get("mmproj") else [])
        return sum(f.get("size") or 0 for f in files)

    def download(self, spec: Dict[str, Any], job: Job, progress) -> Dict[str, Any]:
        """Job work: fetch every file of the model that is not already here, then write its manifest."""
        folder = self._folder(spec["repo"])
        files = spec["files"] + ([spec["mmproj"]] if spec.get("mmproj") else [])
        root = folder.resolve()
        for f in files:
            if root not in (folder / f["path"]).resolve().parents:
                raise DownloadError(f"Refusing a file outside the model folder: {f['path']}")
        missing = [f for f in files if not (folder / f["path"]).is_file()]
        remaining = sum((f.get("size") or 0) - _part_size(folder / f["path"]) for f in missing)
        check_disk_space(folder, max(remaining, 0))
        headers = self.hub.auth_headers()
        for f in files:
            if f not in missing:
                progress(f.get("size") or 0)
                continue
            fetch(self.hub.session,
                  FileSpec(url=self.hub.download_url(spec["repo"], spec["revision"], f["path"]),
                           dest=folder / f["path"], size=f.get("size"), sha256=f.get("sha256"), headers=headers),
                  job.cancel, progress, on_verify=lambda: setattr(job, "state", "verifying"))
            job.state = "downloading"
        manifest = {k: spec[k] for k in ("id", "alias", "label", "repo", "revision", "quant", "files", "mmproj",
                                         "context", "server_flags", "hparams", "role", "curated")}
        manifest["installed"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_json(self._manifest_path(spec["repo"], spec["quant"]), manifest)
        return {"model_id": spec["id"]}

    def disk_usage(self) -> Dict[str, Any]:
        root = self.storage.models
        root.mkdir(parents=True, exist_ok=True)
        used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        return {"models_bytes": used, "free_bytes": shutil.disk_usage(root).free, "folder": str(root)}


def _part_size(path: Path) -> int:
    part = path.with_name(path.name + ".part")
    return part.stat().st_size if part.exists() else 0
