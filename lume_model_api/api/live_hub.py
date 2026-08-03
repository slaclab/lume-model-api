"""Shared live-view broadcast (N1).

One background loop per *active screen* reads the live inputs and evaluates as
fast as it can (paced only by evaluate latency — there is no poll period), then
fans the resulting frame out to every SSE subscriber of that screen. So N viewers
of a screen cost one evaluate loop, not N — which is what lets the singleton live
producer serve many viewers on a small (1-2 worker) pool. The loop starts on the
first subscriber and stops when the last leaves.

Each subscriber gets a size-1 drop-old queue: a slow client always receives the
newest frame, never a backlog.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

ReadInputs = Callable[[float], Awaitable[dict]]


class _Screen:
    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None
        self.latest: dict | None = None
        self.started = time.monotonic()
        self.index = 0


class LiveHub:
    def __init__(self, pool, read_inputs: ReadInputs) -> None:
        self._pool = pool
        self._read_inputs = read_inputs
        self._screens: dict[str, _Screen] = {}

    def subscribe(self, screen: str) -> asyncio.Queue:
        ch = self._screens.get(screen)
        if ch is None:
            ch = _Screen()
            self._screens[screen] = ch
            ch.task = asyncio.create_task(self._run(screen, ch))
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        ch.subscribers.add(q)
        if ch.latest is not None:  # seed the new viewer with the last frame
            q.put_nowait({"event": "frame", "data": ch.latest})
        return q

    def unsubscribe(self, screen: str, q: asyncio.Queue) -> None:
        ch = self._screens.get(screen)
        if ch is None:
            return
        ch.subscribers.discard(q)
        if not ch.subscribers and ch.task is not None:
            ch.task.cancel()
            self._screens.pop(screen, None)

    async def shutdown(self) -> None:
        for ch in list(self._screens.values()):
            if ch.task is not None:
                ch.task.cancel()
        self._screens.clear()

    async def _run(self, screen: str, ch: _Screen) -> None:
        # No poll period: loop as fast as evaluate() allows. `await evaluate` yields to
        # the event loop and takes real time, so a healthy loop paces itself. Only the
        # error path needs a backoff, to avoid a hot spin on repeated failures.
        while True:
            elapsed = time.monotonic() - ch.started
            try:
                inputs = await self._read_inputs(elapsed)
                wire = await self._pool.evaluate(
                    screen, inputs, frame_index=ch.index, title_suffix="live"
                )
                ch.latest = wire
                ch.index += 1
                self._broadcast(ch, {"event": "frame", "data": wire})
            except Exception as exc:  # keep the loop alive on transient errors
                self._broadcast(ch, {"event": "error", "data": {"message": str(exc)}})
                await asyncio.sleep(0.5)

    @staticmethod
    def _broadcast(ch: _Screen, item: dict) -> None:
        for q in list(ch.subscribers):
            if q.full():  # drop the stale frame; keep only the newest
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass
