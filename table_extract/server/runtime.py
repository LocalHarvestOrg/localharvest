"""llama.cpp runtime manager: picks, downloads, checks and installs the llama.cpp build for this computer.

llama.cpp is not bundled with the app (docs/gui-design.md): the right build
depends on the GPU, CUDA builds are large, and new model architectures need
a newer llama.cpp. The app installs one pinned release, verified against the
SHA-256 digests GitHub published for it (copied below), into
<data>/runtime/<build>-<variant>/. Updating llama.cpp means changing PINNED
and the asset table, then the app offers the update.

Which llama.cpp the sidecar uses: --llama-server or TABLE_EXTRACT_LLAMA_SERVER
if set (development), else the installed runtime, else llama-server on the PATH.
"""

import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from .downloads import DownloadError, FileSpec, Job, check_disk_space, fetch
from .storage import Storage, read_json, write_json

PINNED = "b11429"  # llama.cpp 0.6.0, the build the README's measurements were made with
RELEASE_URL = "https://github.com/ggml-org/llama.cpp/releases/download/{build}/{name}"
EXE = ".exe" if os.name == "nt" else ""


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Variant:
    id: str
    os: str  # Darwin, Windows, Linux
    machine: str  # arm64, x86_64
    backend: str  # metal, cuda, vulkan, cpu
    label: str
    assets: Tuple[Asset, ...]

    @property
    def size(self) -> int:
        return sum(a.size for a in self.assets)


def _a(name: str, size: int, sha256: str) -> Asset:
    return Asset(name, size, sha256)


# From the b11429 release (GitHub's asset digests). The CUDA builds need the
# matching CUDA runtime archive next to them.
VARIANTS = {v.id: v for v in [
    Variant("macos-arm64", "Darwin", "arm64", "metal", "Apple Silicon (Metal)", (
        _a("llama-b11429-bin-macos-arm64.tar.gz", 11971406,
           "740288ec6887be94280a5dfa25b5e23a78285cab104519e6c7e218904ee82459"),)),
    Variant("win-cuda-x64", "Windows", "x86_64", "cuda", "NVIDIA (CUDA 12.4)", (
        _a("llama-b11429-bin-win-cuda-12.4-x64.zip", 264478423,
           "dd6df685c1024e6aa55ca55ad35038692d5e284d4dc895e7a646c3245d3d14ff"),
        _a("cudart-llama-bin-win-cuda-12.4-x64.zip", 391443627,
           "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"))),
    Variant("win-vulkan-x64", "Windows", "x86_64", "vulkan", "AMD, Intel or other GPU (Vulkan)", (
        _a("llama-b11429-bin-win-vulkan-x64.zip", 33337769,
           "1bfe78ad9168b79fa02bf67f6af9f5e17a966d824d77238517f7bef12ac73b36"),)),
    Variant("win-cpu-x64", "Windows", "x86_64", "cpu", "Processor only", (
        _a("llama-b11429-bin-win-cpu-x64.zip", 19398918,
           "1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe"),)),
    Variant("win-vulkan-arm64", "Windows", "arm64", "vulkan", "GPU (Vulkan)", (
        _a("llama-b11429-bin-win-vulkan-arm64.zip", 25880280,
           "739747b9912d19525977b0346a02087037eec487fea7b58c2826324407e588ce"),)),
    Variant("win-cpu-arm64", "Windows", "arm64", "cpu", "Processor only", (
        _a("llama-b11429-bin-win-cpu-arm64.zip", 12229035,
           "ee0f631a9e58b146ff50714099d9cb498906af3143a773b580093a9214a8d1e5"),)),
    Variant("linux-cuda-x64", "Linux", "x86_64", "cuda", "NVIDIA (CUDA 12.8)", (
        _a("llama-b11429-bin-ubuntu-cuda-12.8-x64.tar.gz", 171651873,
           "b13c64b9224d3c89d9993945d70cc21177ad00178c21b8d34237e3bc139834f1"),
        _a("cudart-llama-b11429-bin-ubuntu-cuda-12.8-x64.tar.gz", 594377569,
           "f4ab593e31507320b121f2cb21791f4c72420a2483d3df8bf2ca1a97d04e6845"))),
    Variant("linux-vulkan-x64", "Linux", "x86_64", "vulkan", "AMD, Intel or other GPU (Vulkan)", (
        _a("llama-b11429-bin-ubuntu-vulkan-x64.tar.gz", 31636673,
           "632c4e98feba2b94407a2130e3133e0c3aefb0ea1ab41337e926d8bfafdd0b74"),)),
    Variant("linux-cpu-x64", "Linux", "x86_64", "cpu", "Processor only", (
        _a("llama-b11429-bin-ubuntu-x64.tar.gz", 17693462,
           "f6d25dde8f51133143d1453da4fd5f73b145127177612a283bf7995957af3392"),)),
    Variant("linux-vulkan-arm64", "Linux", "arm64", "vulkan", "GPU (Vulkan)", (
        _a("llama-b11429-bin-ubuntu-vulkan-arm64.tar.gz", 24845090,
           "702d99c4219b4314cc6d3b10fb41ae96cbfc68226305c2681269dfb51487af34"),)),
    Variant("linux-cpu-arm64", "Linux", "arm64", "cpu", "Processor only", (
        _a("llama-b11429-bin-ubuntu-arm64.tar.gz", 13682895,
           "ed44c0f79c3d02424f62bdc87e106318b0b89f49050e2e80c5f352723d14e00b"),)),
]}

