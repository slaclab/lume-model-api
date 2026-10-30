"""Pydantic request/response schemas for the API.

The output payload is variable-generic: a response is a map of output id to an `Output`,
discriminated on `kind`. That is what lets one contract serve any model. A caller reads the
kinds it cares about from `GET /api/v1/models/{name}/config` before it ever calls evaluate.

Units travel with every value, in the model's own units. Nothing here converts, because a
generic host cannot tell a beam image from a lattice function.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# A control value a caller may send. `allow_inf_nan=False` is the whole point: pydantic accepts
# the JSON literals `NaN` and `Infinity` in a float field by default, and a NaN handed to
# `model.set` passes lume's range check silently, because every comparison against NaN is False.
# The model then runs on a NaN knob and returns a NaN beam that looks like a physics result.
# Rejecting it here makes that a 422 instead.
#
# RESPONSE non-finite values are a different matter and are deliberately kept: a solver that did
# not converge, or `norm_emit_x` on a degenerate beam, legitimately produces NaN, and
# `serialize.json_safe` renders those as `null` so the frame is still valid JSON. Only inbound
# values are rejected, because only inbound values are a caller's mistake.
ControlValue = Annotated[float, Field(allow_inf_nan=False)]

# --- GET /api/v1/models ----------------------------------------------------------


class ModelListEntry(BaseModel):
    """One model hosted by this deployment."""

    name: str = Field(
        description="URL name of the model, used as `{name}` in every other route."
    )
    description: str = Field(default="", description="Short description from the model.")
    # "<name> (demo)" covers a demo selected by its full factory path.
    version: str = Field(
        description='Model version. `"demo"` or `"<name> (demo)"` marks the demo model.'
    )


# --- GET /api/v1/models/{name}/config ---------------------------------------------


class InputInfo(BaseModel):
    """A writable scalar input that can be sent in an evaluate request."""

    id: str = Field(description="Input id, used as a key in evaluate `inputs`.")
    unit: str = Field(default="", description="Unit, as declared by the model.")
    default: float = Field(description="Design value. Used when a request omits this input.")
    min: float = Field(description="Lower limit.")
    max: float = Field(description="Upper limit.")
    range_source: str = Field(
        description=(
            '`"model"` when the model declared the range. `"derived"` when the service '
            "estimated it from the default because the model declared none."
        )
    )
    constant: bool = Field(
        default=False,
        description="True when `min` equals `max`. The input is fixed and never set.",
    )
    # Present on both InputInfo and OutputInfo so the field means one thing everywhere.
    alias_of: str | None = Field(
        default=None, description="Always null on an input. See `OutputInfo.alias_of`."
    )


class OutputInfo(BaseModel):
    """A value the model can return from an evaluate."""

    id: str = Field(description="Output id, used in evaluate `outputs`.")
    kind: Literal["scalar", "array", "particles", "value"] = Field(
        description="Payload shape this output returns. Matches `kind` in the evaluate response."
    )
    unit: str = Field(default="", description="Unit, as declared by the model.")
    shape: list[int] | None = Field(
        default=None, description="Array shape, when the model declares one."
    )
    element_name: str | None = Field(
        default=None, description="Beamline element this output belongs to, when declared."
    )
    # lume's own class name, since `kind` is coarser: IntVariable and ScalarVariable share
    # "scalar", and every unknown class collapses into "value".
    variable_class: str = Field(
        default="",
        description=(
            'lume variable class, for example `"ScalarVariable"` or `"EnumVariable"`. Finer '
            "than `kind`. Empty when unknown."
        ),
    )
    alias_of: str | None = Field(
        default=None,
        description=(
            "Set when this id controls the same thing as another input, for example a magnet's "
            "`BDES` next to its `BCTRL`. Names the input to set instead. This id can still be "
            "read, but sending it in `inputs` is a 400."
        ),
    )


class ScreenInfo(BaseModel):
    """A diagnostic location with a particle distribution and, optionally, an image."""

    key: str = Field(description="Screen name, used as evaluate `screen`.")
    particles: str = Field(description="Output id of the particle distribution at this screen.")
    image: str | None = Field(
        default=None, description="Output id of the screen image. Null when there is none."
    )


class ConfigResponse(BaseModel):
    """Everything a model publishes: its inputs, outputs and screens."""

    model: str = Field(description="URL name of the model.")
    version: str = Field(description="Model version.")
    description: str = Field(default="", description="Short description from the model.")
    inputs: list[InputInfo] = Field(description="Inputs that can be set.")
    outputs: list[OutputInfo] = Field(description="Outputs that can be requested.")
    screens: list[ScreenInfo] = Field(description="Diagnostic screens.")


# Where one input value in a response came from. Named constants rather than bare literals
# because two independent senders write them (the evaluate route and the live producer), and a
# typo in one of them would be a silent provenance lie rather than an error.
SOURCE_LIVE = "live"  # read off the machine on this frame
SOURCE_REQUEST = "request"  # supplied by the caller in `inputs`
SOURCE_BASELINE = "baseline"  # the model's own design value, filled in by the merge


def live_sources(inputs, live) -> dict[str, str]:
    """Provenance for values produced by a live read overlaid on the baseline.

    Lives here, beside the vocabulary, rather than in either caller: the live producer in
    `live_hub.py` and `machine-snapshot` in `main.py` both need exactly this map, and two copies
    of the same conditional are how the two routes would come to disagree about what "live"
    means.

    Keyed on `inputs` rather than on `live`, so the map covers every id the response reports,
    including the ones that only have a design value. An id present in `inputs` but absent from
    `live` is precisely the case an operator cannot otherwise see.
    """
    return {name: SOURCE_LIVE if name in live else SOURCE_BASELINE for name in inputs}


class SnapshotResponse(BaseModel):
    """Current machine values for a model's inputs."""

    inputs: dict[str, float] = Field(
        description="Input id to value. Unreadable PVs fall back to the design value."
    )
    # A separate map, so `inputs` keeps its `Record<string, number>` shape.
    sources: dict[str, str] = Field(
        default={},
        description=(
            'Same keys as `inputs`. `"live"` when the value was read from the machine, '
            '`"baseline"` when the PV could not be read and the design value was used.'
        ),
    )


