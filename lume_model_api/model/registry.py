"""Model-adapter registry.

A `ModelSpec` captures everything that is inherently per-model — the model factory,
its screens, its writable/EPICS input PVs, the Twiss PV names, and a known-good
`baseline` used for stateless baseline-merge. `ModelImageSource` (beam_monitor.py) is
driven entirely by a spec, so adding a new model (e.g. FACET-II) is a new registry
entry, not new branching code.

This module must not import beam_monitor (beam_monitor imports the registry).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from lume_visualizations.config import (
    EPICS_INPUT_PVS,
    EXCLUDED_EPICS_PVS,
    MANUAL_INPUT_PVS,
    SCREEN_CONFIGS,
    ScreenConfig,
)
from lume_visualizations.fake_epics_ioc import FAKE_INPUT_SPECS


# --- model factories (import heavy deps lazily, only when instantiated) ---

def _create_cu_hxr_staged_model(start_element: str = "OTR2", end_element: str = "TD11"):
    from virtual_accelerator.models.staged_model import get_cu_hxr_staged_model

    return get_cu_hxr_staged_model(
        start_element=start_element, end_element=end_element, track_beam=True
    )


@dataclass(frozen=True)
class ModelSpec:
    """Everything per-model the generic source + webapp need."""

    name: str
    description: str
    make_model: Callable[[], Any]
    screens: dict[str, ScreenConfig]
    input_pvs: list[str]        # writable slider inputs
    epics_input_pvs: list[str]  # read-only inputs for the live view
    excluded_pvs: tuple[str, ...]
    baseline: dict[str, float]  # known-good defaults for input_pvs (baseline-merge)
    twiss_s_pv: str = "s"
    twiss_a_beta_pv: str = "x.beta"
    twiss_b_beta_pv: str = "y.beta"

    @property
    def screen_keys(self) -> list[str]:
        return list(self.screens)


def _baseline_from_fake_specs(input_pvs: list[str]) -> dict[str, float]:
    """Nominal design values for the writable inputs (also what /config publishes)."""
    defaults = {spec.pv_name: float(spec.default) for spec in FAKE_INPUT_SPECS}
    return {pv: defaults[pv] for pv in input_pvs if pv in defaults}


_CU_HXR_STAGED = ModelSpec(
    name="cu_hxr_staged",
    description=(
        "Staged LUMEModel chaining the LCLS Cu Injector ML surrogate (predicting at "
        "OTR2) with a Bmad beamline simulation tracking OTR2 → TD11."
    ),
    make_model=_create_cu_hxr_staged_model,
    screens=SCREEN_CONFIGS,
    input_pvs=list(MANUAL_INPUT_PVS),
    epics_input_pvs=list(EPICS_INPUT_PVS),
    excluded_pvs=EXCLUDED_EPICS_PVS,
    baseline=_baseline_from_fake_specs(list(MANUAL_INPUT_PVS)),
)


MODEL_REGISTRY: dict[str, ModelSpec] = {
    _CU_HXR_STAGED.name: _CU_HXR_STAGED,
}


def get_spec(model_name: str) -> ModelSpec:
    try:
        return MODEL_REGISTRY[model_name]
    except KeyError as exc:
        known = ", ".join(MODEL_REGISTRY)
        raise KeyError(f"Unknown model '{model_name}'. Registered: {known}") from exc
