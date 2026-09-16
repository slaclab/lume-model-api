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

Each subscriber gets a size-1 drop-old queue: a slow client always receives the newest frame,
never a backlog.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Sequence

from .pool import PoolDead
from .serialize import json_safe

ReadInputs = Callable[[], Awaitable[dict]]

Key = tuple[str, ...]

logger = logging.getLogger(__name__)

# How long shutdown waits for a cancelled producer to actually finish. Bounded because it runs
# in a lifespan `finally`, where an unbounded await would hold the whole process past the k8s
# termination grace period and earn a SIGKILL.
SHUTDOWN_TIMEOUT_S = 2.0

# Backoff after a transient error, to avoid a hot spin on repeated failures.
ERROR_BACKOFF_S = 0.5


class _Stream:
    def __init__(self, outputs: Sequence[str]) -> None:
        self.outputs = list(outputs)
        self.subscribers: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None
        self.latest: dict | None = None
        self.index = 0


class LiveHub:
    def __init__(self, pool, read_inputs: ReadInputs, model: str, version: str) -> None:
        self._pool = pool
        self._read_inputs = read_inputs
        # The SSE stream bypasses the HTTP endpoint, which is what normally attaches these
        # two. Without them a streamed frame would not be a complete EvaluateV1Response,
        # which is what every client types the stream as. Attached here, once per frame,
        # rather than per subscriber.
        self._model = model
        self._version = version
        self._streams: dict[Key, _Stream] = {}

    @staticmethod
    def key_for(outputs: Sequence[str]) -> Key:
        return tuple(sorted(set(outputs)))

    def subscribe(self, outputs: Sequence[str]) -> tuple[Key, asyncio.Queue]:
        key = self.key_for(outputs)
        stream = self._streams.get(key)
        if stream is None:
            stream = _Stream(key)
            self._streams[key] = stream
            stream.task = asyncio.create_task(self._run(key, stream))
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
                inputs = await self._read_inputs()
                # kind="live" keeps continuous stream load separable from user-driven load
                # in the metrics.
                wire = await self._pool.evaluate(
                    inputs,
                    stream.outputs,
                    kind="live",
                    frame_index=stream.index,
                )
                wire["model"] = self._model
                wire["version"] = self._version
                # Sanitized once per frame here rather than per subscriber in the SSE route,
                # since every viewer of this stream gets the same dict.
                wire = json_safe(wire)
                stream.latest = wire
                stream.index += 1
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
                return
            except Exception as exc:  # keep the loop alive on transient errors
                self._broadcast(stream, {"event": "error", "data": {"message": str(exc)}})
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
