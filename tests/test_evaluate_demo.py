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
from lume.model import LUMEModel

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


def _block_counts(rows: int, cols: int, factor: int) -> np.ndarray:
    """How many real pixels each output block covers, computed independently of the code."""
    edges = lambda n: [  # noqa: E731
        min(start + factor, n) - start for start in range(0, n, factor)
    ]
    return np.outer(edges(rows), edges(cols)).astype(float)


@pytest.mark.parametrize(
    ("rows", "cols", "max_dim", "factor"),
    [
        # A real sensor at the shipped cap, and a tiny odd shape where every block is partial.
        pytest.param(513, 513, 512, 2, id="513x513"),
        pytest.param(1040, 1392, 512, 3, id="sensor-1040x1392"),
        pytest.param(7, 5, 4, 2, id="tiny-odd"),
    ],
)
def test_downsampling_drops_no_pixel_when_the_shape_is_not_a_multiple(
    monkeypatch, rows, cols, max_dim, factor
) -> None:
    """A trailing partial block must be averaged, not discarded.

    The old implementation trimmed to a whole multiple of the block factor, so up to
    `factor - 1` rows and columns fell off the trailing edge with nothing on the wire saying so.
    A real 1040x1392 sensor lost 2 rows and a 513x513 one lost a row and a column, which means a
    beam or a hot defect sitting at that edge simply did not exist as far as any client could
    tell. If this assertion fails, pixels are being silently dropped again.
    """
    from lume_model_api.api import serialize

    monkeypatch.setattr(serialize, "MAX_IMAGE_DIM", max_dim)
    # Distinct per pixel, so a dropped row or column cannot coincidentally sum to the same total.
    arr = np.arange(rows * cols, dtype="<f4").reshape(rows, cols) + 1.0
    out = serialize._downsample_image(arr)

    counts = _block_counts(rows, cols, factor)
    assert out.shape == counts.shape
    assert out.dtype == arr.dtype  # the wire dtype is float32, and only resolution may change
    # Block means weighted by the pixels each block really covers reconstruct the whole image's
    # intensity, which is only possible if every input pixel landed in some block.
    total = float((out.astype(float) * counts).sum())
    assert total == pytest.approx(float(arr.astype(float).sum()), rel=1e-6)


def test_downsampling_keeps_the_trailing_edge_pixel() -> None:
    """The single sharpest form of the crop: the corner pixel used to vanish entirely.

    An all-zero image with one lit pixel in the last row and column downsampled to all zeros,
    so a client had no way to know the pixel had ever been there.
    """
    from lume_model_api.api import serialize

    arr = np.zeros((513, 513), dtype="<f4")
    arr[-1, -1] = 5.0
    out = serialize._downsample_image(arr)
    # Its block covers exactly one real pixel, so the mean is the pixel itself.
    assert float(out[-1, -1]) == pytest.approx(5.0)


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


def test_particles_payload_raises_when_no_coordinate_reads() -> None:
    """_particles_payload must not silently return an empty beam when every coordinate fails.

    Before this check, passing None as the beam returned a valid-looking dict with n=0 and
    coords={}, which passed schema validation and rendered as an empty scatter plot with no
    error in the logs or in the response. A TypeError surfaces the failure to the caller.
    """
    from lume_model_api.model.evaluate import _particles_payload

    with pytest.raises(TypeError, match="total_failure_output"):
        _particles_payload(None, max_particles=None, output_id="total_failure_output")


# --- classifying a model failure against a bad request ---------------------------
#
# The two halves of docs/POISONED_WORKER.md. A model that breaks itself must be a server fault,
# and a request the model would reject must stay a client fault. The second matters at least as
# much as the first: an over-eager classifier turns every bad value into a latched worker and a
# pod restart, which is worse than the bug being fixed here.


class _Recorder(LUMEModel):
    """A minimal model whose `set()` can be told to fail after validation passes.

    Built here rather than reusing the demo model because the point is what happens *inside*
    `_set`, which on a real model means an unstable lattice and no way to ask for one on demand.
    `raise_on_set` is what a poisoned worker does: the value validated cleanly and the model
    broke while applying it.
    """

    def __init__(self, raise_on_set: BaseException | None = None) -> None:
        from lume.variables import ScalarVariable

        self._variables = {
            "MAG:1:BCTRL": ScalarVariable(
                name="MAG:1:BCTRL", default_value=1.0, value_range=(-5.0, 5.0), unit="kG"
            ),
        }
        self._raise_on_set = raise_on_set
        self.set_calls: list[dict] = []

    @property
    def supported_variables(self) -> dict:
        return self._variables

    def reset(self) -> None:
        pass

    def _set(self, values: dict) -> None:
        self.set_calls.append(dict(values))
        if self._raise_on_set is not None:
            raise self._raise_on_set

    def _get(self, names: list[str]) -> dict:
        return {name: 0.0 for name in names}


