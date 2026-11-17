"""The sidecar's HTTP API. Every request needs the Host and token checks in security.py.

    GET  /health            app name and version
    GET  /about             the engine's libraries, llama.cpp and the curated models, with versions and licenses
    GET  /config/defaults   a RunConfig with every default, prompts included (inputs and output_dir empty)
    GET  /config/schema     RunConfig's JSON schema (field types, limits)
    POST /config/validate   body: a RunConfig. Errors, the PDFs found, what the output folder holds
    POST /run               body: {"config": RunConfig, "overwrite": false}. Starts a run (202)
    GET  /run               status of the run in progress, or of the last one
    POST /run/cancel        stops the run in progress; it records "cancelled" and can be resumed
    GET  /run/events        Server-Sent Events: the run's events.jsonl, then an "end" event
    GET  /hardware          GPU devices (llama-server --list-devices) and system memory

    GET  /settings          {"offline": bool, "data_dir": ...}; PUT changes "offline" (the air-gapped setting)
    GET  /runtime           the llama.cpp in use, the pinned build, the build recommended for this GPU
    POST /runtime/install   body: {"variant": optional}. Downloads and installs llama.cpp (202, a job)
    GET  /models/recommendation   the curated models with memory estimates, and the one to suggest
    GET  /models/installed  downloaded models, and disk use
    DELETE /models/installed?id=  deletes a downloaded model
    POST /models/check      body: {"model_id"}. llama-fit-params' verdict on a downloaded model
    GET  /models/search     ?q=&sort=&all=. Hub vision models with GGUF files and a projector
    GET  /models/hub        ?repo=&revision=&context=. A Hub repo's quantizations with estimates
    POST /downloads         body: {"curated": id} or {"repo", "quant", "revision"?}. Downloads a model (202, a job)
    GET  /downloads         download and install jobs with their progress
    POST /downloads/{id}/cancel   stops a job; starting it again resumes the download
    GET  /servers           the supervised llama-server, and the run settings that point at it
    POST /servers/start     body: {"model_id", "context"?}
    POST /servers/stop      stops them

Only /runtime/install, /models/search, /models/hub and /downloads reach the
network (huggingface.co and github.com), and they refuse while "offline" is on.

Errors are JSON: {"error": code, "message": text}, plus "errors" (a list of
{field, loc, message, type}) for invalid settings.
"""

import asyncio
import inspect
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import RunConfig
from . import estimate, hardware
from .about import about
from .catalog import Catalog
from .checks import check_config, friendly_errors
from .downloads import JobManager
from .hub import HubClient, HubError
from .runs import EventReader, NoActiveRun, OutputConflict, RunBusy, RunManager, RunnerCommand, default_runner_command
from .runtime import VARIANTS, Runtime, recommended_variant, variants_for_this_computer
from .security import LocalOnly
from .storage import Offline, Storage
from .supervisor import ServerSpec, Supervisor

EVENT_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
# Hugging Face repo ids ("owner/name") and commit ids, checked before they go into Hub URLs
REPO = r"^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*/[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*$"
REVISION = r"^[A-Za-z0-9._-]{1,64}$"


@dataclass
class Settings:
    token: str
    port: Optional[int] = None  # the port we listen on, checked in the Host header; None accepts any
    llama_server: Optional[str] = None  # for the hardware probe
    runner_command: RunnerCommand = default_runner_command
    cancel_grace: float = 10.0  # seconds between asking a run to stop and killing it
    poll_interval: float = 0.25  # seconds between reads of events.jsonl while streaming
    heartbeat: float = 15.0  # seconds of silence before a keep-alive comment on the stream
    data_dir: Optional[Path] = None  # None = storage.default_data_dir()


