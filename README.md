# LocalHarvest

LocalHarvest finds the ownership tables in PDF documents (registers of members, share ledgers, shareholder lists, company reports) and turns them into a spreadsheet: one row per relationship between a company and its owners, directors or secretaries. It reads each page with a vision model that runs **on your own computer**, so the documents never leave it.

A desktop app for macOS, Windows and Linux, made by OsloMet, ICIJ and NRK.

## Documents stay on your computer

- The model runs locally (llama.cpp). Pages are only ever sent to a model server on this computer; any other address is refused.
- The internet is used only to download llama.cpp and the model, once. After that, the **Offline** switch in the app blocks all network access.
- No telemetry, no analytics, nothing loaded from the web.

## Install

Download the newest version from the [Releases page](../../releases/latest):

| System | File |
|---|---|
| macOS 14 or later, Apple Silicon | `LocalHarvest-macOS-AppleSilicon.dmg` |
| Windows, 64-bit | `LocalHarvest-Windows-x64-setup.exe` |
| Linux (Ubuntu 22.04 or later) | `LocalHarvest-Linux-x64.AppImage` or `.deb` |

The app is not signed yet, so the system warns the first time:

- **macOS**: open it once and close the warning, then go to **System Settings → Privacy & Security** and click **Open Anyway**.
- **Windows**: on "Windows protected your PC", click **More info**, then **Run anyway**.

The smallest recommended models need 9–12 GB of memory the GPU can use (an Apple Silicon Mac with 24 GB, or an NVIDIA GPU with 16 GB), and 10–20 GB of disk. The app checks your computer and recommends a model that fits.

## How it works

1. **Model**: the app installs llama.cpp and downloads a recommended model.
2. **Documents**: drop in PDFs or folders.
3. **Settings**: defaults work; the prompts can be edited under "Advanced".
4. **Run**: every page is first *triaged* (does it hold a relationship table?), then the selected pages are *extracted*. Runs can be cancelled and resumed.

The results folder holds:

- `relationships.csv`: one row per relationship, with the source file and page (`_filename`, `_page`), the main entity and its jurisdiction, the related person or company with address, country and nationality, its type (`Person` or `Company`), the relationship (`Owner`, `Director`, `Secretary`…) and notes.
- `triage.csv`: the decision for every page.
- `run.json` and the prompts as sent, so every run can be traced and repeated.

## Command line

The same pipeline runs without the app, from a JSON config. You need Python 3.12 and a model server running on this computer ([docs/model-servers.md](docs/model-servers.md)).

```bash
python3.12 -m venv .venv_extract && source .venv_extract/bin/activate
pip install -r requirements_extract.txt
python -m table_extract defaults > run.json    # edit inputs, output_dir and server.model
python -m table_extract run run.json           # run again to resume
```

All options are in [docs/command-line.md](docs/command-line.md).

## Documentation

| | |
|---|---|
| [docs/command-line.md](docs/command-line.md) | Config file, outputs, how extraction works, tuning |
| [docs/model-servers.md](docs/model-servers.md) | Running llama.cpp or vLLM yourself |
| [docs/development.md](docs/development.md) | Working on the app and its local API (the sidecar), tests |
| [docs/releasing.md](docs/releasing.md) | Building and publishing a version |
| [docs/evaluation.md](docs/evaluation.md) | Scoring models against annotated documents |
| [docs/gui-design.md](docs/gui-design.md) | Design decisions for the app |

## Repository layout

| | |
|---|---|
| `table_extract/` | The pipeline (triage, extraction, prompts) and the sidecar in `server/` |
| `app/` | The desktop app: Vue 3 interface, Tauri 2 shell in `app/src-tauri/` |
| `packaging/` | Release build scripts |
| `utils/` | Scoring tools for evaluating new models |
| `tests/` | Python tests (`python -m unittest discover tests`) |

## Status

Working: the local llama.cpp pipeline in the app and on the command line, releases for the three systems.
Not yet: Apple and Windows code signing, an in-app updater, searching Hugging Face for other models from the app, and using a remote vLLM server from the app. The Windows and Linux builds have not been tried on real machines.
