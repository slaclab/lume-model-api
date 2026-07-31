"""Pydantic request/response schemas for the webapp backend."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ScreenInfo(BaseModel):
    key: str
    label: str
    has_image: bool


class InputInfo(BaseModel):
    id: str
    label: str
    min: float
    max: float
    default: float
    unit: str = ""


class ScalarInfo(BaseModel):
    id: str
    label: str
    unit: str


class ConfigResponse(BaseModel):
    model: str
    version: str
    screens: list[ScreenInfo]
    inputs: list[InputInfo]
    scalars: list[ScalarInfo]


class EvaluateRequest(BaseModel):
    screen: str
    inputs: dict[str, float] = {}


class Scalars(BaseModel):
    xrms_um: float
    yrms_um: float
    sigma_z_um: float
    norm_emit_x_um_rad: float
    norm_emit_y_um_rad: float


class FrameResponse(BaseModel):
    screen_key: str
    screen_label: str
    image_b64: Optional[str] = None
    image_shape: Optional[list[int]] = None
    image_message: str = ""
    image_caption: str = ""
    scalars: Scalars
    scatter_x_b64: Optional[str] = None
    scatter_px_b64: Optional[str] = None
    twiss_s: Optional[list[float]] = None
    twiss_a_beta: Optional[list[float]] = None
    twiss_b_beta: Optional[list[float]] = None
    frame_index: int = 0
    title_suffix: str = ""
    timestamp: float = 0.0


class SnapshotResponse(BaseModel):
    inputs: dict[str, float]


# Scalar metadata surfaced by GET /config (fixed for the staged models).
SCALAR_INFO: list[ScalarInfo] = [
    ScalarInfo(id="xrms_um", label="σx", unit="µm"),
    ScalarInfo(id="yrms_um", label="σy", unit="µm"),
    ScalarInfo(id="sigma_z_um", label="σz", unit="µm"),
    ScalarInfo(id="norm_emit_x_um_rad", label="εx", unit="µm·rad"),
    ScalarInfo(id="norm_emit_y_um_rad", label="εy", unit="µm·rad"),
]