# --- The evaluate API (POST /api/v1/models/{name}/evaluate) ----------------------
# ONE contract for every caller: any UI, and programmatic clients such as notebooks and
# emittance GUIs. There is deliberately no separate UI-private endpoint, because a second
# shape would mean every new UI reimplements the unit handling. Large arrays are
# base64-encoded little-endian float32, so decode with e.g.
# numpy.frombuffer(base64.b64decode(s), dtype="<f4").


# Every output kind carries `variable_class` beside its `kind`, the same way `OutputInfo` does on
# the config route, so the two describe an id identically. It is the lume class name, which is
# finer than `kind`: a client that needs IntVariable rather than "scalar" reads it, everything
# else switches on `kind`. Declared on each member rather than a shared base, because the
# discriminated union below needs `kind` narrowed per member anyway.
_VARIABLE_CLASS = "lume variable class. Finer than `kind`. Empty when unknown."


class ScalarOutput(BaseModel):
    """A single number."""

    kind: Literal["scalar"]
    value: float = Field(description="The value. Null when the model returned NaN or Inf.")
    unit: str = Field(default="", description="Unit, as declared by the model.")
    variable_class: str = Field(default="", description=_VARIABLE_CLASS)


class ArrayOutput(BaseModel):
    """An N-dimensional array, base64-encoded. 2-D arrays are usually screen images."""

    kind: Literal["array"]
    shape: list[int] = Field(
        description="Array shape after any image downsampling. Reshape `data_b64` to this."
    )
    dtype: str = Field(default="float32", description="Always `float32`.")
    data_b64: str = Field(description="Base64 of little-endian float32 values, row-major.")
    unit: str = Field(default="", description="Unit, as declared by the model.")
    variable_class: str = Field(default="", description=_VARIABLE_CLASS)


class ParticlesOutput(BaseModel):
    """A particle distribution, subsampled to `max_particles`."""

    kind: Literal["particles"]
    n: int = Field(description="Number of particles in each coordinate array.")
    units: dict[str, str] = Field(
        description='Coordinate name to unit, for example `{"x": "m", "px": "eV/c"}`.'
    )
    coords: dict[str, str] = Field(
        description=(
            "Coordinate name to base64 little-endian float32 array of length `n`. Includes "
            "`weight` (per-particle charge)."
        )
    )
    stats: dict[str, float] = Field(
        description="Beam statistics, computed on the full beam before subsampling."
    )
    stats_units: dict[str, str] = Field(description="Statistic name to unit.")
    variable_class: str = Field(default="", description=_VARIABLE_CLASS)


