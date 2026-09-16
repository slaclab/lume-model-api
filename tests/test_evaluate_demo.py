"""Evaluate and serialize the demo model across every output kind.

The demo model is a real LUMEModel, so this exercises the same `evaluate` -> `serialize` path
a production model takes. That is the point of having it: before, mock mode ran different
code from the real model, so a green test run said nothing about the real pipeline.

Runs on the bare CI setup: no torch, no pytao, no EPICS, no lattice.
"""

from __future__ import annotations

import base64

import numpy as np
import pytest

from lume_model_api.api.serialize import serialize_outputs
from lume_model_api.model.demo import (
    CHARGE,
    N_PARTICLES,
    QUAD,
    SCREEN_A_BEAM,
    SCREEN_B_BEAM,
    SCREEN_B_IMAGE,
    make_demo_model,
)
from lume_model_api.model.evaluate import (
    DEFAULT_MAX_PARTICLES,
    UnknownVariable,
    evaluate,
)
from lume_model_api.model.introspect import describe

ALL_OUTPUTS = [SCREEN_B_BEAM, SCREEN_B_IMAGE, "s", "x.beta", "DEMO:OTRA:XRMS"]


@pytest.fixture(scope="module")
def model():
    return make_demo_model()


@pytest.fixture(scope="module")
def info(model):
    return describe(model, name="demo")


def _decode(data_b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(data_b64), dtype="<f4")


def test_every_kind_serializes(model, info) -> None:
    wire = serialize_outputs(evaluate(model, info, {}, ALL_OUTPUTS).outputs)
    assert set(wire) == set(ALL_OUTPUTS)

    assert wire["DEMO:OTRA:XRMS"]["kind"] == "scalar"
    assert wire["DEMO:OTRA:XRMS"]["unit"] == "m"
    assert wire["DEMO:OTRA:XRMS"]["value"] > 0

    image = wire[SCREEN_B_IMAGE]
    assert image["kind"] == "array"
    assert image["dtype"] == "float32"
    assert _decode(image["data_b64"]).size == image["shape"][0] * image["shape"][1]

    twiss = wire["s"]
    assert twiss["shape"] == [64]
    assert _decode(twiss["data_b64"]).size == 64

    beam = wire[SCREEN_B_BEAM]
    assert beam["kind"] == "particles"
    assert set(beam["coords"]) == {"x", "px", "y", "py", "z", "pz", "weight"}


def test_particle_units_are_the_models_native_ones(model, info) -> None:
    """No server-side scaling. The old API converted m -> um, which a generic host cannot do."""
    beam = serialize_outputs(evaluate(model, info, {}, [SCREEN_A_BEAM]).outputs)[SCREEN_A_BEAM]
    assert beam["units"]["x"] == "m"
    assert beam["units"]["px"] == "eV/c"
    assert beam["units"]["weight"] == "C"
    assert beam["stats_units"] == {
        "sigma_x": "m",
        "sigma_y": "m",
        "sigma_z": "m",
        "norm_emit_x": "m",
        "norm_emit_y": "m",
        "mean_energy": "eV",
        "charge": "C",
    }
    # Cross-check the declared unit against the data: a stray 1e6 would show up here and
    # nowhere else, since it is neither a type error nor a visibly broken plot.
    x = _decode(beam["coords"]["x"])
    assert float(x.std()) == pytest.approx(beam["stats"]["sigma_x"], rel=0.15)


def test_particles_are_subsampled_and_capped(model, info) -> None:
    beam = evaluate(model, info, {}, [SCREEN_A_BEAM], max_particles=250).outputs[SCREEN_A_BEAM]
    assert beam["n"] == 250
    assert all(len(values) == 250 for values in beam["coords"].values())
    # An omitted max_particles must cap rather than ship the whole beam.
    assert DEFAULT_MAX_PARTICLES == 3000
    # ... and so must a nonsense one. A negative cap once meant "no cap", which on a real
    # model is a whole tracked beam in one response.
    for nonsense in (0, -1):
        capped = evaluate(model, info, {}, [SCREEN_A_BEAM], max_particles=nonsense)
        assert capped.outputs[SCREEN_A_BEAM]["n"] == min(N_PARTICLES, DEFAULT_MAX_PARTICLES)


def test_stats_are_computed_before_subsampling(model, info) -> None:
    """A thinner scatter plot must not change the numbers printed beside it."""
    full = evaluate(model, info, {}, [SCREEN_A_BEAM], max_particles=100000)
    thin = evaluate(model, info, {}, [SCREEN_A_BEAM], max_particles=50)
    assert thin.outputs[SCREEN_A_BEAM]["stats"] == full.outputs[SCREEN_A_BEAM]["stats"]


def test_baseline_merge_makes_a_request_history_independent(model, info) -> None:
    """The reason a pooled worker can answer any request: the last one leaves no trace."""
    first = evaluate(model, info, {}, ["DEMO:OTRA:XRMS"]).outputs["DEMO:OTRA:XRMS"]["value"]
    evaluate(model, info, {QUAD: -4.0}, ["DEMO:OTRA:XRMS"])
    again = evaluate(model, info, {}, ["DEMO:OTRA:XRMS"]).outputs["DEMO:OTRA:XRMS"]["value"]
    assert again == first


