"""FastAPI app that hosts one or more LUMEModels, addressed by name in the URL.

`LUME_MODELS` is the only model-specific setting. Everything a client learns about a model
is derived from the instance in a pool worker and shipped back as a plain dataclass, so this
process never imports torch, Bmad or a lattice.

Every route carries the model's URL name: `GET /api/v1/models` lists what this process hosts
and is what a dropdown reads, and the rest live under `/api/v1/models/{name}/`. There is
deliberately no unprefixed default-model route, because an implicit default is how a client
ends up driving a model it did not choose.

Stateless HTTP `evaluate` + a read-only SSE live view. Evaluates run on a per-model pool of K
subprocess model instances, K in parallel with no lock, with baseline-merge making every
request history-independent. Backpressure returns 503 when a pool is saturated.

`GET /healthz` is what the k8s probes target, not `GET /api/v1/models`. It fails once any pool
has lost a worker, which is unrecoverable in-process, so the pod restarts instead of serving
503s forever. It is deliberately outside the `/api/v1` contract and out of `openapi.json`.

One image, `LUME_ROLE`-selected:
  - `eval` serves the config and evaluate routes. EPICS-free, so it scales freely.
  - `live` is the singleton EPICS reader: runs a broadcast hub per model and serves the
    `live/stream` + `machine-snapshot` routes.
  - `all`  both, in one process (default, for dev and single-pod deploys).

Ships no UI. A UI supplied via `LUME_STATIC_DIR` or baked into `lume_model_api/static/` is
served at "/" by whichever role is running, otherwise "/" is a 404. See the mount at the
bottom of this file.
"""

from __future__ import annotations

import os

# Thread pinning for the main process (workers pin themselves in pool._init_worker).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sse_starlette.sse import EventSourceResponse

from lume_model_api.model import live_inputs as live_inputs_module
from lume_model_api.model import loader
from lume_model_api.model.evaluate import InvalidInput, ModelUnusable, UnknownVariable

# Aliased because the route function below is also called `metrics`, and the scrape endpoint
# has to keep that name to keep its path.
from . import metrics as metrics_module
from .pool import ModelPool, PoolDead, PoolFull
from .schemas import (
    SOURCE_BASELINE,
    SOURCE_REQUEST,
    ConfigResponse,
    EvaluateV1Request,
    EvaluateV1Response,
    ModelListEntry,
    SnapshotResponse,
    live_sources,
)
from .serialize import json_safe

# `live` and `all` run the input read loops and the broadcast hubs, `eval` does not.
ROLES = ("eval", "live", "all")
DEFAULT_ROLE = "all"


def _serve_live_from_env() -> bool:
    """Whether this process runs the live machinery, from `LUME_ROLE`.

    Read inside the lifespan rather than at import, so a test can set the environment before
    its TestClient starts and so a bad value fails startup instead of silently degrading. A
    typo used to fall through to the eval behaviour, and the likeliest form of that is not a
    typo at all: a k8s env var declared with an empty value makes `os.environ.get` return `""`,
    not the default.
    """
    role = (os.environ.get("LUME_ROLE") or DEFAULT_ROLE).strip().lower()
    if role not in ROLES:
        raise ValueError(f"Unknown LUME_ROLE {role!r}. Use one of: {', '.join(ROLES)}.")
    return role in {"live", "all"}


# `Retry-After` on both 503 paths, in seconds, because a client that retries immediately against
# a pod that is restarting only tightens the loop. The two numbers differ by what the client is
# waiting for: saturation clears in about one evaluate, whereas an unrecoverable pool clears only
# when the probes have restarted the pod and its workers have rebuilt their models, which takes 25
# to 30 seconds per model.
RETRY_AFTER_SATURATED = "2"
RETRY_AFTER_RESTARTING = "30"


def _discover(name: str) -> str:
    return (
        f"GET /api/v1/models/{name}/config lists every input, output and screen this model "
        "publishes."
    )


