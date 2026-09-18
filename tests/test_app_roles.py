"""`LUME_ROLE` selection, through the real app.

The eval role had no coverage at all, even though it is what the autoscaled Deployment runs.
Worth its own module because the role is fixed for a process, so it needs a second lifespan with
a different environment rather than a fixture in tests/test_app_demo.py.

One hosted model, one worker: this is about which routes answer, not about the model.

Runs on the bare CI setup: no torch, no pytao, no EPICS, no lattice.
"""

from __future__ import annotations

import importlib.util

import pytest
from fastapi.testclient import TestClient

from lume_model_api.api.main import app

DEMO = "/api/v1/models/demo"


@pytest.fixture(scope="module")
def eval_client():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LUME_MODELS", '{"demo": {"workers": 1}}')
        patch.setenv("LUME_ROLE", "eval")
        with TestClient(app) as client:
            yield client


def test_the_eval_role_serves_discovery_config_and_evaluate(eval_client) -> None:
    assert [entry["name"] for entry in eval_client.get("/api/v1/models").json()] == ["demo"]
    assert eval_client.get(f"{DEMO}/config").status_code == 200
    assert eval_client.post(f"{DEMO}/evaluate", json={"outputs": ["s"]}).status_code == 200


def test_the_eval_role_is_healthy(eval_client) -> None:
    """No hub is not a health problem: the eval pool is supposed to run without one."""
    assert eval_client.get("/healthz").json()["status"] == "ok"


def test_the_eval_role_does_not_serve_machine_snapshot(eval_client) -> None:
    response = eval_client.get(f"{DEMO}/machine-snapshot")
    assert response.status_code == 503
    assert response.json()["detail"] == "machine-snapshot not served by this instance"


def test_the_eval_role_does_not_serve_the_live_stream(eval_client) -> None:
    """Checked before the output ids are resolved, so a valid request still gets the 503."""
    response = eval_client.get(f"{DEMO}/live/stream", params={"outputs": "s"})
    assert response.status_code == 503
    assert response.json()["detail"] == "live view not served by this instance"


def test_an_unknown_model_is_still_404_under_the_eval_role(eval_client) -> None:
    """The name lookup comes first, so a bad name is a 404 rather than a misleading 503."""
    assert eval_client.get("/api/v1/models/nope/machine-snapshot").status_code == 404


# --- validation of the setting itself --------------------------------------------


def test_an_unknown_role_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Better than silently degrading to the eval behaviour and dropping the live routes."""
    monkeypatch.setenv("LUME_MODELS", '{"demo": {"workers": 1}}')
    monkeypatch.setenv("LUME_ROLE", "evl")
    with pytest.raises(ValueError, match="Unknown LUME_ROLE"):
        with TestClient(app):
            pass


def test_an_empty_role_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A k8s env var declared with an empty value yields "", not the default, from os.environ."""
    monkeypatch.setenv("LUME_MODELS", '{"demo": {"workers": 1}}')
    monkeypatch.setenv("LUME_ROLE", "")
    monkeypatch.setenv("LUME_LIVE_SOURCE", "synthetic")
    with TestClient(app) as client:
        # The default role is `all`, so the live routes answer.
        assert client.get(f"{DEMO}/machine-snapshot").status_code == 200


def test_a_bad_live_source_fails_startup_rather_than_every_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is read when the first provider is built, so it would otherwise surface per frame."""
    monkeypatch.setenv("LUME_MODELS", '{"demo": {"workers": 1}}')
    monkeypatch.setenv("LUME_ROLE", "live")
    monkeypatch.setenv("LUME_LIVE_SOURCE", "epcis")
    with pytest.raises(ValueError, match="Unknown LUME_LIVE_SOURCE"):
        with TestClient(app):
            pass


def test_the_epics_source_without_pyepics_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gap that defeated check_env's own purpose, at the level an operator sees it.

    pyepics is the optional [epics] extra, and the import only happens when the first provider is
    built, on the first live request. So a live Deployment missing the extra used to pass its
    probes and then raise once per frame forever, which is precisely what check_env exists to
    stop. find_spec is patched rather than relying on CI lacking pyepics, so this still fails on
    a dev box that has it installed.
    """
    monkeypatch.setenv("LUME_MODELS", '{"demo": {"workers": 1}}')
    monkeypatch.setenv("LUME_ROLE", "live")
    monkeypatch.setenv("LUME_LIVE_SOURCE", "epics")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match=r"\[epics\] extra"):
        with TestClient(app):
            pass


def test_a_bad_live_source_is_ignored_by_the_eval_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """The eval role never builds a provider, so it must not be held back by a live setting."""
    monkeypatch.setenv("LUME_MODELS", '{"demo": {"workers": 1}}')
    monkeypatch.setenv("LUME_ROLE", "eval")
    monkeypatch.setenv("LUME_LIVE_SOURCE", "epcis")
    with TestClient(app) as client:
        assert client.get("/healthz").json()["status"] == "ok"
