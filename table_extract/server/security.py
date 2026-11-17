"""Keeps other websites out of the sidecar.

Any web page the user visits can send requests to 127.0.0.1, and with DNS
rebinding read the answers. So every request must:

- name this server in its Host header (127.0.0.1 or localhost, on our port),
  which a rebound domain cannot do;
- carry the per-launch token, `Authorization: Bearer <token>`, which only the
  app's window has.

No CORS headers are ever sent, so browsers refuse to let other pages read
answers, and preflight requests fail.
"""

import secrets
from typing import Optional

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_ENV = "TABLE_EXTRACT_SIDECAR_TOKEN"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def host_allowed(host: Optional[str], port: Optional[int]) -> bool:
    if not host or host.startswith("["):  # we listen on IPv4 only
        return False
    name, _, given_port = host.partition(":")
    if name.lower() not in ALLOWED_HOSTS:
        return False
    if port is None:
        return True
    return (given_port or "80") == str(port)


def token_matches(authorization: Optional[str], token: str) -> bool:
    scheme, _, given = (authorization or "").partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(given.strip().encode(), token.encode())


class LocalOnly:
    """ASGI middleware: Host allowlist, then the token. Runs before routing, so it covers every path."""

    def __init__(self, app: ASGIApp, token: str, port: Optional[int] = None):
        if not token:
            raise ValueError("the sidecar needs a token")
        self.app = app
        self.token = token
        self.port = port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if not host_allowed(headers.get("host"), self.port):
            response = JSONResponse({"error": "bad_host", "message": "Invalid Host header."}, status_code=400)
        elif not token_matches(headers.get("authorization"), self.token):
            response = JSONResponse({"error": "unauthorized", "message": "Missing or wrong token."},
                                    status_code=401, headers={"WWW-Authenticate": "Bearer"})
        else:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http":
            await response(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})
