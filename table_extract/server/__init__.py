"""The desktop app's sidecar: a local HTTP API over the pipeline (docs/gui-design.md, step 2).

    python -m table_extract serve --port 8765

It listens on 127.0.0.1 only, checks the Host header and a per-launch token
on every request, and sends no CORS headers (security.py). Runs are child
processes, one at a time (runs.py); the endpoints are listed in app.py.

Model management: llama.cpp builds (runtime.py), the curated list and Hub
catalog (catalog.py, hub.py, models.json), memory estimates (estimate.py,
gguf.py), downloads (downloads.py) and the llama-server processes
(supervisor.py). Downloads, settings and the server registry live in the
app's data folder (storage.py).
"""