# PCI vendor ids, as in /sys/class/drm/card*/device/vendor
PCI_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}


def _machine() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x86_64" if machine in ("x86_64", "amd64") else machine


def _quiet_run(command: List[str], timeout: float = 15) -> str:
    """stdout and stderr of a command that succeeded, else ""."""
    options: Dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout, **options)
        return out.stdout + out.stderr if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def gpu_vendors() -> List[str]:
    """GPU makers found without llama.cpp: apple, nvidia, amd, intel."""
    system = platform.system()
    if system == "Darwin":
        return ["apple"] if _machine() == "arm64" else []
    found = []
    if system == "Linux":
        for vendor_file in Path("/sys/class/drm").glob("card*/device/vendor"):
            try:
                vendor = PCI_VENDORS.get(vendor_file.read_text().strip().lower())
            except OSError:
                continue
            if vendor and vendor not in found:
                found.append(vendor)
        if "nvidia" not in found and Path("/proc/driver/nvidia/version").exists():
            found.append("nvidia")
        return found
    if system == "Windows":
        names = _quiet_run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"]).lower()
        for vendor, words in (("nvidia", ("nvidia",)), ("amd", ("amd", "radeon")), ("intel", ("intel",))):
            if any(w in names for w in words):
                found.append(vendor)
        if "nvidia" not in found and shutil.which("nvidia-smi"):
            found.append("nvidia")
    return found


def variants_for_this_computer() -> List[Variant]:
    system, machine = platform.system(), _machine()
    return [v for v in VARIANTS.values() if v.os == system and v.machine == machine]


def recommended_variant(vendors: Optional[List[str]] = None) -> Optional[Variant]:
    candidates = {v.backend: v for v in variants_for_this_computer()}
    vendors = gpu_vendors() if vendors is None else vendors
    if "apple" in vendors and "metal" in candidates:
        return candidates["metal"]
    if "nvidia" in vendors and "cuda" in candidates:
        return candidates["cuda"]
    if vendors and "vulkan" in candidates:
        return candidates["vulkan"]
    return candidates.get("cpu") or next(iter(candidates.values()), None)


