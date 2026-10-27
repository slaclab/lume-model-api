"""The live broadcast hub, against a stub pool.

`api/live_hub.py` is the most intricate code in the package and had no direct test: loop sharing
by output set, drop-old queues, seeding a new viewer, cancelling a loop when its last viewer
leaves, surviving a transient error, and stopping dead when the pool is unrecoverable. All of
that is reachable with a stub pool and a stub input reader, so none of it needs a model, a
subprocess or EPICS.

Written with `asyncio.run` rather than `@pytest.mark.asyncio` on purpose: `pytest-asyncio` is
not in this package's `[dev]` extra, so an async test body would silently not run in CI.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from prometheus_client import REGISTRY

from lume_model_api.api.live_hub import GENERIC_ERROR_MESSAGE, LiveHub, TooManyStreams
from lume_model_api.api.pool import PoolDead

# Long enough for the producer to complete several frames, short enough to keep the suite fast.
SETTLE_S = 0.06


class StubPool:
    """Stands in for `ModelPool`, recording calls and optionally failing."""

    def __init__(
        self,
        error: Exception | None = None,
        error_times: int | None = None,
        max_inflight: int = 8,
    ) -> None:
        self.error = error
        self.error_times = error_times  # None means "fail forever"
        # The hub reads this to cap its producer loops, so a stub without it is not a stand-in
        # for `ModelPool`. 8 is the live Deployment's value (2 workers, 4x).
        self.max_inflight = max_inflight
        self.calls: list[dict] = []

    async def evaluate(self, inputs, outputs, kind="interactive", frame_index=0, **kwargs):
        self.calls.append(
            {"inputs": inputs, "outputs": list(outputs), "kind": kind, "frame_index": frame_index}
        )
        # Pace the loop. Without this the producer spins as fast as the event loop allows and
        # starves the test coroutine.
        await asyncio.sleep(0.005)
        if self.error is not None and (self.error_times is None or len(self.calls) <= self.error_times):
            raise self.error
        return {
            "timestamp": 1.0,
            "frame_index": frame_index,
            "inputs": dict(inputs),
            "outputs": {name: {"kind": "scalar", "value": 1.0, "unit": "m"} for name in outputs},
        }


async def _read_inputs() -> tuple[dict, dict]:
    """`(merged, live)`, matching `main._read_live_inputs`. Here every id read off the machine."""
    return {"KNOB": 1.0}, {"KNOB": 1.0}


def _hub(pool=None, read_inputs=_read_inputs, **kwargs) -> LiveHub:
    return LiveHub(pool or StubPool(), read_inputs, model="demo", version="demo", **kwargs)


def _stream_gauge(model: str = "demo") -> float | None:
    return REGISTRY.get_sample_value("lume_live_streams", {"model": model})


def _run(coro):
    return asyncio.run(coro)


async def _first_frame(queue: asyncio.Queue, timeout: float = 2.0) -> dict:
    item = await asyncio.wait_for(queue.get(), timeout=timeout)
    assert item["event"] == "frame", item
    return item["data"]


# --- loop sharing ----------------------------------------------------------------


def test_one_loop_serves_every_subscriber_of_the_same_output_set() -> None:
    """The property that lets a singleton producer serve many browsers."""

    async def scenario():
        hub = _hub()
        key_a, queue_a = hub.subscribe(["s"])
        key_b, queue_b = hub.subscribe(["s"])
        assert key_a == key_b
        assert len(hub._streams) == 1
        first = await _first_frame(queue_a)
        second = await _first_frame(queue_b)
        # Same frame object fanned out, not two independent evaluates.
        assert first["outputs"].keys() == second["outputs"].keys()
        await hub.shutdown()

    _run(scenario())


def test_the_same_ids_in_a_different_order_share_a_loop() -> None:
    """Keyed on the sorted, deduplicated set, so two routes to one view cost one evaluate."""

    async def scenario():
        hub = _hub()
        key_a, _ = hub.subscribe(["s", "x.beta"])
        key_b, _ = hub.subscribe(["x.beta", "s", "s"])
        assert key_a == key_b
        assert len(hub._streams) == 1
        await hub.shutdown()

    _run(scenario())


def test_a_different_output_set_gets_its_own_loop() -> None:
    async def scenario():
        hub = _hub()
        hub.subscribe(["s"])
        hub.subscribe(["s", "x.beta"])
        assert len(hub._streams) == 2
        await hub.shutdown()

    _run(scenario())


def test_a_new_subscriber_is_seeded_with_the_latest_frame() -> None:
    """So a UI paints immediately instead of waiting a whole evaluate."""

    async def scenario():
        hub = _hub()
        _, first = hub.subscribe(["s"])
        await _first_frame(first)
        _, late = hub.subscribe(["s"])
        # Already populated at subscribe time, without awaiting a new frame.
        assert late.qsize() == 1
        assert (await _first_frame(late))["outputs"].keys() == {"s"}
        await hub.shutdown()

    _run(scenario())


def test_the_loop_stops_when_its_last_subscriber_leaves() -> None:
    async def scenario():
        hub = _hub()
        key, queue = hub.subscribe(["s"])
        await _first_frame(queue)
        task = hub._streams[key].task
        hub.unsubscribe(key, queue)
        assert hub._streams == {}
        await asyncio.sleep(0.02)
        assert task.cancelled() or task.done()

    _run(scenario())


def test_a_remaining_subscriber_keeps_the_loop_running() -> None:
    async def scenario():
        hub = _hub()
        key, queue_a = hub.subscribe(["s"])
        _, queue_b = hub.subscribe(["s"])
        await _first_frame(queue_a)
        hub.unsubscribe(key, queue_a)
        assert key in hub._streams
        await _first_frame(queue_b)
        await hub.shutdown()

    _run(scenario())


# --- what a frame must carry -----------------------------------------------------


def test_every_frame_carries_model_and_version() -> None:
    """The SSE path bypasses the HTTP endpoint, so the hub has to attach these itself.

    Clients type the stream from `EvaluateV1Response` and treat every key as present, and
    nothing on that path validates the payload, so a frame without these two is not the shape
    the client was told to expect. This replaces an older test that grepped live_hub.py's
    source text for `wire["model"]`, which passed or failed on how the assignment was spelled.
    """

    async def scenario():
        hub = _hub()
        _, queue = hub.subscribe(["s"])
        frame = await _first_frame(queue)
        assert frame["model"] == "demo"
        assert frame["version"] == "demo"
        await hub.shutdown()

    _run(scenario())


def test_every_frame_says_where_each_input_value_came_from() -> None:
    """An id whose PV did not read carries the model's design value, which looks identical.

    If this assertion fails, a live pod that has quietly degraded to streaming design values is
    indistinguishable at the response level from one reading the real machine, which is the
    whole reason the field exists. The SSE path has no response_model to fill the key in, so a
    producer that stops attaching it sends frames with `input_sources` genuinely absent rather
    than empty.
    """

    async def scenario():
        async def read_partial() -> tuple[dict, dict]:
            # SOLENOID is in the merged values from the baseline alone: its PV did not read.
            return {"KNOB": 1.0, "SOLENOID": 2.0}, {"KNOB": 1.0}

        hub = LiveHub(StubPool(), read_partial, model="demo", version="demo")
        _, queue = hub.subscribe(["s"])
        frame = await _first_frame(queue)
        assert frame["input_sources"] == {"KNOB": "live", "SOLENOID": "baseline"}
        await hub.shutdown()

    _run(scenario())


def test_frame_index_increments_on_the_stream() -> None:
    async def scenario():
        hub = _hub()
        _, queue = hub.subscribe(["s"])
        assert (await _first_frame(queue))["frame_index"] == 0
        indices = set()
        for _ in range(3):
            indices.add((await _first_frame(queue))["frame_index"])
        assert max(indices) >= 1
        await hub.shutdown()

    _run(scenario())


def test_non_finite_values_are_nulled_so_a_frame_is_valid_json() -> None:
    """NaN reaches the wire from a non-converged solver or a degenerate beam.

    `json.dumps` renders it as the literal `NaN`, which is not JSON, so a browser's
    `JSON.parse` throws and the whole frame is lost. The HTTP route's response_model already
    maps non-finite to null, so nulling here is what makes the two paths agree.
    """

    async def scenario():
        pool = StubPool()

        async def read_nan() -> tuple[dict, dict]:
            return {"KNOB": float("nan")}, {"KNOB": float("nan")}

        hub = LiveHub(pool, read_nan, model="demo", version="demo")
        _, queue = hub.subscribe(["s"])
        frame = await _first_frame(queue)
        assert frame["inputs"]["KNOB"] is None
        # The bytes an SSE client actually receives must parse.
        text = json.dumps(frame)
        assert "NaN" not in text and "Infinity" not in text
        assert json.loads(text)["inputs"]["KNOB"] is None
        await hub.shutdown()

    _run(scenario())


# --- failure handling ------------------------------------------------------------


def test_a_transient_error_is_broadcast_and_the_loop_survives() -> None:
    async def scenario():
        pool = StubPool(error=RuntimeError("solver blew up"), error_times=1)
        hub = _hub(pool)
        _, queue = hub.subscribe(["s"])
        first = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert first["event"] == "error"
        assert first["data"]["message"] == GENERIC_ERROR_MESSAGE
        # The loop kept going, so a later frame still arrives.
        assert (await _first_frame(queue))["model"] == "demo"
        await hub.shutdown()

    _run(scenario())


def test_the_exception_text_of_a_transient_error_never_reaches_a_subscriber(caplog) -> None:
    """Subscribers are browsers, so the message a solver raised is not theirs to see.

    An exception string from inside a model routinely carries absolute paths, lattice element
    names and library internals. If this assertion fails, all of that is being pushed to every
    connected browser, and the operator has lost the traceback too, since the log line here is
    the only place it now exists.
    """

    async def scenario():
        pool = StubPool(error=RuntimeError("/opt/lattice/secret.bmad rejected element Q01"))
        hub = _hub(pool)
        _, queue = hub.subscribe(["s"])
        item = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert item["event"] == "error"
        assert "secret.bmad" not in json.dumps(item["data"])
        await hub.shutdown()

    with caplog.at_level("ERROR", logger="lume_model_api.api.live_hub"):
        _run(scenario())
    # Server side keeps everything, including the traceback logger.exception attaches.
    assert "secret.bmad" in caplog.text
    assert "RuntimeError" in caplog.text


def test_a_dead_pool_stops_the_loop_after_one_error() -> None:
    """A lost worker never comes back, so retrying would only spam every viewer.

    Without this the loop would spin at the error backoff forever, broadcasting an error event
    about twice a second to every subscriber and inflating
    lume_evaluate_total{outcome="error"} until the liveness probe restarted the pod.
    """

    async def scenario():
        pool = StubPool(error=PoolDead("model 'demo' lost a worker process"))
        hub = _hub(pool)
        key, queue = hub.subscribe(["s"])
        task = hub._streams[key].task
        item = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert item["event"] == "error"
        assert "lost a worker" in item["data"]["message"]
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
        assert task.done() and not task.cancelled()
        # Stopped producing: exactly one attempt, and no further events queued.
        assert len(pool.calls) == 1
        assert queue.empty()
        # And the stream is forgotten, so it cannot be adopted by a later subscriber.
        assert key not in hub._streams

    _run(scenario())


def test_a_subscriber_arriving_after_a_dead_pool_is_told_rather_than_stalled() -> None:
    """A finished producer must not be reused, or the new viewer waits forever in silence."""

    async def scenario():
        pool = StubPool(error=PoolDead("model 'demo' lost a worker process"))
        hub = _hub(pool)
        key, first = hub.subscribe(["s"])
        assert (await asyncio.wait_for(first.get(), timeout=2.0))["event"] == "error"
        await asyncio.sleep(0.02)
        # A second viewer of the same output set gets its own attempt and its own error.
        _, second = hub.subscribe(["s"])
        item = await asyncio.wait_for(second.get(), timeout=2.0)
        assert item["event"] == "error"
        assert "lost a worker" in item["data"]["message"]

    _run(scenario())


def test_a_slow_subscriber_gets_the_newest_frame_not_a_backlog() -> None:
    """Size-1 drop-old queues: a slow client loses frames rather than growing a queue."""

    async def scenario():
        hub = _hub()
        _, queue = hub.subscribe(["s"])
        await asyncio.sleep(SETTLE_S)  # produce several frames without draining
        assert queue.qsize() == 1
        frame = await _first_frame(queue)
        assert frame["frame_index"] >= 1  # the newest, not frame 0
        assert queue.empty()
        await hub.shutdown()

    _run(scenario())


def test_shutdown_stops_every_producer() -> None:
    async def scenario():
        hub = _hub()
        _, first = hub.subscribe(["s"])
        hub.subscribe(["s", "x.beta"])
        tasks = [stream.task for stream in hub._streams.values()]
        await _first_frame(first)
        await hub.shutdown()
        assert hub._streams == {}
        assert all(task.done() for task in tasks)

    _run(scenario())


def test_shutdown_is_safe_with_no_subscribers() -> None:
    _run(_hub().shutdown())


def test_unsubscribing_twice_is_harmless() -> None:
    """The SSE generator's `finally` can run after the hub already dropped the stream."""

    async def scenario():
        hub = _hub()
        key, queue = hub.subscribe(["s"])
        hub.unsubscribe(key, queue)
        hub.unsubscribe(key, queue)

    _run(scenario())


