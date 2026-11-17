# GUI design: desktop app for the local pipeline

Design for a desktop application that lets non-technical users run the local (llama.cpp) pipeline: pick a model suited to their computer, set the parameters, adjust the prompt, choose the documents and an output folder, and run triage and extraction. Decisions recorded 2026-10-15.

## Scope

**In v1**

- Model picker fed by the Hugging Face Hub, with a recommendation based on the computer's GPU and memory, and in-app download.
- Pipeline parameters (DPI, token budgets, sampling, workers) with safe defaults.
- Prompt editing in a guarded "Advanced" panel.
- Input documents by drag and drop, or by choosing files or a folder.
- Output folder chosen by the user, with a default of `~/Documents/<App name>/<date>_<model>/`.
- Run with plain-language progress, cancel and resume, and an "Open results folder" button at the end.

**Not in v1**

- Viewing results inside the app (results are CSV files the user opens elsewhere).
- Single-page prompt playground, page review viewer, evaluation and run comparison, parameter sweeps.
- Evaluation against the annotated corpus stays a command-line tool for developers.
- The vLLM server path. The pipeline keeps working against vLLM from the command line, but the app only manages llama.cpp.

## Decisions

| Area | Decision | Reason |
|---|---|---|
| Users | Non-technical users (journalists) | Drives everything below: installer, no terminal, plain-language UI. |
| Platforms | macOS (Apple Silicon), Windows, Linux | Intel Macs are not supported. |
| Shell | Tauri 2 | Native file dialogs and drag and drop with real paths, installers, signing, notarization and auto-update built in. |
| Frontend | Vue 3 + Vite + TypeScript, Pinia | Matches the team's other projects. |
| Look | Same as Datashare: bootstrap-vue-next + ICIJ Murmur | Familiar to ICIJ users. |
| Backend | FastAPI, frozen with PyInstaller (one-folder mode) as a Tauri sidecar | The pipeline is Python. One-folder mode avoids Windows Defender false positives and unpacking on every launch. |
| Pipeline integration | Refactored package, launched as a subprocess per run | CLI and app runs are identical; cancel is a kill; a crash in a run never takes the app down. |
| Prompt editing | Guarded "Advanced" panel | Guided questions, grouped by the step that uses them (finding the pages only shown when triage runs), write both prompts. Below them, each prompt can be read and edited as raw text; once edited by hand it no longer follows the answers until rebuilt from them. The output format is never in that text (a marker line shows where it goes), so parsing cannot break. Reset to default, saved presets. |
| PDF handling | pypdfium2 instead of Poppler | Poppler has no reliable Windows builds to bundle and is GPL; pypdfium2 is permissively licensed with wheels for every platform. |
| llama.cpp | Downloaded on first launch, not bundled | The right build differs per machine, CUDA builds are large, and new model architectures need newer llama.cpp without a new installer. |
| Run storage | `run.json` and `events.jsonl` in each output folder | No database. |
| Live updates | Server-Sent Events | Progress and logs only flow one way. |

## Architecture

```
Tauri window (Vue UI)
   │  native dialogs and drag and drop give real file paths
   │  REST + Server-Sent Events, random port, random token
FastAPI sidecar (frozen Python)
   ├─ Hardware probe     ──► GPU type, llama-server --list-devices, RAM
   ├─ Model manager      ──► Hugging Face Hub   (network, model files only)
   ├─ Runtime manager    ──► llama.cpp releases (network, binaries only)
   ├─ Server supervisor  ──► llama-server :vision model   (--offline, 127.0.0.1, random --api-key)
   └─ Run manager        ──► <app binary> --run run.json   (subprocess, writes events.jsonl)
```

The app binary is frozen, so there is no `python` to call: it re-launches itself in runner mode. During development the same entry point runs as `python -m table_extract run run.json`.

## Step 1: pipeline refactor

The scripts (`triage_doc.py`, `extract_info.py`) become one package, `table_extract/`, that both the CLI and the app drive.

- **One config object.** A Pydantic model holds every tunable that is a module constant or hardcoded value today: DPI per stage, `MAX_TOKENS_SCHEDULE`, `TOKENS_PER_ENTITY_LIMIT`, workers, server URL and model, sampling. It validates GUI input, `run.json` and CLI flags alike.
- **Schema-driven prompt.** The extraction fields (names and order) are fixed in code and generate the prompt's row format, the parser's column mapping and the CSV header together. The editable parts are the field descriptions and the instruction sections. This removes the current risk that a prompt edit silently breaks `parse_json`.
- **Runner mode.** `run(config)` executes triage → extraction in sequence, writes `run.json` (full config, prompt hash, model file and llama.cpp build, git commit when available) and appends progress events to `events.jsonl`. Per-page resume is kept.
- **Sampling sent explicitly.** Temperature, top-p and seed are sent with every request, so runs no longer depend on each model file's defaults.
- **Context budget check.** Prompt tokens + image tokens + the largest output budget must fit in the server's per-slot context. With Qwen3.5-9B at 200 DPI they do not: about 5,800 input tokens plus the 12,288-token third attempt exceeds `-c 16384`. Gemma 4 12B uses about 2,400 input tokens per page and fits.
- **API key.** Requests carry an optional `Authorization` header, so the app can start llama-server with a random `--api-key`.
- **Existing commands keep working.** The scripts stay as thin wrappers with their current arguments and output files.
- **Acceptance.** Extraction on the evaluation corpus is no worse than with the old scripts. There is no full-corpus baseline yet; full runs take about an hour on a 16 GB M1 Pro, so they run when the machine is free, not during development.

