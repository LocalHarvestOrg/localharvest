"""Run the whole pipeline from one RunConfig: triage, then extraction.

Everything lands in config.output_dir:

  run.json               the full config, prompt hashes, servers, code version and status
  events.jsonl           progress, for the desktop app (see events.py)
  prompt_triage.txt      the prompts as sent
  prompt_extraction.txt
  triage.csv             one decision per page, by the model or chosen by hand
  triage_times.csv       seconds per page, per file
  relationships.csv      the extracted relationships

Running again with the same config and output folder resumes where the
previous run stopped. A different config needs a new folder, or overwrite.
run.json can itself be the config file (`python -m table_extract run
<folder>/run.json`): the desktop app writes it with the config and no
fingerprint before starting a run, which counts as a new run.
"""

import json
import signal
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .config import RunConfig, ServerConfig, api_key, resolve_workers
from .events import EventLog
from .extract import ContextBudget, run_extraction
from .llm import ChatClient
from .prompts import prompt_hash
from .triage import pages_by_file, pages_without_triage, run_triage

OUTPUTS = {
    "triage": "triage.csv",
    "triage_times": "triage_times.csv",
    "relationships": "relationships.csv",
}


def find_pdfs(inputs: List[Path], recursive: bool = False) -> List[Path]:
    """The PDFs among the inputs (files, or folders searched for *.pdf), in a stable order."""
    files: List[Path] = []
    for item in inputs:
        item = item.expanduser()
        if item.is_dir():
            found = item.rglob("*") if recursive else item.iterdir()
            files += sorted(p for p in found if p.is_file() and p.suffix.lower() == ".pdf")
        elif item.is_file():
            if item.suffix.lower() != ".pdf":
                raise ValueError(f"Not a PDF: {item}")
            files.append(item)
        else:
            raise ValueError(f"Input not found: {item}")

    unique, seen = [], set()
    for path in files:
        if path.resolve() not in seen:
            seen.add(path.resolve())
            unique.append(path)
    # Results are keyed by file name, so two inputs may not share one
    by_name: Dict[str, List[Path]] = {}
    for path in unique:
        by_name.setdefault(path.name, []).append(path)
    clashes = {name: paths for name, paths in by_name.items() if len(paths) > 1}
    if clashes:
        listing = "; ".join(f"{name}: {', '.join(str(p) for p in paths)}" for name, paths in clashes.items())
        raise ValueError(f"Several inputs share a file name, rename them first: {listing}")
    return unique


def fingerprint(config: RunConfig) -> str:
    """Identifies what a run computes; settings that only change speed are left out."""
    data = config.model_dump_json(exclude={"output_dir": True, "triage": {"workers"}, "extraction": {"workers"}})
    return sha256(data.encode("utf-8")).hexdigest()[:16]


def same_run(record: Dict[str, Any], config: RunConfig) -> bool:
    """Whether a run.json record holds a run of these settings, so it can be resumed.

    A run.json from before the page pre-filter was removed has a fingerprint
    that covered it: that run is the same if the pre-filter was off and the
    rest matches. A run that used it sent only some pages to triage, so it is
    not resumed.
    """
    if record["fingerprint"] == fingerprint(config):
        return True
    stored = record.get("config")
    if not isinstance(stored, dict) or not isinstance(stored.get("prefilter"), dict):
        return False
    if stored["prefilter"].get("enabled"):
        return False
    try:
        return fingerprint(RunConfig.model_validate(stored)) == fingerprint(config)
    except ValueError:
        return False


def _git_commit() -> Optional[str]:
    if getattr(sys, "frozen", False):  # the desktop app: written in at build time (packaging/sidecar.spec)
        baked = Path(__file__).parent / "build_commit.txt"
        return baked.read_text(encoding="utf-8").strip() or None if baked.exists() else None
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _raise_interrupt(signum, frame):
    # Once only: a second SIGTERM during cleanup would skip writing the final
    # status. After that only SIGKILL stops the run.
    signal.signal(signum, signal.SIG_IGN)
    raise KeyboardInterrupt


def _client(server: ServerConfig, config: RunConfig) -> ChatClient:
    return ChatClient(server.url, server.resolved_model, api_key(), config.sampling.payload())


