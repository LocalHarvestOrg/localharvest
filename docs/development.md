# Development

Two parts:

- **The app**, in `app/`: a Vue 3 + Vite + TypeScript interface (Pinia, bootstrap-vue-next, ICIJ Murmur, styled like Datashare) inside a Tauri 2 shell (`app/src-tauri/`). Screens: Model, Documents, Settings (prompts under "Advanced") and Run, plus a welcome screen on first launch.
- **The sidecar**, in `table_extract/server/`: a local HTTP API the app drives. It starts runs as `python -m table_extract run` subprocesses, so app and command-line runs are the same code. It also installs llama.cpp, recommends and downloads models, and starts and stops llama-server.

## Setup

- Python 3.12 and the `.venv_extract` environment ([command-line.md](command-line.md#setup)). The app starts the sidecar from it.
- Node.js 24 and npm. The Tauri CLI is an npm dev dependency.
- For the app window: Rust (`rustup`) and, on macOS, the Xcode Command Line Tools.

```bash
cd app && npm install
```

Turn on the checks that stop secrets and real documents before they are committed or pushed, once per clone:

```bash
git config core.hooksPath .githooks
```

## Contributing

`main` is protected: every change is a pull request, squash-merged after the owner's review and the `sensitive-data` check. This repository is public, and anything pushed, even to a branch, can be seen and copied at once, so never commit real documents, annotations, model output or credentials.

The check refuses secrets, documents, datasets, archives, scans, notebook outputs, personal paths and email addresses. Run it by hand with `python3 .github/scripts/check_sensitive.py --all`. A false positive goes in `.github/sensitive-allowlist`, in its own pull request, because CI reads that file from `main`.

## Running the app

**Browser mode**, the quickest for working on screens. Start the sidecar with a fixed token, then Vite:

```bash
TABLE_EXTRACT_SIDECAR_TOKEN=dev-token .venv_extract/bin/python -m table_extract serve --port 8765
```

```bash
cd app && npm run dev
```

Open http://localhost:1420. Vite forwards `/sidecar/*` to `127.0.0.1:8765`, so requests stay same-origin. Set `SIDECAR_PORT` or `VITE_SIDECAR_TOKEN` in `app/.env.local` to change them. A browser cannot give real file paths: type full paths for documents and the results folder.

**Tauri mode**, the real window:

```bash
cd app && npm run tauri dev
```

The app starts the sidecar itself (`.venv_extract/bin/python -m table_extract serve --port 0 --stop-when-stdin-closes`) with a fresh token, and stops it on quit. To start it some other way, set `TABLE_EXTRACT_SIDECAR_COMMAND` to a JSON array, for example `'["/path/to/python", "-m", "table_extract", "serve"]'`. While a run is going the computer is kept awake (idle sleep only).

**Without a model**: a fake model server answers every page with "no ownership table" after a delay, enough to start, cancel and resume runs:

```bash
.venv_extract/bin/python app/dev/fake_model_server.py --port 8000 --delay 3
```

In Settings, open "Use my own model server", choose "Use a model server I started myself" and enter `http://127.0.0.1:8000/v1/chat/completions`.

## Tests

```bash
.venv_extract/bin/python -m unittest discover tests     # Python, about 30 s, no network or model needed
cd app && npm test && npm run typecheck                  # Vitest and vue-tsc
```

The Python tests use fake model, Hugging Face, GitHub and llama-server processes on `127.0.0.1`.

Every pull request into `main` runs both on GitHub Actions ([tests.yml](../.github/workflows/tests.yml)), as the `python-tests` and `app-tests` checks.

An end-to-end check drives the whole app in a Tauri window (debug builds only), with the fake model server running. It starts a run, cancels, resumes and replaces it, opens the results folder, quits and prints `SMOKE TEST PASSED` or `FAILED`. Use a short PDF and a new `output_dir` each time:

```bash
cd app && TABLE_EXTRACT_SMOKE_TEST='{"inputs": ["/full/path/to/short.pdf"], "output_dir": "/tmp/table-extract-smoke-1", "server_url": "http://127.0.0.1:8000/v1/chat/completions"}' npm run tauri dev -- --no-watch
```

## The sidecar

```bash
python -m table_extract serve --port 8765
```

It listens on `127.0.0.1` only and prints one JSON line when ready, with the port and the token: `{"event": "listening", "port": 8765, "url": "...", "token": "..."}`.

- Every request needs `Authorization: Bearer <token>`. The token is `TABLE_EXTRACT_SIDECAR_TOKEN` if set (then not printed), otherwise a new random one.
- `--port 0` (default) picks a free port.
- `--llama-server PATH`: llama-server to use. Default: `TABLE_EXTRACT_LLAMA_SERVER`, then the app's llama.cpp, then the PATH.
- `--data-dir PATH`: where models, llama.cpp and settings go. Default: `TABLE_EXTRACT_DATA_DIR`, then `~/Library/Application Support/org.icij.localharvest` (macOS), `%LOCALAPPDATA%\org.icij.localharvest` (Windows), `~/.local/share/org.icij.localharvest` (Linux).
- `--stop-when-stdin-closes`: exit when the app that started it does.

Trying it with curl:

```bash
export TABLE_EXTRACT_SIDECAR_TOKEN=dev-token
python -m table_extract serve --port 8765 &
curl -s -H "Authorization: Bearer dev-token" http://127.0.0.1:8765/config/defaults > config.json
# edit inputs and output_dir (full paths), then:
curl -s -H "Authorization: Bearer dev-token" -H "Content-Type: application/json" -d "{\"config\": $(cat config.json)}" http://127.0.0.1:8765/run
curl -N -H "Authorization: Bearer dev-token" http://127.0.0.1:8765/run/events
```

### Endpoints

Errors are JSON: `{"error": "<code>", "message": "<text>"}`.

| Endpoint | What it does |
|---|---|
| `GET /health` | App name and version |
| `GET /config/defaults`, `GET /config/schema` | A config with every default; its JSON schema |
| `POST /config/validate` | Checks a config: `errors` per field, the PDFs found, and the output folder's state (`new`, `resume`, `conflict`, `invalid`) |
| `POST /run` | `{"config": {...}, "overwrite": false}`. Starts a run (202). One at a time (409 `run_active`); a folder with different settings is refused (409 `output_conflict`) unless `overwrite` |
| `GET /run` | State, result, progress summary and last stderr lines of the current or last run |
| `POST /run/cancel` | Stops the run (killed after 10 s); it can be resumed |
| `GET /run/events` | Server-Sent Events from `events.jsonl`; reconnect with `Last-Event-ID`; ends with an `end` event |
| `GET /hardware` | GPUs and their memory (`llama-server --list-devices`), RAM, OS, CPU |
| `GET`/`PUT /settings` | `{"offline": false}` and the data folder. While offline, every endpoint that would use the network answers 403 `offline` |
| `GET /runtime`, `POST /runtime/install` | The llama.cpp in use and the builds available; install the pinned release (SHA-256 checked) |
| `GET /models/recommendation` | Curated models (`server/models.json`) with size, memory verdict for this computer (`gpu`, `partial`, `cpu`, `too_big`) and the one to suggest. No network |
| `GET /models/installed`, `DELETE /models/installed?id=` | Downloaded models and disk use; delete one |
| `POST /models/check` | llama.cpp's own fit verdict for a downloaded model |
| `GET /models/search`, `GET /models/hub` | Hugging Face vision GGUF models and their quantizations with memory verdicts (no screen yet) |
| `POST /downloads`, `GET /downloads`, `POST /downloads/{id}/cancel` | Download a model and its projector (disk space checked, resumable, SHA-256 checked) |
| `GET /servers`, `POST /servers/start`, `POST /servers/stop` | The supervised llama-server: state, address, logs, and the run settings that point at it |

### Security

- **Other websites cannot use it.** It answers only requests with `Host` `127.0.0.1` or `localhost` on its port (against DNS rebinding) and the token, and sends no CORS headers. So the app calls it from the Tauri side (`tauri-plugin-http`, scoped to `http://127.0.0.1:*`), not with the webview's `fetch`.
- **Network only for downloads.** Only the Hub search and listing, model downloads and the llama.cpp install reach the network, with plain HTTP requests, never while offline. A Hugging Face token (gated models only) comes from `HF_TOKEN` or `hf auth login`, goes only to `huggingface.co` and is never stored. Runs get `HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1` and `OTEL_SDK_DISABLED=true`. No Swagger pages, since they load scripts from a CDN.
- **Model servers are locked.** Each llama-server runs with `--offline`, `--host 127.0.0.1` and a random API key passed in `LLAMA_API_KEY` (not on the command line, which other users can see). It prefers port 8000, so a run's settings stay the same between launches.
- **Nothing outlives the sidecar.** Runs are in their own process group and stop when the sidecar exits, or when their stdin closes if it was killed (`table_extract/lifeline.py`). llama-server process ids are kept in `servers.json`, so the next launch stops any left behind.
- **The app window.** Its Content Security Policy allows no other origin. Fonts and icons are bundled; no telemetry in the app or its npm and Cargo dependencies. Settings are kept in the app's local storage; documents are read where they are, never copied.
