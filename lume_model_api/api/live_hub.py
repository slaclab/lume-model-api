"""Shared live-view broadcast.

One background loop per *distinct output set* reads the live inputs and evaluates as fast as
it can (paced only by evaluate latency, there is no poll period), then fans the resulting
frame out to every SSE subscriber asking for that same set. So N viewers of one set cost one
evaluate loop, not N, which is what lets the singleton live producer serve many viewers on a
small (1-2 worker) pool. A loop starts on its first subscriber and stops when the last leaves.

Loops are keyed on the sorted output tuple rather than on a screen name, because the output
set is what determines the work: two clients that asked for the same ids by different routes
(one via `screen`, one via `outputs`) share a loop, and a client that asked for an extra id
gets its own.

The number of distinct loops is capped, by default at the pool's `max_inflight`, because that
same sharing means a client can multiply the work simply by varying its output set. See
`LiveHub.__init__`.

Each subscriber gets a size-1 drop-old queue: a slow client always receives the newest frame,
never a backlog.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Sequence

from . import metrics
from .pool import PoolDead
from .schemas import live_sources
from .serialize import json_safe

# Returns `(merged, live)`: the input values to evaluate at, already overlaid on the model's
# baseline, and the subset the provider actually read off the machine. The second half exists
# because the merge is lossy in the one way an operator cares about: an id whose PV could not be
# read carries the model's design value in `merged` and is indistinguishable there from a real
# reading. Comparing the two is what fills `input_sources` on every frame.
ReadInputs = Callable[[], Awaitable[tuple[dict, dict]]]

Key = tuple[str, ...]

logger = logging.getLogger(__name__)

# How long shutdown waits for a cancelled producer to actually finish. Bounded because it runs
# in a lifespan `finally`, where an unbounded await would hold the whole process past the k8s
# termination grace period and earn a SIGKILL.
SHUTDOWN_TIMEOUT_S = 2.0

# Backoff after a transient error, to avoid a hot spin on repeated failures.
ERROR_BACKOFF_S = 0.5

# What a client is told when a frame fails. The real exception text can name file paths, lattice
# elements, library internals and a model's own private structure, and every subscriber here is
# an untrusted browser, so the detail goes to the pod log and the browser gets this. `PoolDead`
# is deliberately exempt: its message is operationally useful, tells the operator to expect a
# pod restart, and contains nothing worth hiding.
GENERIC_ERROR_MESSAGE = (
    "The live view could not produce a frame. The server log has the detail. The stream keeps "
    "retrying, so a transient failure clears by itself."
)


class TooManyStreams(RuntimeError):
    """The hub already runs its maximum number of distinct producer loops.

    Its own class so the SSE route can answer 503 for this and let anything else be a 500. The
    caller's fix is to reuse an output set that is already streaming rather than to retry, so the
    message says that rather than leaving a client to poll.
    """


class _Stream:
    def __init__(self, outputs: Sequence[str]) -> None:
        self.outputs = list(outputs)
        self.subscribers: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None
        self.latest: dict | None = None


class LiveHub:
    def __init__(
        self,
        pool,
        read_inputs: ReadInputs,
        model: str,
        version: str,
        max_streams: int | None = None,
    ) -> None:
        self._pool = pool
        self._read_inputs = read_inputs
        # The SSE stream bypasses the HTTP endpoint, which is what normally attaches these
        # two. Without them a streamed frame would not be a complete EvaluateV1Response,
        # which is what every client types the stream as. Attached here, once per frame,
        # rather than per subscriber.
        self._model = model
        self._version = version
        self._streams: dict[Key, _Stream] = {}
        # Defaults to the pool's in-flight limit because more producer loops than in-flight
        # slots is never useful: the loops past that point only collect PoolFull errors while
        # every legitimate viewer's frame rate falls as 1/P. The live Deployment is a single
        # replica with 1-2 workers, so P is small and one browser opening many distinct output
        # sets is enough to starve everyone else. Overridable for tests and for an operator who
        # has measured something different.
        self._max_streams = pool.max_inflight if max_streams is None else max_streams
        # One counter for the whole hub rather than one per stream. A per-stream counter restarted
        # at 0 whenever the last viewer of that set left and a new one arrived, so a client that
        # reconnected saw `frame_index` go backwards, and docs/API.md promises it only increases.
        # Per hub it is monotonic for every stream, with gaps where a sibling stream produced.
        self._frame_index = 0
        metrics.LIVE_STREAMS.labels(model=model).set(0)

    @staticmethod
    def key_for(outputs: Sequence[str]) -> Key:
        return tuple(sorted(set(outputs)))

    def check_capacity(self, outputs: Sequence[str]) -> None:
        """Raise `TooManyStreams` if `subscribe` would have to start a loop and cannot.

        Separate from `subscribe` so the SSE route can answer 503 before its response starts.
        Once `EventSourceResponse` has begun there is no status code left to send, only an error
        event on a stream the client believes succeeded.
        """
        if self.key_for(outputs) in self._streams:
            return  # joining an existing loop costs nothing, so the cap does not apply
        if len(self._streams) >= self._max_streams:
            raise TooManyStreams(
                f"model {self._model!r} is already producing its maximum of "
                f"{self._max_streams} distinct live output set(s). Subscribe to one of the sets "
                "already streaming instead of asking for a new combination, or request the "
                "outputs you need over the evaluate route. Retrying this request will not help "
                "until another viewer disconnects."
            )

    def subscribe(self, outputs: Sequence[str]) -> tuple[Key, asyncio.Queue]:
        key = self.key_for(outputs)
        stream = self._streams.get(key)
        if stream is None:
            self.check_capacity(key)
            stream = _Stream(key)
            self._streams[key] = stream
            stream.task = asyncio.create_task(self._run(key, stream))
            self._publish_stream_count()
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        stream.subscribers.add(q)
        if stream.latest is not None:  # seed the new viewer with the last frame
            q.put_nowait({"event": "frame", "data": stream.latest})
        return key, q

    def unsubscribe(self, key: Key, q: asyncio.Queue) -> None:
        stream = self._streams.get(key)
        if stream is None:
            return
        stream.subscribers.discard(q)
        if not stream.subscribers and stream.task is not None:
            stream.task.cancel()
            self._streams.pop(key, None)
            self._publish_stream_count()

    def _publish_stream_count(self) -> None:
        metrics.LIVE_STREAMS.labels(model=self._model).set(len(self._streams))

    async def shutdown(self) -> None:
        """Cancel every producer and wait, briefly, for them to actually stop.

        Waiting matters because the caller shuts the pool down next: a producer still inside
        `await pool.evaluate` would otherwise be cancelled but not yet unwound while its
        executor disappears underneath it. `asyncio.wait` rather than `gather` so that a
        lifespan task cancelled by uvicorn's own shutdown timeout does not propagate through
        here, and with a timeout so a wedged producer cannot block process exit.
        """
        tasks = [stream.task for stream in self._streams.values() if stream.task is not None]
        for task in tasks:
            task.cancel()
        self._streams.clear()
        self._publish_stream_count()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT_S)
            if pending:
                logger.warning(
                    "%d live producer(s) for model %r did not stop within %.1fs of being "
                    "cancelled, abandoning them.",
                    len(pending),
                    self._model,
                    SHUTDOWN_TIMEOUT_S,
                )

    async def _run(self, key: Key, stream: _Stream) -> None:
        # No poll period: loop as fast as evaluate() allows. `await evaluate` yields to the
        # event loop and takes real time, so a healthy loop paces itself. Only the error path
        # needs a backoff, to avoid a hot spin on repeated failures.
        while True:
            try:
                inputs, live = await self._read_inputs()
                # kind="live" keeps continuous stream load separable from user-driven load
                # in the metrics.
                wire = await self._pool.evaluate(
                    inputs,
                    stream.outputs,
                    kind="live",
                    frame_index=self._frame_index,
                )
                wire["model"] = self._model
                wire["version"] = self._version
                # The third sender-attached key. The serializer cannot produce it: only this
                # loop knows which ids came off the machine and which fell back to a design
                # value. Keyed on the post-merge `wire["inputs"]`, which is what the frame
                # reports, so an unreadable id is marked rather than silently absent.
                wire["input_sources"] = live_sources(wire["inputs"], live)
                # Sanitized once per frame here rather than per subscriber in the SSE route,
                # since every viewer of this stream gets the same dict.
                wire = json_safe(wire)
                stream.latest = wire
                self._frame_index += 1
                self._broadcast(stream, {"event": "frame", "data": wire})
            except PoolDead as exc:
                # Nothing recovers a dead pool short of a pod restart, so retrying at 2 Hz would
                # only spam every viewer with error events and inflate
                # lume_evaluate_total{outcome="error"} until the liveness probe fires. Tell the
                # subscribers once and stop producing.
                self._broadcast(stream, {"event": "error", "data": {"message": str(exc)}})
                logger.error(
                    "Stopping the live producer for model %r outputs %s: %s",
                    self._model,
                    list(stream.outputs),
                    exc,
                )
                # Forget the stream as well as stopping it. Left in place, its finished task
                # would be adopted by the next subscriber of the same output set, which would
                # be seeded with the last good frame and then wait forever with no error. A
                # fresh stream instead fails immediately and says why.
                self._streams.pop(key, None)
                self._publish_stream_count()
                return
            except Exception:  # keep the loop alive on transient errors
                # The exception text stays server-side. See GENERIC_ERROR_MESSAGE: subscribers
                # here are browsers, and a solver traceback message can carry paths and model
                # internals. `logger.exception` so the pod log keeps the traceback, which is the
                # only place it now exists.
                logger.exception(
                    "Live producer for model %r outputs %s failed to produce a frame.",
                    self._model,
                    list(stream.outputs),
                )
                self._broadcast(
                    stream, {"event": "error", "data": {"message": GENERIC_ERROR_MESSAGE}}
                )
                await asyncio.sleep(ERROR_BACKOFF_S)

    @staticmethod
    def _broadcast(stream: _Stream, item: dict) -> None:
        for q in list(stream.subscribers):
            if q.full():  # drop the stale frame, keep only the newest
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass
