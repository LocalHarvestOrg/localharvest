"""llama-server supervisor: starts, watches and stops the model server a run needs.

One llama-server per role; the only role is "vision" (the page-reading
model). It is started with --offline, --host 127.0.0.1 and a random --api-key
that only the sidecar holds and hands to runs through TABLE_EXTRACT_API_KEY
(passed in LLAMA_API_KEY rather than on the command line, which other users
of the computer can see). It prefers the port in the config's defaults
(8000), so a run's settings and its resume fingerprint stay the same across
launches; when the port is taken another free one is used, and GET /servers
says which. A port counts as taken when something already answers on it, not
only when it cannot be bound: on macOS a program listening on all addresses,
or a port forward (Docker, OrbStack, a VPN), leaves 127.0.0.1 bindable while
connections still go elsewhere.

Cleanup: each server has its own process group and is stopped with the
sidecar. Their process ids are kept in <data>/servers.json, so if the
sidecar was killed outright the next launch stops what it left behind.
"""

import os
import secrets
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

import psutil
import requests

from .storage import Storage, read_json, write_json

POSIX = os.name == "posix"
HOST = "127.0.0.1"
PREFERRED_PORTS = {"vision": 8000}
LOAD_TIMEOUT = 600.0  # seconds; a 10 GB model from a slow disk
ANSWER_TIMEOUT = 15.0  # seconds from "listening" for the server to answer as ours
STOP_GRACE = 5.0
LOG_LINES = 400
ROLES = ("vision",)


@dataclass
class ServerSpec:
    role: str
    model_path: str
    alias: str
    context: int
    mmproj_path: Optional[str] = None
    flags: List[str] = field(default_factory=list)
    model_id: Optional[str] = None


@dataclass
class Server:
    spec: ServerSpec
    port: int
    process: Any
    state: str = "starting"  # starting, ready, failed, stopped
    error: Optional[str] = None
    started: float = field(default_factory=time.time)
    ready_at: Optional[float] = None
    listening_at: Optional[float] = None  # monotonic time llama-server said it listens
    stop_requested: bool = False
    log: Deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LINES))

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def to_json(self) -> Dict[str, Any]:
        return {
            "role": self.spec.role, "state": self.state, "error": self.error,
            "model_id": self.spec.model_id, "alias": self.spec.alias, "context": self.spec.context,
            "url": self.url + "/v1/chat/completions", "port": self.port, "pid": self.process.pid,
            "started": self.started, "ready_at": self.ready_at,
            "exit_code": self.process.poll(), "log": list(self.log)[-30:],
        }


