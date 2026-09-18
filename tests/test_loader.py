"""`LUME_MODELS` parsing, which is what decides the URL of every route.

The name in `LUME_MODELS` becomes a path segment, so a mistake here is a wrong URL rather
than a crash, and a wrong URL is invisible until a client 404s. Every rule the operator-facing
documentation states is pinned below.

Nothing here builds a model: resolution is deliberately import-free, so these tests run with
no torch, no pytao and no lattice.
"""

from __future__ import annotations

import logging

import pytest

from lume_model_api.model import loader
from lume_model_api.model.loader import DEMO_FACTORY, ModelRefError, models_from_env

DEMO_JSON = '{"demo": {"workers": 1}}'
ALT_FACTORY = "lume_model_api.model.demo:make_demo_model"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an unset environment.

    A developer's shell and the other test modules both set these, and a leaked
    LUME_POOL_WORKERS would silently change the `workers` expectations below.
    """
    for name in (
        "LUME_MODELS",
        "LUME_MODEL",
        "LUME_MODEL_KWARGS",
        "LUME_POOL_WORKERS",
        "LUME_MAX_INFLIGHT",
    ):
        monkeypatch.delenv(name, raising=False)


def _by_name(settings) -> dict:
    return {setting.name: setting for setting in settings}


# --- the JSON object form --------------------------------------------------------


def test_json_form_hosts_every_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "LUME_MODELS",
        '{"demo": {"workers": 1}, "alt": {"factory": "' + ALT_FACTORY + '", "workers": 2}}',
    )
    settings = _by_name(models_from_env())
    assert set(settings) == {"demo", "alt"}
    assert settings["demo"].factory_path == DEMO_FACTORY
    assert settings["demo"].workers == 1
    assert settings["alt"].factory_path == ALT_FACTORY
    assert settings["alt"].workers == 2
    # Both are the demo model, whichever way they were named.
    assert settings["demo"].is_demo and settings["alt"].is_demo


def test_json_parse_error_quotes_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A YAML value copied from a shell example carries literal quotes, so show the value."""
    monkeypatch.setenv("LUME_MODELS", "{'demo': {}}")
    with pytest.raises(ModelRefError) as excinfo:
        models_from_env()
    assert "{'demo': {}}" in str(excinfo.value)


def test_json_must_be_an_object_of_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MODELS", '{"demo": "cu_hxr_staged"}')
    with pytest.raises(ModelRefError, match="must be an object"):
        models_from_env()


def test_a_key_without_a_factory_must_be_a_shortcut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MODELS", '{"mystery": {}}')
    with pytest.raises(ModelRefError) as excinfo:
        models_from_env()
    assert "must be a shortcut" in str(excinfo.value)
    assert "cu_hxr_staged" in str(excinfo.value)  # the message lists what is known


def test_a_shortcut_key_merges_kwargs_over_the_shortcut_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LUME_MODELS", '{"cu_hxr_staged": {"kwargs": {"n_particles": 5}}}')
    setting = models_from_env()[0]
    assert setting.kwargs == {"n_particles": 5, "end_element": "TD11"}


def test_an_explicit_factory_ignores_the_keys_shortcut_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key names a shortcut, but an explicit factory means the kwargs are exactly given."""
    monkeypatch.setenv(
        "LUME_MODELS",
        '{"cu_hxr_staged": {"factory": "other.pkg:make", "kwargs": {"n_particles": 5}}}',
    )
    setting = models_from_env()[0]
    assert setting.factory_path == "other.pkg:make"
    assert setting.kwargs == {"n_particles": 5}
    assert setting.is_demo is False


def test_an_invalid_url_name_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dots are excluded too, so no path segment ever needs normalising before lookup."""
    monkeypatch.setenv("LUME_MODELS", '{"my.model": {"factory": "other.pkg:make"}}')
    with pytest.raises(ModelRefError, match="URL segment"):
        models_from_env()


# --- workers and max_inflight ----------------------------------------------------


def test_worker_defaults_are_per_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MODELS", '{"demo": {}, "cu_hxr_staged": {}}')
    settings = _by_name(models_from_env())
    for setting in settings.values():
        assert setting.workers == 4  # the default, applied to each model rather than shared
        assert setting.max_inflight == 16


