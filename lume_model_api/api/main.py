"""FastAPI backend for the LUME live-stream monitor.

Stateless HTTP `evaluate` + a read-only SSE live view. Evaluates run on a pool of
K subprocess model instances — K in parallel, no lock — with baseline-merge in the
source making every request history-independent. Backpressure returns 503 when the
pool is saturated.

One image, `LUME_ROLE`-selected (N1):
  - `eval` — serves the SPA + `/api/config` + `/api/evaluate`; EPICS-free; scalable.
  - `live` — the singleton EPICS reader: runs the broadcast hub and serves
    `/api/live/stream` + `/api/machine-snapshot`.
  - `all`  — both, in one process (default; dev / mock / single-pod).
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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from lume_visualizations.config import EPICS_INPUT_PVS, MANUAL_INPUT_PVS
from lume_visualizations.fake_epics_ioc import FAKE_INPUT_SPECS

from .pool import ModelPool, PoolFull
from .schemas import (
    ConfigResponse,
    EvaluateRequest,
    EvaluateV1Request,
    EvaluateV1Response,
    FrameResponse,
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

        app.state.hub = LiveHub(app.state.pool, lambda elapsed: _read_live_inputs(app, elapsed))
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
        from lume_visualizations.epics_controls import EpicsInputProvider

        app.state.provider = EpicsInputProvider()
    return await asyncio.to_thread(app.state.provider.read_inputs, EPICS_INPUT_PVS)


@app.get("/api/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    return build_config(None, MODEL_NAME, app.state.mock)


@app.post("/api/evaluate", response_model=FrameResponse)
async def evaluate(req: EvaluateRequest):
    try:
        return await app.state.pool.evaluate(req.screen, req.inputs, title_suffix="manual")
    except PoolFull as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown screen: {exc}") from exc


@app.post(
    "/api/v1/evaluate",
    response_model=EvaluateV1Response,
    tags=["model-api"],
    summary="Run the model on a set of inputs and return beam output",
)
async def evaluate_v1(req: EvaluateV1Request):
    """Stateless model evaluation for programmatic clients (notebooks, GUIs).

    `inputs` is a map of PV name -> engineering-unit control value, overlaid on the
    model's design baseline — send only the knobs you want to change (`{}` = design
    machine). `GET /api/config` lists the writable inputs with their ranges/defaults.

    Scalars are always returned. Set `include_image` / `include_distribution` /
    `include_twiss` for the heavier outputs; `max_particles` subsamples the
    distribution. Large arrays are base64-encoded little-endian float32 — decode with
    e.g. `numpy.frombuffer(base64.b64decode(s), dtype='<f4')` (image is row-major,
    reshaped to `image.shape`).
    """
    version = f"{MODEL_NAME} (mock)" if app.state.mock else MODEL_NAME
    try:
        wire = await app.state.pool.evaluate_v1(
            req.screen,
            req.inputs,
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
        from lume_visualizations.epics_controls import EpicsInputProvider

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


# Serve the built frontend (production image). In dev, Vite serves the UI and
# proxies /api here, so dist/ need not exist.
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_STATIC = Path(__file__).resolve().parent / "static"
for _candidate in (_STATIC, _DIST):
    if _candidate.is_dir():
        app.mount("/", StaticFiles(directory=str(_candidate), html=True), name="static")
        break
