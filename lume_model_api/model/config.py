from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SCAN_PV = "QUAD:IN20:525:BCTRL"
MAX_HISTORY = 200
DEFAULT_POLL_PERIOD_S = 1.0
DEFAULT_IMAGE_SCALE_MODE = "robust"
DEFAULT_LOCAL_LATTICE_PATH = Path("/Users/smiskov/SLAC/lcls-lattice")
DEFAULT_CONTAINER_LATTICE_PATH = Path("/opt/lcls-lattice")

MODEL_INPUT_NAMES = [
    "CAMR:IN20:186:R_DIST",
    "FBCK:BCI0:1:CHRG_S",
    "SOLN:IN20:121:BCTRL",
    "QUAD:IN20:121:BCTRL",
    "QUAD:IN20:122:BCTRL",
    "ACCL:IN20:300:L0A_ADES",
    "ACCL:IN20:300:L0A_PDES",
    "ACCL:IN20:400:L0B_ADES",
    "ACCL:IN20:400:L0B_PDES",
    "QUAD:IN20:361:BCTRL",
    "QUAD:IN20:371:BCTRL",
    "QUAD:IN20:425:BCTRL",
    "QUAD:IN20:441:BCTRL",
    "QUAD:IN20:511:BCTRL",
    "QUAD:IN20:525:BCTRL",
]

EXTRA_MACHINE_INPUTS = [
    "CAMR:IN20:186:XRMS",
    "CAMR:IN20:186:YRMS"
]

MANUAL_INPUT_PVS = [
    "SOLN:IN20:121:BCTRL",
    "QUAD:IN20:121:BCTRL",
    "QUAD:IN20:122:BCTRL",
    "ACCL:IN20:300:L0A_PDES",
    "ACCL:IN20:400:L0B_PDES",
    "QUAD:IN20:361:BCTRL",
    "QUAD:IN20:371:BCTRL",
    "QUAD:IN20:425:BCTRL",
    "QUAD:IN20:441:BCTRL",
    "QUAD:IN20:511:BCTRL",
    "QUAD:IN20:525:BCTRL",
]


@dataclass(frozen=True)
class ScreenConfig:
    key: str
    label: str
    particle_source: str
    image_pv: str | None = None
    image_message: str = "Waiting for first shot..."
    scalar_mode: str = "beam"
    xrms_pv: str | None = None
    yrms_pv: str | None = None
    sigma_z_pv: str | None = None
    norm_emit_x_pv: str | None = None
    norm_emit_y_pv: str | None = None


SCREEN_CONFIGS = {
    "OTR2": ScreenConfig(
        key="OTR2",
        label="OTR2",
        particle_source="input_beam",
        image_pv=None,
        image_message="No image generated at OTR2 for this model.",
        scalar_mode="pvs",
        xrms_pv="OTRS:IN20:571:XRMS",
        yrms_pv="OTRS:IN20:571:YRMS",
        sigma_z_pv="sigma_z",
        norm_emit_x_pv="norm_emit_x",
        norm_emit_y_pv="norm_emit_y",
    ),
    "OTR3": ScreenConfig(
        key="OTR3",
        label="OTR3",
        particle_source="OTR3_beam",
        image_pv="OTRS:IN20:621:Image:ArrayData",
    ),
    "OTR4": ScreenConfig(
        key="OTR4",
        label="OTR4",
        particle_source="OTR4_beam",
        image_pv="OTRS:IN20:711:Image:ArrayData",
    ),
}

SCREEN_KEYS = list(SCREEN_CONFIGS)


def resolve_lcls_lattice_path() -> str:
    env_value = os.environ.get("LCLS_LATTICE")
    if env_value:
        return env_value

    for candidate in (DEFAULT_CONTAINER_LATTICE_PATH, DEFAULT_LOCAL_LATTICE_PATH):
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        "LCLS_LATTICE is not set and no default lattice checkout was found. "
        "Set the LCLS_LATTICE environment variable before running the app."
    )
