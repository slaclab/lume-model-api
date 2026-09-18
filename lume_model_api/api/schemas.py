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
    # Always null on a published input. The field exists on both InputInfo and OutputInfo so it
    # means one thing wherever a client meets it: see OutputInfo.alias_of.
    alias_of: str | None = None


class OutputInfo(BaseModel):
    id: str
    kind: Literal["scalar", "array", "particles", "value"]
    unit: str = ""
    shape: list[int] | None = None
    element_name: str | None = None  # beamline element, when the variable declares one
    # The lume variable class this output really is, under lume's own key name, since `kind` is
    # deliberately coarser: IntVariable and ScalarVariable share the "scalar" kind, and every
    # class this service does not know collapses into "value". Advisory, so switch on `kind` and
    # read this only when the finer distinction matters. Empty string when unknown.
    variable_class: str = ""
    # Set when the model published this id as a writable control that is a second handle on the
    # same underlying control as another input, in which case only one of them stays settable and
    # this names that one. Reading this id still works and returns the same value as the input it
    # names. Sending it in `inputs` is a 400. Null for an ordinary read-only output.
    alias_of: str | None = None


class ScreenInfo(BaseModel):
    """A diagnostic location. `image` is null when the model publishes no image for it."""

    key: str
    particles: str
    image: str | None = None


class ConfigResponse(BaseModel):
    model: str
    version: str
    description: str = ""
    inputs: list[InputInfo]
    outputs: list[OutputInfo]
    screens: list[ScreenInfo]


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
    inputs: dict[str, float]
    # Which of the ids in `inputs` are really the machine. An id whose PV could not be read
    # falls back to the model's design value, and until this field existed that fallback was
    # indistinguishable on the wire from a value that came off the machine, so an operator had
    # no way to tell "this is the machine" from "this is a default". Empty by default, so a
    # client that predates it is unaffected and `inputs` keeps its `Record<string, number>`
    # shape.
    sources: dict[str, str] = {}


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
class ScalarOutput(BaseModel):
    kind: Literal["scalar"]
    value: float
    unit: str = ""
    variable_class: str = ""


class ArrayOutput(BaseModel):
    kind: Literal["array"]
    shape: list[int]
    dtype: str = "float32"
    data_b64: str  # base64 little-endian float32, row-major
    unit: str = ""
    variable_class: str = ""


class ParticlesOutput(BaseModel):
    kind: Literal["particles"]
    n: int  # particles per coordinate, after subsampling to max_particles
    units: dict[str, str]  # coord name -> unit, e.g. {"x": "m", "px": "eV/c"}
    coords: dict[str, str]  # coord name -> base64 little-endian float32
    # Computed on the full beam, before subsampling, so a small max_particles thins the
    # scatter plot without changing the numbers beside it.
    stats: dict[str, float]
    stats_units: dict[str, str]
    variable_class: str = ""


class ValueOutput(BaseModel):
    """Anything that is not a number, an array or a beam: enums, strings, flags."""

    kind: Literal["value"]
    # Deliberately untyped: this is the catch-all for variable classes the service does not
    # know, so it cannot promise more than "JSON-serializable".
    value: Any = None
    # The one kind where this really earns its place: "value" says only "not a number, array or
    # beam", so the lume class name is all a client has to tell an EnumVariable from a
    # StrVariable.
    variable_class: str = ""


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
    # id -> control value, overlaid on the model's baseline, so send only the knobs you
    # want to change ({} is the design machine). Only writable ScalarVariables become
    # inputs (see model/introspect.py), so a value is always a number.
    inputs: dict[str, ControlValue] = {}
    # Output ids to return. Every id asked for is present in the response.
    outputs: list[str] = []
    # Convenience: appends this screen's particles id and, when it has one, its image id.
    screen: str | None = None
    # Defaults to 3000, never the full beam. `ge=1` because a zero or negative cap is not a
    # request for anything, and `model/evaluate.py` silently substitutes the default for it,
    # which hides the caller's mistake rather than reporting it.
    max_particles: Annotated[int, Field(ge=1, le=MAX_PARTICLES_LIMIT)] | None = None
    # Opt-in Gaussian blur, in pixels, applied to 2-D array outputs. A screen image built
    # from ~1000 macroparticles is single-count noise at pixel resolution, and convolving
    # with the detector PSF is what makes it look like a camera frame. Off by default,
    # because blurring every 2-D array is wrong for a generic host.
    smooth_images_sigma_px: (
        Annotated[float, Field(ge=0.0, le=MAX_SMOOTH_SIGMA_PX, allow_inf_nan=False)] | None
    ) = None


class EvaluateV1Response(BaseModel):
    model: str
    version: str
    timestamp: float
    frame_index: int
    inputs: dict[str, float]  # effective, post-baseline-merge
    outputs: dict[str, Output]
    # id -> one of SOURCE_LIVE / SOURCE_REQUEST / SOURCE_BASELINE, for the same ids as `inputs`.
    # The merge fills every knob the caller did not send, and on the live stream it also fills
    # every id whose PV could not be read, so without this a design value is indistinguishable
    # from the machine. Kept as a parallel map rather than as richer values inside `inputs`, so
    # a client typed `Record<string, number>` there is untouched. Empty by default, and populated
    # by the sender rather than the serializer, because only the sender knows the provenance:
    # see SENDER_ADDED in tests/test_wire_shape.py.
    input_sources: dict[str, str] = {}
