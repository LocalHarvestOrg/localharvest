"""The app's data folder and its settings.

    models/<owner>/<repo>/<files>   downloaded model files, and one <quant>.json manifest per installed model
    runtime/                        llama.cpp builds (runtime.py)
    cache/                          Hub answers and GGUF headers, so estimates work offline once seen
    settings.json                   {"offline": false}
    servers.json                    llama-server processes started by the sidecar, to clean up after a crash

The folder is TABLE_EXTRACT_DATA_DIR if set, else the platform's local data
folder for the app's identifier (the same folder Tauri's app_local_data_dir
gives): ~/Library/Application Support/org.icij.localharvest on macOS,
%LOCALAPPDATA%\\org.icij.localharvest on Windows, and
$XDG_DATA_HOME/org.icij.localharvest (~/.local/share/...) on Linux.
Documents are never copied here.
"""

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

DATA_DIR_ENV = "TABLE_EXTRACT_DATA_DIR"
APP_IDENTIFIER = "org.icij.localharvest"  # app/src-tauri/tauri.conf.json


def default_data_dir() -> Path:
    if os.environ.get(DATA_DIR_ENV):
        return Path(os.environ[DATA_DIR_ENV]).expanduser()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_IDENTIFIER


def write_json(path: Path, data: Any) -> None:
    """Write through a temporary file, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class Offline(Exception):
    """The air-gapped setting is on: no downloads, no Hub requests."""


class Storage:
    DEFAULT_SETTINGS = {"offline": False}

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else default_data_dir()
        self._lock = threading.Lock()

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    def settings(self) -> Dict[str, Any]:
        stored = read_json(self.root / "settings.json", {})
        settings = dict(self.DEFAULT_SETTINGS)
        if isinstance(stored, dict):
            settings.update({k: v for k, v in stored.items() if k in self.DEFAULT_SETTINGS})
        return settings

    def update_settings(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            settings = self.settings()
            settings.update({k: v for k, v in changes.items() if k in self.DEFAULT_SETTINGS})
            write_json(self.root / "settings.json", settings)
            return settings

    @property
    def offline(self) -> bool:
        return bool(self.settings()["offline"])

    def require_online(self) -> None:
        if self.offline:
            raise Offline("The app is set to work offline. Turn that off in the settings to download.")