**Status (2026-10-15):** done, except the full-corpus extraction comparison.

- **Prompts.** The default prompts render byte-identical to the old ones.
- **Rendering.** Renders match Poppler's to anti-aliasing.
- **Small-file check.** On `bam.pdf` with greedy decoding, triage is identical and extraction finds the same 10 entities. One surname is misread differently, which is the variance to expect from a different renderer.

**Page pre-filter removed (2026-10-26).** A text-layer pre-filter used to run before triage: table-line rules (legal-form suffixes and honorifics next to numbers or dates), the three pages per document most similar to ownership-table prototypes (bge-m3 embeddings), and scans passed straight through. On the annotated tabular_selection_2 set (30 documents, 7,732 pages, 474 gold pages) it kept 51% of the pages but only 37% of the gold ones: triage alone found 465 of the 474 gold pages, and 170 behind the pre-filter. Registry forms with one bare name per page, scans with a noisy OCR text layer and long documents defeated it. It was tuned to one kind of document, so it was removed from the pipeline, the sidecar (the bge-m3 server) and the app: every page now goes to triage. Configs that still hold a `prefilter` section load, and the section is ignored.

## Model picker and memory recommendation

1. **Memory budget.** GPU memory from `llama-server --list-devices`, which reports per-device memory the same way on Metal, CUDA and Vulkan; system RAM from `psutil`. Windows "shared GPU memory" is system RAM over PCIe and counts as the partial-offload tier, not as VRAM.
2. **Catalog.** Hugging Face models tagged image-text-to-text with GGUF files, sorted by trending or downloads, keeping only repos that ship an `mmproj-*.gguf` vision projector. Files are grouped by quantization. By default only trusted publishers are listed (ggml-org, unsloth, bartowski, lmstudio-community and official model organisations), with a toggle to show all.
3. **Curated list.** A `models.yaml` shipped with the app lists known-good models: repo, quantization, projector, and the server flags each needs (`--reasoning off`, `--image-max-tokens`, KV cache type). The default recommendation comes from this list.
4. **Estimate before download.** Weights + projector + KV cache at the chosen context + compute buffer. KV cache size is read from the GGUF header with an HTTP range request, and accounts for architectures with sliding-window or linear-attention layers (Gemma, Qwen3.5), whose cache is much smaller than the naive formula.
5. **Check after download.** `llama-fit-params` gives llama.cpp's own verdict in under a second; the projector is added on top.
6. **What the user sees.** One "Recommended for your computer" choice. Under "Details", each quantization shows: fits on the GPU, partial offload (slower), does not fit, or needs a newer llama.cpp.
7. **Download.** Into the app's data folder, resumable, with progress, a disk space check first, and SHA-256 verification against the hash Hugging Face publishes. Gated models (Gemma) use the token from `hf auth login`; the app never stores it.

## llama.cpp runtime

On first launch the app detects the GPU type (Apple Silicon, NVIDIA, other), downloads the matching pinned llama.cpp release (Metal, CUDA, Vulkan or CPU) from GitHub, verifies its checksum, and stores it in the app's data folder. Updating llama.cpp is a runtime update inside the app, not a new installer.

The app starts one `llama-server` for the vision model, always with `--offline`, `--host 127.0.0.1` and a random `--api-key`. llama.cpp's router mode (one server that loads and unloads models) is a later option.

## Step 2: sidecar

**Status (2026-10-16):** done in `table_extract/server/`; the endpoints are listed in the README. The Vue UI does not use the model endpoints yet.

- **Hardware probe and estimates** (`hardware.py`, `estimate.py`, `gguf.py`). The KV cache estimate matches `llama-fit-params` to the MiB on the local Qwen3.5-9B and Gemma 4 12B files, and the compute buffers to within 2%. On a 16 GB M1 Pro, Qwen3.5-9B Q8_0 and Gemma 4 12B both fit on the GPU.
- **Curated list** (`models.json`). Qwen3.5-9B Q8_0 and Gemma 4 12B Q4_K_M (tested), Qwen3.5-9B Q4_K_M and Qwen3.5-4B Q4_K_M (not yet checked against the corpus). Each entry pins a commit, file sizes and SHA-256, and the hyperparameters the estimate needs, so the recommendation works offline.
- **Hub catalog and downloads** (`hub.py`, `catalog.py`, `downloads.py`). Plain HTTP, no `huggingface_hub`. Remote GGUF headers are read with range requests and cached.
- **llama.cpp runtime** (`runtime.py`). Pinned to b11429, with the SHA-256 GitHub published for each asset. Variants: Metal on Apple Silicon; CUDA 12.4 (Windows) or 12.8 (Linux) for NVIDIA, with the CUDA runtime archive; Vulkan for other GPUs; CPU otherwise. The GPU maker is found without llama.cpp: `/sys/class/drm` on Linux, `Win32_VideoController` on Windows.
- **Server supervisor** (`supervisor.py`). Starts, watches and stops the vision model's llama-server, and cleans up servers a killed sidecar left behind.
- **Checked on a 16 GB M1 Pro**: the app installed llama.cpp b11429, served Gemma 4 12B from it, and ran `bam.pdf` (10 entities extracted). After the sidecar was killed with `kill -9`, the next launch stopped the leftover llama-server.
- **For packaging (step 5)**: `models.json` is package data; `packaging/sidecar.spec` collects it with the prompts.
- **Open**: the Windows and Linux runtime variants have not been run on real machines yet.