def _recorder(raise_on_set: BaseException | None = None):
    from lume_model_api.model.introspect import describe

    model = _Recorder(raise_on_set=raise_on_set)
    return model, describe(model, name="recorder")


def test_a_value_that_fails_validation_is_invalid_input_and_never_reaches_the_model() -> None:
    """The half of the fix that keeps a bad request from restarting the pod.

    Asserting that `set()` was not called as well as the exception type, because the two are one
    claim: a value rejected here cannot have destabilized anything, which is what makes 400 the
    honest answer. If this ever classifies as `ModelUnusable`, every client typo becomes a
    latched worker and a pod restart.
    """
    from lume_model_api.model.evaluate import InvalidInput, ModelUnusable

    model, info = _recorder()
    # Validation is opt-in per variable in lume, so switch it on for the knob being pushed out
    # of range, as a strictly validating model would have it.
    model.supported_variables["MAG:1:BCTRL"].default_validation_config = "error"

    with pytest.raises(InvalidInput) as raised:
        evaluate(model, info, {"MAG:1:BCTRL": 1e6}, [])
    assert not isinstance(raised.value, ModelUnusable)
    assert model.set_calls == [], "a value rejected by pre-validation must never be applied"


def test_a_non_numeric_value_is_invalid_input() -> None:
    """A string where a float belongs is the caller's fault at any validation config.

    lume's type check is mandatory where its range check is opt-in, and it raises `TypeError`,
    which used to share an arm with `lume.exceptions.ReadOnlyError` and with a broken model.
    """
    from lume_model_api.model.evaluate import InvalidInput

    model, info = _recorder()
    with pytest.raises(InvalidInput):
        evaluate(model, info, {"MAG:1:BCTRL": "not a number"}, [])
    assert model.set_calls == []


def test_a_value_error_raised_after_validation_passes_is_model_unusable() -> None:
    """The documented bug. This exact case used to be reported to the caller as a 400.

    A poisoned worker fails with a numpy reshape `ValueError` from deep inside the model, on a
    request whose values are perfectly valid, so the exception type says nothing about whose
    fault it is. Pre-validation having already passed is what settles it.
    """
    from lume_model_api.model.evaluate import InvalidInput, ModelUnusable

    model, info = _recorder(ValueError("cannot reshape array of size 0 into shape (180,)"))
    with pytest.raises(ModelUnusable) as raised:
        evaluate(model, info, {"MAG:1:BCTRL": 2.0}, [])
    # `ModelUnusable` is a RuntimeError, so it cannot be caught by main.py's 400 arm, which
    # catches the two ValueError subclasses. Asserted because that inheritance is the whole
    # mechanism, and a later refactor could break it without breaking anything else here.
    assert not isinstance(raised.value, (InvalidInput, ValueError))
    assert isinstance(raised.value, RuntimeError)
    # The model's own message survives, since it is the only clue about what actually failed.
    assert "reshape" in str(raised.value)


def test_a_runtime_error_after_validation_is_classified_rather_than_leaked() -> None:
    """`TaoCommandError` is a `RuntimeError`, so the old narrow catch missed it entirely.

    It escaped as an unhandled 500, which is why the poisoning request returned 500 on a fresh
    worker and 400 on an already-poisoned one.
    """
    from lume_model_api.model.evaluate import ModelUnusable

    model, info = _recorder(RuntimeError("Command 'set global lattice_calc_on = T' causes errors"))
    with pytest.raises(ModelUnusable):
        evaluate(model, info, {"MAG:1:BCTRL": 2.0}, [])


def test_a_read_only_id_in_inputs_is_a_client_error() -> None:
    """`lume.exceptions.ReadOnlyError` subclasses `TypeError`, so type alone cannot place it.

    Reached only when a read-only id is in the baseline or requested directly, since `describe`
    publishes read-only variables as outputs. Pre-validation is what keeps it a 400.
    """
    from lume_model_api.model.evaluate import InvalidInput

    model, info = _recorder()
    model.supported_variables["MAG:1:BCTRL"].read_only = True
    with pytest.raises(InvalidInput, match="read-only"):
        evaluate(model, info, {"MAG:1:BCTRL": 2.0}, [])
    assert model.set_calls == []


