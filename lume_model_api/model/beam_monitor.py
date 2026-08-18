"""Shared beam data abstractions and staged-model sources for the marimo apps."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional

import numpy as np

from lume_visualizations.config import resolve_lcls_lattice_path
from lume_visualizations.registry import ModelSpec, get_spec

# Screen images are a 2D histogram of the tracked macroparticles (~1000), so at the
# native 17.06 um pixel pitch they are sparse single-count noise. Convolving with a
# Gaussian reproduces the documented incoherent OTR image formation (image = PSF *
# transverse density; Loos et al., FEL08, THBAU01). The LCLS OTR optical PSF (FWHM
# 1.44 lambda/theta ~= 4-11 um) is sub-pixel, so the resolution is pixel-limited:
# sigma = 1 px is the physically honest kernel. Larger only de-noises the finite
# particle sample; it does not model the instrument.
OTR_PSF_SIGMA_PX = 1.0


def _apply_screen_psf(image: Optional[np.ndarray], sigma_px: float = OTR_PSF_SIGMA_PX):
    """Convolve a raw screen histogram with the pixel-limited OTR PSF, renormalized."""
    if image is None or sigma_px <= 0:
        return image
    from scipy.ndimage import gaussian_filter

    smoothed = gaussian_filter(np.asarray(image, dtype=float), sigma=sigma_px)
    peak = float(smoothed.max())
    return smoothed / peak if peak > 0 else smoothed


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
    # Full phase-space distribution for the programmatic API (opt-in). Shape:
    # {"n": int, "units": {coord: unit}, "coords": {coord: np.ndarray}}.
    distribution: Optional[dict] = None
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
    ):
        self.model_name = model_name
        self.spec: ModelSpec = get_spec(model_name)
        # LCLS_LATTICE must be set before the model (Bmad) is built.
        self.lattice_path = resolve_lcls_lattice_path()
        os.environ["LCLS_LATTICE"] = self.lattice_path
        self.model = self.spec.make_model()
        self.max_scatter_points = max_scatter_points
        self.reset_values = reset_values or {}
        self.screens = self.spec.screens
        self.baseline = dict(self.spec.baseline)
        self.twiss_s_pv = self.spec.twiss_s_pv
        self.twiss_a_beta_pv = self.spec.twiss_a_beta_pv
        self.twiss_b_beta_pv = self.spec.twiss_b_beta_pv
        self._writable_variable_names = {
            name
            for name, variable in self.model.supported_variables.items()
            if not getattr(variable, "read_only", False)
            and name not in self.spec.excluded_pvs
        }

    @classmethod
    def create_default(cls):
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
        include_distribution: bool = False,
        max_particles: Optional[int] = None,
    ) -> BeamFrame:
        screen = self.screens[screen_key]
        # Baseline-merge: overlay the request on the known baseline so evaluate is
        # history-independent on any (pooled) instance — a missing key falls back to
        # the design default, never a previous request's value.
        effective = {**self.baseline, **(control_updates or {})}
        writable_updates = self._filter_writable_updates(effective)
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
        if screen.scalar_mode == "pvs":
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
        image = _apply_screen_psf(result.get(screen.image_pv)) if screen.image_pv else None
        xrms_um, yrms_um, sigma_z_um, emit_x_um, emit_y_um = self._extract_scalars(
            screen, result, beam
        )
        beam_x_um, beam_px_evc = self._extract_scatter(beam)
        distribution = (
            self._extract_distribution(beam, max_particles) if include_distribution else None
        )

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
            distribution=distribution,
            twiss_s=None if twiss_s is None else np.asarray(twiss_s, dtype=float),
            twiss_a_beta=None if twiss_a_beta is None else np.asarray(twiss_a_beta, dtype=float),
            twiss_b_beta=None if twiss_b_beta is None else np.asarray(twiss_b_beta, dtype=float),
            title_suffix=title_suffix,
            frame_index=frame_index,
            timestamp=time.time(),
        )

    def _extract_scalars(self, screen, result: Mapping[str, object], beam) -> tuple[float, float, float, float, float]:
        if screen.scalar_mode == "pvs":
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

    # Keys/units for the phase-space distribution surfaced by the v1 API. Verified
    # against beamphysics.ParticleGroup (the object the model returns): positions in
    # metres, momenta in eV/c, weight (charge) in Coulombs. Missing keys are skipped.
    _DIST_COORDS = ("x", "px", "y", "py", "z", "pz")
    _DIST_UNITS = {
        "x": "m", "y": "m", "z": "m",
        "px": "eV/c", "py": "eV/c", "pz": "eV/c",
        "weight": "C",
    }

    def _extract_distribution(self, beam, max_particles: Optional[int]) -> Optional[dict]:
        if beam is None:
            return None
        coords: dict[str, np.ndarray] = {}
        for key in (*self._DIST_COORDS, "weight"):
            try:
                coords[key] = np.asarray(beam[key], dtype=float)
            except Exception:  # coordinate not present on this beam object
                continue
        if not coords:
            return None
        n = len(next(iter(coords.values())))
        if max_particles and n > max_particles:
            indices = np.linspace(0, n - 1, max_particles, dtype=int)
            coords = {k: v[indices] for k, v in coords.items()}
            n = int(max_particles)
        units = {k: self._DIST_UNITS.get(k, "") for k in coords}
        return {"n": int(n), "units": units, "coords": coords}
    