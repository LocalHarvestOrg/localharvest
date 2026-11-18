# Releasing

A release is the app with the sidecar frozen inside (PyInstaller), so users need neither Python nor this repository. llama.cpp and the models are not inside: the app downloads them.

## Publishing a version

From `main`, up to date with GitHub, nothing uncommitted, GitHub CLI logged in (`gh auth login`), on an Apple Silicon Mac with what [development.md](development.md#setup) lists plus [uv](https://docs.astral.sh/uv/):

```bash
packaging/release.sh 0.3.0
```

`main` only changes through pull requests, so this takes two runs. The first sets the version everywhere on a `release/0.3.0` branch, opens a pull request and prints the commands to merge it and continue. Run the same command again once it is merged and pulled:

```bash
gh pr merge release/0.3.0 --squash --admin --delete-branch
git pull --ff-only
packaging/release.sh 0.3.0
```

The second run does the tests, builds the macOS `.dmg` and creates a draft release tagged `v0.3.0`. The tag starts the Release workflow, which adds the Windows and Linux installers to the draft about half an hour later. If a build fails, merge the fix into `main`, pull and run the script again with the same version.

Test the installers, write the release notes on GitHub, then publish:

```bash
gh release edit v0.3.0 --draft=false --latest
```

A published version never changes; a fix is a new version. The file names are the same in every version, so `releases/latest/download/<file>` always gives the newest one (the README links to it).

## Building the Mac app by hand

```bash
packaging/build_sidecar.sh      # freezes the sidecar into app/src-tauri/sidecar/ (about a minute)
cd app && npm run release       # builds the app and the .dmg
```

The results are in `app/src-tauri/target/release/bundle/` (`macos/LocalHarvest.app`, `dmg/LocalHarvest_<version>_aarch64.dmg`). Run `build_sidecar.sh` again whenever the Python code changes. Plain `npm run tauri build` makes an app without the sidecar, which does not start.

- The sidecar is `packaging/sidecar.spec`, built in its own `.venv_build` with only `packaging/requirements_sidecar.txt` (same versions as `requirements_extract.txt`). Runs record the git commit the app was built from, written in at build time.
- It uses uv's own Python, not pyenv's or Homebrew's, which would tie the app to the build machine's macOS. numpy sets the floor: **macOS 14 or later**.
- **Apple Silicon only**: PyInstaller cannot cross-build.

## Windows and Linux

`.github/workflows/release.yml` runs the same two steps on GitHub's runners when `release.sh` pushes a tag, and uploads to the draft. Run by hand (Actions → Release → Run workflow), it keeps the installers as workflow artifacts instead.

- **Windows** (`windows-latest`): an NSIS installer for the current user, no administrator rights.
- **Linux** (`ubuntu-22.04`, the oldest available, because the app needs the build's glibc or newer): an AppImage and a `.deb`.

A release takes about 15 minutes of runner time for Linux and 20 for Windows. The repository is public, so GitHub Actions is free.

## Signing

Not done yet, so users see a warning the first time (the README says how to get past it).

- **macOS**: the app is signed ad hoc. Developer ID signing and notarization need an Apple Developer ID and `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD` (app-specific) and `APPLE_TEAM_ID` set before `npm run release`. The sidecar's binaries must also be signed with that identity and the hardened runtime, which `build_sidecar.sh` does not do yet.
- **Windows**: not signed, so SmartScreen warns.