## Data security

The documents are sensitive and must never leave the machine (see the README's data security section).

- **Network access is limited to two components**: the model manager (model files) and the runtime manager (llama.cpp binaries). Neither sends anything about the documents. Everything else runs with `HF_HUB_OFFLINE=1` and `HF_HUB_DISABLE_TELEMETRY=1`. The UI shows when the app is online, and an air-gapped setting turns both components off.
- **Documents are read in place.** Native dialogs and drag and drop give real paths; documents are never copied into the app's storage.
- **Local servers are not open to other websites.** Any web page the user visits can send requests to `127.0.0.1`, and with DNS rebinding read the answers. The FastAPI sidecar listens on a random port, checks the Host header, requires a random per-launch token that Tauri passes to the window, and sends no CORS headers. llama-server gets a random `--api-key` that only the sidecar holds.
- **No telemetry** in the app, the sidecar or any library it uses.

## Packaging and distribution

**Status (2026-10-19):** macOS Apple Silicon builds work: `packaging/build_sidecar.sh` then `npm run release` (see the README's "Building a release"). The app is signed ad hoc, so recipients need System Settings → Privacy & Security → Open Anyway the first time. Still to do: Developer ID signing and notarization (the sidecar's binaries need the hardened runtime too), Windows signing, the updater.

**Status (2026-10-29):** Windows (NSIS installer) and Linux (AppImage, `.deb`) are built by GitHub Actions (`.github/workflows/release.yml`) and added to the release `packaging/release_dev.sh` publishes. Keeping the computer awake during a run covers all three (a power request on Windows, `systemd-inhibit` on Linux). Not yet tried on real Windows and Linux machines.

**Status (2026-11-02):** Development builds are gone. Releases are versions (`v0.3.0`) made from `main` by `packaging/release.sh`, on a Mac because macOS runners cost ten times the Linux minutes on a private repository. They start as a draft, are published by hand once tested, and are never deleted.

- **macOS floor**: macOS 14, set by numpy's wheels. The sidecar must be built with uv's managed Python: a pyenv or Homebrew Python is compiled for the build machine's macOS and links Homebrew's openssl.
- **Installers**: `.dmg` (macOS), `.msi`/`.exe` (Windows), AppImage and `.deb` (Linux), built by Tauri.
- **Signing**: macOS needs an Apple Developer ID for notarization, otherwise macOS tells users the app cannot be opened. Windows needs a code-signing certificate or Azure Trusted Signing. Linux needs none.
- **CI**: PyInstaller cannot cross-compile, so GitHub Actions builds on macOS arm64, Windows and Ubuntu.
- **Updates**: Tauri's updater, with signed update manifests on GitHub Releases.
- **Process cleanup**: when the app quits or crashes, the sidecar, llama-server and any run must stop (a process group on macOS and Linux, a Job Object on Windows). Otherwise a leftover llama-server keeps holding GPU memory.
- **Long runs**: the app keeps the computer awake during a run (`caffeinate` on macOS, `PowerSetRequest` on Windows, `systemd-inhibit` on Linux), released even if the app crashes.

## Build order

1. **Pipeline refactor** (above).
2. **FastAPI sidecar**: hardware probe, model catalog, estimates and downloads, llama.cpp runtime, server supervision, runs with a progress stream.
3. **Vue UI**: Welcome, then Documents (with which pages to read) → Settings (the model setup first, the rest in closed sections, with the Advanced prompt panel) → Run.
4. **Tauri shell**: sidecar lifecycle, native dialogs, drag and drop.
5. **Distribution**: packaging, CI, signing, updater.

## Open questions

- Does ICIJ have an Apple Developer ID and a Windows signing certificate, or should Azure Trusted Signing be set up?
- The app's name, which also sets the default output folder.
- Which models go in the curated list, and at which quantizations, for 8, 16 and 24+ GB machines. The current list (Step 2 above) has two untested entries, Qwen3.5-9B Q4_K_M and Qwen3.5-4B Q4_K_M, to evaluate on the corpus. Nothing is aimed at 24+ GB machines yet.
