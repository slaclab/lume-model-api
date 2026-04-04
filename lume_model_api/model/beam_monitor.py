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
    EXTRA_MACHINE_INPUTS,
    MODEL_INPUT_NAMES,
    SCREEN_CONFIGS,
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


def _create_safe_cu_hxr_staged_model(lattice_path: str, start_element="OTR2", end_element="OTR4"):
    from pytao import Tao
    from lume_bmad.model import LUMEBmadModel
    from virtual_accelerator.bmad.cu_transformer import CUBmadTransformer
    from virtual_accelerator.bmad.variables import (
        get_cu_hxr_screen_variables,
        get_variables,
    )
    from virtual_accelerator.models import cu_hxr as va_cu_hxr
    from virtual_accelerator.models.staged_model import StagedModel
    from virtual_accelerator.surrogates.injector_surrogate import InjectorSurrogate
    from virtual_accelerator.utils.variables import (
        get_epics_to_name_or_overlay_mapping,
        split_control_and_observable,
    )

    init_file = Path(lattice_path) / "bmad" / "models" / "cu_hxr" / "tao.init"

    # Initializing Tao with the OTR2:OTR4 sliced lattice segfaults in this
    # container runtime. Use the full lattice and query the OTR4 outputs from it.
    with _tao_model_workdir(lattice_path):
        tao = Tao(f"-init {init_file} -noplot -slice_lattice {start_element}:{end_element}")

    control_name_to_element_name = get_epics_to_name_or_overlay_mapping()
    variables = get_variables(tao)
    control_variables, observable_variables = split_control_and_observable(variables)

    screens = ["OTR3", "OTR4", "OTR11", "OTR12", "OTR21"]
    control_variables, screen_attributes, used_screens = get_cu_hxr_screen_variables(
        tao, control_variables, screens
    )

    transformer = CUBmadTransformer(
        control_name_to_bmad=control_name_to_element_name,
        screen_attributes=screen_attributes,
    )
    bmad_model = LUMEBmadModel(
        tao=tao,
        control_variables=control_variables,
        output_variables=observable_variables,
        transformer=transformer,
        dump_locations=used_screens,
    )

    beam_path = (Path(va_cu_hxr.__file__).parent / ".." / "bmad" / "bmad_set_beam2000_pg").resolve()
    bmad_model.tao.cmd(f"set beam_init position_file = {beam_path}")
    bmad_model.set({"track_type": 1})

    return StagedModel([InjectorSurrogate(), bmad_model])


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


class StagedModelImageSource:
    """Read beam images, beam phase space, and scalars from a staged model."""

    thread_safe = False

    def __init__(
        self,
        model,
        max_scatter_points: int = 3000,
        reset_values: Optional[dict[str, object]] = None,
        twiss_s_pv: str = "s",
        twiss_a_beta_pv: str = "a.beta",
        twiss_b_beta_pv: str = "b.beta",
    ):
        self.model = model
        self.max_scatter_points = max_scatter_points
        self.reset_values = reset_values or {}
        self.twiss_s_pv = twiss_s_pv
        self.twiss_a_beta_pv = twiss_a_beta_pv
        self.twiss_b_beta_pv = twiss_b_beta_pv
        self._writable_variable_names = {
            name
            for name, variable in self.model.supported_variables.items()
            if not getattr(variable, "read_only", False)
        }

    @classmethod
    def create_default(cls):
        if _is_virtualapple_emulated_x86():
            return SyntheticLiveImageSource()

        lattice_path = resolve_lcls_lattice_path()
        os.environ["LCLS_LATTICE"] = lattice_path
        model = _create_safe_cu_hxr_staged_model(lattice_path)
        return cls(model=model, reset_values={})

    def reset(self) -> None:
        if self.reset_values:
            self.model.set(self.reset_values)

    def get_model_input_names(self) -> list[str]:
        torch_model = self.model.lume_model_instances[0].model.torch_model
        return list(torch_model.input_names)

    def get_current_inputs(self, input_names: Optional[list[str]] = None) -> dict[str, float]:
        names = input_names or self.get_model_input_names()
        values = self.model.get(names)
        return {name: float(values[name]) for name in names}

    def get_writable_model_input_names(self) -> list[str]:
        return [
            name for name in self.get_model_input_names() if name in self._writable_variable_names
        ]

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


# ---------------------------------------------------------------------------
# Concrete implementation: virtual-accelerator StagedModel
# ---------------------------------------------------------------------------


def default_manual_input_values() -> dict[str, float]:
    return {name: 0.0 for name in MODEL_INPUT_NAMES}


def _is_virtualapple_emulated_x86() -> bool:
    if platform.machine().lower() != "x86_64":
        return False

    cpuinfo_path = Path("/proc/cpuinfo")
    if not cpuinfo_path.exists():
        return False

    try:
        cpuinfo = cpuinfo_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    return "VirtualApple" in cpuinfo


