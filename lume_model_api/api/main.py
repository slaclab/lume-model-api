"""FastAPI backend for the LUME live-stream monitor (M1, single-user).

Replaces the marimo runtime: a stateless HTTP `evaluate` + a read-only SSE live
view. One model instance guarded by one asyncio lock (M1 serializes; M2 adds a pool).
"""

from __future__ import annotations

# Thread pinning + OpenMP workaround must be set before torch / Bmad import.
import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "TORCH_NUM_THREADS"):
    os.environ.setdefault(_var, "2")

import asyncio
import json
import math
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from lume_visualizations.config import EPICS_INPUT_PVS, MANUAL_INPUT_PVS
from lume_visualizations.fake_epics_ioc import FAKE_INPUT_SPECS

from . import serialize
from .schemas import (
    ConfigResponse,
    EvaluateRequest,
    FrameResponse,
    Scalars,
    SnapshotResponse,
)
from .source import build_config, get_source, is_mock

MODEL_NAME = os.environ.get("LUME_MODEL", "cu_hxr_staged")
_SPECS_BY_PV = {spec.pv_name: spec for spec in FAKE_INPUT_SPECS}


def _frame_to_response(frame) -> FrameResponse:
    image_b64, image_shape = serialize.encode_image(frame.image)
    scatter_x = None if frame.beam_x_um is None else serialize.encode_f32(frame.beam_x_um)
    scatter_px = None if frame.beam_px_evc is None else serialize.encode_f32(frame.beam_px_evc)
    return FrameResponse(
        screen_key=frame.screen_key,
        screen_label=frame.screen_label,
        image_b64=image_b64,
        image_shape=image_shape,
        image_message=frame.image_message,
        image_caption=frame.image_caption,
        scalars=Scalars(
            xrms_um=frame.xrms_um,
            yrms_um=frame.yrms_um,
            sigma_z_um=frame.sigma_z_um,
            norm_emit_x_um_rad=frame.norm_emit_x_um_rad,
            norm_emit_y_um_rad=frame.norm_emit_y_um_rad,
        ),
        scatter_x_b64=scatter_x,
        scatter_px_b64=scatter_px,
        twiss_s=serialize.to_list(frame.twiss_s),
        twiss_a_beta=serialize.to_list(frame.twiss_a_beta),
        twiss_b_beta=serialize.to_list(frame.twiss_b_beta),
        frame_index=frame.frame_index,
        title_suffix=frame.title_suffix,
        timestamp=frame.timestamp,
    )


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
    app.state.lock = asyncio.Lock()
    app.state.source = get_source(MODEL_NAME, mock=app.state.mock)
    app.state.provider = None  # lazy EPICS provider (real live view only)
    yield


app = FastAPI(title="LUME Live Stream Monitor", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _snapshot(app: FastAPI, screen: str, inputs: dict[str, float], **kw):
    async with app.state.lock:
        return await asyncio.to_thread(
            app.state.source.snapshot, screen, control_updates=inputs, **kw
        )


@app.get("/api/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    return build_config(app.state.source, MODEL_NAME, app.state.mock)


@app.post("/api/evaluate", response_model=FrameResponse)
async def evaluate(req: EvaluateRequest) -> FrameResponse:
    try:
        frame = await _snapshot(
            app, req.screen, req.inputs, x_axis_value=float(time.time()),
            title_suffix="manual",
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown screen: {exc}") from exc
    return _frame_to_response(frame)


@app.get("/api/machine-snapshot", response_model=SnapshotResponse)
async def machine_snapshot() -> SnapshotResponse:
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
async def live_stream(request: Request, screen: str = "OTR4", period: float = 1.0):
    period = max(0.1, float(period))

    async def event_generator():
        started = time.monotonic()
        index = 0
        while True:
            if await request.is_disconnected():
                break
            elapsed = time.monotonic() - started
            try:
                if app.state.mock:
                    inputs = _mock_live_inputs(elapsed)
                else:
                    if app.state.provider is None:
                        from lume_visualizations.epics_controls import EpicsInputProvider

                        app.state.provider = EpicsInputProvider()
                    inputs = await asyncio.to_thread(
                        app.state.provider.read_inputs, EPICS_INPUT_PVS
                    )
                frame = await _snapshot(
                    app, screen, inputs, frame_index=index, title_suffix="live"
                )
                payload = _frame_to_response(frame).model_dump()
                yield {"event": "frame", "data": json.dumps(payload)}
                index += 1
            except Exception as exc:  # keep the stream alive on transient errors
                yield {"event": "error", "data": json.dumps({"message": str(exc)})}
            await asyncio.sleep(period)

    return EventSourceResponse(event_generator())


# Serve the built frontend (production image). In dev, Vite serves the UI and
# proxies /api here, so dist/ need not exist.
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_STATIC = Path(__file__).resolve().parent / "static"
for _candidate in (_STATIC, _DIST):
    if _candidate.is_dir():
        app.mount("/", StaticFiles(directory=str(_candidate), html=True), name="static")
        break
