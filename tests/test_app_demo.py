"""End-to-end through the real app: TestClient, lifespan, two one-worker subprocess pools.

This is the test that would have caught the whole class of bug the old mock hid, because it
runs the same code a production pod runs: spawn a worker, build a LUMEModel in it, describe it,
serve the config route from that description, and evaluate through the pool.

It hosts the demo model twice, under the names `demo` and `alt`, because one hosted model
cannot show the thing that actually breaks in a multi-model host: a route, a hub or a metric
that quietly answers for the wrong model.

The settings are set with `monkeypatch.setenv` before the TestClient enters the lifespan, which
works because `lifespan` reads the environment rather than module globals. That matters: in a
full run `tests/test_api_contract.py` imports `main` during collection, so anything read at
import time would be fixed before this fixture ran.

Runs on the bare CI setup: no torch, no pytao, no EPICS, no lattice.
"""

from __future__ import annotations

import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lume_model_api.api.main import app
from lume_model_api.model.demo import (
    CHARGE,
    QUAD,
    SCREEN_B_BEAM,
    SCREEN_B_IMAGE,
)

# Both entries build the demo model, one via the shortcut key and one via a full factory path,
# which is also what makes their `version` strings differ.
MODELS = (
    '{"demo": {"workers": 1}, '
    '"alt": {"factory": "lume_model_api.model.demo:make_demo_model", "workers": 1}}'
)

DEMO = "/api/v1/models/demo"
ALT = "/api/v1/models/alt"


@pytest.fixture(scope="module")
def client():
    with pytest.MonkeyPatch.context() as patch:
        # One worker per model: spawning four would cost four model builds for no extra
        # coverage. Read inside lifespan, so setting them here is enough.
        patch.setenv("LUME_MODELS", MODELS)
        patch.setenv("LUME_LIVE_SOURCE", "synthetic")
        # Set through the environment like every other setting: the role is read inside the
        # lifespan, so there is no module global left to patch.
        patch.setenv("LUME_ROLE", "all")
        # The context manager is what runs lifespan, which is what builds the pools. Without
        # it app.state.models is never populated and every route 500s.
        with TestClient(app) as test_client:
            yield test_client


def test_models_lists_what_this_process_hosts(client) -> None:
    """What a UI dropdown reads. Sorted, so the order does not depend on the env var."""
    body = client.get("/api/v1/models").json()
    assert [entry["name"] for entry in body] == ["alt", "demo"]
    versions = {entry["name"]: entry["version"] for entry in body}
    # The demo flag survives selection by full factory path, under the name it was given.
    assert versions == {"alt": "alt (demo)", "demo": "demo"}
    assert all(entry["description"] for entry in body)


def test_config_describes_the_model(client) -> None:
    config = client.get(f"{DEMO}/config").json()
    assert config["model"] == "demo"
    assert config["version"] == "demo"
    assert config["description"]

    inputs = {item["id"]: item for item in config["inputs"]}
    assert inputs[QUAD]["range_source"] == "model"
    assert inputs["DEMO:XCOR:1:BCTRL"]["range_source"] == "derived"
    assert inputs[CHARGE]["constant"] is True

    screens = {item["key"]: item for item in config["screens"]}
    assert screens["OTR_A"]["image"] is None
    assert screens["OTR_B"]["image"] == SCREEN_B_IMAGE

    kinds = {item["id"]: item["kind"] for item in config["outputs"]}
    assert kinds[SCREEN_B_BEAM] == "particles"
    assert kinds[SCREEN_B_IMAGE] == "array"


@pytest.mark.parametrize("name", ["demo", "alt"])
def test_config_model_is_the_url_name(client, name: str) -> None:
    """`config.model` is what a dropdown stored, not the factory it happens to resolve to."""
    config = client.get(f"/api/v1/models/{name}/config").json()
    assert config["model"] == name