@dataclass
class Services:
    """Model and runtime management, built from Settings unless a test passes its own."""

    storage: Storage
    runtime: Runtime
    hub: HubClient
    catalog: Catalog
    jobs: JobManager
    supervisor: Supervisor

    @classmethod
    def create(cls, settings: Settings) -> "Services":
        storage = Storage(settings.data_dir)
        runtime = Runtime(storage, settings.llama_server)
        hub = HubClient(storage.cache)
        return cls(storage=storage, runtime=runtime, hub=hub, catalog=Catalog(storage, hub),
                   jobs=JobManager(), supervisor=Supervisor(storage, runtime.executable))


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        self.status, self.code, self.message, self.extra = status, code, message, extra


class StartRequest(BaseModel):
    config: Dict[str, Any]
    # Replace a run with different settings in the output folder (the CLI's --overwrite)
    overwrite: bool = False


class SettingsChange(BaseModel):
    offline: Optional[bool] = None


class InstallRuntime(BaseModel):
    variant: Optional[str] = None


class DownloadRequest(BaseModel):
    curated: Optional[str] = None
    repo: Optional[str] = Field(None, pattern=REPO)
    quant: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9_.()/ -]{1,80}$")
    revision: Optional[str] = Field(None, pattern=REVISION)
    context: Optional[int] = None


class ModelRef(BaseModel):
    model_id: str


class StartServers(BaseModel):
    model_id: str
    context: Optional[int] = None


def default_config() -> Dict[str, Any]:
    config = RunConfig(inputs=[Path("unset")], output_dir=Path("unset"))
    data = json.loads(config.model_dump_json())
    data.update({"inputs": [], "output_dir": None})
    return data


def _sse(data: Any, event: Optional[str] = None, event_id: Optional[int] = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event and EVENT_NAME.match(event):
        lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, ensure_ascii=False, default=str))
    return "\n".join(lines) + "\n\n"


def _no_telemetry() -> Dict[str, Any]:
    """FastAPI >= 0.140 has OpenTelemetry hooks; they stay off whatever the environment says."""
    if "telemetry" not in inspect.signature(FastAPI.__init__).parameters:
        return {}
    return {"telemetry": {"tracing": False, "metrics": False, "logs": False,
                          "operation_spans": False, "auto_configure": False}}


