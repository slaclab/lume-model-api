"""Derive everything the API publishes from a live `LUMEModel` instance.

No PV name, screen name or unit appears in this file. `supported_variables` already carries
the name, `read_only`, `unit` and, for the variables that have them, `default_value` and
`value_range`, so a new model lights up the whole API without a code change here.

The one gap this fills: variables that are writable but carry no `value_range` (Bmad-side
magnets, for instance) get a range derived from their current value, flagged as
`range_source="derived"` so a UI can show it differently from a model-declared range.

The dataclasses are plain and picklable on purpose. `describe()` runs once per pool worker
and the result is shipped back to the main process across the spawn boundary, which is what
lets the main process serve `/api/v1/models/<name>/config` without ever importing the model.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Fraction of |default| used for a range the model does not declare.
DEFAULT_DERIVED_RANGE_FRACTION = 0.5

# Particle groups a model exposes as its plain output rather than as a screen. A screen is
# a place you can point a camera at, so the generic "the beam at the end" is not one.
NON_SCREEN_PARTICLE_VARIABLES = frozenset({"output_beam"})

IMAGE_NAME_SUFFIX = ":Image:ArrayData"
SCREEN_NAME_SUFFIX = "_beam"


@dataclass
class InputInfo:
    """A writable scalar knob, with the range and default a client needs to drive it."""

    id: str
    unit: str
    default: float
    min: float
    max: float
    range_source: str  # "model" (value_range) or "derived" (from the default)
    constant: bool  # model declares min == max, so it is not settable in practice


@dataclass
class OutputInfo:
    id: str
    kind: str  # scalar | array | particles | value
    unit: str
    shape: list[int] | None
    element_name: str | None  # beamline element the variable belongs to, when declared


@dataclass
class ScreenInfo:
    """A diagnostic location: a particle distribution and, when the model has one, an image."""

    key: str
    particles: str
    image: str | None


@dataclass
class ModelInfo:
    model: str
    description: str
    inputs: list[InputInfo] = field(default_factory=list)
    outputs: list[OutputInfo] = field(default_factory=list)
    screens: list[ScreenInfo] = field(default_factory=list)
    # Non-constant input defaults. Every evaluate is overlaid on this, so a request that
    # omits a knob gets the design value rather than whatever the last request left behind
    # on that pooled worker.
    baseline: dict[str, float] = field(default_factory=dict)

    @property
    def input_ids(self) -> set[str]:
        return {item.id for item in self.inputs}

    @property
    def output_ids(self) -> set[str]:
        return {item.id for item in self.outputs}

    def screen(self, key: str) -> ScreenInfo | None:
        for item in self.screens:
            if item.key == key:
                return item
        return None


def derived_range_fraction() -> float:
    raw = os.environ.get("LUME_DERIVED_RANGE_FRACTION")
    if not raw:
        return DEFAULT_DERIVED_RANGE_FRACTION
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "LUME_DERIVED_RANGE_FRACTION=%r is not a number, using %s",
            raw,
            DEFAULT_DERIVED_RANGE_FRACTION,
        )
        return DEFAULT_DERIVED_RANGE_FRACTION


def variable_kind(variable) -> str:
    """Map a lume variable class onto one of the four wire output kinds."""
    from lume.variables import NDVariable, ParticleGroupVariable, ScalarVariable

    # IntVariable subclasses ScalarVariable, so it lands on "scalar" here too.
    if isinstance(variable, ScalarVariable):
        return "scalar"
    if isinstance(variable, NDVariable):
        return "array"
    if isinstance(variable, ParticleGroupVariable):
        return "particles"
    return "value"


def _shape_of(variable) -> list[int] | None:
    shape = getattr(variable, "shape", None)
    if shape is None:
        return None
    return [int(dim) for dim in shape]


def _current_value(model, name: str) -> float | None:
    """Read a writable variable that declares no default, so it can still get a range."""
    try:
        return float(model.get([name])[name])
    except Exception as exc:  # a model may refuse to read back a control
        logger.warning("Skipping input %s: no default_value and get() failed (%s)", name, exc)
        return None


def _input_info(model, variable, fraction: float) -> InputInfo | None:
    name = variable.name
    default = variable.default_value
    if default is None:
        default = _current_value(model, name)
        if default is None:
            return None
    default = float(default)

    value_range = getattr(variable, "value_range", None)
    if value_range is not None:
        low, high = float(value_range[0]), float(value_range[1])
        return InputInfo(
            id=name,
            unit=variable.unit or "",
            default=default,
            min=low,
            max=high,
            range_source="model",
            constant=low == high,
        )

    # No model-declared range. A span proportional to the value keeps a slider useful across
    # magnets that differ by orders of magnitude, and the zero case needs an absolute span
    # because a proportional one would collapse to a point.
    span = fraction * abs(default) if default != 0.0 else fraction
    return InputInfo(
        id=name,
        unit=variable.unit or "",
        default=default,
        min=default - span,
        max=default + span,
        range_source="derived",
        constant=False,
    )


def _screens(outputs: list[OutputInfo]) -> list[ScreenInfo]:
    images = {
        item.element_name: item.id
        for item in outputs
        if item.kind == "array" and item.element_name and item.id.endswith(IMAGE_NAME_SUFFIX)
    }
    screens: list[ScreenInfo] = []
    for item in outputs:
        if item.kind != "particles" or item.id in NON_SCREEN_PARTICLE_VARIABLES:
            continue
        key = item.id[: -len(SCREEN_NAME_SUFFIX)] if item.id.endswith(SCREEN_NAME_SUFFIX) else item.id
        screens.append(ScreenInfo(key=key, particles=item.id, image=images.get(key)))
    return screens


def describe(model, name: str = "", description: str = "") -> ModelInfo:
    """Build the full published description of `model`.

    Writable non-scalar variables (an `input_beam` `ParticleGroupVariable`, say) appear
    neither as inputs nor as outputs: they are not drivable over a JSON knob API and they
    are not measurements. Listing them as outputs would promise a `get()` that many models
    do not support on their own controls.
    """
    fraction = derived_range_fraction()
    variables = model.supported_variables

    inputs: list[InputInfo] = []
    outputs: list[OutputInfo] = []
    for var_name in sorted(variables):
        variable = variables[var_name]
        kind = variable_kind(variable)
        if getattr(variable, "read_only", False):
            outputs.append(
                OutputInfo(
                    id=var_name,
                    kind=kind,
                    unit=getattr(variable, "unit", None) or "",
                    shape=_shape_of(variable),
                    element_name=getattr(variable, "element_name", None),
                )
            )
            continue
        if kind != "scalar":
            continue
        info = _input_info(model, variable, fraction)
        if info is not None:
            inputs.append(info)

    return ModelInfo(
        model=name or type(model).__name__,
        description=description or (type(model).__doc__ or "").strip().split("\n")[0],
        inputs=inputs,
        outputs=outputs,
        screens=_screens(outputs),
        baseline={item.id: item.default for item in inputs if not item.constant},
    )
