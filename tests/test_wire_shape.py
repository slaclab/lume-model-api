"""Guard the one thing FastAPI cannot guard: the SSE live stream payload.

`POST /api/v1/evaluate` declares a `response_model`, so FastAPI validates and fills it. The
live stream does not. `live_hub` hands the serializer's dict to `json.dumps` in
`main.py live_stream` with no model in the path, so whatever keys the dict happens to have
are exactly what reaches the browser.

The frontend types that payload as `Required<EvaluateV1Response>` in `api/client.ts`, which
asserts every key is always present. Opt-in outputs are `None` when not requested, never
absent. This test is what makes that assertion true. Without it the guarantee is only a
comment in `serialize.py`.

Parametrized over every screen on purpose. OTR2 has no image while OTR3 and OTR4 do, so a
key made conditional on image data would pass on OTR3 and fail only on OTR2. Testing one
frame would let that through.

Runs on the bare CI setup: no torch, no scipy, no EPICS, no LCLS_LATTICE.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `webapp` is importable only from the repo root

from webapp.backend.mock_source import MockImageSource  # noqa: E402
from webapp.backend.schemas import EvaluateV1Response  # noqa: E402
from webapp.backend.serialize import frame_to_wire  # noqa: E402

# `model` and `version` are attached by the endpoint, not the serializer.
ENDPOINT_ADDED = {"model", "version"}
EXPECTED = set(EvaluateV1Response.model_fields) - ENDPOINT_ADDED

SOURCE = MockImageSource()

WHY = (
    "\n\nThe SSE live stream json.dumps this dict without validating it against "
    "EvaluateV1Response,\nso a missing key reaches the browser genuinely absent. The "
    "frontend types it as\nRequired<EvaluateV1Response> in webapp/frontend/src/api/"
    "client.ts, so there is no type\nerror to catch it. Keep frame_to_wire unconditional: "
    "opt-in outputs must be present and\nNone when not requested, never omitted."
)


@pytest.mark.parametrize("screen", sorted(SOURCE.screens))
@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(dict(include_image=False, include_distribution=False, include_twiss=False), id="scalars-only"),
        pytest.param(dict(include_image=True, include_distribution=True, include_twiss=True), id="everything"),
    ],
)
def test_wire_has_every_key(screen: str, flags: dict) -> None:
    frame = SOURCE.snapshot(screen, include_distribution=flags["include_distribution"])
    wire = frame_to_wire(frame, **flags)
    keys = set(wire)
    assert keys == EXPECTED, (
        f"screen {screen} with {flags} produced the wrong key set."
        f"\n  missing: {sorted(EXPECTED - keys)}"
        f"\n  extra:   {sorted(keys - EXPECTED)}" + WHY
    )


def test_endpoint_added_fields_are_exactly_what_live_hub_attaches() -> None:
    """The serializer omits `model` and `version`, so both senders must attach them.

    The HTTP endpoint does it in main.evaluate_v1. The SSE stream bypasses the endpoint
    entirely, so LiveHub._run has to do it too, or a streamed frame is not a complete
    EvaluateV1Response even though the frontend types it as Required<EvaluateV1Response>.
    This pins the set so a newly added endpoint-attached field cannot be forgotten on the
    live path.
    """
    src = (Path(__file__).resolve().parents[1] / "webapp/backend/live_hub.py").read_text()
    for name in sorted(ENDPOINT_ADDED):
        assert f'wire["{name}"]' in src, (
            f"LiveHub does not attach {name!r}, so SSE frames are missing it while the "
            "frontend's Required<EvaluateV1Response> claims it is present."
        )


def test_opt_in_outputs_are_none_not_absent() -> None:
    """The distinction the frontend's Required<> depends on."""
    frame = SOURCE.snapshot("OTR4")
    wire = frame_to_wire(frame)
    for key in ("image", "distribution", "twiss"):
        assert key in wire, f"{key} was omitted rather than set to None." + WHY
        assert wire[key] is None, f"{key} should be None when not requested."


def test_distribution_positions_are_micrometres() -> None:
    """Cross-check the m to µm conversion against an independently computed scalar.

    The scalars are µm by definition, so the distribution's x spread must be the same
    order as `xrms_um`. A missing 1e6 makes this 1e-6 too small and a doubled one 1e6 too
    large, and neither shows up as a type error or a visibly broken plot.
    """
    import base64

    import numpy as np

    frame = SOURCE.snapshot("OTR4", include_distribution=True)
    wire = frame_to_wire(frame, include_distribution=True)
    dist = wire["distribution"]
    assert dist["units"]["x"] == "µm", dist["units"]
    x = np.frombuffer(base64.b64decode(dist["coords"]["x"]), dtype="<f4")
    ratio = float(x.std()) / wire["scalars"]["xrms_um"]
    assert 0.8 < ratio < 1.25, (
        f"distribution x rms is {x.std():.4g} but scalars.xrms_um is "
        f"{wire['scalars']['xrms_um']:.4g} (ratio {ratio:.4g}). The µm conversion in "
        "beam_monitor._extract_distribution is likely missing or applied twice."
    )
