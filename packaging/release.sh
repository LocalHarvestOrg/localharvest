#!/usr/bin/env bash
# Release a version of LocalHarvest from main: packaging/release.sh 0.3.0
#
# Runs the tests, sets the version everywhere and commits it, builds the .dmg on
# this Mac, pushes the commit to main and creates a draft GitHub release, tagged
# v0.3.0. Pushing the tag starts the GitHub Actions workflow
# (.github/workflows/release.yml), which adds the Windows and Linux installers
# to the draft. Publishing the draft, once they are there and tested, is left to
# you: the script prints the command. Nobody sees the release before that.
#
# Needs uv, Node.js, Rust, .venv_extract and the GitHub CLI, logged in
# (gh auth login). If a build fails, fix it on main and run the script again
# with the same version.
set -euo pipefail

# Stable name, so .../releases/latest/download/<name> always gives the newest release
DMG_NAME="LocalHarvest-macOS-AppleSilicon.dmg"

VERSION="${1:-}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: packaging/release.sh <version>, for example packaging/release.sh 0.3.0" >&2
  exit 1
fi
TAG="v$VERSION"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

if ! gh auth status > /dev/null 2>&1; then
  echo "The GitHub CLI is not logged in: run gh auth login first." >&2
  exit 1
fi
if [ "$(git rev-parse --abbrev-ref HEAD)" != main ]; then
  echo "Releases are made from main: switch to it first." >&2
  exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "There are uncommitted changes: commit or stash them, the app must match a commit." >&2
  exit 1
fi
git fetch --quiet --no-tags origin
if [ -n "$(git rev-list HEAD..origin/main)" ]; then
  echo "main is behind GitHub: pull first." >&2
  exit 1
fi
# The only commit allowed to be unpushed is this release's, left by a failed build
AHEAD="$(git log --format=%s origin/main..HEAD)"
if [ -n "$AHEAD" ] && [ "$AHEAD" != "Release $VERSION" ]; then
  echo "main has commits that are not on GitHub: push them first." >&2
  exit 1
fi
if git ls-remote --exit-code --tags origin "refs/tags/$TAG" > /dev/null; then
  echo "$TAG already exists on GitHub: a released version never changes, pick a new number." >&2
  exit 1
fi
if gh release view "$TAG" > /dev/null 2>&1; then
  echo "A draft release $TAG already exists: delete it first (gh release delete $TAG)." >&2
  exit 1
fi
CURRENT="$(sed -nE 's/^ *"version": "(.*)",$/\1/p' app/src-tauri/tauri.conf.json)"
if [ "$(printf '%s\n%s\n' "$CURRENT" "$VERSION" | sort -V | tail -n 1)" != "$VERSION" ]; then
  echo "$VERSION is older than the current version, $CURRENT." >&2
  exit 1
fi

.venv_extract/bin/python -m unittest discover tests
(cd app && npm ci && npm test && npm run typecheck)

# The commit comes before the build: the app records it in every run
sed -i '' -E "s/^( *\"version\": )\".*\",$/\1\"$VERSION\",/" app/src-tauri/tauri.conf.json
sed -i '' -E "1,5s/^version = \".*\"/version = \"$VERSION\"/" app/src-tauri/Cargo.toml pyproject.toml
sed -i '' -E "s/^__version__ = \".*\"/__version__ = \"$VERSION\"/" table_extract/__init__.py
(cd app && npm version "$VERSION" --no-git-tag-version --allow-same-version > /dev/null)
cargo update --workspace --offline --quiet --manifest-path app/src-tauri/Cargo.toml
uv lock --quiet
git add app/src-tauri/tauri.conf.json app/src-tauri/Cargo.toml app/src-tauri/Cargo.lock \
  app/package.json app/package-lock.json pyproject.toml uv.lock table_extract/__init__.py
if ! git diff --cached --quiet; then
  git commit --quiet -m "Release $VERSION"
fi
COMMIT="$(git rev-parse HEAD)"

packaging/build_sidecar.sh
(cd app && npm run release)

BUILD="$REPO/build/release"
mkdir -p "$BUILD"
cp "app/src-tauri/target/release/bundle/dmg/LocalHarvest_${VERSION}_aarch64.dmg" "$BUILD/$DMG_NAME"

cat > "$BUILD/notes.md" <<EOF
LocalHarvest $VERSION, built from commit ${COMMIT:0:7} on $(date -u '+%Y-%m-%d').

| File | For |
| --- | --- |
| \`$DMG_NAME\` | Macs with Apple Silicon (M1 or later), macOS 14 (Sonoma) or later |
| \`LocalHarvest-Windows-x64-setup.exe\` | Windows 10 or 11, 64-bit (Intel or AMD) |
| \`LocalHarvest-Linux-x64.AppImage\`, \`.deb\` | 64-bit Linux, Ubuntu 22.04 or later (or similar) |

### Opening it the first time

The app is not signed yet, so each system warns about it once.

**macOS.** macOS says it cannot check the app for malicious software:

1. Open the .dmg and drag LocalHarvest into Applications.
2. Double-click LocalHarvest once and close the warning.
3. Open **System Settings → Privacy & Security**, scroll down, and click **Open Anyway** next to the message about LocalHarvest.
4. Confirm. From then on it opens normally.

**Windows.** Run the setup file. If Windows says "Windows protected your PC", click **More info**, then **Run anyway**. It installs for the current user, without administrator rights.

**Linux.** Either install the .deb (\`sudo apt install ./LocalHarvest-Linux-x64.deb\`), or make the AppImage executable (\`chmod +x LocalHarvest-Linux-x64.AppImage\`) and run it.
EOF

git push --quiet origin HEAD:refs/heads/main

# The draft comes before the tag, so the workflow always finds it to upload to.
# A draft does not create its tag on GitHub, so this leaves nothing behind if it fails.
gh release create "$TAG" "$BUILD/$DMG_NAME" \
  --draft \
  --target "$COMMIT" \
  --title "LocalHarvest $VERSION" \
  --notes-file "$BUILD/notes.md"

git tag --force --annotate "$TAG" --message "LocalHarvest $VERSION" "$COMMIT"
git push --quiet origin "refs/tags/$TAG"

NAME="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
cat <<EOF

Draft release $TAG is ready, with the macOS installer: https://github.com/$NAME/releases
Windows and Linux are building, about half an hour: https://github.com/$NAME/actions/workflows/release.yml

Test the installers, say what changed in the notes on GitHub, then publish:

  gh release edit $TAG --draft=false --latest
EOF
