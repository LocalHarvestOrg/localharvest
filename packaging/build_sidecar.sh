#!/usr/bin/env bash
# Freeze the sidecar with PyInstaller and put it where the Tauri bundle picks it
# up: app/src-tauri/sidecar/table-extract (plus its _internal/ folder).
#
# Uses its own virtual environment, .venv_build, with only what the sidecar
# needs (packaging/requirements_sidecar.txt), so the app does not ship the
# notebook and pandas packages of .venv_extract. Needs uv. On Windows, run it
# in Git Bash (as the GitHub Actions release workflow does).
#
# PyInstaller cannot cross-compile: the result runs on this OS and CPU only.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO/.venv_build"
WORK="$REPO/build/sidecar"
DEST="$REPO/app/src-tauri/sidecar"
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) BIN="$VENV/Scripts" EXE=".exe" ;;
  *) BIN="$VENV/bin" EXE="" ;;
esac

cd "$REPO"
# uv's own Python build, not pyenv or Homebrew: theirs are compiled for this
# Mac's macOS version and link Homebrew's openssl, so the app would not start
# on older macOS.
if [ ! -x "$BIN/python$EXE" ]; then
  uv venv --managed-python --python "$(tr -d '\r\n' < .python-version)" "$VENV"
fi
uv pip install --python "$BIN/python$EXE" -r packaging/requirements_sidecar.txt -c requirements_extract.txt

"$BIN/pyinstaller$EXE" packaging/sidecar.spec --noconfirm --clean \
  --workpath "$WORK" --distpath "$WORK/dist"

rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$WORK/dist/table-extract/." "$DEST/"

# A quick check that the frozen program starts and has its package data
"$DEST/table-extract$EXE" defaults > /dev/null
echo "Sidecar ready in $DEST ($(du -sh "$DEST" | cut -f1))"
