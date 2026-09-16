"""Introspection is what makes this service model-generic, so it is what needs pinning.

Every one of these assertions used to be a hand-typed table of PV names, defaults and ranges
in this repo, kept in step with the model by hand. Now it is derived, and if the derivation
regresses the API does not error: it publishes a slightly wrong machine. A slider with the
wrong limits, a screen that reports no image, an input that silently stops being driven. None
of that shows up as a 500, which is why each case gets its own assertion here.

Runs on the bare CI setup: no torch, no pytao, no EPICS, no lattice.
"""

from __future__ import annotations

import pytest

from lume_model_api.model.demo import (
    CHARGE,
    CORRECTOR,
    QUAD,
    SCREEN_A_BEAM,
    SCREEN_B_BEAM,
    SCREEN_B_IMAGE,
    SOLENOID,
    make_demo_model,
)
from lume_model_api.model.introspect import describe


@pytest.fixture(scope="module")
def info():
    return describe(make_demo_model(), name="demo")


def _by_id(items):
    return {item.id: item for item in items}


def test_writable_scalars_are_the_inputs(info) -> None:
    assert set(info.input_ids) == {QUAD, SOLENOID, CORRECTOR, CHARGE}


def test_model_declared_range_is_reported_as_such(info) -> None:
    quad = _by_id(info.inputs)[QUAD]
    assert (quad.min, quad.max, quad.default) == (-5.0, 5.0, 2.0)
    assert quad.range_source == "model"
    assert quad.unit == "kG"
    assert not quad.constant


def test_missing_range_is_derived_from_the_default(info) -> None:
    """A Bmad-side magnet has unit and read_only but no value_range, so it needs a range."""
    corrector = _by_id(info.inputs)[CORRECTOR]
    assert corrector.range_source == "derived"
    # default is 0, so the span is absolute rather than proportional (which would collapse).
    assert (corrector.min, corrector.max) == (-0.5, 0.5)


def test_derived_range_fraction_is_configurable(monkeypatch) -> None:
    monkeypatch.setenv("LUME_DERIVED_RANGE_FRACTION", "0.1")
    corrector = _by_id(describe(make_demo_model()).inputs)[CORRECTOR]
    assert (corrector.min, corrector.max) == pytest.approx((-0.1, 0.1))


def test_proportional_span_for_a_nonzero_default(monkeypatch) -> None:
    """Sanity-check the |default| branch, which the demo's zero-default input cannot reach."""
    from lume.variables import ScalarVariable

    from lume_model_api.model.introspect import _input_info

    variable = ScalarVariable(name="X", default_value=4.0)
    info = _input_info(None, variable, 0.25)
    assert (info.min, info.max) == pytest.approx((3.0, 5.0))


def test_constant_input_is_flagged_and_excluded_from_the_baseline(info) -> None:
    charge = _by_id(info.inputs)[CHARGE]
    assert charge.constant
    assert charge.min == charge.max
    assert CHARGE not in info.baseline
    # ... while every drivable input is in it, or an omitted knob would drift.
    assert set(info.baseline) == {QUAD, SOLENOID, CORRECTOR}


def test_output_kinds(info) -> None:
    kinds = {item.id: item.kind for item in info.outputs}
    assert kinds[SCREEN_A_BEAM] == "particles"
    assert kinds[SCREEN_B_BEAM] == "particles"
    assert kinds[SCREEN_B_IMAGE] == "array"
    assert kinds["s"] == "array"
    assert kinds["DEMO:OTRA:XRMS"] == "scalar"
    # Writable variables are never outputs, whatever their kind.
    assert QUAD not in kinds


def test_array_outputs_declare_their_shape(info) -> None:
    outputs = _by_id(info.outputs)
    assert outputs[SCREEN_B_IMAGE].shape == [240, 320]
    assert outputs["x.beta"].shape == [64]
    assert outputs[SCREEN_A_BEAM].shape is None


def test_screens_pair_particles_with_an_image_when_one_exists(info) -> None:
    """The trap the old mock hid: a screen without an image must still be a screen."""
    screens = {item.key: item for item in info.screens}
    assert set(screens) == {"OTR_A", "OTR_B"}
    assert screens["OTR_A"].particles == SCREEN_A_BEAM
    assert screens["OTR_A"].image is None
    assert screens["OTR_B"].particles == SCREEN_B_BEAM
    assert screens["OTR_B"].image == SCREEN_B_IMAGE


def test_image_is_paired_by_element_name(info) -> None:
    """Pairing is `element_name`, not a name convention, so record the dependency.

    A virtual-accelerator revision that drops `element_name` from its image variables makes
    every screen report `image: null`. That is the documented degraded result, and this test
    is the reason someone will recognise it as such instead of hunting the API.
    """
    outputs = _by_id(info.outputs)
    assert outputs[SCREEN_B_IMAGE].element_name == "OTR_B"


def test_an_int_variable_is_classified_as_a_scalar() -> None:
    """`variable_kind` relies on `IntVariable` subclassing `ScalarVariable` in lume-base.

    That coupling is version-dependent and the failure is silent, not an error: on a lume-base
    where `IntVariable` does not subclass `ScalarVariable`, `variable_kind` returns "value", and
    `describe` then drops every writable integer knob from the API entirely (it keeps only
    scalars) with no log line. A read-only one would be published under the wrong `kind`.

    Same class of upstream drift as test_image_is_paired_by_element_name, which is why it gets
    the same treatment: pinned here so it fails at the dependency rather than in production.
    """
    from lume.variables import IntVariable

    from lume_model_api.model.introspect import variable_kind

    assert variable_kind(IntVariable(name="N", default_value=1)) == "scalar"


def test_info_is_picklable(info) -> None:
    """ModelInfo crosses the spawn boundary from a pool worker to the main process."""
    import pickle

    assert pickle.loads(pickle.dumps(info)).baseline == info.baseline