class ValueOutput(BaseModel):
    """Anything that isn't a number, an array or a beam, such as an enum, a string or a flag."""

    kind: Literal["value"]
    # Untyped: this is the catch-all for variable classes the service does not know.
    value: Any = Field(default=None, description="Any JSON value.")
    variable_class: str = Field(
        default="",
        description="lume variable class. The only way to tell an enum from a string here.",
    )


Output = Annotated[
    ScalarOutput | ArrayOutput | ParticlesOutput | ValueOutput,
    Field(discriminator="kind"),
]


# Upper bound on `max_particles`. A real tracked beam can be 1e5+ particles, so this cap
# protects the PAYLOAD rather than the model: at seven float32 coordinates per particle,
# 200000 is already ~7.5MB of base64 in one response, which is the same order as the
# full-resolution sensor image that `serialize.MAX_IMAGE_DIM` exists to avoid shipping. Asking
# for more than this is asking for a response no browser will render anyway.
MAX_PARTICLES_LIMIT = 200_000

# Upper bound on `smooth_images_sigma_px`. `serialize._smooth` calls
# `scipy.ndimage.gaussian_filter`, whose separable kernel is ~8*sigma taps wide, so cost grows
# linearly with sigma and one request can pin a pool worker for hours on a real 1040x1392 sensor
# (measured: 0.84s at sigma=2000 on a 240x320 image, 8.5s at sigma=20000). Since
# `run_in_executor` cannot cancel work already handed to a subprocess, that worker is
# unreclaimable, which makes an unbounded sigma a denial of service against the whole pool.
# 50 is well past any real detector point spread function, which is a few pixels wide: a
# Gaussian wider than a few tens of pixels does not make a screen image look like a camera
# frame, it makes it a uniform smear with no beam left in it.
MAX_SMOOTH_SIGMA_PX = 50.0


class EvaluateV1Request(BaseModel):
    """Inputs to set and outputs to return. At least one of `outputs` or `screen` is required."""

    inputs: dict[str, ControlValue] = Field(
        default={},
        description=(
            "Input id to value. Omitted inputs use their design value, so `{}` evaluates the "
            "design machine. Values must be finite."
        ),
    )
    outputs: list[str] = Field(default=[], description="Output ids to return.")
    screen: str | None = Field(
        default=None,
        description=(
            "Screen key. Adds that screen's particle output and its image, when it has one, "
            "to `outputs`."
        ),
    )
    # `ge=1`: `model/evaluate.py` silently substitutes the default for a zero or negative cap,
    # which would hide the caller's mistake.
    max_particles: Annotated[int, Field(ge=1, le=MAX_PARTICLES_LIMIT)] | None = Field(
        default=None, description="Maximum particles per particle output. Defaults to 3000."
    )
    # A screen image built from ~1000 macroparticles is single-count noise at pixel resolution.
    # Off by default, because blurring every 2-D array is wrong for a generic host.
    smooth_images_sigma_px: (
        Annotated[float, Field(ge=0.0, le=MAX_SMOOTH_SIGMA_PX, allow_inf_nan=False)] | None
    ) = Field(
        default=None,
        description=(
            "Gaussian blur width in pixels, applied to every 2-D array output. Off by default. "
            "Intensities are not renormalized."
        ),
    )


class EvaluateV1Response(BaseModel):
    """One model evaluation. Also the body of each live stream `frame` event."""

    model: str = Field(description="URL name of the model.")
    version: str = Field(description="Model version.")
    timestamp: float = Field(description="Unix time in seconds when the result was serialized.")
    frame_index: int = Field(
        description=(
            "0 on the evaluate route. On the live stream, a counter that only increases, with "
            "gaps."
        )
    )
    inputs: dict[str, float] = Field(
        description="Every input value the model ran with, including the filled-in defaults."
    )
    outputs: dict[str, Output] = Field(
        description="Output id to payload. Every requested id is present. Switch on `kind`."
    )
    # Populated by the sender rather than the serializer, because only the sender knows the
    # provenance. See SENDER_ADDED in tests/test_wire_shape.py.
    input_sources: dict[str, str] = Field(
        default={},
        description=(
            'Same keys as `inputs`. `"request"` when sent in the request, `"baseline"` when '
            'filled in from the design value, `"live"` when read from the machine (live stream '
            "only)."
        ),
    )
