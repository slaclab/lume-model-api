"""Guard the one thing FastAPI cannot guard: the SSE live stream payload.

`POST /api/v1/evaluate` declares a `response_model`, so FastAPI validates and fills it. The
live stream does not. `live_hub` hands the serializer's dict to `json.dumps` in
`main.py live_stream` with no model in the path, so whatever keys the dict happens to have
are exactly what reaches the browser.

Clients therefore generate their stream types from `EvaluateV1Response` and treat every key as
always present, which is the only workable assumption when nothing validates the payload. Opt-in
outputs are `None` when not requested, never absent. This test is what makes that assumption
true. Without it the guarantee is only a comment in `serialize.py`.

That matters more now than when the UI lived in this repo. A client in another repo cannot see
a change here until it refetches the schema, and a key that goes missing produces no schema
change at all, so there would be nothing to refetch.

Parametrized over every screen on purpose. OTR2 has no image while OTR3 and OTR4 do, so a
key made conditional on image data would pass on OTR3 and fail only on OTR2. Testing one
frame would let that through.

Runs on the bare CI setup: no torch, no scipy, no EPICS, no LCLS_LATTICE.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lume_model_api.api import live_hub
from lume_model_api.api.mock_source import MockImageSource
from lume_model_api.api.schemas import EvaluateV1Response
from lume_model_api.api.serialize import frame_to_wire

# `model` and `version` are attached by the endpoint, not the serializer.
ENDPOINT_ADDED = {"model", "version"}
EXPECTED = set(EvaluateV1Response.model_fields) - ENDPOINT_ADDED

SOURCE = MockImageSource()

WHY = (
    "\n\nThe SSE live stream json.dumps this dict without validating it against "
    "EvaluateV1Response,\nso a missing key reaches the client genuinely absent, and clients "
    "type the stream as\nfully populated because nothing on that path can tell them "
    "otherwise. Dropping a key is\nalso invisible in openapi.json, so no consumer can detect "
    "it by refetching the schema.\nKeep frame_to_wire unconditional: opt-in outputs must be "
    "present and None when not\nrequested, never omitted."
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
    EvaluateV1Response even though clients type it as one. This pins the set so a newly added
    endpoint-attached field cannot be forgotten on the live path.

    Reads the module through its own __file__ rather than a path relative to this test, so it
    does not care where the package is installed.
    """
    src = Path(live_hub.__file__).read_text()
    for name in sorted(ENDPOINT_ADDED):
        assert f'wire["{name}"]' in src, (
            f"LiveHub does not attach {name!r}, so SSE frames are missing it while every "
            "client's generated type claims it is present."
        )


def test_opt_in_outputs_are_none_not_absent() -> None:
    """The distinction every generated client type depends on."""
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