def test_pod_wide_defaults_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_POOL_WORKERS", "2")
    monkeypatch.setenv("LUME_MODELS", DEMO_JSON.replace('{"workers": 1}', "{}"))
    setting = models_from_env()[0]
    assert setting.workers == 2
    assert setting.max_inflight == 8  # 4 * workers when LUME_MAX_INFLIGHT is unset


def test_max_inflight_env_wins_over_the_worker_multiple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MAX_INFLIGHT", "3")
    monkeypatch.setenv("LUME_MODELS", "demo")
    assert models_from_env()[0].max_inflight == 3


def test_per_model_max_inflight_wins_over_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MAX_INFLIGHT", "3")
    monkeypatch.setenv("LUME_MODELS", '{"demo": {"workers": 1, "max_inflight": 7}}')
    setting = models_from_env()[0]
    assert (setting.workers, setting.max_inflight) == (1, 7)


# --- the comma list form ---------------------------------------------------------


def test_comma_list_of_shortcuts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MODELS", "demo, cu_hxr_staged")
    settings = models_from_env()
    assert [setting.name for setting in settings] == ["demo", "cu_hxr_staged"]
    # A shortcut still brings its default kwargs in this form.
    assert settings[1].kwargs == {"n_particles": 1000, "end_element": "TD11"}


def test_comma_list_accepts_name_equals_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MODELS", f"mine={ALT_FACTORY}")
    setting = models_from_env()[0]
    assert (setting.name, setting.factory_path, setting.kwargs) == ("mine", ALT_FACTORY, {})


def test_a_bare_module_function_needs_a_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare reference would put ':' in the URL, so it is an error rather than a guess."""
    monkeypatch.setenv("LUME_MODELS", ALT_FACTORY)
    with pytest.raises(ModelRefError) as excinfo:
        models_from_env()
    assert "needs a URL name" in str(excinfo.value)


def test_a_duplicate_name_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MODELS", f"demo,demo={ALT_FACTORY}")
    with pytest.raises(ModelRefError, match="twice"):
        models_from_env()


def test_an_unknown_shortcut_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MODELS", "nope")
    with pytest.raises(ModelRefError, match="Unknown model reference"):
        models_from_env()


# --- the single-model fallback ---------------------------------------------------


def test_empty_environment_hosts_the_demo_model() -> None:
    setting = models_from_env()[0]
    assert (setting.name, setting.factory_path, setting.is_demo) == ("demo", DEMO_FACTORY, True)


def test_set_but_empty_lume_models_hosts_demo_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A declared-but-empty LUME_MODELS is a common k8s misconfiguration that must warn."""
    monkeypatch.setenv("LUME_MODELS", "")
    with caplog.at_level(logging.WARNING, logger=loader.__name__):
        setting = models_from_env()[0]
    assert (setting.name, setting.is_demo) == ("demo", True)
    assert "LUME_MODELS" in caplog.text and "demo" in caplog.text


def test_lume_model_set_is_a_hard_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ported manifest that sets LUME_MODEL must fail loudly rather than serve the wrong model."""
    monkeypatch.setenv("LUME_MODEL", "cu_hxr_staged")
    with pytest.raises(ModelRefError) as excinfo:
        models_from_env()
    assert "LUME_MODEL" in str(excinfo.value)
    assert "LUME_MODELS" in str(excinfo.value)


def test_lume_model_kwargs_set_is_a_hard_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUME_MODEL_KWARGS", '{"n_particles": 7}')
    with pytest.raises(ModelRefError) as excinfo:
        models_from_env()
    assert "LUME_MODEL_KWARGS" in str(excinfo.value)
    assert "LUME_MODELS" in str(excinfo.value)


def test_startup_logs_one_line_per_model(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LUME_MODELS", '{"demo": {"workers": 1}, "cu_hxr_staged": {"workers": 2}}')
    with caplog.at_level(logging.INFO, logger=loader.__name__):
        models_from_env()
    hosting = [message for message in caplog.messages if message.startswith("hosting ")]
    assert hosting == [
        f"hosting demo -> {DEMO_FACTORY} workers=1 at /api/v1/models/demo",
        "hosting cu_hxr_staged -> virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model "
        "workers=2 at /api/v1/models/cu_hxr_staged",
    ]
