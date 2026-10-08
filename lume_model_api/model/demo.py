"""A small real `LUMEModel` so the service runs, and CI tests, with no heavy stack.

This is not a mock of the API: it is a model the API hosts exactly like any other one.
`LUME_MODELS=demo` goes through the same `describe()`, `evaluate()` and serialization path as
`cu_hxr_staged`, which is what makes a green test run mean something. The previous mock
lived in the api layer and derived every screen from one knob, so switching screens changed
nothing and the bug was invisible until someone ran the real model.

It exercises every branch the generic code has:

- an input with both `default_value` and `value_range` (`range_source="model"`)
- an input with no `value_range` (`range_source="derived"`)
- an input whose range is a single point (`constant=true`, excluded from the baseline)
- two `ParticleGroupVariable` screens, one with an image and one without
- array outputs that are not images (`s`, `x.beta`, `y.beta`)
- a plain read-only scalar

Physics is illustrative, not predictive. It is deterministic given the inputs (seeded
per-screen generators), which is what makes the wire-shape and subsampling tests stable.
"""

from __future__ import annotations

import numpy as np
from lume.model import LUMEModel
from lume.variables import NDVariable, ParticleGroupVariable, ScalarVariable

QUAD = "DEMO:QUAD:1:BCTRL"
SOLENOID = "DEMO:SOLN:1:BCTRL"
CORRECTOR = "DEMO:XCOR:1:BCTRL"
CHARGE = "DEMO:CHARGE"

SCREEN_A_BEAM = "OTR_A_beam"
SCREEN_B_BEAM = "OTR_B_beam"
SCREEN_B_IMAGE = "DEMO:OTRB:Image:ArrayData"
SCREEN_A_XRMS = "DEMO:OTRA:XRMS"
TWISS_S = "s"
TWISS_BETA_X = "x.beta"
TWISS_BETA_Y = "y.beta"

IMAGE_SHAPE = (240, 320)
TWISS_POINTS = 64
N_PARTICLES = 2000
# Roughly the LCLS injector energy, so the eV/c momenta look plausible next to a real model.
BEAM_MOMENTUM_EV = 1.35e8
# Half-width of the image sensor in metres, i.e. the histogram extent.
IMAGE_HALF_WIDTH_M = 1.2e-3


class ElementNDVariable(NDVariable):
    """An `NDVariable` that names the beamline element it belongs to.

    virtual-accelerator's image variables carry `element_name`, and `introspect._screens`
    pairs an image with a screen by matching it. Without the attribute the screen reports
    `image: null`, so the demo model needs it to exercise the paired case.
    """

    element_name: str | None = None


