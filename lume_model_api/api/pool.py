"""In-pod subprocess pool of model instances (M2 concurrency).

Each worker process holds ONE model instance (process isolation is required: torch
double-load segfault + pytao thread-unsafety). K workers run K evaluates in parallel,
replacing M1's single asyncio-lock serialization. Baseline-merge lives in the source's
`snapshot`, so every request is history-independent.

Uses a spawn context (fork + torch/OpenMP is unsafe). A simple in-flight counter gives
backpressure (PoolFull -> HTTP 503 when saturated).
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor

from . import metrics

# Per-worker global — the one model instance for this process.
_SOURCE = None


class PoolFull(RuntimeError):
    pass


def _init_worker(model_name: str, mock: bool) -> None:
    # Pin threads per worker (K workers each spawning many BLAS/OMP threads would
    # thrash the pod). Default 1; override via env.
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "TORCH_NUM_THREADS"):
        os.environ.setdefault(var, os.environ.get("LUME_WORKER_THREADS", "1"))
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    # K instances share the read-only design-beam HDF5; HDF5's default file lock
    # rejects the concurrent open ([Errno 11]). Disable locking (must be set before
    # HDF5 loads) and give each worker its own cwd so any file it *writes* can't
    # collide with a sibling worker.
    os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
    import tempfile

    os.chdir(tempfile.mkdtemp(prefix="lume-worker-"))
    from webapp.backend.source import get_source

    global _SOURCE
    _SOURCE = get_source(model_name, mock=mock)


def _worker_evaluate(screen: str, inputs: dict, frame_index: int, title_suffix: str, x_axis_value: float) -> dict:
    from webapp.backend.serialize import frame_to_wire

    frame = _SOURCE.snapshot(
        screen,
        control_updates=inputs,
        x_axis_value=x_axis_value,
        frame_index=frame_index,
        title_suffix=title_suffix,
    )
    return frame_to_wire(frame)


def _worker_evaluate_v1(
    screen: str,
    inputs: dict,
    include_image: bool,
    include_distribution: bool,
    include_twiss: bool,
    max_particles,
    x_axis_value: float,
) -> dict:
    from webapp.backend.serialize import frame_to_v1_wire

    frame = _SOURCE.snapshot(
        screen,
        control_updates=inputs,
        x_axis_value=x_axis_value,
        title_suffix="v1",
        include_distribution=include_distribution,
        max_particles=max_particles,
    )
    return frame_to_v1_wire(
        frame,
        include_image=include_image,
        include_distribution=include_distribution,
        include_twiss=include_twiss,
    )


def _worker_ping() -> bool:
    return _SOURCE is not None


class ModelPool:
    def __init__(self, model_name: str, mock: bool, workers: int, max_inflight: int):
        self.workers = workers
        self.max_inflight = max_inflight
        self._inflight = 0
        self._ctx = mp.get_context("spawn")
        self._ex = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=self._ctx,
            initializer=_init_worker,
            initargs=(model_name, mock),
        )
        metrics.POOL_WORKERS.set(workers)
        metrics.POOL_MAX_INFLIGHT.set(max_inflight)
        metrics.POOL_INFLIGHT.set(0)

    async def warmup(self) -> None:
        """Force all K workers to spawn + build their model up front (parallel)."""
        loop = asyncio.get_running_loop()
        # Enough concurrent pings that the executor must spin up every worker.
        tasks = [loop.run_in_executor(self._ex, _worker_ping) for _ in range(self.workers * 3)]
        await asyncio.gather(*tasks)

    async def _submit(self, kind: str, fn, *args) -> dict:
        if self._inflight >= self.max_inflight:
            metrics.POOL_REJECTED_TOTAL.labels(kind).inc()
            raise PoolFull(f"pool saturated ({self.max_inflight} in flight)")
        self._inflight += 1
        metrics.POOL_INFLIGHT.set(self._inflight)
        start = time.perf_counter()
        outcome = "ok"
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._ex, fn, *args)
        except Exception:
            outcome = "error"
            raise
        finally:
            self._inflight -= 1
            metrics.POOL_INFLIGHT.set(self._inflight)
            metrics.EVALUATE_SECONDS.labels(kind).observe(time.perf_counter() - start)
            metrics.EVALUATE_TOTAL.labels(kind, outcome).inc()

    async def evaluate(
        self,
        screen: str,
        inputs: dict,
        frame_index: int = 0,
        title_suffix: str = "",
        x_axis_value: float | None = None,
    ) -> dict:
        return await self._submit(
            "evaluate",
            _worker_evaluate,
            screen,
            inputs,
            frame_index,
            title_suffix,
            time.time() if x_axis_value is None else x_axis_value,
        )

    async def evaluate_v1(
        self,
        screen: str,
        inputs: dict,
        include_image: bool = False,
        include_distribution: bool = False,
        include_twiss: bool = False,
        max_particles: int | None = None,
    ) -> dict:
        return await self._submit(
            "v1",
            _worker_evaluate_v1,
            screen,
            inputs,
            include_image,
            include_distribution,
            include_twiss,
            max_particles,
            time.time(),
        )

    def shutdown(self) -> None:
        self._ex.shutdown(wait=False, cancel_futures=True)