def test_the_producer_asks_the_pool_for_live_kind_and_the_resolved_outputs() -> None:
    """`kind="live"` is what keeps stream load separable from user load in the metrics."""

    async def scenario():
        pool = StubPool()
        hub = _hub(pool)
        _, queue = hub.subscribe(["x.beta", "s"])
        await _first_frame(queue)
        assert pool.calls[0]["kind"] == "live"
        assert pool.calls[0]["outputs"] == ["s", "x.beta"]  # sorted key order
        assert pool.calls[0]["inputs"] == {"KNOB": 1.0}
        await hub.shutdown()

    _run(scenario())


@pytest.mark.parametrize("outputs", [["s"], ["s", "x.beta"]])
def test_key_for_is_sorted_and_deduplicated(outputs: list[str]) -> None:
    assert LiveHub.key_for(outputs + outputs) == tuple(sorted(set(outputs)))


# --- how many producer loops one hub will run -------------------------------------


def test_the_stream_cap_defaults_to_the_pools_in_flight_limit() -> None:
    """More loops than in-flight slots cannot help, so that is the natural ceiling.

    Past it the extra loops only collect PoolFull errors while dividing every legitimate
    viewer's frame rate by the number of loops.
    """

    async def scenario():
        hub = _hub(StubPool(max_inflight=3))
        for index in range(3):
            hub.subscribe([f"out{index}"])
        with pytest.raises(TooManyStreams):
            hub.subscribe(["one.too.many"])
        assert len(hub._streams) == 3
        await hub.shutdown()

    _run(scenario())