def test_an_unknown_model_is_404_listing_the_hosted_names(client) -> None:
    response = client.get("/api/v1/models/nope/config")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "alt" in detail and "demo" in detail


def test_an_unknown_model_is_404_on_evaluate_too(client) -> None:
    response = client.post("/api/v1/models/nope/evaluate", json={"screen": "OTR_B"})
    assert response.status_code == 404


def test_both_models_evaluate_independently(client) -> None:
    """Two pools, two workers, each answering under its own name."""
    for name in ("demo", "alt"):
        body = client.post(f"/api/v1/models/{name}/evaluate", json={"outputs": ["s"]}).json()
        assert body["model"] == name
        assert set(body["outputs"]) == {"s"}
    demo_body = client.post(f"{DEMO}/evaluate", json={"outputs": ["DEMO:OTRA:XRMS"]}).json()
    alt_body = client.post(
        f"{ALT}/evaluate", json={"inputs": {QUAD: -3.0}, "outputs": ["DEMO:OTRA:XRMS"]}
    ).json()
    # A knob set on one model does not reach the other, which is the point of two pools.
    assert demo_body["inputs"][QUAD] != alt_body["inputs"][QUAD]
    assert alt_body["version"] == "alt (demo)"


@pytest.mark.parametrize(
    "path",
    ["/api/config", "/api/v1/evaluate", "/api/machine-snapshot", "/api/live/stream"],
)
def test_the_unprefixed_routes_are_gone(client, path: str) -> None:
    """One scheme only. An implicit default model is how a client drives the wrong one."""
    assert client.get(path).status_code == 404
    assert client.post(path, json={}).status_code == 404


def test_evaluate_with_a_screen(client) -> None:
    body = client.post(f"{DEMO}/evaluate", json={"screen": "OTR_B"}).json()
    assert set(body["outputs"]) == {SCREEN_B_BEAM, SCREEN_B_IMAGE}
    assert body["model"] == "demo"
    assert body["outputs"][SCREEN_B_BEAM]["kind"] == "particles"
    image = body["outputs"][SCREEN_B_IMAGE]
    decoded = np.frombuffer(base64.b64decode(image["data_b64"]), dtype="<f4")
    assert decoded.size == image["shape"][0] * image["shape"][1]


def test_evaluate_echoes_the_effective_inputs(client) -> None:
    body = client.post(f"{DEMO}/evaluate", json={"screen": "OTR_A", "inputs": {QUAD: -3.0}}).json()
    assert body["inputs"][QUAD] == -3.0
    # The baseline filled in the knobs the request left out.
    assert "DEMO:SOLN:1:BCTRL" in body["inputs"]


def test_evaluate_with_explicit_outputs(client) -> None:
    ids = ["s", "x.beta", "y.beta", "DEMO:OTRA:XRMS"]
    body = client.post(f"{DEMO}/evaluate", json={"outputs": ids}).json()
    assert set(body["outputs"]) == set(ids)
    assert body["outputs"]["DEMO:OTRA:XRMS"]["unit"] == "m"


def test_screen_and_explicit_outputs_combine_without_duplicating(client) -> None:
    body = client.post(
        f"{DEMO}/evaluate", json={"screen": "OTR_B", "outputs": [SCREEN_B_BEAM, "s"]}
    ).json()
    assert set(body["outputs"]) == {SCREEN_B_BEAM, SCREEN_B_IMAGE, "s"}


def test_unknown_output_is_400(client) -> None:
    response = client.post(f"{DEMO}/evaluate", json={"outputs": ["NO:SUCH:PV"]})
    assert response.status_code == 400
    # The hint names the model that was addressed, so it is a path the caller can fetch.
    assert f"{DEMO}/config" in response.json()["detail"]


def test_unknown_screen_is_400(client) -> None:
    response = client.post(f"{DEMO}/evaluate", json={"screen": "OTR9"})
    assert response.status_code == 400
    assert "OTR_A" in response.json()["detail"]


