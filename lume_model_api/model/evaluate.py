"""The one evaluate path: baseline-merge, `set`, `get`, convert by variable kind.

Values come back in the model's own units. There is no unit conversion anywhere in this
package: a generic host cannot know which arrays are beam images and which are lattice
functions, so it declares the units it was given and lets the caller scale for display.
The openPMD-beamphysics conventions a `ParticleGroup` already uses (m, eV/c, C) are what
the particle payload reports.

Arrays leave here as numpy. Encoding happens in `api/serialize.py`, which still runs inside
the pool worker, so nothing large crosses the process boundary un-encoded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from lume_model_api.model.introspect import variable_kind

# An omitted `max_particles` must not ship the whole beam, so the default is a cap and not
# "unbounded".
DEFAULT_MAX_PARTICLES = 3000

# Phase-space coordinates published for a ParticleGroup, in openPMD-beamphysics native
# units. `weight` is included deliberately: a distribution without per-particle charge is
# incomplete for a physics caller, and it is only ~17% of the payload.
PARTICLE_COORDS = ("x", "px", "y", "py", "z", "pz", "weight")
COORD_UNITS = {
    "x": "m",
    "y": "m",
    "z": "m",
    "px": "eV/c",
    "py": "eV/c",
    "pz": "eV/c",
    "weight": "C",
}

# Summary statistics computed on the full beam, before subsampling, so that a small
# `max_particles` degrades the scatter plot without corrupting the numbers next to it.
PARTICLE_STATS = (
    "sigma_x",
    "sigma_y",
    "sigma_z",
    "norm_emit_x",
    "norm_emit_y",
    "mean_energy",
    "charge",
)
STATS_UNITS = {
    "sigma_x": "m",
    "sigma_y": "m",
    "sigma_z": "m",
    "norm_emit_x": "m",
    "norm_emit_y": "m",
    "mean_energy": "eV",
    "charge": "C",
}


class UnknownVariable(ValueError):
    """A requested input or output id is not one the model publishes (maps to HTTP 400)."""


class InvalidInput(ValueError):
    """The model rejected a control value: wrong type or outside its declared range.

    Raised from the model's own `set()` validation, so the message is whatever lume or the
    model said. Maps to HTTP 400, because the request was wrong and a retry will not help.
    """


@dataclass
class EvaluateResult:
    # The post-merge control values actually applied, echoed so a caller can see what the
    # baseline filled in for the knobs it did not send.
    inputs: dict[str, Any]
    # id -> {"kind": ..., ...}. Arrays are still numpy at this point.
    outputs: dict[str, dict]


def _dedupe(ids: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in ids:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _coerce_control(variable, value):
    """Match the variable's declared numeric type, which lume validates strictly."""
    from lume.variables import IntVariable

    if isinstance(variable, IntVariable):
        return int(round(float(value)))
    try:
        return float(value)
    except (TypeError, ValueError):
        return value  # an enum or string control, passed through untouched


def _plain(value):
    """Make a value JSON-safe without assuming it is numeric."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def _particles_payload(beam, max_particles: int | None) -> dict:
    # A missing or non-positive cap falls back to the default rather than meaning
    # "unbounded". A negative one would otherwise ship the whole beam, which on a real model
    # is hundreds of thousands of particles in one response.
    cap = int(max_particles) if max_particles else DEFAULT_MAX_PARTICLES
    if cap <= 0:
        cap = DEFAULT_MAX_PARTICLES
    stats = {}
    for key in PARTICLE_STATS:
        try:
            stats[key] = float(beam[key])
        except Exception:  # not every ParticleGroup supports every derived quantity
            continue

    coords: dict[str, np.ndarray] = {}
    for key in PARTICLE_COORDS:
        try:
            coords[key] = np.asarray(beam[key], dtype=float)
        except Exception:  # coordinate absent on this beam object
            continue

    n = len(next(iter(coords.values()))) if coords else 0
    if n > cap:
        # linspace rather than a random draw: the subsample is reproducible, so two
        # identical requests to two different pool workers return the same particles.
        indices = np.linspace(0, n - 1, cap, dtype=int)
        coords = {key: value[indices] for key, value in coords.items()}
        n = cap

    return {
        "kind": "particles",
        "n": int(n),
        "units": {key: COORD_UNITS.get(key, "") for key in coords},
        "coords": coords,
        "stats": stats,
        "stats_units": {key: STATS_UNITS.get(key, "") for key in stats},
    }


def _config_hint(info) -> str:
    """The config route for the model being evaluated, for use in an error message.

    `info.model` is the URL name the API hosts this model under, so the hint is a path the
    caller can actually fetch. A model described without a name (from a notebook, say) gets
    the template instead.
    """
    return f"GET /api/v1/models/{info.model or '<name>'}/config"


def evaluate(
    model,
    info,
    inputs: Mapping[str, Any] | None = None,
    outputs: Sequence[str] | None = None,
    max_particles: int | None = None,
    smooth_sigma_px: float | None = None,
) -> EvaluateResult:
    """Run `model` at `inputs` and return the requested `outputs`.

    `inputs` is overlaid on `info.baseline`, so an evaluate is history-independent: any
    worker in the pool answers a given request identically no matter what it ran before.
    Constants (model range with min == max) are dropped, because setting them back is at
    best a no-op and at worst a validation error.

    `smooth_sigma_px` is not applied here. It travels with each array output so that
    `api/serialize.py` can apply it while encoding, which keeps scipy out of this layer.
    """
    requested_inputs = dict(inputs or {})
    known = info.input_ids
    unknown_inputs = sorted(set(requested_inputs) - known)
    if unknown_inputs:
        raise UnknownVariable(
            f"Unknown input id(s): {', '.join(unknown_inputs)}. "
            f"{_config_hint(info)} lists every input this model accepts."
        )

    settable = {item.id for item in info.inputs if not item.constant}
    effective = {
        name: value
        for name, value in {**info.baseline, **requested_inputs}.items()
        if name in settable
    }

    requested_outputs = _dedupe(outputs or [])
    unknown_outputs = [name for name in requested_outputs if name not in info.output_ids]
    if unknown_outputs:
        raise UnknownVariable(
            f"Unknown output id(s): {', '.join(sorted(unknown_outputs))}. "
            f"{_config_hint(info)} lists every output this model publishes."
        )

    variables = model.supported_variables
    applied = {
        name: _coerce_control(variables[name], value) for name, value in effective.items()
    }
    if applied:
        try:
            model.set(applied)
        except (TypeError, ValueError) as exc:
            # lume raises these from `validate_value`: a non-numeric value, or one outside
            # `value_range` when the variable validates with config "error". Anything else
            # (a solver crash, say) is a genuine server error and propagates as one.
            raise InvalidInput(f"Model rejected the inputs: {exc}") from exc

    values = model.get(requested_outputs) if requested_outputs else {}

    result: dict[str, dict] = {}
    for name in requested_outputs:
        variable = variables[name]
        value = values.get(name)
        unit = getattr(variable, "unit", None) or ""
        # Same classifier the config route publishes, so the kind a caller reads there is the
        # kind it gets back here.
        kind = variable_kind(variable)
        if kind == "scalar":
            result[name] = {"kind": "scalar", "value": float(value), "unit": unit}
        elif kind == "particles":
            result[name] = _particles_payload(value, max_particles)
        elif kind == "array":
            result[name] = {
                "kind": "array",
                "array": np.asarray(value),
                "unit": unit,
                "smooth_sigma_px": smooth_sigma_px,
            }
        else:
            result[name] = {"kind": "value", "value": _plain(value)}

    return EvaluateResult(inputs=applied, outputs=result)
