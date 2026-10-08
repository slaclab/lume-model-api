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

import asyncio
import base64
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lume_model_api.api.main import app, live_stream
from lume_model_api.api.schemas import MAX_PARTICLES_LIMIT, MAX_SMOOTH_SIGMA_PX
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


def test_evaluate_says_which_inputs_the_caller_sent_and_which_the_baseline_filled(client) -> None:
    """`inputs` echoes the merge, so without this a design value looks like a chosen one.

    The evaluate route never reads the machine, so every id is one of two things and a client
    that wants to render "you set this" differently from "this is the model default" has no other
    way to tell. If this assertion fails, `input_sources` is missing or disagrees with `inputs`,
    and a client indexing it by an id from `inputs` gets a KeyError.
    """
    body = client.post(f"{DEMO}/evaluate", json={"screen": "OTR_A", "inputs": {QUAD: -3.0}}).json()
    sources = body["input_sources"]
    # Covers exactly the ids the response reports, so the two maps can be zipped by key.
    assert set(sources) == set(body["inputs"])
    assert sources[QUAD] == "request"
    assert sources["DEMO:SOLN:1:BCTRL"] == "baseline"
    # Never "live": this route does not touch the control system at all.
    assert "live" not in set(sources.values())


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


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_input_value_is_422(client, value: str) -> None:
    """A NaN knob passes lume's range check silently, because NaN compares False to everything.

    If this assertion fails the request is a 200 and the model has been run at NaN, returning a
    NaN beam that a client cannot tell from a physics result: `serialize.json_safe` renders it as
    `null`, and a stats field of `null` looks like a quantity the beam does not support. The
    range check cannot catch it, so the schema has to.
    """
    # Sent as raw text, because `json=` cannot express the non-JSON literals that Python's own
    # `json.loads` (and so FastAPI's body parser) nonetheless accepts. That leniency is exactly
    # how a NaN reaches the model in the first place.
    response = client.post(
        f"{DEMO}/evaluate",
        content=f'{{"outputs": ["s"], "inputs": {{"{QUAD}": {value}}}}}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    # The 422 body has to be valid JSON that names the field. FastAPI's own handler echoes the
    # value it rejected and serializes with allow_nan=False, so it cannot encode the very value
    # that caused the error: without the app's handler this becomes an unhandled 500 that tells
    # the caller nothing about which input was wrong.
    detail = response.json()["detail"]
    assert any(QUAD in [str(part) for part in item["loc"]] for item in detail), detail


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_a_non_finite_smoothing_sigma_is_422(client, value: str) -> None:
    """An infinite sigma reaches `scipy.ndimage.gaussian_filter` and never comes back.

    The bound below rejects a merely huge sigma. Non-finite has to be rejected separately,
    because pydantic accepts `Infinity` in a float field by default and `Infinity` is neither
    greater than the maximum nor less than the minimum.
    """
    response = client.post(
        f"{DEMO}/evaluate",
        content=f'{{"screen": "OTR_B", "smooth_images_sigma_px": {value}}}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("sigma", [-1.0, 51.0, 20000.0])
def test_an_out_of_range_smoothing_sigma_is_422(client, sigma: float) -> None:
    """An unbounded sigma is a denial of service against the whole pool.

    `gaussian_filter` cost grows with sigma, and on a real 1040x1392 sensor a large one runs for
    hours. `run_in_executor` cannot cancel work already handed to a subprocess, so that worker is
    never reclaimed and one request permanently removes a slot from a 1-2 worker pool. If this
    assertion fails, a single unauthenticated POST can brick a live pod.
    """
    response = client.post(
        f"{DEMO}/evaluate", json={"screen": "OTR_B", "smooth_images_sigma_px": sigma}
    )
    assert response.status_code == 422


def test_the_largest_allowed_smoothing_sigma_is_still_accepted(client) -> None:
    """The bound must not be so tight that a legitimately wide PSF is rejected."""
    response = client.post(
        f"{DEMO}/evaluate",
        json={"screen": "OTR_B", "smooth_images_sigma_px": MAX_SMOOTH_SIGMA_PX},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("count", [0, -1, MAX_PARTICLES_LIMIT + 1])
def test_an_out_of_range_max_particles_is_422(client, count: int) -> None:
    """The cap protects the payload, not the model, and a non-positive one hid a mistake.

    A real tracked beam can be 1e5+ particles, so the upper bound is about response size: seven
    float32 coordinates per particle means the limit is already megabytes of base64. Zero and
    negative used to fall back to the default inside `model/evaluate.py`, which silently ignored
    what the caller asked for instead of telling it.
    """
    response = client.post(
        f"{DEMO}/evaluate", json={"screen": "OTR_B", "max_particles": count}
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


def test_snapshot_says_which_values_are_really_the_machine(client) -> None:
    """An id whose PV could not be read reports its design value, which reads as machine state.

    The `lume_live_inputs_*` gauges make the aggregate alertable, but they cannot tell an
    operator looking at one snapshot WHICH id degraded. If this assertion fails, `sources` no
    longer covers `inputs` and that question has no answer on the wire again.
    """
    body = client.get(f"{DEMO}/machine-snapshot").json()
    sources = body["sources"]
    assert set(sources) == set(body["inputs"])
    # The synthetic source reads every drivable input, so all three are genuinely live here.
    assert set(sources.values()) == {"live"}


def test_snapshot_values_stay_inside_the_declared_range(client) -> None:
    """A synthetic value outside its range is what a strict model raises on, per frame."""
    limits = {item["id"]: item for item in client.get(f"{DEMO}/config").json()["inputs"]}
    for name, value in client.get(f"{DEMO}/machine-snapshot").json()["inputs"].items():
        assert limits[name]["min"] <= value <= limits[name]["max"], name


def test_metrics_is_served_per_model(client) -> None:
    body = client.get("/metrics").text
    assert 'lume_pool_workers{model="demo"}' in body
    assert 'lume_pool_workers{model="alt"}' in body


def test_live_input_gauges_are_published_per_model(client) -> None:
    """How much of a live frame is real machine data is otherwise invisible.

    An id the provider could not read keeps its design value in the overlay, indistinguishable
    on the wire from a value that came off the machine, so `readable` against `total` is the only
    signal that a pod has quietly degraded to streaming design values.
    """
    client.get(f"{DEMO}/machine-snapshot")
    body = client.get("/metrics").text
    # Three drivable inputs on the demo model, all of them readable from the synthetic source.
    assert 'lume_live_inputs_total{model="demo"} 3.0' in body
    assert 'lume_live_inputs_readable{model="demo"} 3.0' in body


def test_the_live_stream_gauge_is_published_per_model(client) -> None:
    """Set when the hub is built, so a live pod exports it before anyone subscribes."""
    body = client.get("/metrics").text
    assert 'lume_live_streams{model="demo"}' in body
    assert 'lume_live_streams{model="alt"}' in body


def test_live_stream_requires_a_screen_or_outputs(client) -> None:
    assert client.get(f"{DEMO}/live/stream").status_code == 400


def test_too_many_distinct_output_sets_is_503_and_says_what_to_do(client, monkeypatch) -> None:
    """The cap has to be a status code, not an error event on a stream that looks healthy.

    A 200 carrying an error event is indistinguishable to a client from a machine fault, and the
    fix here is the client's: reuse an output set that is already streaming.
    """
    monkeypatch.setattr(app.state.models["demo"].hub, "_max_streams", 0)
    response = client.get(f"{DEMO}/live/stream", params={"outputs": "s"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "already streaming" in detail
    # The other model has its own hub and its own cap.
    assert app.state.models["alt"].hub._max_streams > 0


def test_a_bad_screen_is_still_400_and_not_an_error_event(client) -> None:
    """Validation has to stay ahead of the response, even though subscribe moved into the body."""
    response = client.get(f"{DEMO}/live/stream", params={"screen": "OTR9"})
    assert response.status_code == 400
    assert "OTR_A" in response.json()["detail"]


def test_the_live_stream_subscribes_only_when_its_generator_starts(client, monkeypatch) -> None:
    """A subscriber acquired before the generator runs is a subscriber that leaks.

    sse-starlette can be cancelled before it ever calls `__anext__`, and a generator that never
    started never runs its `finally`. Subscribing outside the generator therefore left a queue in
    the hub and its producer loop evaluating the model forever with no viewer, which on the
    singleton live pod is unrecoverable without a restart.
    """

    class RecordingHub:
        def __init__(self) -> None:
            self.subscribed = 0
            self.released = 0

        def check_capacity(self, outputs) -> None:
            pass

        def subscribe(self, outputs):
            self.subscribed += 1
            queue: asyncio.Queue = asyncio.Queue(maxsize=1)
            queue.put_nowait({"event": "frame", "data": {"frame_index": 0}})
            return tuple(outputs), queue

        def unsubscribe(self, key, queue) -> None:
            self.released += 1

    hub = RecordingHub()
    monkeypatch.setattr(app.state.models["demo"], "hub", hub)

    async def scenario():
        response = await live_stream("demo", screen=None, outputs="s")
        assert hub.subscribed == 0  # nothing to leak if the response is dropped here
        frames = response.body_iterator
        first = await frames.__anext__()
        assert hub.subscribed == 1
        assert json.loads(first["data"])["frame_index"] == 0
        await frames.aclose()  # what a client disconnect does
        assert hub.released == 1

    asyncio.run(scenario())


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
    # Retry-After distinguishes the two 503s, which otherwise look identical to a client.
    # Saturation clears in about one evaluate, so this is the short one.
    assert int(response.headers["Retry-After"]) <= 5


# --- a model that breaks itself --------------------------------------------------
#
# docs/POISONED_WORKER.md. A worker whose model stopped running used to answer 400, blaming the
# caller for the server's own broken state, while `/healthz` reported ok so nothing restarted the
# pod. These drive the escalation from the main process, by substituting an executor that fails
# the way a poisoned worker does, because the demo model cannot be asked to break on demand and a
# model that can is a pytao lattice. The worker-side half is covered in
# tests/test_evaluate_demo.py, which is where the classification lives.


class _FailingExecutor:
    """Stands in for `ProcessPoolExecutor`, failing every submit with one exception.

    `run_in_executor` needs nothing but `submit`, so this is enough to exercise `_submit`'s
    classification without a subprocess. A real one cannot be used here: the exception has to
    come from inside the model, and the demo model works.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def submit(self, fn, *args, **kwargs):
        import concurrent.futures

        future: concurrent.futures.Future = concurrent.futures.Future()
        future.set_exception(self._exc)
        return future


def _series_value(body: str, series: str) -> float:
    """One metric value out of the scrape text, by exact series name and labels."""
    for line in body.splitlines():
        if line.startswith(f"{series} "):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{series} is not in /metrics, so no query can read it")


def _fail_the_pool(monkeypatch, exc: BaseException):
    """Point the demo pool at a failing executor, restoring its death state afterwards.

    `dead` and `dead_reason` are set through monkeypatch before the request so that the latch
    `_mark_dead` sets is undone at teardown. Without that, one test here would leave the
    module-scoped client hosting a permanently dead pool and every later test would 503.
    """
    pool = app.state.models["demo"].pool
    monkeypatch.setattr(pool, "dead", False)
    monkeypatch.setattr(pool, "dead_reason", "")
    monkeypatch.setattr(pool, "_ex", _FailingExecutor(exc))
    return pool


def test_an_unusable_model_is_503_and_never_400(client, monkeypatch) -> None:
    """The defect, at the level a client sees it.

    A request that sets nothing at all used to come back 400 with a numpy reshape message. 400
    tells a client its request was invalid and not to retry, so nothing escalated and no probe
    ever failed.
    """
    from lume_model_api.api.pool import ModelPoisoned

    _fail_the_pool(monkeypatch, ModelPoisoned("model 'demo' became unusable in this worker"))
    response = client.post(f"{DEMO}/evaluate", json={"inputs": {}, "outputs": ["s"]})
    assert response.status_code == 503
    # A pod restart is what clears this, so the client is told to wait for one rather than to
    # retry straight into a terminating pod.
    assert int(response.headers["Retry-After"]) >= 10
    assert "unusable" in response.json()["detail"]


def test_an_unusable_model_makes_healthz_fail_so_the_pod_restarts(client, monkeypatch) -> None:
    """The second half of the bug: no worker process dies, so nothing used to notice.

    `/healthz` is the probe target, and it reported ok at any failing fraction. The pool now
    latches dead on the same chain a lost worker uses, which is what turns this into a restart.
    """
    from lume_model_api.api.pool import ModelPoisoned

    pool = _fail_the_pool(monkeypatch, ModelPoisoned("unusable"))
    client.post(f"{DEMO}/evaluate", json={"outputs": ["s"]})

    assert pool.dead is True
    assert pool.dead_reason == "unusable"
    health = client.get("/healthz")
    assert health.status_code == 503
    # The reason is in the detail because the three causes of a dead pool need different
    # follow-up even though the restart is the same.
    assert "unusable" in health.json()["detail"]
    # Discovery is not health, so the models this pod hosts are still listed.
    assert client.get("/api/v1/models").status_code == 200


def test_the_dead_pool_metric_says_why(client, monkeypatch) -> None:
    """`lume_pool_dead` alone cannot tell a broken model from an OOM kill or a timeout.

    All three end in the same pod restart, so an alert sums over `reason`, but they call for
    different investigations.

    The zero-initialization is asserted on `alt`, which nothing in this module poisons. A
    Prometheus series that appears only once it is non-zero breaks any query written with
    `absent()`, and the registry is process-wide, so asserting it on `demo` would depend on
    which of these tests ran first.
    """
    from lume_model_api.api.pool import ModelPoisoned

    body = client.get("/metrics").text
    for reason in ("worker_lost", "unusable", "timeout"):
        assert f'lume_pool_dead{{model="alt",reason="{reason}"}} 0.0' in body
    assert 'lume_pool_recovery_total{model="alt",outcome="unavailable"} 0.0' in body

    recovery = 'lume_pool_recovery_total{model="demo",outcome="unavailable"}'
    before = _series_value(body, recovery)

    _fail_the_pool(monkeypatch, ModelPoisoned("unusable"))
    client.post(f"{DEMO}/evaluate", json={"outputs": ["s"]})
    body = client.get("/metrics").text
    assert 'lume_pool_dead{model="demo",reason="unusable"} 1.0' in body
    # The cause is distinguished rather than collapsed: a model that broke itself is not a lost
    # worker, even though the pod restart is the same.
    assert 'lume_pool_dead{model="demo",reason="worker_lost"} 0.0' in body
    # A delta rather than an absolute: the counter is process-wide and the tests above this one
    # poison the same model, so what this can honestly claim is that a poisoning was counted.
    assert _series_value(body, recovery) == before + 1


def test_a_recovered_model_is_503_without_killing_the_pool(client, monkeypatch) -> None:
    """`ModelUnusable` reaching the route means the worker recovered, so the pod keeps serving.

    Inert today, because recovery needs the model to define `recover()` and nothing does (see
    docs/ARCHITECTURE.md). Pinned anyway, because the arm exists so that a model which grows one
    does not cost a pod restart per poisoning: without it this would be a 500.
    """
    from lume_model_api.model.evaluate import ModelUnusable

    pool = _fail_the_pool(monkeypatch, ModelUnusable("the model failed while applying inputs"))
    response = client.post(f"{DEMO}/evaluate", json={"outputs": ["s"]})
    assert response.status_code == 503
    assert "Retry-After" in response.headers
    assert pool.dead is False
    assert client.get("/healthz").status_code == 200
