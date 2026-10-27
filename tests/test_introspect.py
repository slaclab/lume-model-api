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
from lume.model import LUMEModel
from lume.variables import ScalarVariable

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


def test_outputs_publish_the_lume_variable_class_beside_the_kind(info) -> None:
    """`variable_class` is the finer name `kind` throws away.

    `kind` is a wire-handling category, so it is lossy on purpose: IntVariable and
    ScalarVariable both become "scalar", and everything unrecognised collapses into "value". A
    client that needs the real class has only this field, and the key name is lume's own, from
    `Variable.model_dump`.

    The image output pins the part that is easy to get wrong: the demo model declares it as
    `ElementNDVariable`, a local NDVariable subclass, so this must report the concrete class a
    model actually used rather than the lume base it inherits from. A real model's beams are
    `BeamAtElementVariable` for the same reason.
    """
    outputs = _by_id(info.outputs)
    assert outputs[SCREEN_B_IMAGE].variable_class == "ElementNDVariable"
    assert outputs[SCREEN_B_IMAGE].kind == "array"
    assert outputs[SCREEN_A_BEAM].variable_class == "ParticleGroupVariable"
    assert outputs["DEMO:OTRA:XRMS"].variable_class == "ScalarVariable"
    assert outputs["s"].variable_class == "NDVariable"
    # Every output gets one, so a client never has to handle the key being absent.
    assert all(item.variable_class for item in info.outputs)


def test_info_is_picklable(info) -> None:
    """ModelInfo crosses the spawn boundary from a pool worker to the main process."""
    import pickle

    assert pickle.loads(pickle.dumps(info)).baseline == info.baseline


# --- Aliased controls ------------------------------------------------------------------
#
# A model may publish two writable handles on one underlying control. virtual-accelerator does
# this for every magnet, mapping `BCTRL` and `BDES` to the same variable class and so to the same
# Bmad attribute. The baseline merge writes every non-constant knob in one `model.set()`, so both
# handles are written and the last one applied wins: a request setting only `BCTRL` was a silent
# no-op, echoed back as `source: request` while the magnet held its default.
#
# The demo model has no aliases, deliberately, so these build a small model of their own rather
# than teaching the demo an LCLS naming quirk.


class _AliasModel(LUMEModel):
    """Two handles on one control, beside knobs that must be left alone."""

    def __init__(self) -> None:
        self._variables: dict[str, object] = {
            # An alias pair. The model-declared range sits on BDES on purpose: preference by name
            # has to win, because on the real injector only 9 of 50 pairs have a model range on
            # BCTRL and the other 41 have none on either, so the range cannot be the primary rule.
            "MAG:1:BCTRL": ScalarVariable(name="MAG:1:BCTRL", default_value=-3.2, unit="kG"),
            "MAG:1:BDES": ScalarVariable(
                name="MAG:1:BDES", default_value=-3.2, unit="kG", value_range=(-8.0, -1.0)
            ),
            # Same prefix and unit, different defaults, so these are genuinely distinct controls
            # and must both survive. Two handles on one control cannot disagree at startup.
            "MAG:2:BCTRL": ScalarVariable(name="MAG:2:BCTRL", default_value=1.0, unit="kG"),
            "MAG:2:BDES": ScalarVariable(name="MAG:2:BDES", default_value=2.0, unit="kG"),
            # No alias, and no colon hierarchy, which must not group with anything.
            "ACCL:1:PDES": ScalarVariable(name="ACCL:1:PDES", default_value=0.0, unit="deg"),
            "k1": ScalarVariable(name="k1", default_value=0.0, unit=""),
            "k2": ScalarVariable(name="k2", default_value=0.0, unit=""),
            "readback": ScalarVariable(name="readback", read_only=True, unit="m"),
        }
        self._controls = {
            name: float(variable.default_value)
            for name, variable in self._variables.items()
            if not variable.read_only
        }

    @property
    def supported_variables(self) -> dict:
        return self._variables

    def reset(self) -> None:
        pass

    def _set(self, values: dict) -> None:
        self._controls.update({name: float(value) for name, value in values.items()})

    def _get(self, names: list[str]) -> dict:
        return {name: self._controls.get(name, 0.0) for name in names}


@pytest.fixture(scope="module")
def alias_info():
    return describe(_AliasModel(), name="alias")


def test_only_one_handle_of_an_alias_group_stays_settable(alias_info) -> None:
    assert "MAG:1:BCTRL" in alias_info.input_ids
    assert "MAG:1:BDES" not in alias_info.input_ids


def test_a_demoted_alias_leaves_the_baseline(alias_info) -> None:
    """The actual fix. In the baseline it is written on every request and overwrites the winner."""
    assert "MAG:1:BCTRL" in alias_info.baseline
    assert "MAG:1:BDES" not in alias_info.baseline


def test_a_demoted_alias_is_still_readable_and_names_its_winner(alias_info) -> None:
    """Nothing vanishes from the API. It stops being a knob and becomes a measurement."""
    demoted = _by_id(alias_info.outputs)["MAG:1:BDES"]
    assert demoted.alias_of == "MAG:1:BCTRL"
    assert demoted.kind == "scalar"
    assert demoted.unit == "kG"
    # A surviving input is not an alias of anything, so the field stays null there.
    assert _by_id(alias_info.inputs)["MAG:1:BCTRL"].alias_of is None


def test_preference_by_name_beats_a_model_declared_range(alias_info) -> None:
    """BDES carries the value_range here, and BCTRL still wins.

    Ordering the tie-break the other way round would pick BDES for this pair and the alphabet for
    the 41 real pairs where neither alias declares a range.
    """
    # BCTRL survived while carrying only a derived range, so the range did not decide this.
    assert _by_id(alias_info.inputs)["MAG:1:BCTRL"].range_source == "derived"
    # And the loser is the one the model described best: a demoted alias becomes an OutputInfo,
    # which carries no range at all, so its value_range is dropped from the published contract.
    assert _AliasModel().supported_variables["MAG:1:BDES"].value_range == (-8.0, -1.0)
    assert not hasattr(_by_id(alias_info.outputs)["MAG:1:BDES"], "range_source")


def test_same_prefix_with_different_defaults_is_not_an_alias(alias_info) -> None:
    """The guard against eating genuinely distinct knobs.

    Two handles on one control read back the same value at startup, because they read the same
    attribute. Different defaults therefore prove these are different controls.
    """
    assert {"MAG:2:BCTRL", "MAG:2:BDES"} <= alias_info.input_ids
    assert {"MAG:2:BCTRL", "MAG:2:BDES"} <= set(alias_info.baseline)


def test_ids_without_a_hierarchy_are_never_grouped(alias_info) -> None:
    """`k1` and `k2` share an empty prefix, a unit and a default, and are still distinct.

    An id with no ':' declares no hierarchy, so it declares no alias. Without this guard a
    backend naming knobs `k1`, `k2` would have them demote each other.
    """
    assert {"k1", "k2"} <= alias_info.input_ids
    assert {"k1", "k2"} <= set(alias_info.baseline)


def test_an_unaliased_model_is_untouched(info) -> None:
    """The demo model has no alias pairs, so nothing is demoted and no input is an alias."""
    assert all(item.alias_of is None for item in info.inputs)
    assert all(item.alias_of is None for item in info.outputs)