def port_free(port: int) -> bool:
    for address in (HOST, "::1"):
        try:
            with socket.create_connection((address, port), timeout=0.5):
                return False  # something answers there, whether or not the port can be bound
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if POSIX:  # as llama-server binds: a port whose old connections linger in TIME_WAIT is free
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def server_env(api_key: str) -> Dict[str, str]:
    """The sidecar's environment without anything that could steer llama-server or leak to it."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LLAMA_", "TABLE_EXTRACT_", "HF_TOKEN", "HUGGING_FACE"))}
    env.update({"LLAMA_API_KEY": api_key, "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1"})
    return env


def command(llama_server: str, spec: ServerSpec, port: int) -> List[str]:
    cmd = [llama_server, "-m", spec.model_path]
    if spec.mmproj_path:
        cmd += ["--mmproj", spec.mmproj_path]
    cmd += ["--offline", "--host", HOST, "--port", str(port),
            "-c", str(spec.context), "-np", "1", "--alias", spec.alias, "--no-webui"]
    return cmd + list(spec.flags)


class Supervisor:
    def __init__(self, storage: Storage, llama_server: Callable[[], Optional[str]],
                 popen: Callable[..., Any] = subprocess.Popen, load_timeout: float = LOAD_TIMEOUT,
                 answer_timeout: float = ANSWER_TIMEOUT):
        self.storage = storage
        self.llama_server = llama_server
        self.popen = popen
        self.load_timeout = load_timeout
        self.answer_timeout = answer_timeout
        self.api_key = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._servers: Dict[str, Server] = {}
        self._ports: Dict[str, int] = {}

    @property
    def registry(self) -> Path:
        return self.storage.root / "servers.json"

    # Leftovers from a sidecar that was killed

    def clean_up_leftovers(self) -> List[int]:
        stopped = []
        for entry in read_json(self.registry, []) or []:
            try:
                process = psutil.Process(int(entry["pid"]))
                if abs(process.create_time() - float(entry["create_time"])) > 1:
                    continue  # the id now belongs to another program
                process.terminate()
                try:
                    process.wait(STOP_GRACE)
                except psutil.TimeoutExpired:
                    process.kill()
                stopped.append(process.pid)
            except (psutil.Error, KeyError, ValueError, TypeError):
                continue
        self._save_registry()
        return stopped

    def _save_registry(self) -> None:
        entries = []
        with self._lock:
            for server in self._servers.values():
                if server.process.poll() is None:
                    try:
                        created = psutil.Process(server.process.pid).create_time()
                    except psutil.Error:
                        continue
                    entries.append({"pid": server.process.pid, "create_time": created, "role": server.spec.role})
        try:
            write_json(self.registry, entries)
        except OSError:
            pass

    # Start and stop

    def start(self, spec: ServerSpec) -> Server:
        """Start a server for spec.role, replacing any other one in that role. Returns at once; poll status()."""
        executable = self.llama_server()
        if not executable:
            raise RuntimeError("llama.cpp is not installed.")
        with self._lock:
            current = self._servers.get(spec.role)
            if current and current.spec == spec and current.state in ("starting", "ready"):
                return current
        if current:
            self.stop(spec.role)

        with self._lock:
            port = self._ports.get(spec.role) or PREFERRED_PORTS[spec.role]
            if not port_free(port):
                port = free_port()
            options: Dict[str, Any] = {}
            if POSIX:
                options["start_new_session"] = True
            else:
                options["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                            | getattr(subprocess, "CREATE_NO_WINDOW", 0))
            process = self.popen(command(executable, spec, port), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=server_env(self.api_key),
                                 text=True, encoding="utf-8", errors="replace", **options)
            server = Server(spec=spec, port=port, process=process)
            self._servers[spec.role] = server
            self._ports[spec.role] = port
        self._save_registry()
        threading.Thread(target=self._read_log, args=(server,), name=f"{spec.role}-log", daemon=True).start()
        threading.Thread(target=self._wait_ready, args=(server,), name=f"{spec.role}-ready", daemon=True).start()
        return server

    def _read_log(self, server: Server) -> None:
        stream = server.process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                server.log.append(line.rstrip("\n"))
                if server.listening_at is None and "listening on http" in line:
                    server.listening_at = time.monotonic()
        except (OSError, ValueError):
            pass
        finally:
            stream.close()

    def _wait_ready(self, server: Server) -> None:
        session = requests.Session()
        session.trust_env = False  # a system proxy must not get the requests for 127.0.0.1
        session.headers["Authorization"] = f"Bearer {self.api_key}"
        deadline = time.monotonic() + self.load_timeout
        while time.monotonic() < deadline:
            if server.stop_requested:
                return
            code = server.process.poll()
            if code is not None:
                server.state = "failed"
                server.error = self._failure(server, code)
                return
            loading = False
            try:
                health = session.get(f"{server.url}/health", timeout=2).status_code
                if health == 200 and self._is_ours(session, server):
                    server.state, server.ready_at = "ready", time.time()
                    return
                loading = health == 503
            except requests.RequestException:
                pass
            # Ours says it listens, yet what answers on its port is not ours (or nothing does)
            if (not loading and server.listening_at is not None
                    and time.monotonic() - server.listening_at > self.answer_timeout):
                self._give_up(server, f"Another program is answering on port {server.port}, so the model "
                                      "server cannot be reached. Quit that program, or turn off any proxy "
                                      "or VPN, and try again.")
                return
            time.sleep(0.5)
        self._give_up(server, f"The model did not load within {self.load_timeout:.0f} seconds.")

    def _give_up(self, server: Server, error: str) -> None:
        # Stop it before saying so: whoever sees "failed" may start another one on the same port
        self._terminate(server)
        if server.stop_requested:
            return
        server.state, server.error = "failed", error

    def _is_ours(self, session: requests.Session, server: Server) -> bool:
        """Another program could answer on the port before ours fails to bind it: check the model."""
        try:
            props = session.get(f"{server.url}/props", timeout=2).json()
        except (requests.RequestException, ValueError):
            return False
        path = props.get("model_path") or ""
        return server.process.poll() is None and Path(path).name == Path(server.spec.model_path).name

    @staticmethod
    def _failure(server: Server, code: int) -> str:
        log = "\n".join(server.log)
        if "address already in use" in log.lower() or "couldn't bind" in log.lower():
            return f"Port {server.port} is already in use."
        if "unknown model architecture" in log.lower():
            return "This llama.cpp is too old for this model. Update llama.cpp."
        if "failed to allocate" in log.lower() or "out of memory" in log.lower():
            return "Not enough memory to load the model."
        last = next((line for line in reversed(server.log) if line.strip()), "")
        return f"llama-server stopped (exit code {code})" + (f": {last.strip()}" if last else ".")

    def _terminate(self, server: Server) -> None:
        process = server.process
        if process.poll() is not None:
            return
        try:
            if POSIX:
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            process.wait(STOP_GRACE)
        except subprocess.TimeoutExpired:
            try:
                if POSIX:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                process.wait(2)
            except subprocess.TimeoutExpired:
                pass

    def stop(self, role: Optional[str] = None) -> None:
        """Stop one server or all."""
        with self._lock:
            servers = [s for r, s in self._servers.items() if role in (None, r)]
            for server in servers:
                server.stop_requested = True
        for server in servers:
            self._terminate(server)
            server.state = "stopped" if server.state != "failed" else server.state
        self._save_registry()

    def shutdown(self) -> None:
        self.stop()

    # State, and what runs need

    def get(self, role: str) -> Optional[Server]:
        server = self._servers.get(role)
        if server and server.state in ("starting", "ready") and server.process.poll() is not None:
            server.state = "failed"
            server.error = self._failure(server, server.process.poll())
        return server

    def running(self) -> bool:
        return any(s.process.poll() is None for s in self._servers.values())

    def status(self) -> Dict[str, Any]:
        servers = {role: (s.to_json() if (s := self.get(role)) else None) for role in ROLES}
        return {"servers": servers, "config": self.config_patch()}

    def config_patch(self) -> Dict[str, Any]:
        """The run settings that point at these servers, for the app to merge into its config."""
        patch: Dict[str, Any] = {}
        vision = self._servers.get("vision")
        if vision and vision.state != "stopped":
            patch["server"] = {"url": f"{vision.url}/v1/chat/completions", "model": vision.spec.alias}
        return patch

    def run_env(self) -> Dict[str, str]:
        """Added to a run's environment: the key for these servers."""
        return {"TABLE_EXTRACT_API_KEY": self.api_key} if self._servers else {}