@dataclass
class HostedModel:
    """One model this process serves: its settings, its pool, and its live machinery."""

    setting: loader.ModelSetting
    pool: ModelPool
    info: Any = None  # ModelInfo, populated by pool.warmup()
    hub: Any = None  # LiveHub, live roles only
    provider: Any = None  # built lazily on the first live read

    @property
    def version(self) -> str:
        # The demo is flagged so a client can tell a demo deployment from a real one even when
        # it was selected by full factory path rather than by the `demo` shortcut.
        if not self.setting.is_demo:
            return self.setting.name
        return "demo" if self.setting.name == loader.DEFAULT_MODEL else f"{self.setting.name} (demo)"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn configures only its own loggers, so the loader's "hosting ..." lines would fall
    # through to the handlerless root logger and never reach a pod log. basicConfig is a no-op
    # when handlers already exist, so an embedding application keeps its own configuration.
    logging.basicConfig(level=logging.INFO)
    # Read here rather than at import: a bad setting then fails startup with one clear message
    # instead of K worker tracebacks, and a test can set the environment before the TestClient
    # enters this lifespan even though other test modules import this file at collection time.
    settings = loader.models_from_env()
    serve_live = _serve_live_from_env()
    if serve_live:
        # Same reason the role is validated here: both live settings are otherwise read when
        # the first provider is built, so a bad value would surface once per frame rather than
        # once at startup.
        live_inputs_module.check_env()
    hosted: dict[str, HostedModel] = {}
    app.state.models = hosted
    try:
        for setting in settings:
            hosted[setting.name] = HostedModel(
                setting=setting,
                pool=ModelPool(
                    setting.factory_path,
                    setting.kwargs,
                    workers=setting.workers,
                    max_inflight=setting.max_inflight,
                    model_name=setting.name,
                ),
            )
        for entry in hosted.values():
            # Sequentially, one model at a time. Warming M pools at once would build the sum of
            # their K model instances simultaneously, which is neither what the pod memory
            # limits nor what the startup probe timeout were sized for.
            entry.info = await entry.pool.warmup()
            if serve_live:
                from .live_hub import LiveHub

                entry.hub = LiveHub(
                    entry.pool,
                    # Bound by default argument: a bare closure over the loop variable would
                    # leave every hub reading the last model's inputs.
                    lambda model=entry: _read_live_inputs(model),
                    model=entry.setting.name,
                    version=entry.version,
                )
    except BaseException:
        # A model that fails to build must not leave the pools built before it holding spawned
        # workers for a process that is about to exit.
        for entry in hosted.values():
            entry.pool.shutdown()
        raise
    try:
        yield
    finally:
        for entry in hosted.values():
            if entry.hub is not None:
                await entry.hub.shutdown()
            entry.pool.shutdown()


