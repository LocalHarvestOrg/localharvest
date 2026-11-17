"""Run manager: one pipeline run at a time, in a child process.

A run is `python -m table_extract run <output_dir>/run.json` (the frozen app
re-launches itself as `<app> run ...`), so app and command-line runs are the
same code. The child:

- gets its own process group (a new session on macOS and Linux), so cancel
  stops it together with anything it started;
- holds the read end of a pipe on stdin and stops when it closes, so it also
  stops when the sidecar is killed outright (see lifeline.py);
- reports progress in events.jsonl, which the sidecar tails;
- gets the supervised llama-servers' API key in its environment (extra_env).

Cancel sends SIGTERM to the group on macOS and Linux and closes the child's
stdin on Windows; either way the runner records "cancelled" and stops. If it
is still running after the grace period it is killed.
"""

import json
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from ..runner import fingerprint
from .checks import Check
from .security import TOKEN_ENV

POSIX = os.name == "posix"
# Folder that holds the table_extract package, the child's working folder in development
PACKAGE_PARENT = Path(__file__).resolve().parents[2]
STDERR_LINES = 200

RunnerCommand = Callable[[Path, bool], List[str]]


class RunBusy(Exception):
    """A run is already in progress."""


class NoActiveRun(Exception):
    """There is no run in progress."""


class OutputConflict(Exception):
    """The output folder holds a run with different settings."""


def default_runner_command(run_json: Path, overwrite: bool) -> List[str]:
    if getattr(sys, "frozen", False):  # PyInstaller: the app binary is the CLI
        command = [sys.executable, "run"]
    else:
        command = [sys.executable, "-m", "table_extract", "run"]
    command += [str(run_json), "--stop-when-stdin-closes"]
    return command + (["--overwrite"] if overwrite else [])


def child_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ)
    env.update({
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        # Nothing in a run may reach the network (see the README's data security section)
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "OTEL_SDK_DISABLED": "true",
        "DO_NOT_TRACK": "1",
    })
    env.pop(TOKEN_ENV, None)  # a run has no business calling the sidecar
    env.update(extra or {})
    return env


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class EventReader:
    """Reads the complete lines appended to events.jsonl after a byte offset."""

    def __init__(self, path: Path, offset: int = 0):
        self.path = path
        self.offset = offset

    def read(self) -> List[Tuple[int, Dict[str, Any]]]:
        """New events, each with the byte offset just after its line (its SSE id)."""
        try:
            with open(self.path, "rb") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() < self.offset:  # replaced by a shorter file: start over
                    self.offset = 0
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []  # the line being written is not complete yet
        events, position = [], self.offset
        for line in data[:end + 1].splitlines(keepends=True):
            position += len(line)
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                events.append((position, record))
        self.offset += end + 1
        return events


@dataclass
class Progress:
    """A summary of the events so far, for status requests."""

    files: Optional[int] = None
    stage: Optional[str] = None
    done: int = 0
    total: Optional[int] = None
    warnings: int = 0
    errors: int = 0
    last_message: Optional[str] = None
    last_error: Optional[str] = None
    finished: Optional[Dict[str, Any]] = None  # the run_finished event

    def update(self, record: Dict[str, Any]) -> None:
        kind = record.get("event")
        if kind == "run_started":
            self.files = len(record.get("files") or [])
        elif kind == "stage_started":
            self.stage, self.done, self.total = record.get("stage"), 0, record.get("total")
        elif kind == "page_done":
            self.stage = record.get("stage", self.stage)
            self.done, self.total = record.get("done", self.done), record.get("total", self.total)
        elif kind == "warning":
            self.warnings += 1
        elif kind == "error":
            self.errors += 1
            self.last_error = (record.get("message") or "").strip() or None
        elif kind == "run_finished":
            self.finished = {k: record.get(k) for k in ("status", "failed_pages", "outputs")}
        if record.get("message"):
            self.last_message = record["message"].strip()


@dataclass
class Run:
    output_dir: Path
    process: Any  # subprocess.Popen
    events: EventReader
    started: str
    pdf_count: int
    events_offset: int  # where this run's events start in events.jsonl
    cancel_requested: bool = False
    finished: Optional[str] = None
    exit_code: Optional[int] = None
    progress: Progress = field(default_factory=Progress)
    stderr: Deque[str] = field(default_factory=lambda: deque(maxlen=STDERR_LINES))
    exited: threading.Event = field(default_factory=threading.Event)
    kill_timer: Optional[threading.Timer] = None

    @property
    def running(self) -> bool:
        return self.exit_code is None

    def result(self) -> Optional[str]:
        """done, failed or cancelled once the process has exited."""
        if self.running:
            return None
        if self.progress.finished and self.progress.finished.get("status"):
            return self.progress.finished["status"]
        if self.exit_code == 0:
            return "done"
        return "cancelled" if self.cancel_requested else "failed"


def _signal_group(process: Any, signum: int) -> None:
    if process.returncode is not None:
        return  # reaped: its process group id may belong to someone else by now
    try:
        os.killpg(process.pid, signum)  # the child leads its own group (start_new_session)
    except (ProcessLookupError, PermissionError):
        pass


