"""Checks on a config from the app, before it is run: settings, inputs and output folder."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from pydantic import ValidationError

from .. import pdf
from ..config import RunConfig
from ..prompts import ExtractionPrompt, Guide, TriagePrompt
from ..runner import find_pdfs, same_run


def _field(loc: Sequence[Any]) -> str:
    """("inputs", 0) -> "inputs[0]", ("triage", "dpi") -> "triage.dpi"."""
    return "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in loc).lstrip(".")


def problem(loc: Sequence[Any], message: str, kind: str) -> Dict[str, Any]:
    return {"field": _field(loc), "loc": list(loc), "message": message, "type": kind}


def friendly_errors(errors: Iterable[Dict[str, Any]], strip: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """Pydantic errors as {field, loc, message, type}, without the submitted values.

    strip drops leading loc parts, e.g. FastAPI's "body".
    """
    result = []
    for error in errors:
        loc = list(error.get("loc", ()))
        if loc[:len(strip)] == list(strip):
            loc = loc[len(strip):]
        if error.get("type") == "json_invalid":
            loc = []  # its loc is a character position, not a field
        message = error.get("msg", "")
        # Our own validators raise ValueError; show their text without "Value error, "
        if error.get("type") == "value_error" and "error" in (error.get("ctx") or {}):
            message = str(error["ctx"]["error"])
        result.append(problem(loc, message, error.get("type", "invalid")))
    return result


def output_state(config: RunConfig) -> Dict[str, Any]:
    """What starting this config in its output folder would do.

    new       nothing there yet (or a run.json that was prepared and never started)
    resume    a run with the same settings: it continues where it stopped
    conflict  a run with different settings: refused unless the app asks to overwrite it
    invalid   the path is a file
    """
    out = config.output_dir
    if out.exists() and not out.is_dir():
        return {"state": "invalid", "message": f"{out} is a file, not a folder."}
    run_json = out / "run.json"
    if not run_json.exists():
        return {"state": "new", "message": "A new run will start in this folder."}
    try:
        record = json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "conflict", "previous_status": None,
                "message": f"{run_json} cannot be read. Choose another folder, or overwrite the run in it."}
    if not isinstance(record, dict) or "fingerprint" not in record:
        return {"state": "new", "message": "A new run will start in this folder."}
    if same_run(record, config):
        return {"state": "resume", "previous_status": record.get("status"),
                "message": "This folder holds a run with the same settings. It will continue where it stopped."}
    return {"state": "conflict", "previous_status": record.get("status"),
            "message": "This folder holds a run with different settings. "
                       "Choose another folder, or overwrite the run in it."}


@dataclass
class Check:
    config: Optional[RunConfig] = None
    errors: List[Dict[str, Any]] = field(default_factory=list)
    pdfs: List[Path] = field(default_factory=list)
    output: Optional[Dict[str, Any]] = None
    # Each prompt as written from the answers (prompt_previews), whatever else is wrong
    prompts: Dict[str, Optional[str]] = field(default_factory=lambda: {"triage": None, "extraction": None})

    @property
    def valid(self) -> bool:
        return self.config is not None and not self.errors

    def to_json(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "config": json.loads(self.config.model_dump_json()) if self.valid else None,
            "pdfs": [str(p) for p in self.pdfs],
            "output": self.output,
            "prompts": self.prompts,
        }


def _section(data: Any, *keys: str) -> Dict[str, Any]:
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data if isinstance(data, dict) else {}


def prompt_previews(data: Any) -> Dict[str, Optional[str]]:
    """The raw text of each prompt as the answers write it, for the app to show next to the questions.

    It does not need the rest of the config to be valid, and ignores a prompt edited by hand (raw). A
    prompt that cannot be written (an answer or a template has a problem) is None.
    """
    previews: Dict[str, Optional[str]] = {"triage": None, "extraction": None}
    try:
        guide = Guide.model_validate(_section(data, "guide"))
    except ValidationError:
        return previews
    for stage, model in (("triage", TriagePrompt), ("extraction", ExtractionPrompt)):
        templates = {k: v for k, v in _section(data, stage, "prompt").items() if k != "raw"}
        try:
            previews[stage] = model.model_validate(templates).editable(guide)
        except (ValidationError, ValueError, KeyError, IndexError):
            pass
    return previews


def chosen_pages_problems(config: RunConfig, pdfs: List[Path]) -> List[Dict[str, Any]]:
    """Pages chosen by hand for a document that is not among the PDFs, or beyond its last page."""
    by_name = {p.name: p for p in pdfs}
    errors = []
    for name, pages in config.triage.chosen_pages().items():
        loc = ["triage", "pages", name]
        if name not in by_name:
            errors.append(problem(loc, "This document is not among the PDFs to read. Remove it here, "
                                       "or add it in Documents.", "unknown_document"))
            continue
        try:
            total_pages = pdf.page_count(by_name[name])
        except Exception as e:
            errors.append(problem(loc, f"Cannot open this document: {e}", "unreadable_document"))
            continue
        if max(pages) > total_pages:
            errors.append(problem(loc, f"This document has {total_pages} page{'s' if total_pages > 1 else ''}.",
                                  "page_out_of_range"))
    return errors


def check_config(data: Any) -> Check:
    """Validate a config the way a run would, and list what it would process.

    Paths must be absolute: the sidecar's working folder means nothing to the
    user, and the app's file dialogs always give full paths.
    """
    prompts = prompt_previews(data)
    try:
        config = RunConfig.model_validate(data)
    except ValidationError as e:
        return Check(errors=friendly_errors(e.errors(include_url=False)), prompts=prompts)

    errors = [problem(["inputs", i], f"Use a full path: {p}", "absolute_path")
              for i, p in enumerate(config.inputs) if not p.expanduser().is_absolute()]
    if not config.output_dir.expanduser().is_absolute():
        errors.append(problem(["output_dir"], f"Use a full path: {config.output_dir}", "absolute_path"))
    if errors:
        return Check(config=config, errors=errors, prompts=prompts)
    config = config.model_copy(update={"inputs": [p.expanduser() for p in config.inputs],
                                       "output_dir": config.output_dir.expanduser()})

    check = Check(config=config, prompts=prompts)
    try:
        check.pdfs = find_pdfs(config.inputs, config.recursive)
        if not check.pdfs:
            check.errors.append(problem(["inputs"], "No PDF files found in the inputs.", "no_pdfs"))
    except ValueError as e:
        check.errors.append(problem(["inputs"], str(e), "inputs"))
    except OSError as e:
        check.errors.append(problem(["inputs"], f"Cannot read the inputs: {e}", "inputs"))
    if check.pdfs:
        check.errors += chosen_pages_problems(config, check.pdfs)
    check.output = output_state(config)
    if check.output["state"] == "invalid":
        check.errors.append(problem(["output_dir"], check.output["message"], "output_dir"))
    return check
