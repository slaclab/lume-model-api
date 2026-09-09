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
    scan_pv: str  # magnet the quad scan sweeps, per-model (see ModelSpec.scan_pv)


class Scalars(BaseModel):
    xrms_um: float
    yrms_um: float
    sigma_z_um: float
    norm_emit_x_um_rad: float
    norm_emit_y_um_rad: float


class SnapshotResponse(BaseModel):
    inputs: dict[str, float]


# --- The evaluate API (/api/v1/evaluate) ----------------------------------------
# ONE contract for every caller: this web UI, any future UI, and programmatic clients
# such as notebooks and emittance GUIs. There is deliberately no separate UI-private
# endpoint, because a second shape would mean every new UI reimplements the unit
# handling. Large arrays are base64-encoded little-endian float32. Units travel with
# the data in V1Distribution.units, so no client hard-codes them.


class V1Image(BaseModel):
    shape: list[int]  # [rows, cols]
    dtype: str = "float32"
    data_b64: str  # base64 little-endian float32, row-major


class V1Distribution(BaseModel):
    n: int  # particles per coordinate
    units: dict[str, str]  # coord name -> unit, e.g. {"x": "µm", "px": "eV/c"}
    coords: dict[str, str]  # coord name -> base64 little-endian float32


class V1Twiss(BaseModel):
    s: list[float]
    beta_x: list[float]
    beta_y: list[float]


class EvaluateV1Request(BaseModel):
    screen: str
    inputs: dict[str, float] = {}  # PV name -> value; overlaid on the design baseline
    include_image: bool = False
    include_distribution: bool = False
    include_twiss: bool = False
    max_particles: Optional[int] = None  # defaults to DEFAULT_MAX_PARTICLES (3000)


class EvaluateV1Response(BaseModel):
    model: str
    version: str
    screen: str
    screen_label: str  # human-readable screen name, e.g. "OTR4"
    frame_index: int
    timestamp: float
    # Why an image may be absent, e.g. "No image generated at OTR2 for this model."
    # Populated even when include_image was false, so a client can explain a null image.
    image_message: str = ""
    image_caption: str = ""  # echoed back from the request, for UI captions
    scalars: Scalars  # always returned
    image: Optional[V1Image] = None
    distribution: Optional[V1Distribution] = None
    twiss: Optional[V1Twiss] = None


# Scalar metadata surfaced by GET /config (fixed for the staged models).
SCALAR_INFO: list[ScalarInfo] = [
    ScalarInfo(id="xrms_um", label="σx", unit="µm"),
    ScalarInfo(id="yrms_um", label="σy", unit="µm"),
    ScalarInfo(id="sigma_z_um", label="σz", unit="µm"),
    ScalarInfo(id="norm_emit_x_um_rad", label="εx", unit="µm·rad"),
    ScalarInfo(id="norm_emit_y_um_rad", label="εy", unit="µm·rad"),
]