def _safe_extract(archive: Path, dest: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            root = dest.resolve()
            for member in z.infolist():
                target = (dest / member.filename).resolve()
                if root != target and root not in target.parents:
                    raise DownloadError(f"{archive.name} contains an unsafe path: {member.filename}")
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")  # refuses absolute paths, .. and links out of dest


def _find_binary(folder: Path, name: str) -> Optional[Path]:
    matches = sorted(folder.rglob(name + EXE), key=lambda p: len(p.parts))
    return matches[0] if matches else None


def build_of(executable: Path) -> Optional[str]:
    """"version: 0.6.0 (build 11429, commit d81235049)" -> "b11429"."""
    match = re.search(r"build (\d+)", _quiet_run([str(executable), "--version"], timeout=30))
    return f"b{match.group(1)}" if match else None


class Runtime:
    def __init__(self, storage: Storage, override: Optional[str] = None,
                 session: Optional[requests.Session] = None, release_url: str = RELEASE_URL):
        self.storage = storage
        self.override = override or os.environ.get("TABLE_EXTRACT_LLAMA_SERVER")
        self.session = session or requests.Session()
        self.release_url = release_url
        self._vendors: Optional[List[str]] = None

    @property
    def record_path(self) -> Path:
        return self.storage.runtime / "runtime.json"

    def installed(self) -> Optional[Dict[str, Any]]:
        record = read_json(self.record_path)
        if not isinstance(record, dict) or not record.get("bin_dir"):
            return None
        if not (Path(record["bin_dir"]) / ("llama-server" + EXE)).exists():
            return None
        return record

    def executable(self, name: str = "llama-server") -> Optional[str]:
        """The llama.cpp program to use, in the order described at the top of this file."""
        if self.override:
            server = shutil.which(self.override) or (self.override if os.path.isfile(self.override) else None)
            if server is None:
                return None
            if name == "llama-server":
                return server
            sibling = Path(server).with_name(name + EXE)
            return str(sibling) if sibling.exists() else shutil.which(name)
        record = self.installed()
        if record:
            path = Path(record["bin_dir"]) / (name + EXE)
            return str(path) if path.exists() else None
        return shutil.which(name)

    def gpu_vendors(self) -> List[str]:
        """Cached: on Windows this asks PowerShell, which takes a second or two."""
        if self._vendors is None:
            self._vendors = gpu_vendors()
        return self._vendors

    def status(self) -> Dict[str, Any]:
        record = self.installed()
        vendors = self.gpu_vendors()
        recommended = recommended_variant(vendors)
        server = self.executable()
        source = "override" if self.override else "installed" if record else "path" if server else None
        return {
            "pinned_build": PINNED,
            "installed": record,
            "llama_server": server,
            "source": source,
            "build": (record or {}).get("build") or (build_of(Path(server)) if server and not record else None),
            "update_available": bool(record) and record.get("build") != PINNED,
            "gpu_vendors": vendors,
            "recommended_variant": recommended.id if recommended else None,
            "variants": [{"id": v.id, "backend": v.backend, "label": v.label, "download_bytes": v.size}
                         for v in variants_for_this_computer()],
        }

    def install(self, variant: Variant, job: Job, progress: Callable[[int], None]) -> Dict[str, Any]:
        """Download, verify, unpack and check a build, then make it the current runtime."""
        root = self.storage.runtime
        downloads = root / "downloads"
        target = root / f"{PINNED}-{variant.id}"
        check_disk_space(root, variant.size * 3)  # archives, plus about twice that unpacked
        archives = []
        for asset in variant.assets:
            spec = FileSpec(url=self.release_url.format(build=PINNED, name=asset.name),
                            dest=downloads / asset.name, size=asset.size, sha256=asset.sha256)
            if spec.dest.exists():
                progress(asset.size)  # verified when it was downloaded
            else:
                job.state = "downloading"
                fetch(self.session, spec, job.cancel, progress, on_verify=lambda: setattr(job, "state", "verifying"))
            archives.append(spec.dest)

        job.state = "installing"
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=root))
        try:
            for archive in archives:
                _safe_extract(archive, staging)
            server = _find_binary(staging, "llama-server")
            if server is None:
                raise DownloadError("The llama.cpp download holds no llama-server.")
            bin_dir = server.parent
            # The CUDA runtime archive unpacks its libraries next to the binaries' folder
            # or in its own; llama.cpp looks for them beside llama-server
            for lib in staging.rglob("*"):
                if lib.is_file() and lib.parent != bin_dir and re.search(r"cudart|cublas", lib.name, re.I):
                    shutil.move(str(lib), bin_dir / lib.name)
            if os.name == "posix":
                for exe in bin_dir.glob("llama-*"):
                    exe.chmod(exe.stat().st_mode | 0o111)
            relative_bin = bin_dir.relative_to(staging)
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        bin_dir = target / relative_bin
        build = build_of(bin_dir / ("llama-server" + EXE))
        if build is None:
            raise DownloadError("The installed llama-server does not start on this computer.")
        record = {"build": build, "variant": variant.id, "backend": variant.backend,
                  "bin_dir": str(bin_dir), "path": str(target)}
        previous = self.installed()
        write_json(self.record_path, record)
        for archive in archives:
            archive.unlink(missing_ok=True)
        if previous and Path(previous["path"]) != target:
            shutil.rmtree(previous["path"], ignore_errors=True)
        return record
