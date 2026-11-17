"""Every tunable of the pipeline, in one validated object.

The command-line scripts build parts of it from their flags; the desktop app
writes the whole thing to run.json and runs `python -m table_extract run run.json`.
Defaults match the scripts' previous behaviour.
"""

import os
from pathlib import Path
from typing import Annotated, Dict, List, Optional, Set
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from .prompts import ExtractionPrompt, Guide, TriagePrompt

# Model shortcuts accepted by --model; anything else is sent to the server as is
MODEL_SHORTCUTS = {
    "qianfan": "models/qianfan-ocr",
    "qwen": "models/qwen3-vl-8b",
    "qwen4": "models/qwen3-vl-4b",
}

# Read from the environment rather than stored in the config, so the key never
# lands in run.json next to the results
API_KEY_ENV = "TABLE_EXTRACT_API_KEY"

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Pages sent at once when workers is automatic and the server does not say how
# many it serves at once (vLLM has no slots: it batches requests itself)
AUTO_WORKERS = 4


# Above any real document: keeps a typo like 1-9999999 from listing millions of pages
MAX_PAGE = 100_000


def parse_pages(text: str) -> Set[int]:
    """1-based pages from "3, 7-12" (commas or spaces between items, a dash for a range)."""
    pages: Set[int] = set()
    for item in text.replace(",", " ").replace(";", " ").split():
        first, dash, last = item.partition("-")
        try:
            start, end = int(first), int(last) if dash else int(first)
        except ValueError:
            raise ValueError(f"Use page numbers and ranges such as 3, 7-12 (not \"{item}\").") from None
        if start < 1:
            raise ValueError("Pages start at 1.")
        if end < start:
            raise ValueError(f"Write the range {item} from the lower page to the higher one.")
        if end > MAX_PAGE:
            raise ValueError(f"{end} is above the highest page this app reads ({MAX_PAGE}).")
        pages.update(range(start, end + 1))
    return pages


def format_pages(pages: Set[int]) -> str:
    """{3, 7, 8, 9} -> "3, 7-9"."""
    ranges: List[List[int]] = []
    for page in sorted(pages):
        if ranges and page == ranges[-1][1] + 1:
            ranges[-1][1] = page
        else:
            ranges.append([page, page])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)


# Written out the same way however it was typed, so "7,8,9" and "7-9" are the same run
PageList = Annotated[str, AfterValidator(lambda text: format_pages(parse_pages(text)))]


def api_key() -> Optional[str]:
    return os.environ.get(API_KEY_ENV) or None


def resolve_workers(workers: Optional[int], slots: Optional[int]) -> int:
    """The workers setting, or when it is None (automatic) the server's slot count."""
    return workers or slots or AUTO_WORKERS


def _check_local(url: str, allow_remote: bool) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"not an http(s) URL: {url}")
    if not allow_remote and parsed.hostname not in LOCAL_HOSTS:
        raise ValueError(
            f"{url} is not on this machine. Page images would leave it, so only "
            "localhost servers are allowed (see the README's data security section)."
        )
    return url


class ServerConfig(BaseModel):
    """An OpenAI-compatible chat completions endpoint (llama-server or vLLM)."""

    url: str = "http://localhost:8000/v1/chat/completions"
    model: str = "qwen"
    # Only for a server you control on another machine, e.g. through an SSH tunnel
    allow_remote: bool = False

    @model_validator(mode="after")
    def _local_only(self) -> "ServerConfig":
        _check_local(self.url, self.allow_remote)
        return self

    @property
    def resolved_model(self) -> str:
        return MODEL_SHORTCUTS.get(self.model, self.model)


class Sampling(BaseModel):
    """Sampling settings sent with every request. None leaves the server's default."""

    temperature: Optional[float] = Field(default=None, ge=0)
    top_p: Optional[float] = Field(default=None, gt=0, le=1)
    top_k: Optional[int] = Field(default=None, ge=0)
    seed: Optional[int] = None

    def payload(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class TriageConfig(BaseModel):
    enabled: bool = True
    dpi: int = Field(default=150, ge=36, le=600)
    max_tokens: int = Field(default=100, ge=1)
    workers: Optional[int] = Field(default=None, ge=1)  # None = the server's slot count
    server: Optional[ServerConfig] = None  # None = RunConfig.server
    prompt: TriagePrompt = Field(default_factory=TriagePrompt)
    # Pages chosen by hand, per PDF file name, e.g. {"report.pdf": "3, 7-12"}: only
    # these are extracted and the model does not look at that document. Applies
    # whether or not the model triage is enabled.
    pages: Dict[str, PageList] = Field(default_factory=dict)

    @field_validator("pages")
    @classmethod
    def _drop_empty(cls, value: Dict[str, str]) -> Dict[str, str]:
        return {name.strip(): text for name, text in value.items() if text}

    def chosen_pages(self) -> Dict[str, Set[int]]:
        return {name: parse_pages(text) for name, text in self.pages.items()}


class ExtractionConfig(BaseModel):
    dpi: int = Field(default=200, ge=36, le=600)
    # Output budget per attempt. Each retry gets a stronger prompt and more room;
    # the number of entries is the number of attempts.
    max_tokens_schedule: List[int] = Field(default_factory=lambda: [4096, 8192, 12288])
    # A complete answer whose output tokens per entity exceed this is treated as
    # under-extracted and retried. One compact row costs roughly 20-60 tokens.
    tokens_per_entity_limit: int = Field(default=200, ge=1)
    workers: Optional[int] = Field(default=None, ge=1)  # None = the server's slot count
    server: Optional[ServerConfig] = None  # None = RunConfig.server
    prompt: ExtractionPrompt = Field(default_factory=ExtractionPrompt)

    @field_validator("max_tokens_schedule")
    @classmethod
    def _schedule(cls, value: List[int]) -> List[int]:
        if not value or any(v <= 0 for v in value):
            raise ValueError("max_tokens_schedule needs at least one positive budget")
        return value


class RunConfig(BaseModel):
    """A whole run: triage and extraction over the input PDFs."""

    # Unknown keys are ignored (pydantic's default), so a run.json written before
    # the page pre-filter was removed still loads; its "prefilter" section is dropped

    inputs: List[Path] = Field(min_length=1)  # PDF files and/or folders of PDFs
    recursive: bool = False  # also look for PDFs in subfolders of input folders
    output_dir: Path
    server: ServerConfig = Field(default_factory=ServerConfig)
    sampling: Sampling = Field(default_factory=Sampling)
    triage: TriageConfig = Field(default_factory=TriageConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    # The answers to the guided questions; they fill the placeholders of both prompts
    guide: Guide = Field(default_factory=Guide)

    def triage_server(self) -> ServerConfig:
        return self.triage.server or self.server

    def extraction_server(self) -> ServerConfig:
        return self.extraction.server or self.server