class SyntheticLiveImageSource:
    """Container-safe fallback source for amd64-on-Apple emulation."""

    thread_safe = True

    def __init__(
        self,
        image_shape: tuple[int, int] = (256, 256),
        max_scatter_points: int = 3000,
    ):
        from lume_visualizations.fake_epics_ioc import FAKE_INPUT_SPECS

        defaults = {spec.pv_name: float(spec.default) for spec in FAKE_INPUT_SPECS}
        defaults.setdefault("CAMR:IN20:186:R_DIST", 423.867825)
        self.default_inputs = {
            name: float(defaults.get(name, 0.0))
            for name in [*MODEL_INPUT_NAMES, *EXTRA_MACHINE_INPUTS]
        }
        self.current_inputs = dict(self.default_inputs)
        self.image_shape = image_shape
        self.max_scatter_points = max_scatter_points

    def reset(self) -> None:
        self.current_inputs = dict(self.default_inputs)

    def get_model_input_names(self) -> list[str]:
        return list(MODEL_INPUT_NAMES)

    def get_current_inputs(self, input_names: Optional[list[str]] = None) -> dict[str, float]:
        names = input_names or self.get_model_input_names()
        return {name: float(self.current_inputs[name]) for name in names}

    def get_writable_model_input_names(self) -> list[str]:
        return list(MODEL_INPUT_NAMES)

    def snapshot(
        self,
        screen_key: str,
        control_updates: Optional[Mapping[str, float]] = None,
        x_axis_value: float | datetime = 0.0,
        frame_index: int = 0,
        image_caption: str = "",
        title_suffix: str = "",
    ) -> BeamFrame:
        if control_updates:
            for key, value in control_updates.items():
                if key in self.current_inputs:
                    self.current_inputs[key] = float(value)

        screen = SCREEN_CONFIGS[screen_key]
        screen_scale = {"OTR2": 0.92, "OTR3": 1.0, "OTR4": 1.08}.get(screen_key, 1.0)
        focus_term = (
            0.25 * self.current_inputs["QUAD:IN20:361:BCTRL"]
            - 0.18 * self.current_inputs["QUAD:IN20:371:BCTRL"]
            + 0.12 * self.current_inputs["QUAD:IN20:425:BCTRL"]
            - 0.09 * self.current_inputs["QUAD:IN20:441:BCTRL"]
        )
        phase_term = 0.05 * (
            self.current_inputs["ACCL:IN20:300:L0A_PDES"]
            + self.current_inputs["ACCL:IN20:400:L0B_PDES"]
        )
        solenoid_term = 40.0 * self.current_inputs["SOLN:IN20:121:BCTRL"]
        xrms_base = self.current_inputs["CAMR:IN20:186:XRMS"]
        yrms_base = self.current_inputs["CAMR:IN20:186:YRMS"]

        xrms_um = max(60.0, screen_scale * xrms_base + solenoid_term + 22.0 * np.sin(focus_term))
        yrms_um = max(60.0, screen_scale * yrms_base - 0.6 * solenoid_term + 18.0 * np.cos(focus_term))
        sigma_z_um = max(200.0, 140.0 * self.current_inputs["Pulse_length"] + 15.0 * np.cos(phase_term))
        norm_emit_x_um_rad = max(0.08, 0.42 + 0.03 * np.sin(focus_term + 0.4 * phase_term))
        norm_emit_y_um_rad = max(0.08, 0.39 + 0.03 * np.cos(focus_term - 0.3 * phase_term))

        rng = np.random.default_rng(seed=2719 + frame_index)
        beam_x_um = rng.normal(0.0, xrms_um, size=self.max_scatter_points)
        beam_px_evc = rng.normal(0.0, max(0.02, yrms_um / 1800.0), size=self.max_scatter_points)

        twiss_s = np.linspace(0.0, 40.0, 200)
        twiss_a_beta = 5.5 + 1.3 * np.sin(twiss_s / 6.0 + 0.1 * focus_term)
        twiss_b_beta = 7.8 + 1.8 * np.cos(twiss_s / 7.5 - 0.08 * focus_term)

        image = None
        if screen.image_pv:
            nrow, ncol = self.image_shape
            cy, cx = nrow / 2, ncol / 2
            sigma_x_px = max(4.0, xrms_um / 18.0)
            sigma_y_px = max(4.0, yrms_um / 18.0)
            y_idx, x_idx = np.mgrid[0:nrow, 0:ncol]
            image = np.exp(
                -0.5 * ((x_idx - cx) / sigma_x_px) ** 2
                - 0.5 * ((y_idx - cy) / sigma_y_px) ** 2
            )
            image += rng.normal(0.0, 0.02, size=image.shape)
            image = np.clip(image, 0.0, None)

        return BeamFrame(
            screen_key=screen.key,
            screen_label=screen.label,
            x_axis_value=x_axis_value,
            xrms_um=float(xrms_um),
            yrms_um=float(yrms_um),
            sigma_z_um=float(sigma_z_um),
            norm_emit_x_um_rad=float(norm_emit_x_um_rad),
            norm_emit_y_um_rad=float(norm_emit_y_um_rad),
            image=image,
            image_message=screen.image_message if image is None else "",
            image_caption=image_caption,
            beam_x_um=beam_x_um,
            beam_px_evc=beam_px_evc,
            twiss_s=twiss_s,
            twiss_a_beta=twiss_a_beta,
            twiss_b_beta=twiss_b_beta,
            title_suffix=title_suffix,
            frame_index=frame_index,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Concrete implementation: synthetic / mock source (no hardware required)
# ---------------------------------------------------------------------------


class MockImageSource:
    """
    Generates synthetic beam images and scalars for development / CI.

    The beam spot is a 2-D Gaussian whose sigma_x grows with |scan_value|,
    mimicking a real quadrupole scan.

    Parameters
    ----------
    image_shape:
        (nRow, nCol) of the fake OTR image.
    n_particles:
        Number of particles in the phase-space scatter.
    noise_level:
        Gaussian noise amplitude added to the image.
    sleep_s:
        Artificial latency per step to simulate computation time.
    """

    thread_safe = True

    def __init__(
        self,
        image_shape: tuple[int, int] = (256, 256),
        n_particles: int = 3000,
        noise_level: float = 0.02,
        sleep_s: float = 0.2,
    ):
        self.image_shape = image_shape
        self.n_particles = n_particles
        self.noise_level = noise_level
        self.sleep_s = sleep_s

    def snapshot(
        self,
        screen_key: str,
        x_axis_value: float | datetime,
        frame_index: int,
        image_caption: str,
        title_suffix: str,
    ) -> BeamFrame:
        if self.sleep_s > 0:
            time.sleep(self.sleep_s)

        nrow, ncol = self.image_shape
        cy, cx = nrow / 2, ncol / 2

        # Spot size varies with quad strength
        position_value = frame_index if isinstance(x_axis_value, datetime) else float(x_axis_value)
        sigma_x_px = max(4.0, 20.0 + 2.5 * position_value)
        sigma_y_px = max(4.0, 20.0 - 1.5 * position_value)

        y_idx, x_idx = np.mgrid[0:nrow, 0:ncol]
        image = np.exp(
            -0.5 * ((x_idx - cx) / sigma_x_px) ** 2
            - 0.5 * ((y_idx - cy) / sigma_y_px) ** 2
        )
        image += np.random.normal(0, self.noise_level, image.shape)
        image = np.clip(image, 0, None)

        # Scalar diagnostics
        res_um = 12.0  # µm/px (approximate)
        xrms = sigma_x_px * res_um
        yrms = sigma_y_px * res_um
        norm_emit_x = max(1e-7, 5e-7 + position_value * 2e-8)
        norm_emit_y = max(1e-7, 5e-7 - position_value * 1.5e-8)
        sigma_z = 4.6e-4

        # Phase-space scatter
        bx = np.random.normal(0, xrms, self.n_particles)
        bpx = np.random.normal(0, yrms, self.n_particles)

        # Simple synthetic Twiss functions along s
        twiss_s = np.linspace(0.0, 40.0, 200)
        twiss_a_beta = 6.0 + 1.5 * np.sin(twiss_s / 6.0 + 0.15 * position_value)
        twiss_b_beta = 8.0 + 2.0 * np.cos(twiss_s / 7.5 - 0.12 * position_value)

        return BeamFrame(
            screen_key=screen_key,
            screen_label=screen_key,
            x_axis_value=x_axis_value,
            xrms_um=xrms,
            yrms_um=yrms,
            sigma_z_um=sigma_z * 1e6,
            norm_emit_x_um_rad=norm_emit_x * 1e6,
            norm_emit_y_um_rad=norm_emit_y * 1e6,
            image=image,
            image_caption=image_caption,
            beam_x_um=bx,
            beam_px_evc=bpx,
            twiss_s=twiss_s,
            twiss_a_beta=twiss_a_beta,
            twiss_b_beta=twiss_b_beta,
            title_suffix=title_suffix,
            frame_index=frame_index,
            timestamp=time.time(),
        )

    def get_frame(self, scan_pv: str, scan_value: float, step_index: int) -> BeamFrame:
        return self.snapshot(
            screen_key="OTR4",
            x_axis_value=scan_value,
            frame_index=step_index,
            image_caption=f"{scan_pv} = {scan_value:.2f} kG",
            title_suffix=f"Mock step {step_index + 1}",
        )