def test_the_stream_cap_can_be_overridden() -> None:
    async def scenario():
        hub = _hub(StubPool(max_inflight=8), max_streams=1)
        hub.subscribe(["s"])
        with pytest.raises(TooManyStreams):
            hub.subscribe(["x.beta"])
        await hub.shutdown()

    _run(scenario())


def test_joining_an_existing_output_set_is_never_capped() -> None:
    """The cap counts loops, not viewers. Sharing a loop is exactly what the hub is for."""

    async def scenario():
        hub = _hub(StubPool(max_inflight=1))
        _, first = hub.subscribe(["s"])
        _, second = hub.subscribe(["s"])
        assert len(hub._streams) == 1
        await _first_frame(second)
        await hub.shutdown()

    _run(scenario())


def test_a_disconnected_viewer_frees_a_slot() -> None:
    """Otherwise the cap would be a one-way ratchet and the pod would need a restart."""

    async def scenario():
        hub = _hub(StubPool(max_inflight=1))
        key, queue = hub.subscribe(["s"])
        hub.unsubscribe(key, queue)
        hub.subscribe(["x.beta"])  # the slot came back
        await hub.shutdown()

    _run(scenario())


def test_the_cap_message_tells_the_client_what_to_do_instead() -> None:
    """Retrying does not help, so a message that does not say so produces a polling client."""

    async def scenario():
        hub = _hub(max_streams=1)
        hub.subscribe(["s"])
        with pytest.raises(TooManyStreams) as excinfo:
            hub.check_capacity(["x.beta"])
        message = str(excinfo.value)
        assert "already streaming" in message
        assert "Retrying" in message
        await hub.shutdown()

    _run(scenario())


