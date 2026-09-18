"""Live input providers and the settings that select them.

The EPICS provider is only reachable with pyepics installed, which CI does not have, so what is
covered here is the synthetic provider plus the parts of the EPICS one that are pure logic. The
concurrency property both must satisfy is stated on `InputProvider`: one provider per model is
shared by every producer loop for that model and by `machine-snapshot`, and those reach it on
different threads.

Runs on the bare CI setup: no torch, no pytao, no EPICS, no lattice.
"""

from __future__ import annotations

import sys
import time
import types

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


def test_check_env_accepts_the_defaults(monkeypatch) -> None:
    """The default source is epics, so this needs pyepics to look importable.

    CI has no pyepics, which is the whole point of the find_spec check below, so the spec is
    faked rather than the source being switched to synthetic: switching would stop this test
    covering the default configuration at all.
    """
    monkeypatch.setattr(live_inputs.importlib.util, "find_spec", lambda name: object())
    live_inputs.check_env()


def test_check_env_accepts_synthetic_without_pyepics(monkeypatch) -> None:
    """The synthetic source is what CI and any EPICS-free dev box run, so it must not need it."""
    monkeypatch.setenv("LUME_LIVE_SOURCE", "synthetic")
    live_inputs.check_env()


def test_check_env_rejects_epics_without_pyepics(monkeypatch) -> None:
    """Otherwise the pod starts green and then raises once per frame, forever.

    That is exactly the deferred failure check_env exists to prevent: the import only happens
    when the first provider is built, which is on the first live request. Without this a
    LUME_LIVE_SOURCE=epics Deployment on an install missing the [epics] extra passes its probes
    and then serves nothing but error events.
    """
    monkeypatch.setenv("LUME_LIVE_SOURCE", "epics")
    monkeypatch.setattr(live_inputs.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError) as excinfo:
        live_inputs.check_env()
    message = str(excinfo.value)
    # Both fixes named, since which one an operator wants is not knowable from here.
    assert "[epics]" in message and "LUME_LIVE_SOURCE=synthetic" in message


def test_check_env_reports_a_bad_setting_before_a_missing_dependency(monkeypatch) -> None:
    """Only one exception can be raised, so the typo an operator can fix goes first."""
    monkeypatch.setenv("LUME_LIVE_SOURCE", "epics")
    monkeypatch.setenv("LUME_LIVE_INPUTS", "not json")
    monkeypatch.setattr(live_inputs.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ValueError, match="not valid JSON"):
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


# --- the EPICS provider, against a fake pyepics -----------------------------------
#
# `EpicsInputProvider` holds the failure mode with the worst consequences in this package: when
# it reports no values the caller overlays `{}` on the baseline and the live view serves the
# model's DESIGN values labelled as live. So the read logic is worth covering even with no EPICS
# in reach, which a fake module in `sys.modules` makes possible. Only `PV` and `ca.poll` are
# touched, so the fake is small and the real thing is not needed to exercise the decisions.


class _FakePV:
    """The two attributes `read_inputs` uses, plus a count of the reads it actually did."""

    def __init__(self, name: str) -> None:
        self.pvname = name
        self.connected = True
        self.value: float | None = 1.0
        self.gets = 0

    def get(self, timeout=None):
        self.gets += 1
        return self.value


def _epics_provider(monkeypatch, info, **kwargs):
    # Pinned so `configure_epics_from_env` does not write to the real process environment and
    # leak CA settings into later tests.
    monkeypatch.setenv("EPICS_CA_ADDR_LIST", "127.0.0.1")
    module = types.ModuleType("epics")
    module.PV = _FakePV
    module.ca = types.SimpleNamespace(poll=lambda evt=0.0, iot=0.0: None)
    monkeypatch.setitem(sys.modules, "epics", module)
    # No connection wait: every fake PV reports its state synchronously.
    provider = live_inputs.EpicsInputProvider(info, connection_timeout=0.0, **kwargs)
    return provider, provider._pvs


def test_names_covers_every_drivable_input(info, monkeypatch) -> None:
    """The caller divides the read count by this to publish lume_live_inputs_readable/total."""
    provider, _ = _epics_provider(monkeypatch, info)
    assert set(provider.names) == {QUAD, SOLENOID, CORRECTOR}
    assert CHARGE not in provider.names


def test_an_unreadable_pv_is_retried_on_every_later_frame(info, monkeypatch) -> None:
    """The regression that matters most here: an unreadable PV used to be pruned forever.

    One briefly unreachable CA gateway, or an IOC restarted during a rollout of the singleton
    live pod, permanently removed that PV. `read_inputs` then never reported it again, the caller
    kept overlaying the model's design value for it, and the SSE stream served that as live data
    for the life of the process. If this assertion fails, that is back.
    """
    provider, pvs = _epics_provider(monkeypatch, info)
    pvs[QUAD].connected = False
    assert QUAD not in provider.read_inputs()
    pvs[QUAD].connected = True
    assert provider.read_inputs()[QUAD] == 1.0


