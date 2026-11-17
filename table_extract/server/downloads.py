"""Background downloads: model files from Hugging Face and llama.cpp builds from GitHub.

Each download is a job with progress that the app polls (GET /downloads). A
file goes to "<name>.part" and is resumed from there with an HTTP range
request; once complete its size and SHA-256 are checked against the values
published by Hugging Face or pinned in runtime.py before it takes its real
name. Free disk space is checked before starting.

These are the only components that reach the network (see the README's data
security section); nothing about the documents is ever sent.
"""

import hashlib
import itertools
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

CHUNK = 1024 * 1024
DISK_MARGIN = 1024 * 1024 * 1024  # left free after a download
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60
USER_AGENT = "LocalHarvest"


class DownloadError(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass
class FileSpec:
    url: str
    dest: Path
    size: Optional[int] = None
    sha256: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)  # e.g. the Hugging Face token, for gated models


def sha256_of(path: Path, cancel: Optional[threading.Event] = None, progress: Optional[Callable[[int], None]] = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            block = f.read(8 * CHUNK)
            if not block:
                return digest.hexdigest()
            digest.update(block)
            if progress:
                progress(len(block))


def check_disk_space(folder: Path, needed: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(folder).free
    if needed + DISK_MARGIN > free:
        gb = 1024 ** 3
        raise DownloadError(f"Not enough disk space: {needed / gb:.1f} GB needed, "
                            f"{free / gb:.1f} GB free in {folder}.")


def fetch(session: requests.Session, spec: FileSpec, cancel: threading.Event,
          progress: Callable[[int], None], on_verify: Callable[[], None] = lambda: None) -> None:
    """Download spec.url to spec.dest, resuming a previous .part file. Raises Cancelled or DownloadError."""
    part = spec.dest.with_name(spec.dest.name + ".part")
    spec.dest.parent.mkdir(parents=True, exist_ok=True)
    offset = part.stat().st_size if part.exists() else 0
    if spec.size is not None and offset > spec.size:
        part.unlink()
        offset = 0

    if spec.size is None or offset < spec.size:
        headers = {"User-Agent": USER_AGENT, **spec.headers}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            response = session.get(spec.url, headers=headers, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        except requests.RequestException as e:
            raise DownloadError(f"Cannot reach {_host(spec.url)}: {e}") from e
        with response:
            if response.status_code == 416 and offset:  # already complete
                pass
            elif response.status_code in (401, 403):
                raise DownloadError("Access refused. This model may require accepting its license on "
                                    "huggingface.co and logging in with `hf auth login`.")
            elif response.status_code >= 400:
                raise DownloadError(f"Download failed: HTTP {response.status_code} from {_host(spec.url)}")
            else:
                if offset and response.status_code != 206:
                    offset = 0  # the server ignored the range: start over
                progress(offset)
                try:
                    with open(part, "ab" if offset else "wb") as f:
                        for block in response.iter_content(CHUNK):
                            if cancel.is_set():
                                raise Cancelled()
                            f.write(block)
                            progress(len(block))
                except requests.RequestException as e:
                    raise DownloadError(f"The download was interrupted: {e}. Start it again to resume.") from e

    on_verify()
    size = part.stat().st_size
    if spec.size is not None and size != spec.size:
        part.unlink(missing_ok=True)
        raise DownloadError(f"{spec.dest.name} is incomplete ({size} of {spec.size} bytes). Start it again.")
    if spec.sha256:
        if sha256_of(part, cancel) != spec.sha256.lower():
            part.unlink(missing_ok=True)
            raise DownloadError(f"{spec.dest.name} is damaged (its checksum does not match). Start it again.")
    os.replace(part, spec.dest)


def _host(url: str) -> str:
    return url.split("/")[2] if "://" in url else url


@dataclass
class Job:
    id: str
    kind: str  # "model" or "runtime"
    target: str  # what is being installed, e.g. "unsloth/Qwen3.5-9B-GGUF:Q8_0" or "llama.cpp b11429 macos-arm64"
    label: str
    bytes_total: int = 0
    bytes_done: int = 0
    state: str = "queued"  # queued, downloading, verifying, installing, done, failed, cancelled
    error: Optional[str] = None
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    cancel: threading.Event = field(default_factory=threading.Event)
    _rate: List[Any] = field(default_factory=list)  # (time, bytes_done) samples for the speed

    def to_json(self) -> Dict[str, Any]:
        speed = None
        if len(self._rate) >= 2:
            (t0, b0), (t1, b1) = self._rate[0], self._rate[-1]
            speed = (b1 - b0) / (t1 - t0) if t1 > t0 else None
        return {
            "id": self.id, "kind": self.kind, "target": self.target, "label": self.label,
            "state": self.state, "error": self.error,
            "bytes_total": self.bytes_total, "bytes_done": self.bytes_done,
            "bytes_per_second": speed, "started": self.started, "finished": self.finished,
            "result": self.result,
        }


Work = Callable[[Job, Callable[[int], None]], Optional[Dict[str, Any]]]


class JobManager:
    """Runs each job in its own thread; one job per target at a time."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, Job] = {}
        self._ids = itertools.count(1)

    def submit(self, kind: str, target: str, label: str, total: int, work: Work) -> Job:
        with self._lock:
            for job in self._jobs.values():
                if job.target == target and job.finished is None:
                    return job  # already under way
            job = Job(id=str(next(self._ids)), kind=kind, target=target, label=label, bytes_total=total)
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, work), name=f"job-{job.id}", daemon=True).start()
        return job

    def _run(self, job: Job, work: Work) -> None:
        def progress(n: int) -> None:
            with self._lock:
                job.bytes_done += n
                now = time.monotonic()
                job._rate.append((now, job.bytes_done))
                while len(job._rate) > 2 and now - job._rate[0][0] > 5:
                    job._rate.pop(0)

        job.state = "downloading"
        try:
            job.result = work(job, progress)
            job.state = "done"
        except Cancelled:
            job.state = "cancelled"
        except DownloadError as e:
            job.state, job.error = "failed", str(e)
        except Exception as e:  # never let a job thread die silently
            job.state, job.error = "failed", f"{type(e).__name__}: {e}"
        finally:
            job.finished = time.time()

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [job.to_json() for job in self._jobs.values()]

    def active(self, kind: Optional[str] = None) -> List[Job]:
        return [j for j in self._jobs.values() if j.finished is None and (kind is None or j.kind == kind)]

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self._jobs.get(job_id)
        if job is not None:
            job.cancel.set()
        return job

    def shutdown(self) -> None:
        for job in list(self._jobs.values()):
            job.cancel.set()
