"""Start the sidecar: `python -m table_extract serve [--port N]` (frozen: `<app> serve`).

The token comes from TABLE_EXTRACT_SIDECAR_TOKEN, which the Tauri shell sets
to a fresh random value at each launch; without it a token is generated. Once
listening, the sidecar prints one JSON line on stdout:

    {"event": "listening", "host": "127.0.0.1", "port": 53817, "url": "http://127.0.0.1:53817"}

with "token" added when it was generated. Nothing else is written to stdout.
"""

import atexit
import json
import os
import secrets
import socket
import sys
from pathlib import Path
from typing import Optional

from .app import Services, Settings, create_app
from .runs import RunManager
from .security import TOKEN_ENV

HOST = "127.0.0.1"


def serve(port: int = 0, llama_server: Optional[str] = None, stop_when_stdin_closes: bool = False,
          data_dir: Optional[Path] = None) -> int:
    import uvicorn

    # Out of the environment, so runs and llama-server never inherit it
    token = os.environ.pop(TOKEN_ENV, None)
    generated = not token
    token = token or secrets.token_urlsafe(32)
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "posix":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, port))
    except OSError as e:
        print(f"Cannot listen on {HOST}:{port}: {e}", file=sys.stderr)
        return 1
    port = sock.getsockname()[1]

    settings = Settings(token=token, port=port, llama_server=llama_server, data_dir=data_dir)
    manager = RunManager(settings.runner_command, cancel_grace=settings.cancel_grace)
    services = Services.create(settings)
    app = create_app(settings, manager, services)
    # In case the server stops without its shutdown hook
    atexit.register(manager.shutdown)
    atexit.register(services.supervisor.shutdown)

    announce = {"event": "listening", "host": HOST, "port": port, "url": f"http://{HOST}:{port}"}
    if generated:
        announce["token"] = token
    print(json.dumps(announce), flush=True)

    if stop_when_stdin_closes:
        from ..lifeline import stop_when_stdin_closes as watch_stdin
        watch_stdin(exit_code=0)

    config = uvicorn.Config(
        app, log_level="warning", access_log=False, server_header=False, proxy_headers=False,
        # Explicit choices keep the frozen build the same on every platform
        loop="asyncio", http="h11", ws="none", lifespan="on",
        # Event streams stay open; give them this long, then stop the run and exit
        timeout_graceful_shutdown=3,
    )
    uvicorn.Server(config).run(sockets=[sock])
    return 0
