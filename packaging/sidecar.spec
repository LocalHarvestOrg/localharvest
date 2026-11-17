# PyInstaller spec for the desktop app's sidecar (one-folder mode, see docs/gui-design.md).
# Built by packaging/build_sidecar.sh; the output, dist/table-extract/, is copied
# to app/src-tauri/sidecar/, where the Tauri shell looks for it (sidecar.rs).

import subprocess
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

REPO = Path(SPECPATH).parent
BUILD = Path(workpath)
BUILD.mkdir(parents=True, exist_ok=True)

# collect_* import the package to find it
sys.path.insert(0, str(REPO))
package_data = collect_data_files("table_extract")
# (dest uses the OS's separator: backslashes on Windows)
assert any(Path(dest).as_posix().endswith("default_prompts/triage") for _, dest in package_data), "default_prompts not found"
assert any(Path(src).name == "models.json" for src, _ in package_data), "models.json not found"

# The About panel lists these libraries with the versions and licenses from their metadata
from table_extract.server.about import LIBRARIES  # noqa: E402
library_metadata = [entry for name in LIBRARIES for entry in copy_metadata(name)]

# Runs record the code version in run.json. A frozen app has no git checkout,
# and calling git on a Mac without the developer tools opens an install prompt,
# so the commit is written in at build time (runner._git_commit reads it).
commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
(BUILD / "build_commit.txt").write_text(commit, encoding="utf-8")

a = Analysis(
    [str(REPO / "packaging" / "sidecar_entry.py")],
    pathex=[str(REPO)],
    datas=[
        # models.json and default_prompts/
        *package_data,
        (str(BUILD / "build_commit.txt"), "table_extract"),
        *library_metadata,
    ],
    hiddenimports=[
        *collect_submodules("table_extract"),
        # serve.py names these explicitly; uvicorn imports them by string
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
    ],
    excludes=["tkinter", "pandas", "matplotlib", "IPython", "jupyter", "notebook", "pytest", "pdf2image", "fitz"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    # UTF-8 mode: Windows would otherwise encode stdout and files in its ANSI
    # code page (cp1252), which cannot write characters such as "→"
    [("X utf8", None, "OPTION")],
    exclude_binaries=True,
    name="table-extract",
    console=True,
    strip=False,
    upx=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="table-extract", strip=False, upx=False)
