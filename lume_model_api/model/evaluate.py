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

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from lume_model_api.model.introspect import variable_class, variable_kind

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

    Raised by `_prevalidate` below, which runs the same checks `LUMEModel.set` runs, so the
    message is whatever lume or the model said. Maps to HTTP 400, because the request was
    wrong and a retry will not help.
    """


class ModelUnusable(RuntimeError):
    """The model instance failed in a way that is not attributable to the request.

    `_prevalidate` has already excluded every fault `LUMEModel.set` would blame on the
    caller, so anything still escaping `model.set()` is the model's own failure. Maps to HTTP
    503, and in the pool it latches the worker (see `api/pool.py`).

    `RuntimeError` rather than `ValueError` on purpose. `UnknownVariable` and `InvalidInput`
    are both `ValueError`s, and `api.main.evaluate_v1` catches that pair to answer 400, so a
    `ValueError` subclass here would be reported as the caller's fault again, which is the
    defect documented in `docs/POISONED_WORKER.md`.
    """


@dataclass
class EvaluateResult:
    # The post-merge control values actually applied, echoed so a caller can see what the
    # baseline filled in for the knobs it did not send.
    inputs: dict[str, Any]
    # id -> {"kind": ..., ...}. Arrays are still numpy at this point.
    outputs: dict[str, dict]



def _coerce_control(variable, value):
    """Match the variable's declared numeric type, which lume validates strictly."""
    from lume.variables import IntVariable

    if isinstance(variable, IntVariable):
        return int(round(float(value)))
    try:
        return float(value)
    except (TypeError, ValueError):
        return value  # an enum or string control, passed through untouched


def _prevalidate(model, applied: Mapping[str, Any]) -> None:
    """Run the checks `LUMEModel.set` runs, here, where the outcome can be attributed.

    This duplicates lume's own validation loop deliberately. `LUMEModel.set` completes that
    whole loop before it calls `_set`, so a failure raised here is the complete set of faults
    the model would blame on the request, and anything raised from inside `model.set()`
    afterwards is by construction the model failing rather than the caller being wrong. That
    is what lets the classification below be exception-type-free.

    Inferring the same thing from the exception type after the fact does not work, which is
    the bug in `docs/POISONED_WORKER.md`: lume raises `ValueError` and `TypeError` for a bad
    value, and so do numpy and a broken lattice. `lume.exceptions.ReadOnlyError` subclasses
    `TypeError`, so it shares an arm with a genuine model failure too.
    """
    from lume.variables import Variable

    # Fetched once: `supported_variables` is a property on every model, and on an action-backed
    # one it rebuilds the dict per access.
    variables = model.supported_variables
    for name, value in applied.items():
        variable = variables.get(name)
        if variable is None:
            raise InvalidInput(f"Variable {name!r} is not supported by the model.")
        if not isinstance(variable, Variable):
            raise InvalidInput(f"Variable {name!r} is not a valid Variable instance.")
        if getattr(variable, "read_only", False):
            raise InvalidInput(f"Variable {name!r} is read-only and cannot be set.")
        try:
            # No `config` argument, so each variable's own `default_validation_config`
            # decides whether the range is enforced, exactly as `LUMEModel.set` leaves it.
            variable.validate_value(value)
        except (TypeError, ValueError) as exc:
            raise InvalidInput(f"Model rejected input {name!r}: {exc}") from exc


def _plain(value):
    """Make a value JSON-safe without assuming it is numeric."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def _particles_payload(beam, max_particles: int | None, output_id: str = "") -> dict:
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

    if not coords:
        # A genuinely empty ParticleGroup still yields coords with zero-length arrays, so
        # the real-but-empty case stays distinguishable from this total failure to read any
        # coordinate at all. Total failure means the output variable is None or is an
        # unexpected type, which the caller should surface rather than return a silent n=0.
        label = f" {output_id!r}" if output_id else ""
        raise TypeError(
            f"Could not read any coordinate from beam output{label}. "
            "The output variable may be None or an unexpected type."
        )

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

    requested_outputs = list(dict.fromkeys(outputs or []))
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
        # Every client fault is rejected here, before the model runs, so the catch below does
        # not have to tell one apart from a model failure.
        _prevalidate(model, applied)
        try:
            model.set(applied)
        except asyncio.CancelledError:
            # A cancelled request is neither a client fault nor a broken model, and turning
            # one into `ModelUnusable` would latch the worker and restart the pod every time a
            # live viewer disconnects mid-frame.
            raise
        except BaseException as exc:
            # Deliberately everything else, including `BaseException`: a `KeyboardInterrupt`
            # landing inside a long Tao call leaves the instance in exactly the half-applied
            # state this exists to report, so it must not escape unclassified.
            raise ModelUnusable(
                "The model failed while applying inputs and may be left inconsistent: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    values = model.get(requested_outputs) if requested_outputs else {}

    result: dict[str, dict] = {}
    for name in requested_outputs:
        variable = variables[name]
        value = values.get(name)
        unit = getattr(variable, "unit", None) or ""
        # Same classifiers the config route publishes, so the kind and variable_class a caller
        # reads there are the ones it gets back here.
        kind = variable_kind(variable)
        declared_class = variable_class(variable)
        if kind == "scalar":
            result[name] = {"kind": "scalar", "value": float(value), "unit": unit}
        elif kind == "particles":
            result[name] = _particles_payload(value, max_particles, output_id=name)
        elif kind == "array":
            result[name] = {
                "kind": "array",
                "array": np.asarray(value),
                "unit": unit,
                "smooth_sigma_px": smooth_sigma_px,
            }
        else:
            result[name] = {"kind": "value", "value": _plain(value)}
        # Set outside the branches so a new kind cannot forget it. The serializer copies it onto
        # every wire output, and `result_to_wire` must emit every key unconditionally.
        result[name]["variable_class"] = declared_class

    return EvaluateResult(inputs=applied, outputs=result)
