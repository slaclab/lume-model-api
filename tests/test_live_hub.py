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

from lume_model_api.api.live_hub import LiveHub
from lume_model_api.api.pool import PoolDead

# Long enough for the producer to complete several frames, short enough to keep the suite fast.
SETTLE_S = 0.06


class StubPool:
    """Stands in for `ModelPool`, recording calls and optionally failing."""

    def __init__(self, error: Exception | None = None, error_times: int | None = None) -> None:
        self.error = error
        self.error_times = error_times  # None means "fail forever"
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


async def _read_inputs() -> dict:
    return {"KNOB": 1.0}


def _hub(pool=None, read_inputs=_read_inputs) -> LiveHub:
    return LiveHub(pool or StubPool(), read_inputs, model="demo", version="demo")


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

        async def read_nan() -> dict:
            return {"KNOB": float("nan")}

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
        assert "solver blew up" in first["data"]["message"]
        # The loop kept going, so a later frame still arrives.
        assert (await _first_frame(queue))["model"] == "demo"
        await hub.shutdown()

    _run(scenario())


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
