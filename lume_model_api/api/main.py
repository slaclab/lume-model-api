"""FastAPI app for the LUME model API.

Stateless HTTP `evaluate` + a read-only SSE live view. Evaluates run on a pool of
K subprocess model instances, K in parallel with no lock, with baseline-merge in the
source making every request history-independent. Backpressure returns 503 when the
pool is saturated.

One image, `LUME_ROLE`-selected:
  - `eval` serves `/api/config` + `/api/v1/evaluate`. EPICS-free, so it scales freely.
  - `live` is the singleton EPICS reader: runs the broadcast hub and serves
    `/api/live/stream` + `/api/machine-snapshot`.
  - `all`  both, in one process (default, for dev / mock / single-pod).

Ships no UI. A UI supplied via `LUME_STATIC_DIR` or baked into `lume_model_api/static/`
is served at "/" by whichever role is running, otherwise "/" is a 404. See the mount at
the bottom of this file.
"""

from __future__ import annotations

import os

# Thread pinning for the main process (workers pin themselves in pool._init_worker).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import asyncio
import json
import math
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sse_starlette.sse import EventSourceResponse

from lume_model_api.model.config import EPICS_INPUT_PVS, MANUAL_INPUT_PVS
from lume_model_api.model.fake_epics_ioc import FAKE_INPUT_SPECS

from .pool import ModelPool, PoolFull
from .schemas import (
    ConfigResponse,
    EvaluateV1Request,
    EvaluateV1Response,
    SnapshotResponse,
)
from .source import build_config, is_mock

MODEL_NAME = os.environ.get("LUME_MODEL", "cu_hxr_staged")
POOL_WORKERS = int(os.environ.get("LUME_POOL_WORKERS", "4"))
MAX_INFLIGHT = int(os.environ.get("LUME_MAX_INFLIGHT", str(POOL_WORKERS * 4)))
# eval | live | all (default). `live`/`all` run the EPICS read loop + broadcast hub.
ROLE = os.environ.get("LUME_ROLE", "all").lower()
SERVE_LIVE = ROLE in {"all", "live"}
_SPECS_BY_PV = {spec.pv_name: spec for spec in FAKE_INPUT_SPECS}


def _mock_live_inputs(elapsed: float) -> dict[str, float]:
    """Slowly-varying inputs so the mock live view scrolls."""
    inputs: dict[str, float] = {}
    for pv in MANUAL_INPUT_PVS:
        spec = _SPECS_BY_PV.get(pv)
        if spec is None:
            continue
        span = float(spec.maximum) - float(spec.minimum)
        if span <= 0:
            inputs[pv] = float(spec.default)
        else:
            amp = 0.25 * span
            inputs[pv] = float(spec.default) + amp * math.sin(elapsed / 6.0 + spec.phase_offset_rad)
    return inputs


def _model_version(app: FastAPI) -> str:
    return f"{MODEL_NAME} (mock)" if app.state.mock else MODEL_NAME


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.mock = is_mock()
    app.state.provider = None  # lazy EPICS provider (live role only)
    app.state.pool = ModelPool(
        MODEL_NAME, mock=app.state.mock, workers=POOL_WORKERS, max_inflight=MAX_INFLIGHT
    )
    await app.state.pool.warmup()  # build all K models up front (parallel)
    app.state.hub = None
    if SERVE_LIVE:
        from .live_hub import LiveHub

        app.state.hub = LiveHub(
            app.state.pool,
            lambda elapsed: _read_live_inputs(app, elapsed),
            model=MODEL_NAME,
            version=_model_version(app),
        )
    try:
        yield
    finally:
        if app.state.hub is not None:
            await app.state.hub.shutdown()
        app.state.pool.shutdown()


