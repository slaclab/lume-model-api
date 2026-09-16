"""Pydantic request/response schemas for the API.

The output payload is variable-generic: a response is a map of output id to an `Output`,
discriminated on `kind`. That is what lets one contract serve any model. A caller reads the
kinds it cares about from `GET /api/v1/models/{name}/config` before it ever calls evaluate.

Units travel with every value, in the model's own units. Nothing here converts, because a
generic host cannot tell a beam image from a lattice function.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field

# --- GET /api/v1/models ----------------------------------------------------------


class ModelListEntry(BaseModel):
    """One model this process hosts. A UI reads this list rather than hard-coding names."""

    # The URL segment this model answers on: /api/v1/models/{name}/config and friends.
    name: str
    description: str = ""
    # "demo" or "<name> (demo)" marks a demo deployment, so a client can tell one from a real
    # machine model even when the demo was selected by full factory path.
    version: str


# --- GET /api/v1/models/{name}/config ---------------------------------------------


class InputInfo(BaseModel):
    """A writable scalar knob."""

    id: str
    unit: str = ""
    default: float
    min: float
    max: float
    # "model" when the model declared a value_range, "derived" when it was inferred from
    # the default (see model/introspect.py). A UI may want to mark a derived range as a
    # suggestion rather than a limit.
    range_source: str
    # The model declares min == max, so there is nothing to drive. Excluded from the
    # baseline and from the live input reads.
    constant: bool = False


class OutputInfo(BaseModel):
    id: str
    kind: Literal["scalar", "array", "particles", "value"]
    unit: str = ""
    shape: Optional[list[int]] = None
    element_name: Optional[str] = None  # beamline element, when the variable declares one


class ScreenInfo(BaseModel):
    """A diagnostic location. `image` is null when the model publishes no image for it."""

    key: str
    particles: str
    image: Optional[str] = None


class ConfigResponse(BaseModel):
    model: str
    version: str
    description: str = ""
    inputs: list[InputInfo]
    outputs: list[OutputInfo]
    screens: list[ScreenInfo]


class SnapshotResponse(BaseModel):
    inputs: dict[str, float]


# --- The evaluate API (POST /api/v1/models/{name}/evaluate) ----------------------
# ONE contract for every caller: any UI, and programmatic clients such as notebooks and
# emittance GUIs. There is deliberately no separate UI-private endpoint, because a second
# shape would mean every new UI reimplements the unit handling. Large arrays are
# base64-encoded little-endian float32, so decode with e.g.
# numpy.frombuffer(base64.b64decode(s), dtype="<f4").


class ScalarOutput(BaseModel):
    kind: Literal["scalar"]
    value: float
    unit: str = ""


class ArrayOutput(BaseModel):
    kind: Literal["array"]
    shape: list[int]
    dtype: str = "float32"
    data_b64: str  # base64 little-endian float32, row-major
    unit: str = ""


class ParticlesOutput(BaseModel):
    kind: Literal["particles"]
    n: int  # particles per coordinate, after subsampling to max_particles
    units: dict[str, str]  # coord name -> unit, e.g. {"x": "m", "px": "eV/c"}
    coords: dict[str, str]  # coord name -> base64 little-endian float32
    # Computed on the full beam, before subsampling, so a small max_particles thins the
    # scatter plot without changing the numbers beside it.
    stats: dict[str, float]
    stats_units: dict[str, str]


class ValueOutput(BaseModel):
    """Anything that is not a number, an array or a beam: enums, strings, flags."""

    kind: Literal["value"]
    # Deliberately untyped: this is the catch-all for variable classes the service does not
    # know, so it cannot promise more than "JSON-serializable".
    value: Any = None


Output = Annotated[
    Union[ScalarOutput, ArrayOutput, ParticlesOutput, ValueOutput],
    Field(discriminator="kind"),
]


class EvaluateV1Request(BaseModel):
    # id -> control value, overlaid on the model's baseline, so send only the knobs you
    # want to change ({} is the design machine). Only writable ScalarVariables become
    # inputs (see model/introspect.py), so a value is always a number.
    inputs: dict[str, float] = {}
    # Output ids to return. Every id asked for is present in the response.
    outputs: list[str] = []
    # Convenience: appends this screen's particles id and, when it has one, its image id.
    screen: Optional[str] = None
    max_particles: Optional[int] = None  # defaults to 3000, never the full beam
    # Opt-in Gaussian blur, in pixels, applied to 2-D array outputs. A screen image built
    # from ~1000 macroparticles is single-count noise at pixel resolution, and convolving
    # with the detector PSF is what makes it look like a camera frame. Off by default,
    # because blurring every 2-D array is wrong for a generic host.
    smooth_images_sigma_px: Optional[float] = None


class EvaluateV1Response(BaseModel):
    model: str
    version: str
    timestamp: float
    frame_index: int
    inputs: dict[str, float]  # effective, post-baseline-merge
    outputs: dict[str, Output]