class RunManager:
    def __init__(self, runner_command: RunnerCommand = default_runner_command,
                 popen: Callable[..., Any] = subprocess.Popen, cancel_grace: float = 10.0,
                 extra_env: Callable[[], Dict[str, str]] = dict):
        self.runner_command = runner_command
        self.popen = popen
        self.cancel_grace = cancel_grace
        self.extra_env = extra_env
        self._lock = threading.RLock()
        self._run: Optional[Run] = None

    @property
    def current(self) -> Optional[Run]:
        """The run in progress, or else the last one this sidecar started."""
        return self._run

    def busy(self) -> bool:
        with self._lock:
            return self._run is not None and self._run.running

    def start(self, check: Check, overwrite: bool = False) -> Run:
        """Write run.json in the output folder and launch the runner on it."""
        assert check.valid and check.output is not None
        config = check.config
        with self._lock:
            if self.busy():
                raise RunBusy()
            state = check.output["state"]
            if state == "conflict" and not overwrite:
                raise OutputConflict(check.output["message"])

            out = config.output_dir
            out.mkdir(parents=True, exist_ok=True)
            run_json = out / "run.json"
            record: Dict[str, Any] = {}
            if state in ("resume", "conflict"):
                try:
                    record = json.loads(run_json.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    record = {}
                if not isinstance(record, dict):
                    record = {}
                # Keeping the old fingerprint is what tells the runner to resume,
                # or, with --overwrite, to delete the old results first
                record.setdefault("fingerprint", "unreadable")
                if state == "resume":
                    # The same run, though maybe fingerprinted before the pre-filter was
                    # removed: the runner only sees the new config below
                    record["fingerprint"] = fingerprint(config)
            record.update({"status": "starting", "updated": _now(),
                           "config": json.loads(config.model_dump_json())})
            _write_json(run_json, record)

            events_path = out / "events.jsonl"
            offset = events_path.stat().st_size if events_path.exists() else 0
            options: Dict[str, Any] = {}
            if POSIX:
                options["start_new_session"] = True
            else:
                options["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                            | getattr(subprocess, "CREATE_NO_WINDOW", 0))
            process = self.popen(
                self.runner_command(run_json, overwrite and state == "conflict"),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                cwd=None if getattr(sys, "frozen", False) else str(PACKAGE_PARENT),
                env=child_env(self.extra_env()), text=True, encoding="utf-8", errors="replace",
                **options,
            )
            run = Run(output_dir=out, process=process, events=EventReader(events_path, offset),
                      started=_now(), pdf_count=len(check.pdfs), events_offset=offset)
            self._run = run
        threading.Thread(target=self._read_stderr, args=(run,), name="run-stderr", daemon=True).start()
        threading.Thread(target=self._wait, args=(run,), name="run-wait", daemon=True).start()
        return run

    def _read_stderr(self, run: Run) -> None:
        if run.process.stderr is None:
            return
        try:
            for line in run.process.stderr:
                run.stderr.append(line.rstrip("\n"))
        except (OSError, ValueError):
            pass
        finally:
            run.process.stderr.close()

    def _wait(self, run: Run) -> None:
        code = run.process.wait()
        with self._lock:
            run.exit_code = code
            run.finished = _now()
            if run.kill_timer is not None:
                run.kill_timer.cancel()
        if run.process.stdin is not None:
            try:
                run.process.stdin.close()
            except OSError:
                pass
        run.exited.set()

    def cancel(self) -> Run:
        with self._lock:
            run = self._run
            if run is None or not run.running:
                raise NoActiveRun()
            if not run.cancel_requested:
                run.cancel_requested = True
                self._interrupt(run)
                run.kill_timer = threading.Timer(self.cancel_grace, self._kill, args=(run,))
                run.kill_timer.daemon = True
                run.kill_timer.start()
            return run

    def _interrupt(self, run: Run) -> None:
        """Ask the run to stop cleanly; it records "cancelled" and exits."""
        if POSIX:
            _signal_group(run.process, signal.SIGTERM)
        elif run.process.stdin is not None:
            try:
                run.process.stdin.close()  # the runner's lifeline: stdin closed means stop
            except OSError:
                pass

    def _kill(self, run: Run) -> None:
        with self._lock:
            if not run.running:
                return
            if POSIX:
                _signal_group(run.process, signal.SIGKILL)
            else:
                try:
                    run.process.kill()
                except OSError:
                    pass

    def shutdown(self, grace: float = 3.0) -> None:
        """Stop the run in progress, if any: the sidecar is exiting."""
        with self._lock:
            run = self._run
            if run is None or not run.running:
                return
            if not run.cancel_requested:
                run.cancel_requested = True
                self._interrupt(run)
        if not run.exited.wait(grace):
            self._kill(run)
            run.exited.wait(2.0)

    def refresh(self, run: Run) -> List[Tuple[int, Dict[str, Any]]]:
        with self._lock:
            events = run.events.read()
            for _, record in events:
                run.progress.update(record)
        return events

    def status(self) -> Dict[str, Any]:
        with self._lock:
            run = self._run
            if run is None:
                return {"state": "idle"}
            self.refresh(run)
            if run.running:
                state = "cancelling" if run.cancel_requested else "running"
            else:
                state = "finished"
            return {
                "state": state,
                "result": run.result(),
                "output_dir": str(run.output_dir),
                "pdfs": run.pdf_count,
                "pid": run.process.pid,
                "started": run.started,
                "finished": run.finished,
                "exit_code": run.exit_code,
                "cancel_requested": run.cancel_requested,
                "events_offset": run.events_offset,
                "progress": asdict(run.progress),
                "stderr": list(run.stderr)[-20:],
            }
