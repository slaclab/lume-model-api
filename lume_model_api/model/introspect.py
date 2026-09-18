"""Derive everything the API publishes from a live `LUMEModel` instance.

No screen name or unit appears in this file. `supported_variables` already carries the name,
`read_only`, `unit` and, for the variables that have them, `default_value` and `value_range`, so
a new model lights up the whole API without a code change here.

The one gap this fills: variables that are writable but carry no `value_range` (Bmad-side
magnets, for instance) get a range derived from their current value, flagged as
`range_source="derived"` so a UI can show it differently from a model-declared range.

The one exception to knowing no names is `ALIAS_PREFERENCE`, which breaks a tie between two
writable handles on one control when the model itself gives nothing else to choose between them.
See `_resolve_aliases`, and REVISIT_ALIASES below for when to reconsider the whole approach.

REVISIT_ALIASES: alias detection is inferred from published metadata rather than declared by the
model, and it has only ever been exercised against Bmad models via virtual-accelerator. Revisit
it when a second simulator backend arrives (Cheetah, Impact, a surrogate hosting its own
controls). Two things to check then. First, whether `(id prefix, unit, default)` still identifies
aliases: a backend that names knobs without a colon-delimited hierarchy collapses every input
into one group keyed on an empty prefix, and only the unit and default would separate them.
Second, whether `ALIAS_PREFERENCE` should be replaced by something the model declares, since
comparing each input's own variable class is the rigorous test and is what this approximates.
Until then a model with no aliased inputs is unaffected: single-member groups pass straight
through untouched.

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

# Final id segments that win an alias group, best first. See `_resolve_aliases` for what an
# alias group is and why a name is needed at all: a model that publishes two handles on one
# control leaves nothing to choose between them, so something has to break the tie, and the
# alternative to a name is picking by alphabet. A model with no aliased inputs never reaches
# this. Any group whose members are all absent from this tuple falls through to the range and
# then to `sorted`, so an unknown naming scheme still resolves deterministically.
ALIAS_PREFERENCE = ("BCTRL", "BDES")


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
    # Set on an input that lost an alias group, naming the input that won it. Always None on a
    # published input, since a demoted alias becomes an output. Present on both dataclasses so
    # the field means the same thing wherever a client meets it.
    alias_of: str | None = None


@dataclass
class OutputInfo:
    id: str
    kind: str  # scalar | array | particles | value
    unit: str
    shape: list[int] | None
    element_name: str | None  # beamline element the variable belongs to, when declared
    # The lume class this variable actually is, using lume's own `variable_class` key. `kind`
    # above is the coarser wire category a client switches on. Defaulted, and last, because the
    # existing fields are constructed positionally here and in tests.
    variable_class: str = ""
    # Set when this output is a writable variable demoted out of an alias group, naming the
    # input that stayed settable. None for an ordinary read-only output.
    alias_of: str | None = None


@dataclass
class ScreenInfo: # rename to particle beam?
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


def variable_class(variable) -> str:
    """The lume variable's own class name, published beside the coarser `kind`.

    `kind` is a wire-handling category, so it is deliberately lossy: `IntVariable` and
    `ScalarVariable` both become "scalar" because a JSON client decodes them identically, and
    every class this service does not know collapses into "value". A caller that needs the
    distinction (an integer spinner rather than a float slider, say) has no way back from the
    kind alone.

    The key name matches lume's own: `Variable.model_dump` emits `variable_class` with exactly
    this value, so a client that already reads a serialized lume variable sees the same
    vocabulary here. Reported for the concrete subclass a model actually used, which for an
    action-backed model is its own class (`BeamAtElementVariable`, `ScreenImageVariable`) rather
    than the lume base it mixes in, so treat it as a hint and switch on `kind`.
    """
    return type(variable).__name__


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


def _alias_key(item: InputInfo) -> tuple[str, str, float]:
    """The bucket two inputs share when they are two handles on one control.

    Everything before the final id segment, plus the unit and the default. The final segment is
    what distinguishes the aliases (`...:BCTRL` from `...:BDES`), so it is the part left out.

    The default is what makes this safe rather than merely plausible. Two handles on one control
    necessarily read back the same value at startup, because they read the same underlying
    attribute, so two same-device same-unit knobs with different defaults are genuinely distinct
    and never collide here. The unit is a cheap extra guard: two knobs on one device in different
    units cannot be the same control.

    `element_name` would be the obvious key and does not work. It is None for most injector
    magnets, `QUAD:IN20:525` among them, so it would miss precisely the aliases that matter.
    """
    prefix, _, _ = item.id.rpartition(":")
    return (prefix, item.unit, item.default)


def _alias_rank(item: InputInfo) -> tuple[int, int, str]:
    """Sort key deciding which member of an alias group stays settable. Lowest wins.

    Preference by final segment first, then a model-declared range over a derived one, then the
    id for determinism. The preference has to lead: on the LCLS injector only 9 of 50 pairs have
    a model-declared range on `BCTRL`, and for the other 41 both aliases are `derived`, so the
    range alone cannot decide and would leave the choice to the alphabet.
    """
    _, _, segment = item.id.rpartition(":")
    try:
        preference = ALIAS_PREFERENCE.index(segment)
    except ValueError:
        preference = len(ALIAS_PREFERENCE)  # unnamed segments rank after every named one
    return (preference, 0 if item.range_source == "model" else 1, item.id)


def _resolve_aliases(inputs: list[InputInfo]) -> tuple[list[InputInfo], list[InputInfo]]:
    """Split inputs into the ones that stay settable and the aliases demoted out of the way.

    A model may publish several writable handles on one underlying control. virtual-accelerator
    does exactly this for every magnet, mapping `BCTRL` and `BDES` to the same variable class and
    so to the same Bmad attribute, which mirrors how the real machine exposes both.

    That breaks the baseline merge, and quietly. `evaluate` applies every non-constant knob on
    every request so an evaluate is history-independent, so both aliases are written inside one
    `model.set()`. The one applied last wins, which made a request setting only `BCTRL` a silent
    no-op: the response echoed the caller's value with `source: request` while the magnet kept its
    default, and a full-range scan came back perfectly flat.

    Publishing one handle per control is what fixes it. The rest become read-only outputs, so
    they stay readable and keep returning the same number as the winner, and crucially they leave
    the baseline and can no longer overwrite it.
    """
    groups: dict[tuple[str, str, float], list[InputInfo]] = {}
    kept_singletons: list[InputInfo] = []
    for item in inputs:
        key = _alias_key(item)
        # An id with no ':' has an empty prefix, so every such input would land in one group
        # keyed only on unit and default. The demo model's knobs are colon-delimited and Bmad's
        # are PV names, but a backend naming knobs `k1`, `k2` would otherwise see unrelated
        # controls demote each other. An id that declares no hierarchy declares no alias.
        if not key[0]:
            kept_singletons.append(item)
            continue
        groups.setdefault(key, []).append(item)

    kept: list[InputInfo] = list(kept_singletons)
    demoted: list[InputInfo] = []
    for members in groups.values():
        if len(members) == 1:
            kept.append(members[0])
            continue
        winner, *losers = sorted(members, key=_alias_rank)
        kept.append(winner)
        for loser in losers:
            loser.alias_of = winner.id
            demoted.append(loser)
        # WARNING, not INFO: a knob that stops being settable is invisible in a response, which
        # is the whole reason the original bug survived as long as it did.
        logger.warning(
            "Inputs %s are aliases of one control (same id prefix, unit and default). "
            "Publishing %r as the settable knob and the rest as read-only outputs.",
            ", ".join(repr(item.id) for item in sorted(members, key=lambda x: x.id)),
            winner.id,
        )
    # Restore the caller's ordering. `describe` builds inputs from `sorted(variables)`, and
    # grouping would otherwise leave them ordered by first appearance within a group.
    kept.sort(key=lambda item: item.id)
    demoted.sort(key=lambda item: item.id)
    return kept, demoted


def _screens(outputs: list[OutputInfo]) -> list[ScreenInfo]:
    images = {
        item.element_name: item.id
        for item in outputs
        if item.kind == "array" and item.element_name and item.id.endswith(IMAGE_NAME_SUFFIX)
    }
    seen_keys: dict[str, str] = {}  # key -> first particles variable id that claimed it
    screens: list[ScreenInfo] = []
    for item in outputs:
        if item.kind != "particles" or item.id in NON_SCREEN_PARTICLE_VARIABLES:
            continue
        key = item.id[: -len(SCREEN_NAME_SUFFIX)] if item.id.endswith(SCREEN_NAME_SUFFIX) else item.id
        if key in seen_keys:
            # Both variables map to the same screen key. Only the first is reachable via
            # ModelInfo.screen(), so the second is effectively invisible. Logging rather
            # than raising keeps a pod alive for the screens that do work.
            logger.warning(
                "Screen key collision: %r and %r both strip to key %r. "
                "%r will be unreachable via ModelInfo.screen(). "
                "Rename one of those variables upstream in the model to resolve the conflict.",
                seen_keys[key],
                item.id,
                key,
                item.id,
            )
            continue
        seen_keys[key] = item.id
        screens.append(ScreenInfo(key=key, particles=item.id, image=images.get(key)))
    return screens


def describe(model, name: str = "", description: str = "") -> ModelInfo:
    """Build the full published description of `model`.

    Writable non-scalar variables (an `input_beam` `ParticleGroupVariable`, say) appear
    neither as inputs nor as outputs: they are not drivable over a JSON knob API and they
    are not measurements. Listing them as outputs would promise a `get()` that many models
    do not support on their own controls. Dropped writable non-scalar variables are logged
    at INFO, once per describe call with their ids and kinds, so an operator can see why a
    knob is missing from the published inputs.
    """
    fraction = derived_range_fraction()
    variables = model.supported_variables
    model_label = name or type(model).__name__

    inputs: list[InputInfo] = []
    outputs: list[OutputInfo] = []
    dropped_non_scalar: list[str] = []
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
                    variable_class=variable_class(variable),
                )
            )
            continue
        if kind != "scalar":
            dropped_non_scalar.append(f"{var_name} ({kind})")
            continue
        info = _input_info(model, variable, fraction)
        if info is not None:
            inputs.append(info)

    if dropped_non_scalar:
        logger.info(
            "model %r: %d writable non-scalar variable(s) excluded from inputs "
            "(not drivable over a JSON knob API): %s",
            model_label,
            len(dropped_non_scalar),
            ", ".join(dropped_non_scalar),
        )

    # One settable handle per control. A demoted alias becomes a read-only output so it stays
    # readable, and leaves the baseline so it can no longer overwrite the handle that stayed.
    inputs, demoted = _resolve_aliases(inputs)
    for item in demoted:
        variable = variables[item.id]
        outputs.append(
            OutputInfo(
                id=item.id,
                kind=variable_kind(variable),
                unit=item.unit,
                shape=_shape_of(variable),
                element_name=getattr(variable, "element_name", None),
                variable_class=variable_class(variable),
                alias_of=item.alias_of,
            )
        )
    if demoted:
        # Keep outputs in id order, as the read-only pass produced them, so the config route does
        # not list the demoted aliases in a separate block at the end.
        outputs.sort(key=lambda item: item.id)

    return ModelInfo(
        model=name or type(model).__name__,
        description=description or (type(model).__doc__ or "").strip().split("\n")[0],
        inputs=inputs,
        outputs=outputs,
        screens=_screens(outputs),
        baseline={item.id: item.default for item in inputs if not item.constant},
    )