def create_app(settings: Settings, manager: Optional[RunManager] = None,
               services: Optional[Services] = None) -> FastAPI:
    services = services or Services.create(settings)
    supervisor, catalog, jobs, runtime, storage = (services.supervisor, services.catalog, services.jobs,
                                                   services.runtime, services.storage)
    manager = manager or RunManager(settings.runner_command, cancel_grace=settings.cancel_grace)
    manager.extra_env = supervisor.run_env
    probe_cache: Dict[str, Any] = {}

    def probe(refresh: bool = False) -> Dict[str, Any]:
        """The hardware probe takes about a second: estimates reuse the last one."""
        if refresh or "result" not in probe_cache:
            probe_cache["result"] = hardware.probe(runtime.executable())
        return probe_cache["result"]

    def memory() -> Dict[str, Any]:
        return estimate.budget(probe())

    def online() -> None:
        storage.require_online()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(supervisor.clean_up_leftovers)  # servers a killed sidecar left running
        yield
        # The sidecar is exiting: no run, server or download outlives it
        await asyncio.to_thread(manager.shutdown)
        await asyncio.to_thread(supervisor.shutdown)
        jobs.shutdown()

    app = FastAPI(title="table_extract sidecar", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, **_no_telemetry())
    app.state.manager = manager
    app.state.settings = settings
    app.state.services = services
    app.add_middleware(LocalOnly, token=settings.token, port=settings.port)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse({"error": exc.code, "message": exc.message, **exc.extra}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse({"error": code, "message": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(Offline)
    async def offline_error(request: Request, exc: Offline):
        return JSONResponse({"error": "offline", "message": str(exc)}, status_code=403)

    @app.exception_handler(HubError)
    async def hub_error(request: Request, exc: HubError):
        return JSONResponse({"error": "hub_error", "message": str(exc)}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def request_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid_request", "message": "The request is not valid.",
                             "errors": friendly_errors(exc.errors(), strip=("body",))}, status_code=422)

    @app.get("/health")
    def health():
        return {"app": "table_extract", "version": __version__}

    @app.get("/about")
    def about_app():
        return about(runtime.status())

    @app.get("/config/defaults")
    def config_defaults():
        return default_config()

    @app.get("/config/schema")
    def config_schema():
        return RunConfig.model_json_schema()

    @app.post("/config/validate")
    def config_validate(config: Any = Body(...)):
        return check_config(config).to_json()

    @app.post("/run", status_code=202)
    def run_start(request: StartRequest):
        if manager.busy():
            raise ApiError(409, "run_active", "A run is already in progress.")
        check = check_config(request.config)
        if not check.valid:
            raise ApiError(422, "invalid_config", "The settings have errors.", errors=check.errors)
        try:
            manager.start(check, overwrite=request.overwrite)
        except RunBusy:
            raise ApiError(409, "run_active", "A run is already in progress.")
        except OutputConflict as e:
            raise ApiError(409, "output_conflict", str(e), output=check.output)
        except OSError as e:
            raise ApiError(500, "start_failed", f"The run could not start: {e}")
        return manager.status()

    @app.get("/run")
    def run_status():
        return manager.status()

    @app.post("/run/cancel")
    def run_cancel():
        try:
            manager.cancel()
        except NoActiveRun:
            raise ApiError(409, "no_active_run", "No run is in progress.")
        return manager.status()

    @app.get("/run/events")
    async def run_events(request: Request):
        """The run's events, oldest first; each has an id to resume from (Last-Event-ID).

        Ends with an "end" event holding the final status once the run has exited.
        """
        run = manager.current
        if run is None:
            raise ApiError(404, "no_run", "No run has been started.")
        offset = run.events_offset
        last_id = request.headers.get("last-event-id", "")
        if last_id.isdigit():
            offset = max(offset, int(last_id))

        async def stream():
            reader = EventReader(run.events.path, offset)
            quiet = 0.0
            while True:
                exited = not run.running  # checked before reading, so no line written before exit is missed
                sent = False
                for position, record in reader.read():
                    yield _sse(record, record.get("event"), position)
                    sent = True
                if exited:
                    yield _sse(manager.status(), "end")
                    return
                quiet = 0.0 if sent else quiet + settings.poll_interval
                if quiet >= settings.heartbeat:
                    yield ": keep-alive\n\n"
                    quiet = 0.0
                await asyncio.sleep(settings.poll_interval)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/hardware")
    def hardware_probe():
        return probe(refresh=True)

    # Settings

    @app.get("/settings")
    def get_settings():
        return {**storage.settings(), "data_dir": str(storage.root)}

    @app.put("/settings")
    def put_settings(change: SettingsChange):
        storage.update_settings(change.model_dump(exclude_none=True))
        if storage.offline:
            for job in jobs.active():
                jobs.cancel(job.id)
        return get_settings()

    # llama.cpp

    @app.get("/runtime")
    def runtime_status():
        status = runtime.status()
        active = jobs.active("runtime")
        status["job"] = active[0].to_json() if active else None
        return status

    @app.post("/runtime/install", status_code=202)
    def runtime_install(request: InstallRuntime):
        online()
        if manager.busy() or supervisor.running():
            raise ApiError(409, "runtime_in_use", "Stop the model servers and any run before changing llama.cpp.")
        allowed = {v.id for v in variants_for_this_computer()}
        variant = VARIANTS.get(request.variant) if request.variant else recommended_variant(runtime.gpu_vendors())
        if variant is None or variant.id not in allowed:
            raise ApiError(400, "unknown_variant", f"No llama.cpp build {request.variant!r} for this computer.",
                           variants=sorted(allowed))

        def work(job, progress):
            record = runtime.install(variant, job, progress)
            probe_cache.clear()  # the GPU list comes from the new llama-server
            return record

        job = jobs.submit("runtime", f"llama.cpp {variant.id}", f"llama.cpp ({variant.label})", variant.size, work)
        return job.to_json()

    # Models

    @app.get("/models/recommendation")
    def models_recommendation():
        mem = memory()
        return {**catalog.recommendation(mem), "memory": mem}

    @app.get("/models/installed")
    def models_installed():
        return {"models": catalog.installed(), "disk": catalog.disk_usage()}

    @app.delete("/models/installed")
    def models_delete(id: str = Query(...)):
        server = supervisor.get("vision")
        if server and server.spec.model_id == id and server.state in ("starting", "ready"):
            raise ApiError(409, "model_in_use", "This model is running. Stop the model server first.")
        if any(job.target == id for job in jobs.active("model")):
            raise ApiError(409, "model_downloading", "This model is downloading. Cancel the download first.")
        if not catalog.remove(id):
            raise ApiError(404, "not_installed", "This model is not installed.")
        return models_installed()

    @app.post("/models/check")
    def models_check(request: ModelRef):
        model = catalog.get_installed(request.model_id)
        if model is None:
            raise ApiError(404, "not_installed", "This model is not installed.")
        fit_params = runtime.executable("llama-fit-params")
        if fit_params is None:
            raise ApiError(409, "no_runtime", "llama.cpp is not installed.")
        mmproj = (model.get("mmproj") or {}).get("size") or 0
        result = estimate.fit_check(fit_params, model["model_path"], model["context"], probe()["devices"],
                                    mmproj, model.get("server_flags") or [])
        if not result["ok"] and "unknown model architecture" in (result.get("error") or "").lower():
            result["verdict"] = "needs_newer_runtime"
        return result

    @app.get("/models/search")
    def models_search(q: str = "", sort: str = "trending", all: bool = False, limit: int = Query(30, ge=1, le=100)):
        online()
        return {"models": services.hub.search(q, sort, trusted_only=not all, limit=limit)}

    @app.get("/models/hub")
    def models_hub(repo: str = Query(..., pattern=REPO), revision: Optional[str] = Query(None, pattern=REVISION),
                   context: Optional[int] = Query(None, ge=2048, le=1048576)):
        online()
        try:
            return catalog.hub_options(repo, memory(), revision, context)
        except ValueError as e:
            raise ApiError(422, "unusable_model", str(e))

    # Downloads

    @app.post("/downloads", status_code=202)
    def downloads_start(request: DownloadRequest):
        online()
        try:
            spec = catalog.spec_for(request.repo, request.quant, request.revision, request.curated, request.context)
        except ValueError as e:
            raise ApiError(422, "unusable_model", str(e))
        job = jobs.submit("model", spec["id"], spec["label"], catalog.download_size(spec),
                          lambda job, progress: catalog.download(spec, job, progress))
        return job.to_json()

    @app.get("/downloads")
    def downloads_list():
        return {"jobs": jobs.list()}

    @app.post("/downloads/{job_id}/cancel")
    def downloads_cancel(job_id: str):
        job = jobs.cancel(job_id)
        if job is None:
            raise ApiError(404, "not_found", "No such download.")
        return job.to_json()

    # Model servers

    @app.get("/servers")
    def servers_status():
        return supervisor.status()

    @app.post("/servers/start", status_code=202)
    def servers_start(request: StartServers):
        if manager.busy():
            raise ApiError(409, "run_active", "A run is in progress.")
        model = catalog.get_installed(request.model_id)
        # role "embedding": bge-m3, which versions with the page pre-filter downloaded; it cannot read pages
        if model is None or model.get("role") == "embedding":
            raise ApiError(404, "not_installed", "This model is not installed.")
        try:
            supervisor.start(ServerSpec(role="vision", model_path=model["model_path"], alias=model["alias"],
                                        context=request.context or model["context"],
                                        mmproj_path=model["mmproj_path"],
                                        flags=model.get("server_flags") or [], model_id=model["id"]))
        except (RuntimeError, OSError) as e:
            supervisor.stop()
            raise ApiError(409 if isinstance(e, RuntimeError) else 500, "start_failed", str(e))
        return supervisor.status()

    @app.post("/servers/stop")
    def servers_stop():
        if manager.busy():
            raise ApiError(409, "run_active", "A run is in progress.")
        supervisor.stop()
        return supervisor.status()

    return app
