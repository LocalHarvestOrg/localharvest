"""Progress reporting: human-readable lines on stdout, and machine-readable events.jsonl.

The desktop app follows a run by reading events.jsonl, one JSON object per
line. Every event has "time" and "event"; the others depend on the event:

  run_started    output_dir, files
  stage_started  stage, total (pages that need a model call)
  page_done      stage, file, page, done, total, plus a stage-specific result
  file_done      stage, file
  stage_done     stage, output
  warning/error  message, and file/page when they apply
  run_finished   status ("done", "failed" or "cancelled"), outputs
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class EventLog:
    def __init__(self, path: Optional[Path] = None, echo: bool = True):
        self.path = path
        self.echo = echo
        self._lock = threading.Lock()

    def emit(self, event: str, message: str = "", **fields) -> None:
        record = {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event}
        if message:
            record["message"] = message
        record.update(fields)
        with self._lock:
            if self.echo and message:
                print(message, flush=True)
            if self.path is not None:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def info(self, message: str) -> None:
        """A log line with no event of its own (still recorded, for the app's log view)."""
        self.emit("log", message)

    def warning(self, message: str, **fields) -> None:
        self.emit("warning", message, **fields)

    def error(self, message: str, **fields) -> None:
        self.emit("error", message, **fields)
