"""What GET /about reports for the app's About panel: the engine's libraries with
their versions and licenses, the llama.cpp in use, and the models the app offers.

LIBRARIES are the sidecar's direct dependencies, as pinned in
packaging/requirements_sidecar.txt. Their versions and licenses come from the
installed packages' metadata, which the frozen sidecar keeps because
sidecar.spec copies it.
"""

import platform
from email.message import Message
from importlib import metadata
from typing import Any, Dict, Optional

from .. import __version__
from .catalog import curated

LIBRARIES = ("fastapi", "starlette", "uvicorn", "pydantic", "requests", "pypdfium2", "pillow", "psutil")


def _license(meta: Message) -> Optional[str]:
    """The SPDX expression when there is one, else a short License field, else the classifier."""
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()
    text = (meta.get("License") or "").strip()
    if text and "\n" not in text and len(text) <= 60:  # some packages put the whole license text here
        return text
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License :: OSI Approved :: "):
            return classifier.rsplit(" :: ", 1)[1]
    return None


def library(name: str) -> Dict[str, Any]:
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return {"name": name, "version": None, "license": None}
    return {"name": dist.metadata["Name"] or name, "version": dist.version, "license": _license(dist.metadata)}


def about(runtime_status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": __version__,
        "libraries": [
            {"name": "Python", "version": platform.python_version(), "license": "PSF-2.0"},
            *(library(name) for name in LIBRARIES),
        ],
        "llama_cpp": {
            "build": runtime_status.get("build"),
            "pinned_build": runtime_status.get("pinned_build"),
            "license": "MIT",
        },
        "models": [
            {"label": m["label"], "repo": m["repo"], "license": m.get("license"), "license_url": m.get("license_url")}
            for m in curated()["models"]
        ],
    }