def test_check_capacity_does_not_start_a_loop() -> None:
    """The SSE route calls it before its response starts, and must not acquire anything."""

    async def scenario():
        hub = _hub()
        hub.check_capacity(["s"])
        assert hub._streams == {}

    _run(scenario())


def test_the_stream_gauge_follows_the_live_loop_count() -> None:
    """The degraded state has to be alertable, and a leaked loop is only visible here."""

    async def scenario():
        hub = LiveHub(StubPool(), _read_inputs, model="gaugehub", version="demo")
        assert _stream_gauge("gaugehub") == 0
        key, queue = hub.subscribe(["s"])
        hub.subscribe(["s", "x.beta"])
        assert _stream_gauge("gaugehub") == 2
        hub.unsubscribe(key, queue)
        assert _stream_gauge("gaugehub") == 1
        await hub.shutdown()
        assert _stream_gauge("gaugehub") == 0

    _run(scenario())


# --- frame_index is monotonic per hub --------------------------------------------


def test_frame_index_does_not_restart_when_the_last_viewer_leaves() -> None:
    """docs/API.md promises a counter that only increases, and a UI may key on it.

    Per stream it restarted at 0 every time a stream's last viewer left and a new one arrived,
    so an ordinary browser reload made frame_index go backwards.
    """

    async def scenario():
        hub = _hub()
        key, queue = hub.subscribe(["s"])
        await asyncio.sleep(SETTLE_S)
        highest = (await _first_frame(queue))["frame_index"]
        assert highest >= 1
        hub.unsubscribe(key, queue)
        await asyncio.sleep(0.02)
        _, fresh = hub.subscribe(["s"])
        assert (await _first_frame(fresh))["frame_index"] > highest
        await hub.shutdown()

    _run(scenario())
