"""Shared beam data abstractions and staged-model sources for the marimo apps."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import platform
from typing import Mapping, Optional

import numpy as np

from lume_visualizations.config import (
    EPICS_INPUT_PVS,
    MODEL_INPUT_NAMES,
    SCREEN_CONFIGS,
    EXCLUDED_EPICS_PVS,
    resolve_lcls_lattice_path,
)


@contextmanager
def _tao_model_workdir(lattice_path: str):
    previous_cwd = Path.cwd()
    model_dir = Path(lattice_path) / "bmad" / "models" / "cu_hxr"
    os.chdir(model_dir)
    try:
        yield
    finally:
        os.chdir(previous_cwd)

def _create_cu_hxr_staged_model(start_element="OTR2", end_element="TD11"):
    from virtual_accelerator.models.staged_model import get_cu_hxr_staged_model
    return get_cu_hxr_staged_model(start_element=start_element, end_element=end_element, track_beam=True)


def _create_cu_hxr_bmad_model(start_element="OTR2", end_element="TD11"):
    from virtual_accelerator.models.cu_hxr import get_cu_hxr_bmad_model
    return get_cu_hxr_bmad_model(start_element=start_element, end_element=end_element, track_beam=True)

MODELS = {
    "cu_hxr_staged": _create_cu_hxr_staged_model,
    "cu_hxr_bmad": _create_cu_hxr_bmad_model,
}

MODEL_INFO = {
    "cu_hxr_staged": {"description": "Staged LUMEModel chaining the LCLS Cu Injector ML model (predicting at OTR2) with a Bmad beamline simulation tracking from OTR2 to TD11."},
    "cu_hxr_bmad": {"description": "LUMEModel of Bmad linac simulation tracking from OTR2 to TD11."},
}


# ---------------------------------------------------------------------------
# Data container returned by every source on each "shot"
# ---------------------------------------------------------------------------


@dataclass
class BeamFrame:
    """One rendered dashboard frame with values converted into display units."""

    screen_key: str
    screen_label: str
    x_axis_value: float | datetime
    xrms_um: float
    yrms_um: float
    sigma_z_um: float
    norm_emit_x_um_rad: float
    norm_emit_y_um_rad: float
    image: Optional[np.ndarray] = None
    image_message: str = ""
    image_caption: str = ""
    beam_x_um: Optional[np.ndarray] = None
    beam_px_evc: Optional[np.ndarray] = None
    twiss_s: Optional[np.ndarray] = None
    twiss_a_beta: Optional[np.ndarray] = None
    twiss_b_beta: Optional[np.ndarray] = None
    title_suffix: str = ""
    frame_index: int = 0
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Abstract base class – implement this to swap sources
# ---------------------------------------------------------------------------


class ModelImageSource:
    """Read beam images, beam phase space, and scalars from a staged model."""

    thread_safe = False

    def __init__(
        self,
        model_name: str,
        max_scatter_points: int = 3000,
        reset_values: Optional[dict[str, object]] = None,
        twiss_s_pv: str = "s",
        twiss_a_beta_pv: str = "x.beta",
        twiss_b_beta_pv: str = "y.beta",
    ):
        self.model_name = model_name
        self.model = MODELS.get(model_name)()
        self.max_scatter_points = max_scatter_points
        self.reset_values = reset_values or {}
        self.twiss_s_pv = twiss_s_pv
        self.twiss_a_beta_pv = twiss_a_beta_pv
        self.twiss_b_beta_pv = twiss_b_beta_pv
        self._writable_variable_names = {
            name
            for name, variable in self.model.supported_variables.items()
            if not getattr(variable, "read_only", False)
            and name not in EXCLUDED_EPICS_PVS
        }
        self.reset_values = reset_values or {}
        self.lattice_path = resolve_lcls_lattice_path()
        os.environ["LCLS_LATTICE"] = self.lattice_path

    @classmethod
    def create_default(cls):
        lattice_path = resolve_lcls_lattice_path()
        os.environ["LCLS_LATTICE"] = lattice_path
        return cls(model_name="cu_hxr_staged", reset_values={})

    def reset(self) -> None:
        if self.reset_values:
            self.model.set(self.reset_values)

    def _filter_writable_updates(
        self, control_updates: Mapping[str, float]
    ) -> dict[str, float]:
        return {
            key: float(value)
            for key, value in control_updates.items()
            if key in self._writable_variable_names
        }

    def snapshot(
        self,
        screen_key: str,
        control_updates: Optional[Mapping[str, float]] = None,
        x_axis_value: float | datetime = 0.0,
        frame_index: int = 0,
        image_caption: str = "",
        title_suffix: str = "",
    ) -> BeamFrame:
        screen = SCREEN_CONFIGS[screen_key]
        if control_updates:
            writable_updates = self._filter_writable_updates(control_updates)
            if writable_updates:
                self.model.set(writable_updates)

        pvs: list[str] = [
            screen.particle_source,
            self.twiss_s_pv,
            self.twiss_a_beta_pv,
            self.twiss_b_beta_pv,
        ]
        if screen.image_pv:
            pvs.insert(0, screen.image_pv)
        if screen.scalar_mode == "pvs" and self.model_name == "cu_hxr_staged":
            pvs.extend(
                [
                    screen.xrms_pv,
                    screen.yrms_pv,
                    screen.sigma_z_pv,
                    screen.norm_emit_x_pv,
                    screen.norm_emit_y_pv,
                ]
            )

        result = self.model.get(pvs)
        beam = result.get(screen.particle_source)
        image = result.get(screen.image_pv) if screen.image_pv else None
        xrms_um, yrms_um, sigma_z_um, emit_x_um, emit_y_um = self._extract_scalars(
            screen, result, beam
        )
        beam_x_um, beam_px_evc = self._extract_scatter(beam)

        twiss_s = result.get(self.twiss_s_pv)
        twiss_a_beta = result.get(self.twiss_a_beta_pv)
        twiss_b_beta = result.get(self.twiss_b_beta_pv)

        return BeamFrame(
            screen_key=screen.key,
            screen_label=screen.label,
            x_axis_value=x_axis_value,
            xrms_um=xrms_um,
            yrms_um=yrms_um,
            sigma_z_um=sigma_z_um,
            norm_emit_x_um_rad=emit_x_um,
            norm_emit_y_um_rad=emit_y_um,
            image=image,
            image_message=screen.image_message if image is None else "",
            image_caption=image_caption,
            beam_x_um=beam_x_um,
            beam_px_evc=beam_px_evc,
            twiss_s=None if twiss_s is None else np.asarray(twiss_s, dtype=float),
            twiss_a_beta=None if twiss_a_beta is None else np.asarray(twiss_a_beta, dtype=float),
            twiss_b_beta=None if twiss_b_beta is None else np.asarray(twiss_b_beta, dtype=float),
            title_suffix=title_suffix,
            frame_index=frame_index,
            timestamp=time.time(),
        )

    def _extract_scalars(self, screen, result: Mapping[str, object], beam) -> tuple[float, float, float, float, float]:
        if screen.scalar_mode == "pvs" and self.model_name == "cu_hxr_staged":
            return (
                float(result[screen.xrms_pv]),
                float(result[screen.yrms_pv]),
                float(result[screen.sigma_z_pv]) * 1e6,
                float(result[screen.norm_emit_x_pv]) * 1e6,
                float(result[screen.norm_emit_y_pv]) * 1e6,
            )

        if beam is None:
            return (0.0, 0.0, 0.0, 0.0, 0.0)

        # for bmad-only model, OTR2 scalars are from "input_beam" particle distribution
        return (
            float(beam["sigma_x"]) * 1e6,
            float(beam["sigma_y"]) * 1e6,
            float(beam["sigma_z"]) * 1e6,
            float(beam["norm_emit_x"]) * 1e6,
            float(beam["norm_emit_y"]) * 1e6,
        )

    def _extract_scatter(self, beam) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if beam is None:
            return (None, None)
        x = np.asarray(beam["x"], dtype=float)
        px = np.asarray(beam["px"], dtype=float)
        if len(x) > self.max_scatter_points:
            indices = np.linspace(0, len(x) - 1, self.max_scatter_points, dtype=int)
            x = x[indices]
            px = px[indices]
        return (x * 1e6, px)
    