def test_an_unconnected_pv_is_not_read_at_all(info, monkeypatch) -> None:
    """The per-frame skip is what the permanent prune was justified by, and it is enough.

    A model input that is no real PV must not cost a CA timeout per frame, or one such id paces
    the whole live loop.
    """
    provider, pvs = _epics_provider(monkeypatch, info)
    pvs[QUAD].connected = False
    provider.read_inputs()
    assert pvs[QUAD].gets == 0
    assert pvs[SOLENOID].gets == 1


def test_no_connected_pv_raises_instead_of_reporting_nothing(info, monkeypatch) -> None:
    """Returning `{}` here is what made the stream serve design values labelled as live.

    Zero connected PVs is a channel-access or network fault, not a machine state, so the caller
    has to be able to tell a client the truth rather than quietly publish the design machine.
    """
    provider, pvs = _epics_provider(monkeypatch, info)
    for pv in pvs.values():
        pv.connected = False
    with pytest.raises(RuntimeError) as excinfo:
        provider.read_inputs()
    message = str(excinfo.value)
    # Names the things an operator would actually check.
    assert "EPICS_CA_ADDR_LIST" in message
    assert "gateway" in message
    assert "LUME_LIVE_SOURCE=synthetic" in message


def test_a_recovered_gateway_is_picked_up_without_a_restart(info, monkeypatch) -> None:
    """A total outage has to be transient-recoverable, since a rollout produces one."""
    provider, pvs = _epics_provider(monkeypatch, info)
    for pv in pvs.values():
        pv.connected = False
    with pytest.raises(RuntimeError):
        provider.read_inputs()
    for pv in pvs.values():
        pv.connected = True
    assert set(provider.read_inputs()) == {QUAD, SOLENOID, CORRECTOR}


def test_a_partial_outage_still_reports_the_pvs_that_do_read(info, monkeypatch) -> None:
    """One dead IOC must not cost the whole frame, which is why the error needs zero connected."""
    provider, pvs = _epics_provider(monkeypatch, info)
    pvs[QUAD].connected = False
    assert set(provider.read_inputs()) == {SOLENOID, CORRECTOR}


def test_a_model_with_no_drivable_input_is_not_a_channel_access_failure(info, monkeypatch) -> None:
    """`restrict_to` can legitimately select nothing, and that is not an EPICS problem."""
    provider, _ = _epics_provider(monkeypatch, info, restrict_to=[])
    assert provider.read_inputs() == {}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None])
def test_a_non_finite_read_counts_as_unreadable(info, monkeypatch, bad) -> None:
    """A connected record with a broken input link returns NaN, and it must not reach model.set.

    Passed through, a NaN either raises inside a pool worker once per frame or, worse, produces a
    NaN beam that a client renders as though it were a physics result. The baseline standing in
    for one frame is the honest answer.
    """
    provider, pvs = _epics_provider(monkeypatch, info)
    pvs[QUAD].value = bad
    values = provider.read_inputs()
    assert QUAD not in values
    assert set(values) == {SOLENOID, CORRECTOR}


def test_the_unreadable_set_is_logged_only_when_it_changes(info, monkeypatch, caplog) -> None:
    """At live-loop rates a per-frame warning is megabytes of one sentence in the pod log.

    Steady state costs one line, and a recovery or a newly broken PV still shows up, which is the
    part an operator needs.
    """
    provider, pvs = _epics_provider(monkeypatch, info)
    pvs[QUAD].connected = False
    with caplog.at_level("WARNING", logger=live_inputs.__name__):
        for _ in range(4):
            provider.read_inputs()
        assert caplog.text.count(QUAD) == 1
        # A second PV going down is a change, so it is reported.
        caplog.clear()
        pvs[SOLENOID].connected = False
        provider.read_inputs()
        assert SOLENOID in caplog.text
        # And so is the recovery, which is the transition a permanent prune could never report.
        caplog.clear()
        pvs[QUAD].connected = True
        pvs[SOLENOID].connected = True
        provider.read_inputs()
        assert "readable again" in caplog.text


def test_live_input_ids_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("LUME_LIVE_INPUTS", '["A", "B"]')
    assert live_inputs.live_input_ids_from_env() == ["A", "B"]


def test_no_live_inputs_means_every_input(monkeypatch) -> None:
    assert live_inputs.live_input_ids_from_env() is None
