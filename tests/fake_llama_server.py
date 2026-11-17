"""A stand-in for llama-server in tests/test_models.py: same flags, same endpoints, no model.

    fake_llama_server.py [--behaviour ok|fail|slow|deaf] <llama-server arguments>

--version and --list-devices answer like llama.cpp b11429. "deaf" says it
listens but serves nothing, as when another program answers on its port.
Otherwise it serves /health (503 while "loading", then 200), /props, /v1/models and
/v1/chat/completions on --host:--port, and requires the LLAMA_API_KEY from
its environment on everything but /health.
"""

import argparse
import json
import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--behaviour", default="ok")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("-m")
    parser.add_argument("--mmproj")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--alias")
    parser.add_argument("-c", type=int, default=4096)
    args, _ = parser.parse_known_args()

    if args.version:
        print("version: 0.6.0 (build 11429, commit d81235049)", file=sys.stderr)
        return 0
    if args.list_devices:
        print("Available devices:\n  MTL0: Apple M1 Pro (12124 MiB, 12123 MiB free)")
        return 0
    print(f"main: loading model {args.m}", file=sys.stderr, flush=True)
    if args.behaviour == "fail":
        print("llama_model_load: error loading model: unknown model architecture: 'future'", file=sys.stderr)
        return 1

    if args.behaviour == "deaf":
        print(f"main: server is listening on http://{args.host}:{args.port}", file=sys.stderr, flush=True)
        time.sleep(3600)
        return 0

    key = os.environ.get("LLAMA_API_KEY")
    ready_at = time.monotonic() + (3.0 if args.behaviour == "slow" else 0.3)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            if key and self.headers.get("Authorization") != f"Bearer {key}":
                self._send(401, {"error": {"message": "Invalid API Key"}})
                return False
            return True

        def do_GET(self):
            if self.path == "/health":
                loading = time.monotonic() < ready_at
                self._send(503 if loading else 200, {"status": "loading model" if loading else "ok"})
            elif not self._authorized():
                return
            elif self.path == "/props":
                self._send(200, {"model_path": args.m, "total_slots": 1, "build_info": "b11429-fake",
                                 "default_generation_settings": {"n_ctx": args.c}})
            elif self.path == "/v1/models":
                self._send(200, {"data": [{"id": args.alias}]})
            else:
                self._send(404, {})

        def do_POST(self):
            if not self._authorized():
                return
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if self.path == "/v1/chat/completions":
                self._send(200, {"choices": [{"message": {"content": "NO"}, "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": 10, "completion_tokens": 1}})
            else:
                self._send(404, {})

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    print(f"main: server is listening on http://{args.host}:{args.port}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