def test_effective_inputs_are_echoed(model, info) -> None:
    result = evaluate(model, info, {QUAD: -1.0}, ["DEMO:OTRA:XRMS"])
    assert result.inputs[QUAD] == -1.0
    assert set(result.inputs) == set(info.baseline)
    # Constants are never set: doing so is a no-op at best and a validation error at worst.
    assert CHARGE not in result.inputs


def test_inputs_change_the_outputs(model, info) -> None:
    weak = evaluate(model, info, {QUAD: -4.0}, ["DEMO:OTRA:XRMS"])
    strong = evaluate(model, info, {QUAD: 4.0}, ["DEMO:OTRA:XRMS"])
    assert weak.outputs["DEMO:OTRA:XRMS"]["value"] != strong.outputs["DEMO:OTRA:XRMS"]["value"]


def test_screens_differ_from_each_other(model, info) -> None:
    """The sharp edge of the old mock: both screens returned identical beam values."""
    both = evaluate(model, info, {}, [SCREEN_A_BEAM, SCREEN_B_BEAM]).outputs
    assert both[SCREEN_A_BEAM]["stats"] != both[SCREEN_B_BEAM]["stats"]


def test_unknown_output_raises(model, info) -> None:
    with pytest.raises(UnknownVariable):
        evaluate(model, info, {}, ["NO:SUCH:OUTPUT"])


def test_unknown_input_raises(model, info) -> None:
    with pytest.raises(UnknownVariable):
        evaluate(model, info, {"NO:SUCH:INPUT": 1.0}, ["DEMO:OTRA:XRMS"])


def test_a_read_only_variable_is_not_an_accepted_input(model, info) -> None:
    with pytest.raises(UnknownVariable):
        evaluate(model, info, {"DEMO:OTRA:XRMS": 1.0}, ["DEMO:OTRA:XRMS"])


def test_model_validation_errors_become_invalid_input(model, info) -> None:
    """lume's own range check surfaces as a 400-class error, not a worker crash.

    The demo variables validate with config "none", so the check is switched on for one of
    them here to exercise the path a strictly validating model takes.
    """
    from lume_model_api.model.evaluate import InvalidInput

    quad = model.supported_variables["DEMO:QUAD:1:BCTRL"]
    previous = quad.default_validation_config
    quad.default_validation_config = "error"
    try:
        with pytest.raises(InvalidInput):
            evaluate(model, info, {"DEMO:QUAD:1:BCTRL": 1e6}, ["DEMO:OTRA:XRMS"])
    finally:
        quad.default_validation_config = previous


def test_smoothing_conserves_intensity(model, info) -> None:
    """The blur must not rescale, or the declared unit stops meaning anything."""
    raw = serialize_outputs(evaluate(model, info, {}, [SCREEN_B_IMAGE]).outputs)
    blurred = serialize_outputs(
        evaluate(model, info, {}, [SCREEN_B_IMAGE], smooth_sigma_px=2.0).outputs
    )
    total = lambda entry: float(  # noqa: E731
        np.frombuffer(base64.b64decode(entry["data_b64"]), dtype="<f4").sum()
    )
    assert total(blurred[SCREEN_B_IMAGE]) == pytest.approx(total(raw[SCREEN_B_IMAGE]), rel=1e-3)


def test_image_downsampling_respects_max_image_dim(model, info, monkeypatch) -> None:
    from lume_model_api.api import serialize

    monkeypatch.setattr(serialize, "MAX_IMAGE_DIM", 64)
    wire = serialize_outputs(evaluate(model, info, {}, [SCREEN_B_IMAGE]).outputs)
    # 240x320 with the longest side capped at 64 means a block factor of 5.
    assert wire[SCREEN_B_IMAGE]["shape"] == [48, 64]


def test_smoothing_is_opt_in(model, info) -> None:
    """Blurring every 2-D array is wrong for a generic host, so it has to be asked for."""
    raw = serialize_outputs(evaluate(model, info, {}, [SCREEN_B_IMAGE]).outputs)
    blurred = serialize_outputs(
        evaluate(model, info, {}, [SCREEN_B_IMAGE], smooth_sigma_px=2.0).outputs
    )
    assert raw[SCREEN_B_IMAGE]["data_b64"] != blurred[SCREEN_B_IMAGE]["data_b64"]
    assert raw[SCREEN_B_IMAGE]["shape"] == blurred[SCREEN_B_IMAGE]["shape"]


def test_non_image_arrays_are_never_smoothed(model, info) -> None:
    """A 1-D lattice function must survive a request that asks for image smoothing."""
    plain = serialize_outputs(evaluate(model, info, {}, ["x.beta"]).outputs)
    asked = serialize_outputs(
        evaluate(model, info, {}, ["x.beta"], smooth_sigma_px=3.0).outputs
    )
    assert plain["x.beta"]["data_b64"] == asked["x.beta"]["data_b64"]