class DemoBeamModel(LUMEModel):
    """Dependency-light demonstration beamline with two screens."""

    def __init__(self) -> None:
        self._variables: dict[str, object] = {
            QUAD: ScalarVariable(
                name=QUAD, default_value=2.0, value_range=(-5.0, 5.0), unit="kG"
            ),
            SOLENOID: ScalarVariable(
                name=SOLENOID, default_value=0.45, value_range=(0.3, 0.6), unit="kG*m"
            ),
            # No value_range on purpose: this is the input whose range gets derived.
            CORRECTOR: ScalarVariable(name=CORRECTOR, default_value=0.0, unit="kG-m"),
            # A single-point range, which introspection reports as constant.
            CHARGE: ScalarVariable(
                name=CHARGE, default_value=0.25, value_range=(0.25, 0.25), unit="nC"
            ),
            SCREEN_A_BEAM: ParticleGroupVariable(name=SCREEN_A_BEAM, read_only=True),
            SCREEN_B_BEAM: ParticleGroupVariable(name=SCREEN_B_BEAM, read_only=True),
            SCREEN_B_IMAGE: ElementNDVariable(
                name=SCREEN_B_IMAGE,
                read_only=True,
                shape=IMAGE_SHAPE,
                element_name="OTR_B",
                unit="counts",
            ),
            SCREEN_A_XRMS: ScalarVariable(name=SCREEN_A_XRMS, read_only=True, unit="m"),
            TWISS_S: NDVariable(name=TWISS_S, read_only=True, shape=(TWISS_POINTS,), unit="m"),
            TWISS_BETA_X: NDVariable(
                name=TWISS_BETA_X, read_only=True, shape=(TWISS_POINTS,), unit="m"
            ),
            TWISS_BETA_Y: NDVariable(
                name=TWISS_BETA_Y, read_only=True, shape=(TWISS_POINTS,), unit="m"
            ),
        }
        self._controls: dict[str, float] = {}
        self.reset()

    # --- LUMEModel interface -----------------------------------------------------

    @property
    def supported_variables(self) -> dict:
        return self._variables

    def reset(self) -> None:
        self._controls = {
            name: float(variable.default_value)
            for name, variable in self._variables.items()
            if not variable.read_only
        }
        self._run()

    def _set(self, values: dict) -> None:
        self._controls.update({name: float(value) for name, value in values.items()})
        self._run()

    def _get(self, names: list[str]) -> dict:
        return {name: self._state[name] for name in names}

    # --- the "simulation" --------------------------------------------------------

    def _run(self) -> None:
        beam_a = self._beam("OTR_A", seed=11, drift=1.0)
        beam_b = self._beam("OTR_B", seed=29, drift=2.4)
        self._state = {
            SCREEN_A_BEAM: beam_a,
            SCREEN_B_BEAM: beam_b,
            SCREEN_B_IMAGE: self._image(beam_b),
            SCREEN_A_XRMS: float(beam_a["sigma_x"]),
            **self._twiss(),
        }

    def _envelope(self, drift: float) -> tuple[float, float, float]:
        """Beam sizes and the horizontal offset, as functions of the three live knobs."""
        quad = self._controls[QUAD]
        solenoid = self._controls[SOLENOID]
        corrector = self._controls[CORRECTOR]
        # A stronger solenoid focuses both planes, a quadrupole trades one plane for the
        # other, and the imbalance grows with the drift to the screen. The clamp keeps the
        # sizes positive across the whole declared quad range.
        focus = max(0.15, 1.0 + 0.12 * quad * drift)
        common = 1.5e-4 * (0.45 / solenoid) * drift
        offset = corrector * 8.0e-4 * drift
        return common / focus, common * focus, offset

    def _beam(self, screen: str, seed: int, drift: float):
        # openpmd-beamphysics renamed its top-level package, so accept either name. lume's
        # own ParticleGroupVariable does the same, and it is what decides whether the object
        # built here validates.
        try:
            from beamphysics import ParticleGroup
        except ImportError:
            from pmd_beamphysics import ParticleGroup

        sigma_x, sigma_y, offset = self._envelope(drift)
        total_charge = self._controls[CHARGE] * 1e-9  # nC -> C
        rng = np.random.default_rng(seed)
        n = N_PARTICLES
        divergence = 2.0e-5 * BEAM_MOMENTUM_EV
        return ParticleGroup(
            data=dict(
                x=rng.normal(offset, sigma_x, n),
                px=rng.normal(0.0, divergence, n),
                y=rng.normal(0.0, sigma_y, n),
                py=rng.normal(0.0, divergence, n),
                # A bunch length in z with t = 0, so sigma_z is non-zero and the payload
                # exercises the longitudinal coordinates too.
                z=rng.normal(0.0, 1.0e-4, n),
                pz=rng.normal(BEAM_MOMENTUM_EV, 1.0e-3 * BEAM_MOMENTUM_EV, n),
                t=np.zeros(n),
                weight=np.full(n, total_charge / n),
                status=np.ones(n, dtype=int),
                species="electron",
            )
        )

    def _image(self, beam) -> np.ndarray:
        """A screen image as a 2-D histogram of the macroparticles, rows = y, cols = x."""
        edges_y = np.linspace(-IMAGE_HALF_WIDTH_M, IMAGE_HALF_WIDTH_M, IMAGE_SHAPE[0] + 1)
        edges_x = np.linspace(-IMAGE_HALF_WIDTH_M, IMAGE_HALF_WIDTH_M, IMAGE_SHAPE[1] + 1)
        counts, _, _ = np.histogram2d(beam["y"], beam["x"], bins=(edges_y, edges_x))
        return np.ascontiguousarray(counts, dtype=np.float64)

    def _twiss(self) -> dict:
        s = np.linspace(0.0, 24.0, TWISS_POINTS)
        quad = self._controls[QUAD]
        solenoid = self._controls[SOLENOID]
        waist = 6.0 + 0.4 * quad
        beta_min = 2.0 * (0.45 / solenoid)
        beta_x = beta_min + (s - waist) ** 2 / beta_min
        beta_y = beta_min + (s - (24.0 - waist)) ** 2 / beta_min
        return {
            TWISS_S: np.ascontiguousarray(s, dtype=np.float64),
            TWISS_BETA_X: np.ascontiguousarray(beta_x, dtype=np.float64),
            TWISS_BETA_Y: np.ascontiguousarray(beta_y, dtype=np.float64),
        }


def make_demo_model() -> DemoBeamModel:
    """Factory for the `demo` shortcut (see `loader.DEMO_FACTORY`)."""
    return DemoBeamModel()