# Behind an ingress prefix, Swagger at <prefix>/docs would otherwise fetch /openapi.json from
# the host root and break, because the page has no way to guess the prefix. The ingress rewrite
# strips the prefix before the app sees it, so the app needs it only for generated links.
app = FastAPI(
    title="LUME model API",
    lifespan=lifespan,
    root_path=os.environ.get("LUME_ROOT_PATH", ""),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 with any non-finite offending value rendered as null.

    FastAPI's own handler echoes the value it rejected back in the error detail and serializes
    the response with `json.dumps(allow_nan=False)`. `EvaluateV1Request.inputs` now refuses
    non-finite floats, so the value that caused the 422 is precisely one that handler cannot
    encode: it raises inside the exception handler, and the rejection would reach the caller as
    an unhandled 500 with no hint of which field was wrong. Reusing `json_safe` keeps the error
    body consistent with the response path, which already renders a non-finite float as `null`.

    Registered on the app rather than fixed per field, because the same trap applies to any
    future numeric field that rejects NaN.
    """
    return JSONResponse(
        status_code=422, content={"detail": json_safe(jsonable_encoder(exc.errors()))}
    )


def _hosted(name: str) -> HostedModel:
    """The named model, or a 404 that says what this process does host."""
    models: dict[str, HostedModel] = getattr(app.state, "models", {})
    entry = models.get(name)
    if entry is None:
        known = ", ".join(sorted(models)) or "none"
        raise HTTPException(
            status_code=404,
            detail=f"Unknown model {name!r}. This process hosts: {known}. See GET /api/v1/models.",
        )
    return entry


def _provider(hosted: HostedModel):
    # One provider per model, built from that model's own inputs. Two models sharing PVs
    # therefore hold duplicate epics.PV objects and read them once each per frame, which is
    # accepted: deduplicating would mean a pod-wide PV cache keyed by name.
    if hosted.provider is None:
        hosted.provider = live_inputs_module.get_input_provider(
            hosted.info,
            source=live_inputs_module.live_source_from_env(),
            restrict_to=live_inputs_module.live_input_ids_from_env(),
        )
    return hosted.provider


async def _read_live_inputs(hosted: HostedModel) -> tuple[dict[str, float], dict[str, float]]:
    """Live input values, overlaid on the baseline so an unreadable id keeps its default.

    Returns `(merged, live)`: the values to evaluate at, and the subset the provider actually
    read off the machine. Both, because the merge is lossy in exactly the way that matters. An
    id the provider could not read keeps its design value in `merged`, indistinguishable there
    from a value that came off the machine, so a caller that wants to tell an operator which is
    which needs `live` to compare against. That is what fills `input_sources` on a streamed frame
    and `sources` on a snapshot.

    The overlay is also why the gauges below matter, and they stay: they are the aggregate an
    alert fires on, whereas the provenance maps are per response. The provider cannot publish
    them itself, because it lives in the model layer, which never imports the api layer, so the
    api-layer caller does it from what the provider returned.
    """
    provider = _provider(hosted)
    name = hosted.setting.name
    metrics_module.LIVE_INPUTS_TOTAL.labels(model=name).set(len(provider.names))
    try:
        # Channel access blocks, so keep it off the event loop that is serving the SSE clients.
        # Every producer loop for this model, plus machine-snapshot, shares the one provider and
        # lands here on different threads, which is why InputProvider documents read_inputs as
        # safe to call concurrently.
        values = await asyncio.to_thread(provider.read_inputs)
    except Exception:
        # A raising read means nothing was read, and an alert on the gauge must not go quiet
        # just because the failure got worse than partial.
        metrics_module.LIVE_INPUTS_READABLE.labels(model=name).set(0)
        raise
    metrics_module.LIVE_INPUTS_READABLE.labels(model=name).set(len(values))
    return {**hosted.info.baseline, **values}, values


def _resolve_outputs(info, outputs: list[str], screen: str | None) -> list[str]:
    """Expand a request's `screen` shortcut and reject a request that asks for nothing."""
    ids = list(outputs or [])
    # info.model is the URL name, set when the worker described the model, so an error message
    # can point at the config route of the model the caller actually addressed.
    discover = _discover(info.model)
    if screen:
        found = info.screen(screen)
        if found is None:
            known = ", ".join(item.key for item in info.screens) or "none"
            raise HTTPException(
                status_code=400,
                detail=f"Unknown screen {screen!r}. This model has: {known}.",
            )
        ids.append(found.particles)
        if found.image:
            ids.append(found.image)
    if not ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "Nothing requested: send `outputs` (a list of output ids) and/or `screen`. "
                + discover
            ),
        )
    unknown = sorted({name for name in ids if name not in info.output_ids})
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown output id(s): {', '.join(unknown)}. {discover}",
        )
    # Dedupe while preserving order, so `screen` plus an explicit id is not evaluated twice.
    return list(dict.fromkeys(ids))


@app.get(
    "/api/v1/models",
    response_model=list[ModelListEntry],
    tags=["model-api"],
    summary="List the models this process hosts",
)
async def list_models() -> list[dict]:
    """Every model served here, sorted by name. Read this instead of hard-coding names.

    `name` is the URL segment for that model's other routes, so a dropdown stores `name` and
    interpolates it. `version` marks a demo deployment.

    Client discovery, deliberately not the health check: a pod hosting several models keeps
    listing the ones that still work even when another model's pool has died. `GET /healthz` is
    what the k8s probes target.
    """
    models: dict[str, HostedModel] = getattr(app.state, "models", {})
    # `entry.info` is read straight through with no `if entry.info` guard. The lifespan warms
    # every pool and assigns `info` before it yields, so a None here would be a real bug in
    # startup ordering, and a guard would answer with an empty description instead of surfacing it.
    return [
        {
            "name": name,
            "description": entry.info.description,
            "version": entry.version,
        }
        for name, entry in sorted(models.items())
    ]


