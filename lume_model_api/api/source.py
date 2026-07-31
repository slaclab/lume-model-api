"""Source factory + config assembly.

The source is either the real ``ModelImageSource`` (imported lazily so torch /
virtual_accelerator are only loaded when running for real) or the dependency-free
``MockImageSource``. Both expose ``.snapshot()``, ``.reset()`` and
``._writable_variable_names``.
"""

from __future__ import annotations

import os

from lume_visualizations.config import MANUAL_INPUT_PVS, SCREEN_CONFIGS
from lume_visualizations.fake_epics_ioc import FAKE_INPUT_SPECS

from .schemas import ConfigResponse, InputInfo, ScreenInfo, SCALAR_INFO


def is_mock() -> bool:
    return os.environ.get("LUME_MOCK", "").lower() in {"1", "true", "yes"}


def get_source(model_name: str = "cu_hxr_staged", mock: bool | None = None):
    mock = is_mock() if mock is None else mock
    if mock:
        from .mock_source import MockImageSource

        return MockImageSource(model_name)

    from lume_visualizations.beam_monitor import ModelImageSource

    return ModelImageSource(model_name=model_name, reset_values={})


def build_config(source, model_name: str, mock: bool) -> ConfigResponse:
    writable = getattr(source, "_writable_variable_names", set(MANUAL_INPUT_PVS))
    specs_by_pv = {spec.pv_name: spec for spec in FAKE_INPUT_SPECS}

    inputs: list[InputInfo] = []
    for pv in MANUAL_INPUT_PVS:
        if pv not in writable:
            continue
        spec = specs_by_pv.get(pv)
        if spec is None:
            continue
        lo, hi, default = float(spec.minimum), float(spec.maximum), float(spec.default)
        if hi <= lo:  # zero-range PV (e.g. correctors); give a nominal span
            lo, hi = default - 0.1, default + 0.1
        inputs.append(
            InputInfo(id=pv, label=pv, min=lo, max=hi, default=default, unit="")
        )

    screens = [
        ScreenInfo(key=s.key, label=s.label, has_image=s.image_pv is not None)
        for s in SCREEN_CONFIGS.values()
    ]

    version = f"{model_name} (mock)" if mock else model_name
    return ConfigResponse(
        model=model_name,
        version=version,
        screens=screens,
        inputs=inputs,
        scalars=SCALAR_INFO,
    )
