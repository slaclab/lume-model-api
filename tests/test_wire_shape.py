"""Guard the one thing FastAPI cannot guard: the SSE live stream payload.

`POST /api/v1/models/{name}/evaluate` declares a `response_model`, so FastAPI validates and
fills it. The live stream does not. `live_hub` hands the serializer's dict to `json.dumps` in
`main.py live_stream` with no model in the path, so whatever keys the dict happens to have are
exactly what reaches the browser.

Clients therefore generate their stream types from `EvaluateV1Response` and treat every key as
always present, which is the only workable assumption when nothing validates the payload. This
test is what makes that assumption true. Without it the guarantee is only a comment in
`serialize.py`.

Two invariants, and the second is the one a generic host adds:

1. The top-level key set is exactly `EvaluateV1Response` minus what the sender attaches.
2. Every requested output id appears in `outputs`. The old shape had a fixed set of opt-in
   keys that could be None, while the new one is keyed by the caller's own ids, so "omitted" now
   means a client's `outputs["OTR_B_beam"]` is a KeyError rather than a null.

Parametrized over both demo screens on purpose. OTR_A has no image while OTR_B does, so a key
made conditional on image data would pass on OTR_B and fail only on OTR_A. Testing one frame
would let that through.

Runs on the bare CI setup: no torch, no pytao, no EPICS, no lattice.
"""

from __future__ import annotations

import pytest

from lume_model_api.api.schemas import EvaluateV1Response
from lume_model_api.api.serialize import result_to_wire
from lume_model_api.model.demo import make_demo_model
from lume_model_api.model.evaluate import evaluate
from lume_model_api.model.introspect import describe

# Attached by the sender, not the serializer. `model` and `version` because the SSE stream
# bypasses the HTTP endpoint that would otherwise fill them, and `input_sources` because the
# serializer is handed the post-merge values only and cannot tell a live reading from a design
# value that the merge filled in.
SENDER_ADDED = {"model", "version", "input_sources"}
EXPECTED = set(EvaluateV1Response.model_fields) - SENDER_ADDED

MODEL = make_demo_model()
INFO = describe(MODEL, name="demo")

WHY = (
    "\n\nThe SSE live stream json.dumps this dict without validating it against "
    "EvaluateV1Response,\nso a missing key reaches the client genuinely absent, and clients "
    "type the stream as\nfully populated because nothing on that path can tell them "
    "otherwise. Dropping a key is\nalso invisible in openapi.json, so no consumer can detect "
    "it by refetching the schema."
)


def _screen_outputs(key: str) -> list[str]:
    screen = INFO.screen(key)
    ids = [screen.particles]
    if screen.image:
        ids.append(screen.image)
    return ids


ALL_OUTPUT_IDS = sorted(INFO.output_ids)
SCREEN_KEYS = sorted(item.key for item in INFO.screens)


@pytest.mark.parametrize(
    "outputs",
    [pytest.param(_screen_outputs(key), id=f"screen-{key}") for key in SCREEN_KEYS]
    + [
        pytest.param(["DEMO:OTRA:XRMS"], id="one-scalar"),
        pytest.param(ALL_OUTPUT_IDS, id="everything"),
    ],
)
def test_wire_has_every_top_level_key(outputs: list[str]) -> None:
    wire = result_to_wire(evaluate(MODEL, INFO, {}, outputs), frame_index=7)
    keys = set(wire)
    assert keys == EXPECTED, (
        f"{outputs} produced the wrong key set."
        f"\n  missing: {sorted(EXPECTED - keys)}"
        f"\n  extra:   {sorted(keys - EXPECTED)}" + WHY
    )


@pytest.mark.parametrize(
    "outputs",
    [pytest.param(_screen_outputs(key), id=f"screen-{key}") for key in SCREEN_KEYS]
    + [pytest.param(ALL_OUTPUT_IDS, id="everything")],
)
def test_every_requested_output_id_is_present(outputs: list[str]) -> None:
    wire = result_to_wire(evaluate(MODEL, INFO, {}, outputs))
    missing = sorted(set(outputs) - set(wire["outputs"]))
    assert not missing, (
        f"requested {outputs} but {missing} came back absent."
        "\n\nEvery requested id must be present. A client indexes outputs by the id it asked "
        "for,\nso an omitted id is a KeyError at the call site rather than a null it can "
        "render around." + WHY
    )


def test_every_output_carries_its_kind() -> None:
    """The discriminator. Without it the response model cannot pick a union member."""
    wire = result_to_wire(evaluate(MODEL, INFO, {}, ALL_OUTPUT_IDS))
    for name, output in wire["outputs"].items():
        assert output.get("kind") in {"scalar", "array", "particles", "value"}, name


def test_every_output_carries_its_variable_class() -> None:
    """Published beside `kind` on every output, so the SSE path must emit it too.

    `kind` is coarse by design, so this is the only way a streaming client can tell an
    IntVariable from a ScalarVariable or one "value" class from another. It is set outside the
    per-kind branches in both `evaluate` and `serialize_output` precisely so a kind cannot ship
    without it, and this is what holds that.
    """
    wire = result_to_wire(evaluate(MODEL, INFO, {}, ALL_OUTPUT_IDS))
    missing = sorted(name for name, out in wire["outputs"].items() if not out.get("variable_class"))
    assert not missing, f"outputs missing variable_class: {missing}" + WHY
    # The config route and an evaluate response must agree about what an id is, or a client that
    # reads one and switches on the other is wrong for that id.
    from_config = {item.id: item.variable_class for item in INFO.outputs}
    disagreeing = {
        name: (out["variable_class"], from_config[name])
        for name, out in wire["outputs"].items()
        if out["variable_class"] != from_config[name]
    }
    assert not disagreeing, f"evaluate and config disagree (wire, config): {disagreeing}"


def test_wire_validates_against_the_response_model() -> None:
    """The HTTP route fills gaps from the response_model, the SSE stream cannot. Check both."""
    wire = result_to_wire(evaluate(MODEL, INFO, {}, ALL_OUTPUT_IDS))
    EvaluateV1Response(model="demo", version="demo", **wire)


def test_sender_added_fields_are_exactly_the_expected_set() -> None:
    """The serializer omits these, so both senders must attach every one of them.

    The HTTP endpoint does it in main.evaluate_v1. The SSE stream bypasses the endpoint
    entirely, so LiveHub._run has to do it too, or a streamed frame is not a complete
    EvaluateV1Response even though clients type it as one.

    This pins only the *set*, so that adding a field to EvaluateV1Response forces a decision
    about which side attaches it. That the live path really does attach each of them is asserted
    behaviourally in tests/test_live_hub.py, which replaced an earlier version of this test
    that grepped live_hub.py for the literal `wire["model"]` and so passed or failed on how the
    assignment happened to be spelled.
    """
    assert SENDER_ADDED == set(EvaluateV1Response.model_fields) - EXPECTED
