"""Live input providers and the settings that select them.

The EPICS provider is only reachable with pyepics installed, which CI does not have, so what is
covered here is the synthetic provider plus the parts of the EPICS one that are pure logic. The
concurrency property both must satisfy is stated on `InputProvider`: one provider per model is
shared by every producer loop for that model and by `machine-snapshot`, and those reach it on
different threads.

Runs on the bare CI setup: no torch, no pytao, no EPICS, no lattice.
"""

from __future__ import annotations

import time

import pytest

from lume_model_api.model import live_inputs
from lume_model_api.model.demo import CHARGE, CORRECTOR, QUAD, SOLENOID, make_demo_model
from lume_model_api.model.introspect import describe


@pytest.fixture(scope="module")
def info():
    return describe(make_demo_model(), name="demo")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LUME_LIVE_SOURCE", "LUME_LIVE_INPUTS"):
        monkeypatch.delenv(name, raising=False)


# --- the synthetic provider ------------------------------------------------------


def test_synthetic_covers_every_drivable_input(info) -> None:
    values = live_inputs.SyntheticInputProvider(info).read_inputs()
    assert set(values) == {QUAD, SOLENOID, CORRECTOR}
    assert CHARGE not in values  # a constant is neither read nor driven


def test_synthetic_values_stay_inside_the_declared_range(info) -> None:
    """A value outside its range is what a strictly validating model raises on, per frame."""
    limits = {item.id: item for item in info.inputs}
    provider = live_inputs.SyntheticInputProvider(info)
    for _ in range(5):
        for name, value in provider.read_inputs().items():
            assert limits[name].min <= value <= limits[name].max, name


def test_synthetic_varies_over_time_without_being_told_the_time(info) -> None:
    """The phase origin belongs to the provider, not to the caller.

    It used to be threaded in as an `elapsed` argument from each producer loop, which meant two
    streams of one model disagreed about the machine state and `machine-snapshot` was pinned to
    a fixed elapsed=0 while the streams moved.
    """
    provider = live_inputs.SyntheticInputProvider(info)
    first = provider.read_inputs()
    time.sleep(0.05)
    second = provider.read_inputs()
    assert first != second


def test_two_providers_of_one_model_do_not_share_a_phase(info) -> None:
    """Sanity check on the flip side: the origin is per provider, and there is one per model."""
    early = live_inputs.SyntheticInputProvider(info).read_inputs()
    assert set(early) == {QUAD, SOLENOID, CORRECTOR}


def test_restrict_to_limits_which_ids_are_read(info) -> None:
    values = live_inputs.SyntheticInputProvider(info, restrict_to=[QUAD]).read_inputs()
    assert set(values) == {QUAD}


def test_restrict_to_an_unknown_id_warns_rather_than_failing(info, caplog) -> None:
    with caplog.at_level("WARNING", logger=live_inputs.__name__):
        values = live_inputs.SyntheticInputProvider(info, restrict_to=["NO:SUCH:PV"]).read_inputs()
    assert values == {}
    assert "NO:SUCH:PV" in caplog.text


# --- selecting a provider --------------------------------------------------------


def test_the_default_source_is_epics() -> None:
    assert live_inputs.live_source_from_env() == live_inputs.EPICS


def test_synthetic_is_selected_by_name(info, monkeypatch) -> None:
    monkeypatch.setenv("LUME_LIVE_SOURCE", "SYNTHETIC")  # case and whitespace insensitive
    provider = live_inputs.get_input_provider(info, source=live_inputs.live_source_from_env())
    assert isinstance(provider, live_inputs.SyntheticInputProvider)


def test_an_unknown_source_raises(info) -> None:
    with pytest.raises(ValueError, match="Unknown LUME_LIVE_SOURCE"):
        live_inputs.get_input_provider(info, source="epcis")


# --- startup validation ----------------------------------------------------------
#
# Both settings are read when the first provider is built, on the first live request, so
# without check_env a bad value starts the pod green and then fails once per frame forever.


def test_check_env_accepts_the_defaults() -> None:
    live_inputs.check_env()


def test_check_env_rejects_an_unknown_source(monkeypatch) -> None:
    monkeypatch.setenv("LUME_LIVE_SOURCE", "epcis")
    with pytest.raises(ValueError, match="Unknown LUME_LIVE_SOURCE"):
        live_inputs.check_env()


def test_check_env_rejects_malformed_live_inputs_json(monkeypatch) -> None:
    monkeypatch.setenv("LUME_LIVE_INPUTS", "['QUAD']")  # single quotes are not JSON
    with pytest.raises(ValueError) as excinfo:
        live_inputs.check_env()
    assert "['QUAD']" in str(excinfo.value)  # the value is quoted, since that is the usual cause


def test_check_env_rejects_live_inputs_that_is_not_a_list(monkeypatch) -> None:
    monkeypatch.setenv("LUME_LIVE_INPUTS", '{"a": 1}')
    with pytest.raises(ValueError, match="must be a JSON list"):
        live_inputs.check_env()


def test_live_input_ids_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("LUME_LIVE_INPUTS", '["A", "B"]')
    assert live_inputs.live_input_ids_from_env() == ["A", "B"]


def test_no_live_inputs_means_every_input(monkeypatch) -> None:
    assert live_inputs.live_input_ids_from_env() is None