def test_prevalidation_accepts_a_value_the_model_accepts() -> None:
    """Pre-validation must not be stricter than the model, or it invents 400s of its own."""
    model, info = _recorder()
    model.supported_variables["MAG:1:BCTRL"].default_validation_config = "error"

    result = evaluate(model, info, {"MAG:1:BCTRL": 2.5}, [])
    assert model.set_calls == [{"MAG:1:BCTRL": 2.5}]
    assert result.inputs["MAG:1:BCTRL"] == 2.5


def test_prevalidation_runs_on_the_coerced_value_not_the_caller_s() -> None:
    """An `IntVariable` fails lume's type check on a float, so the order of the two matters.

    A caller sending `4.0` for an integer knob is valid, because `_coerce_control` rounds it to
    the declared type before anything is applied. Pre-validation running on the raw request
    instead of on the coerced values would reject it as a 400 the model itself would have
    accepted.

    Calls `_prevalidate` directly rather than going through `evaluate`, because whether an
    `IntVariable` is published as an input at all depends on the installed lume-base version
    (see test_an_int_variable_is_classified_as_a_scalar), and this claim does not.
    """
    from lume.variables import IntVariable

    from lume_model_api.model.evaluate import InvalidInput, _coerce_control, _prevalidate

    steps = IntVariable(name="MAG:1:NSTEPS", default_value=3, value_range=(0, 10))
    steps.default_validation_config = "error"

    class _Holder:
        supported_variables = {"MAG:1:NSTEPS": steps}

    coerced = _coerce_control(steps, 4.0)
    assert coerced == 4 and isinstance(coerced, int)
    _prevalidate(_Holder(), {"MAG:1:NSTEPS": coerced})  # must not raise

    with pytest.raises(InvalidInput):
        _prevalidate(_Holder(), {"MAG:1:NSTEPS": 4.0})


def test_a_cancelled_evaluate_is_not_treated_as_a_broken_model() -> None:
    """A live viewer disconnecting mid-frame must not latch the worker and restart the pod.

    `CancelledError` is a `BaseException`, and the catch around `model.set()` is deliberately
    that wide, so it has to be re-raised ahead of the classification.
    """
    import asyncio

    from lume_model_api.model.evaluate import ModelUnusable

    model, info = _recorder(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        evaluate(model, info, {"MAG:1:BCTRL": 2.0}, [])
    assert not isinstance(asyncio.CancelledError(), ModelUnusable)


def test_a_demoted_alias_is_never_written_to_the_model() -> None:
    """The regression test for the flat quad scan.

    virtual-accelerator publishes `BCTRL` and `BDES` per magnet as two handles on one Bmad
    attribute. The baseline merge applies every non-constant knob in a single `model.set()`, so
    both were written and the last one applied won. A request setting only `BCTRL` therefore did
    nothing: the response echoed the caller's value with `source: request` while the magnet kept
    its default, and sweeping the knob across its whole range produced an identical beam every
    time.

    Asserting on what reaches `model.set()` rather than on the response, because the response
    looked correct throughout. That is what made the bug expensive to find.
    """
    from lume.model import LUMEModel
    from lume.variables import ScalarVariable

    from lume_model_api.model.evaluate import evaluate
    from lume_model_api.model.introspect import describe

    class Recorder(LUMEModel):
        def __init__(self) -> None:
            self._variables = {
                "MAG:1:BCTRL": ScalarVariable(name="MAG:1:BCTRL", default_value=-3.2, unit="kG"),
                "MAG:1:BDES": ScalarVariable(name="MAG:1:BDES", default_value=-3.2, unit="kG"),
            }
            self.applied: dict = {}

        @property
        def supported_variables(self) -> dict:
            return self._variables

        def reset(self) -> None:
            pass

        def _set(self, values: dict) -> None:
            self.applied.update(values)

        def _get(self, names: list[str]) -> dict:
            return {name: 0.0 for name in names}

    model = Recorder()
    info = describe(model, name="recorder")
    evaluate(model, info, {"MAG:1:BCTRL": -7.0}, [])

    assert model.applied == {"MAG:1:BCTRL": -7.0}, (
        "the caller's value must be the only thing written for this control. Before the alias "
        "resolution in introspect._resolve_aliases, MAG:1:BDES arrived from the baseline at its "
        "default and overwrote it."
    )
