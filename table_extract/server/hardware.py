"""Hardware probe: GPU devices as llama.cpp sees them, and system memory.

`llama-server --list-devices` reports each backend device with its memory the
same way on Metal, CUDA, Vulkan and the others, e.g.

    Available devices:
      MTL0: Apple M1 Pro (12124 MiB, 12123 MiB free)
      CUDA0: NVIDIA GeForce RTX 4090 (24563 MiB, 23889 MiB free)

On Apple Silicon the Metal figure is the share of the unified memory the GPU
may use, not extra memory on top of the RAM. No recommendation is made here.
"""

import os
import platform
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

import psutil

LLAMA_SERVER_ENV = "TABLE_EXTRACT_LLAMA_SERVER"
MIB = 1024 * 1024
LIST_TIMEOUT = 30  # seconds; the first GPU initialisation can be slow

# The description may itself contain parentheses ("Intel(R) Arc(TM) A770 Graphics"),
# so the memory figures are matched from the end of the line
DEVICE_LINE = re.compile(
    r"^\s*(?P<name>[^\s:]+):\s+(?P<description>.*?)\s*"
    r"\((?P<total>\d+) MiB, (?P<free>\d+) MiB free\)\s*$"
)
# Backends that list the CPU (or a CPU-side library) rather than a GPU
NOT_GPU = {"BLAS", "CPU", "AMX"}


def parse_devices(output: str) -> List[Dict[str, Any]]:
    devices = []
    for line in output.splitlines():
        match = DEVICE_LINE.match(line)
        if not match:
            continue
        name = match["name"]
        backend = re.sub(r"\d+$", "", name)
        total, free = int(match["total"]) * MIB, int(match["free"]) * MIB
        devices.append({
            "name": name,
            "description": match["description"],
            "backend": backend,
            "gpu": backend.upper() not in NOT_GPU and total > 0,
            "memory_total_bytes": total,
            "memory_free_bytes": free,
        })
    return devices


def find_llama_server(configured: Optional[str] = None) -> Optional[str]:
    """The llama-server to probe: the configured one, else the environment's, else the PATH's."""
    candidate = configured or os.environ.get(LLAMA_SERVER_ENV)
    if candidate:
        return shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
    return shutil.which("llama-server")


def list_devices(executable: str, timeout: float = LIST_TIMEOUT) -> Dict[str, Any]:
    options: Dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run([executable, "--list-devices"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, **options)
    except subprocess.TimeoutExpired:
        return {"devices": [], "error": f"llama-server --list-devices did not answer within {timeout:.0f} s"}
    except OSError as e:
        return {"devices": [], "error": f"Cannot run llama-server: {e}"}
    devices = parse_devices(out.stdout + "\n" + out.stderr)
    error = None
    if out.returncode != 0 and not devices:
        lines = [line for line in (out.stderr or out.stdout).splitlines() if line.strip()]
        error = f"llama-server --list-devices failed (exit code {out.returncode})" + (
            f": {lines[-1].strip()}" if lines else "")
    return {"devices": devices, "error": error}


def probe(llama_server: Optional[str] = None) -> Dict[str, Any]:
    memory = psutil.virtual_memory()
    system = platform.system()
    machine = platform.machine()
    result: Dict[str, Any] = {
        "system": {
            "os": system,
            "os_release": platform.release(),
            "machine": machine,
            "cpu_count": os.cpu_count(),
            "cpu_physical_cores": psutil.cpu_count(logical=False),
            # GPU memory and RAM are one pool: do not add them up
            "unified_memory": system == "Darwin" and machine == "arm64",
        },
        "memory": {"total_bytes": memory.total, "available_bytes": memory.available},
        "llama_server": {"path": None, "found": False, "error": None},
        "devices": [],
    }
    executable = find_llama_server(llama_server)
    if executable is None:
        result["llama_server"]["error"] = "llama-server was not found"
        return result
    listing = list_devices(executable)
    result["llama_server"] = {"path": executable, "found": True, "error": listing["error"]}
    result["devices"] = listing["devices"]
    return result