app = FastAPI(title="LUME Live Stream Monitor", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _read_live_inputs(app: FastAPI, elapsed: float) -> dict[str, float]:
    if app.state.mock:
        return _mock_live_inputs(elapsed)
    if app.state.provider is None:
        from lume_model_api.model.epics_controls import EpicsInputProvider

        app.state.provider = EpicsInputProvider()
    return await asyncio.to_thread(app.state.provider.read_inputs, EPICS_INPUT_PVS)


@app.get("/api/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    return build_config(None, MODEL_NAME, app.state.mock)


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus scrape endpoint. KEDA autoscales the eval pool on lume_pool_inflight."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post(
    "/api/v1/evaluate",
    response_model=EvaluateV1Response,
    tags=["model-api"],
    summary="Run the model on a set of inputs and return beam output",
)
async def evaluate_v1(req: EvaluateV1Request):
    """Stateless model evaluation. The one evaluate endpoint for every caller.

    Used by this app's own web UI, by any other UI, and by programmatic clients such as
    notebooks and emittance GUIs. There is deliberately no separate UI-private endpoint.

    `inputs` is a map of PV name -> engineering-unit control value, overlaid on the
    model's design baseline, so send only the knobs you want to change (`{}` is the
    design machine). `GET /api/config` lists the writable inputs with their ranges and
    defaults.

    Scalars are always returned. Set `include_image`, `include_distribution` and
    `include_twiss` for the heavier outputs, and `max_particles` to subsample the
    distribution (it defaults to 3000, never the full beam). Large arrays are
    base64-encoded little-endian float32, so decode with e.g.
    `numpy.frombuffer(base64.b64decode(s), dtype='<f4')`. The image is row-major,
    reshaped to `image.shape`.

    Particle positions are in µm and momenta in eV/c, matching the µm-based scalars.
    Every response states its own units in `distribution.units`, so do not hard-code
    them.
    """
    version = _model_version(app)
    try:
        wire = await app.state.pool.evaluate(
            req.screen,
            req.inputs,
            kind="interactive",
            include_image=req.include_image,
            include_distribution=req.include_distribution,
            include_twiss=req.include_twiss,
            max_particles=req.max_particles,
        )
    except PoolFull as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown screen: {exc}") from exc
    return {**wire, "model": MODEL_NAME, "version": version}


@app.get("/api/machine-snapshot", response_model=SnapshotResponse)
async def machine_snapshot() -> SnapshotResponse:
    if not SERVE_LIVE:
        raise HTTPException(status_code=503, detail="machine-snapshot not served by this instance")
    if app.state.mock:
        inputs = {
            pv: float(_SPECS_BY_PV[pv].default)
            for pv in MANUAL_INPUT_PVS
            if pv in _SPECS_BY_PV
        }
        return SnapshotResponse(inputs=inputs)

    if app.state.provider is None:
        from lume_model_api.model.epics_controls import EpicsInputProvider

        app.state.provider = EpicsInputProvider()
    values = await asyncio.to_thread(app.state.provider.read_inputs, EPICS_INPUT_PVS)
    inputs = {pv: float(values[pv]) for pv in MANUAL_INPUT_PVS if pv in values}
    return SnapshotResponse(inputs=inputs)


@app.get("/api/live/stream")
async def live_stream(screen: str = "OTR4"):
    if app.state.hub is None:
        raise HTTPException(status_code=503, detail="live view not served by this instance")
    q = app.state.hub.subscribe(screen)

    async def event_generator():
        # sse-starlette cancels this generator on client disconnect -> finally
        # unsubscribes, and the screen loop stops once its last viewer leaves.
        try:
            while True:
                item = await q.get()
                event = "frame" if item["event"] == "frame" else "error"
                yield {"event": event, "data": json.dumps(item["data"])}
        finally:
            app.state.hub.unsubscribe(screen, q)

    return EventSourceResponse(event_generator())


# Optionally serve a single-page app at "/". This service ships no UI of its own: the image
# is API-only and this mount stays inactive unless someone supplies a build. Two ways to do
# that, both used by UI repos rather than by this one:
#   - bake it in, by copying a dist/ into lume_model_api/static/ in a derived image
#   - mount it at runtime, by pointing LUME_STATIC_DIR at any directory
# When neither exists the app is a pure API and "/" returns 404, which is why the k8s probes
# target /api/config instead.
_STATIC = Path(
    os.environ.get("LUME_STATIC_DIR") or Path(__file__).resolve().parent.parent / "static"
)
if _STATIC.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