@app.get(
    "/api/v1/models/{name}/config",
    response_model=ConfigResponse,
    tags=["model-api"],
    summary="Everything a client needs to drive one model",
)
async def get_config(name: str) -> dict:
    """Everything a client needs to drive this model: inputs, outputs and screens.

    Derived from the model instance itself, so it is accurate for whatever `LUME_MODELS`
    selected. `range_source` says whether an input's min/max came from the model or was
    derived from its default, and `constant` marks an input the model pins to one value.
    """
    hosted = _hosted(name)
    info = hosted.info
    return {
        "model": name,
        "version": hosted.version,
        "description": info.description,
        "inputs": [vars(item) for item in info.inputs],
        "outputs": [vars(item) for item in info.outputs],
        "screens": [vars(item) for item in info.screens],
    }


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    """Liveness and readiness for the k8s probes. Not part of the /api/v1 contract.

    Separate from `GET /api/v1/models` because the two answer different questions. The model
    list is client-facing discovery, and on a pod hosting several models it must keep answering
    for the models that still work even if one pool has died. A probe wants the opposite: any
    dead pool means this pod should be taken out of the Service and restarted, because a pool
    whose worker died can never serve another evaluate (see pool.PoolDead) and nothing short of
    a restart recovers it.

    `include_in_schema=False` keeps it out of `openapi.json`, so it is not a contract consumers
    can pin, and adding it does not require regenerating the committed schema.
    """
    models: dict[str, HostedModel] = getattr(app.state, "models", None) or {}
    if not models:
        # Either the lifespan has not finished or it never ran. Reporting healthy here would
        # let a pod pass its startupProbe before any model was built.
        raise HTTPException(status_code=503, detail="no model is hosted yet")
    dead = sorted(
        f"{name} ({entry.pool.dead_reason})" if entry.pool.dead_reason else name
        for name, entry in models.items()
        if entry.pool.dead
    )
    if dead:
        # The reason is in the detail because the three causes call for different follow-up:
        # `worker_lost` points at pod memory, `unusable` at whatever request broke the model,
        # and `timeout` at LUME_EVALUATE_TIMEOUT_S. The restart is the same either way.
        raise HTTPException(
            status_code=503,
            detail=f"model pool(s) cannot recover and need a pod restart: {', '.join(dead)}",
        )
    return {"status": "ok", "models": sorted(models)}


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus scrape endpoint. KEDA autoscales the eval pool on lume_pool_inflight.

    Every series carries a `model` label, one per hosted model, so an aggregate has to sum by
    pod before averaging. See deploy/kubernetes/keda-scaledobject.yaml.
    """
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post(
    "/api/v1/models/{name}/evaluate",
    response_model=EvaluateV1Response,
    tags=["model-api"],
    summary="Run one model on a set of inputs and return the requested outputs",
)
async def evaluate_v1(name: str, req: EvaluateV1Request):
    """Stateless model evaluation. The one evaluate endpoint for every caller.

    `inputs` maps input id to control value, overlaid on the model's baseline, so send only
    the knobs you want to change (`{}` is the design machine). The effective post-merge
    values come back in the response's `inputs`.

    Ask for outputs by id in `outputs`, or pass `screen` to get that screen's particle
    distribution plus its image when it has one. Both may be combined. Every requested id is
    present in `outputs`, keyed by id, and each value declares its own `kind` and unit.

    Large arrays are base64-encoded little-endian float32, so decode with e.g.
    `numpy.frombuffer(base64.b64decode(s), dtype='<f4')` and reshape to `shape`. Particle
    coordinates are subsampled to `max_particles` (default 3000, never the full beam).

    Values are in the model's own units, declared in the payload. Nothing is converted
    server-side, so do not hard-code units.
    """
    hosted = _hosted(name)
    outputs = _resolve_outputs(hosted.info, req.outputs, req.screen)
    try:
        wire = await hosted.pool.evaluate(
            req.inputs,
            outputs,
            kind="interactive",
            max_particles=req.max_particles,
            smooth_sigma_px=req.smooth_images_sigma_px,
        )
    except PoolDead as exc:
        # 503 like saturation, but retrying will not help until the pod restarts. /healthz is
        # already reporting unhealthy, so the probes are on their way to doing that, which is
        # what the longer `Retry-After` reflects.
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": RETRY_AFTER_RESTARTING},
        ) from exc
    except PoolFull as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": RETRY_AFTER_SATURATED},
        ) from exc
    except ModelUnusable as exc:
        # The model broke itself rather than rejecting the request, so this is a server fault
        # and never a 400. Kept out of the arm below deliberately: reporting it as the caller's
        # fault is the defect in docs/POISONED_WORKER.md, and it survived because
        # `InvalidInput` and a numpy `ValueError` are indistinguishable by type.
        #
        # A backstop in practice. The worker normally converts this to `ModelPoisoned` and the
        # pool to `PoolDead`, and what reaches here is the case where in-process recovery
        # worked, so the worker is serving again and a retry is worth the client's time.
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": RETRY_AFTER_SATURATED},
        ) from exc
    except (UnknownVariable, InvalidInput) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Attached by the sender, not by the serializer, because only the sender knows where the
    # values came from: this route never reads the machine, so an id is either one the caller
    # sent or one the baseline merge filled in. The live producer attaches the same key from its
    # own knowledge (see live_hub._run), and both must, since the SSE path has no response_model
    # to fill a gap. See SENDER_ADDED in tests/test_wire_shape.py.
    sources = {
        input_id: SOURCE_REQUEST if input_id in req.inputs else SOURCE_BASELINE
        for input_id in wire["inputs"]
    }
    return {**wire, "model": name, "version": hosted.version, "input_sources": sources}


@app.get(
    "/api/v1/models/{name}/machine-snapshot",
    response_model=SnapshotResponse,
    tags=["model-api"],
    summary="The live input values for one model, read-only",
)
async def machine_snapshot(name: str) -> dict:
    """The live input values, read-only. Ids with no live value report their baseline.

    `sources` says which is which, per id: "live" for a value read off the machine on this call,
    "baseline" for one the merge filled in from the model's design value because the PV could not
    be read. Without it the two are the same number on the wire.
    """
    hosted = _hosted(name)
    # `hub is None` is the single "this process does not do live" signal, shared with
    # live/stream. Two separate checks meant a role change could leave the two routes
    # disagreeing about whether this instance serves live data.
    if hosted.hub is None:
        raise HTTPException(status_code=503, detail="machine-snapshot not served by this instance")
    values, live = await _read_live_inputs(hosted)
    return {
        "inputs": {key: float(value) for key, value in values.items()},
        "sources": live_sources(values, live),
    }


@app.get("/api/v1/models/{name}/live/stream", tags=["model-api"])
async def live_stream(
    name: str,
    screen: str | None = Query(default=None),
    outputs: str | None = Query(default=None, description="Comma-separated output ids"),
):
    """SSE `frame` events with the same body as this model's evaluate route.

    Requires `screen` or `outputs` (or both). Loops are shared by output set within a model, so
    many viewers of the same set cost one evaluate loop. Errors arrive as an `error` event and
    the loop keeps running.
    """
    hosted = _hosted(name)
    if hosted.hub is None:
        raise HTTPException(status_code=503, detail="live view not served by this instance")
    # Imported here, below the role check, for the same reason `lifespan` imports `LiveHub`
    # lazily: the eval role must never pull this module in.
    from .live_hub import TooManyStreams

    requested = [item for item in (outputs or "").split(",") if item.strip()]
    # Every rejection has to happen out here, before EventSourceResponse is returned. Once the
    # response has started there is no status code left to send, so a bad `screen` would become a
    # 200 carrying an error event, which a client cannot tell from a machine fault.
    resolved = _resolve_outputs(hosted.info, [item.strip() for item in requested], screen)
    try:
        hosted.hub.check_capacity(resolved)
    except TooManyStreams as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    async def event_generator():
        # Subscribing in here rather than above is what keeps the subscriber from leaking.
        # sse-starlette can be cancelled before it ever calls `__anext__`, and a generator that
        # never started does not run its `finally`, so a subscribe done outside would leave a
        # queue in the set and its producer loop evaluating forever with no viewer. Acquire and
        # release therefore live in the same try/finally.
        try:
            key, q = hosted.hub.subscribe(resolved)
        except TooManyStreams as exc:
            # `check_capacity` above is what normally makes this a 503. Reaching here means
            # another client took the last slot in between, which cannot be a status code any
            # more, so the client is told on the stream instead.
            yield {"event": "error", "data": json.dumps({"message": str(exc)})}
            return
        # sse-starlette cancels this generator on client disconnect -> finally unsubscribes,
        # and the loop stops once its last viewer leaves.
        try:
            while True:
                item = await q.get()
                event = "frame" if item["event"] == "frame" else "error"
                yield {"event": event, "data": json.dumps(item["data"])}
        finally:
            hosted.hub.unsubscribe(key, q)

    return EventSourceResponse(event_generator())


# Optionally serve a single-page app at "/". This service ships no UI of its own: the image
# is API-only and this mount stays inactive unless someone supplies a build. Two ways to do
# that, both used by UI repos rather than by this one:
#   - bake it in, by copying a dist/ into lume_model_api/static/ in a derived image
#   - mount it at runtime, by pointing LUME_STATIC_DIR at any directory
# When neither exists the app is a pure API and "/" returns 404, which is why the k8s probes
# target /healthz instead.
_STATIC = Path(
    os.environ.get("LUME_STATIC_DIR") or Path(__file__).resolve().parent.parent / "static"
)
if _STATIC.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