def run(config: RunConfig, overwrite: bool = False) -> int:
    """Run the pipeline; returns a process exit code (0 done, 1 failed, 2 refused, 130 cancelled)."""
    out = config.output_dir.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    events = EventLog(out / "events.jsonl")
    run_json = out / "run.json"
    paths = {key: out / name for key, name in OUTPUTS.items()}

    record: Dict[str, Any] = {}
    if run_json.exists():
        record = json.loads(run_json.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or "fingerprint" not in record:
            record = {}  # a config file, or a run the desktop app prepared and has not started
        elif not same_run(record, config):
            if not overwrite:
                events.error(f"{out} already holds a run with different settings. "
                             "Choose another output folder, or overwrite it.")
                return 2
            for path in paths.values():
                path.unlink(missing_ok=True)
            record = {}
        else:
            events.info(f"Resuming the run in {out}")

    try:
        signal.signal(signal.SIGTERM, _raise_interrupt)
    except ValueError:
        pass  # not in the main thread

    # The prompts as sent: the templates with the answers of the guide filled in
    triage_prompt = config.triage.prompt.render(config.guide)
    extraction_prompt = config.extraction.prompt.render(config.guide)

    record.update({
        "app": "table_extract",
        "version": __version__,
        "git_commit": _git_commit(),
        "created": record.get("created") or _now(),
        "updated": _now(),
        "status": "running",
        "fingerprint": fingerprint(config),
        "config": json.loads(config.model_dump_json()),
        "prompts": {
            "triage_sha256": prompt_hash(triage_prompt),
            "extraction_sha256": prompt_hash(extraction_prompt),
        },
        "outputs": {key: path.name for key, path in paths.items()},
    })

    def save_record():
        run_json.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    status, failed = "failed", 0
    try:
        pdfs = find_pdfs(config.inputs, config.recursive)
        if not pdfs:
            raise ValueError("No PDF files found in the inputs")
        record["inputs"] = [str(p.resolve()) for p in pdfs]
        unknown = sorted(set(config.triage.pages) - {p.name for p in pdfs})
        if unknown:
            events.warning(f"Pages were chosen for documents that are not in the inputs: {', '.join(unknown)}")
        save_record()
        (out / "prompt_triage.txt").write_text(triage_prompt, encoding="utf-8")
        (out / "prompt_extraction.txt").write_text(extraction_prompt, encoding="utf-8")
        events.emit("run_started", f"Run folder: {out}\nFound {len(pdfs)} PDF(s)",
                    output_dir=str(out), files=[p.name for p in pdfs])

        triage_client = _client(config.triage_server(), config) if config.triage.enabled else None
        extraction_client = _client(config.extraction_server(), config)
        servers = {}
        # The stage settings with workers resolved: automatic means one page per server slot
        stages = {"triage": config.triage, "extraction": config.extraction}
        for stage, client in (("triage", triage_client), ("extraction", extraction_client)):
            if client is None:
                continue
            client.check()
            info = client.server_info()
            automatic = stages[stage].workers is None
            workers = resolve_workers(stages[stage].workers, info["slots"])
            stages[stage] = stages[stage].model_copy(update={"workers": workers})
            servers[stage] = {"url": client.url, "model": client.model, **info, "workers": workers}
            events.info(f"{stage.capitalize()} model: {client.model} at {client.url}")
            if automatic:
                source = "one per server slot" if info["slots"] else "the server does not report its slots"
                events.info(f"{stage.capitalize()} sends {workers} page(s) at once ({source})")
            elif info["slots"] and workers > info["slots"]:
                events.warning(f"{stage.capitalize()} uses {workers} workers but the server has "
                               f"{info['slots']} slot(s); the extra requests only queue.")
        record["servers"] = servers
        save_record()

        # The text prompt alone already shows whether the budgets can fit; pages
        # add the image on top, which the extraction stage checks on its first answer
        n_ctx = servers["extraction"].get("n_ctx")
        prompt_tokens = extraction_client.count_tokens(extraction_prompt)
        ContextBudget(n_ctx, max(config.extraction.max_tokens_schedule), events).check(
            prompt_tokens or 0, "The extraction instructions alone")

        if config.triage.enabled:
            failed += run_triage(pdfs, paths["triage"], paths["triage_times"], triage_client,
                                 stages["triage"], events, guide=config.guide)
            pages = pages_by_file(paths["triage"], positive_only=True)
        else:
            pages = pages_without_triage(pdfs, config.triage, events)

        failed += run_extraction(pdfs, paths["relationships"], extraction_client, stages["extraction"],
                                 events, pages, n_ctx, guide=config.guide)
        status = "done"
    except KeyboardInterrupt:
        status = "cancelled"
        events.warning("Run cancelled. Run it again with the same settings and folder to resume.")
    except Exception as e:
        events.error(f"Run failed: {e}")
    finally:
        record.update({"status": status, "updated": _now(), "failed_pages": failed})
        save_record()
        events.emit("run_finished", f"Run {status}." + (f" {failed} page(s) failed." if failed else ""),
                    status=status, failed_pages=failed,
                    outputs={key: str(path) for key, path in paths.items() if path.exists()})
    return {"done": 0, "cancelled": 130}.get(status, 1)
