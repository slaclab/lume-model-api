"""Synthetic beam source for fast frontend iteration (LUME_MOCK=1).

Produces ``BeamFrame`` objects with the same shape as
``lume_visualizations.beam_monitor.ModelImageSource.snapshot`` but with no torch /
virtual_accelerator / Bmad dependency, so the whole UI can be developed and tested
in the plain ``.venv``. Outputs respond to a couple of input knobs so slider changes
are visibly reflected.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Mapping, Optional

import numpy as np

from lume_visualizations.beam_monitor import BeamFrame
from lume_visualizations.registry import get_spec

_IMG_ROWS = 240
_IMG_COLS = 320
_N_SCATTER = 3000
# Optional synthetic per-eval latency (seconds) to simulate model cost for load tests.
_MOCK_DELAY = float(os.environ.get("LUME_MOCK_DELAY", "0"))


class MockImageSource:
    """Drop-in stand-in for ModelImageSource that fabricates plausible frames.

    Carries the model's baseline and applies baseline-merge just like the real
    source, so `{}` inputs are deterministic and the mock exercises the same
    stateless contract.
    """

    def __init__(self, model_name: str = "cu_hxr_staged", **_ignored) -> None:
        self.model_name = model_name
        self.spec = get_spec(model_name)
        self.screens = self.spec.screens
        self.baseline = dict(self.spec.baseline)
        self._writable_variable_names = set(self.spec.input_pvs)
        self._rng = np.random.default_rng(2719)

    def reset(self) -> None:  # noqa: D401 - parity with ModelImageSource
        return None

    def _knob(self, effective: Mapping[str, float]) -> float:
        """Map inputs to a bounded [0, 1] factor so sliders visibly change output."""
        soln = float(effective.get("SOLN:IN20:121:BCTRL", 0.478))
        quad = float(effective.get("QUAD:IN20:525:BCTRL", -3.2))
        # Normalize each into ~[0,1] over its slider span, then average.
        soln_n = (soln - 0.377) / (0.498 - 0.377)
        quad_n = (quad - (-7.56)) / ((-1.08) - (-7.56))
        return float(np.clip(0.5 * soln_n + 0.5 * quad_n, 0.0, 1.0))

    def snapshot(
        self,
        screen_key: str,
        control_updates: Optional[Mapping[str, float]] = None,
        x_axis_value: float | datetime = 0.0,
        frame_index: int = 0,
        image_caption: str = "",
        title_suffix: str = "",
    ) -> BeamFrame:
        if _MOCK_DELAY:
            time.sleep(_MOCK_DELAY)
        screen = self.screens[screen_key]
        effective = {**self.baseline, **(control_updates or {})}
        knob = self._knob(effective)

        # Beam widths (µm) driven by the knob so the UI reacts to sliders.
        sigma_x = 40.0 + 120.0 * knob
        sigma_y = 55.0 + 90.0 * (1.0 - knob)
        sigma_z = 30.0 + 20.0 * knob
        emit_x = 0.4 + 0.6 * knob
        emit_y = 0.4 + 0.6 * (1.0 - knob)

        has_image = screen.image_pv is not None
        image = None
        if has_image:
            image = self._gaussian_image(sigma_x, sigma_y)

        # Phase-space scatter in (x µm, px eV/c).
        x = self._rng.normal(0.0, sigma_x, _N_SCATTER)
        px = self._rng.normal(0.0, 1.5e4 * (0.5 + knob), _N_SCATTER) + 3.0e3 * (x / max(sigma_x, 1.0))

        # Twiss beta functions along s.
        s = np.linspace(0.0, 20.0, 120)
        beta_x = 8.0 + 6.0 * np.sin(0.4 * s + knob) ** 2 + 0.5 * s
        beta_y = 7.0 + 5.0 * np.cos(0.35 * s + knob) ** 2 + 0.4 * s

        return BeamFrame(
            screen_key=screen.key,
            screen_label=screen.label,
            x_axis_value=x_axis_value,
            xrms_um=float(sigma_x),
            yrms_um=float(sigma_y),
            sigma_z_um=float(sigma_z),
            norm_emit_x_um_rad=float(emit_x),
            norm_emit_y_um_rad=float(emit_y),
            image=image,
            image_message="" if has_image else screen.image_message,
            image_caption=image_caption,
            beam_x_um=x,
            beam_px_evc=px,
            twiss_s=s,
            twiss_a_beta=beta_x,
            twiss_b_beta=beta_y,
            title_suffix=title_suffix or "mock",
            frame_index=frame_index,
            timestamp=time.time(),
        )

    def _gaussian_image(self, sigma_x_um: float, sigma_y_um: float) -> np.ndarray:
        rows, cols = _IMG_ROWS, _IMG_COLS
        yy, xx = np.mgrid[0:rows, 0:cols]
        cy, cx = rows / 2.0, cols / 2.0
        # Map µm widths to pixel widths (arbitrary but responsive scale).
        px_x = max(sigma_x_um / 2.0, 3.0)
        px_y = max(sigma_y_um / 2.0, 3.0)
        blob = np.exp(-(((xx - cx) ** 2) / (2 * px_x**2) + ((yy - cy) ** 2) / (2 * px_y**2)))
        noise = self._rng.normal(0.0, 0.02, size=blob.shape)
        return np.clip(blob + noise, 0.0, None).astype(np.float32)