def test_unknown_input_is_400(client) -> None:
    response = client.post(
        f"{DEMO}/evaluate", json={"screen": "OTR_A", "inputs": {"NO:SUCH:PV": 1.0}}
    )
    assert response.status_code == 400


def test_non_numeric_input_is_422_not_500(client) -> None:
    """Only scalar variables become inputs, so the schema itself rejects a string value."""
    response = client.post(
        f"{DEMO}/evaluate", json={"screen": "OTR_A", "inputs": {QUAD: "wide open"}}
    )
    assert response.status_code == 422


def test_empty_request_is_400_and_says_where_to_look(client) -> None:
    """A generic host has no sensible default output set, so it has to say so usefully."""
    response = client.post(f"{DEMO}/evaluate", json={})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "outputs" in detail and "screen" in detail and f"{DEMO}/config" in detail


def test_machine_snapshot_covers_every_drivable_input(client) -> None:
    inputs = client.get(f"{DEMO}/machine-snapshot").json()["inputs"]
    assert set(inputs) == {QUAD, "DEMO:SOLN:1:BCTRL", "DEMO:XCOR:1:BCTRL"}
    assert CHARGE not in inputs  # constants are neither read nor driven
    assert all(isinstance(value, float) for value in inputs.values())


def test_snapshot_values_stay_inside_the_declared_range(client) -> None:
    """A synthetic value outside its range is what a strict model raises on, per frame."""
    limits = {item["id"]: item for item in client.get(f"{DEMO}/config").json()["inputs"]}
    for name, value in client.get(f"{DEMO}/machine-snapshot").json()["inputs"].items():
        assert limits[name]["min"] <= value <= limits[name]["max"], name


def test_metrics_is_served_per_model(client) -> None:
    body = client.get("/metrics").text
    assert 'lume_pool_workers{model="demo"}' in body
    assert 'lume_pool_workers{model="alt"}' in body


def test_live_stream_requires_a_screen_or_outputs(client) -> None:
    assert client.get(f"{DEMO}/live/stream").status_code == 400


# --- health and backpressure -----------------------------------------------------


def test_healthz_is_ok_when_every_pool_has_warmed(client) -> None:
    """The k8s probe target. It must not be part of the published schema."""
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["models"] == ["alt", "demo"]
    assert "/healthz" not in app.openapi()["paths"]


def test_healthz_fails_when_a_pool_is_dead(client, monkeypatch) -> None:
    """A lost worker is unrecoverable, so the pod has to be restarted rather than serve 503s."""
    monkeypatch.setattr(app.state.models["demo"].pool, "dead", True)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert "demo" in response.json()["detail"]


def test_the_model_list_still_answers_when_a_pool_is_dead(client, monkeypatch) -> None:
    """Discovery is not health. One dead model must not hide the others from a client."""
    monkeypatch.setattr(app.state.models["demo"].pool, "dead", True)
    names = [entry["name"] for entry in client.get("/api/v1/models").json()]
    assert names == ["alt", "demo"]


def test_evaluate_on_a_dead_pool_is_503(client, monkeypatch) -> None:
    monkeypatch.setattr(app.state.models["demo"].pool, "dead", True)
    response = client.post(f"{DEMO}/evaluate", json={"outputs": ["s"]})
    assert response.status_code == 503
    assert "cannot recover" in response.json()["detail"]
    # The other model is unaffected, which is the point of a pool per model.
    assert client.post(f"{ALT}/evaluate", json={"outputs": ["s"]}).status_code == 200


def test_a_saturated_pool_is_503(client, monkeypatch) -> None:
    """Backpressure. monkeypatch so the module-scoped client is restored for later tests."""
    monkeypatch.setattr(app.state.models["demo"].pool, "max_inflight", 0)
    response = client.post(f"{DEMO}/evaluate", json={"outputs": ["s"]})
    assert response.status_code == 503
    assert "saturated" in response.json()["detail"]
