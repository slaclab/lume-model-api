"""In-pod subprocess pool of model instances.

Each worker process holds ONE model instance (process isolation is required: torch
double-load segfault + pytao thread-unsafety). K workers run K evaluates in parallel, rather
than serializing them behind a single asyncio lock. Baseline-merge lives in
`model/evaluate.py`, so every request is history-independent.

Uses a spawn context (fork + torch/OpenMP is unsafe). A simple in-flight counter gives
backpressure (PoolFull -> HTTP 503 when saturated).

The main process never imports the model. `warmup()` fetches the `ModelInfo` a worker built,
which is how `/api/v1/models/<name>/config` is served from a process that has no torch, no
Bmad and no lattice.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor

# Not re-exported from `concurrent.futures`, so it has to come from the submodule. Raised by
# every subsequent submit once a worker dies abruptly, not just by the call that lost one.
from concurrent.futures.process import BrokenProcessPool

from . import metrics

logger = logging.getLogger(__name__)

# Per-worker globals: the one model instance for this process, and the description built
# from it once at startup rather than on every request.
_MODEL = None
_INFO = None


class PoolFull(RuntimeError):
    pass


class PoolDead(RuntimeError):
    """A worker died abruptly, so this pool can never serve another evaluate.

    `ProcessPoolExecutor` latches broken: once a worker is lost (an OOM kill of a multi-GB
    model, or the torch/pytao segfault process isolation exists to contain), every later
    submit raises `BrokenProcessPool` for the life of the executor. The pool is deliberately
    not rebuilt, because spawning K replacement workers while the old ones tear down would
    peak at twice the model memory and turn a restart-recoverable failure into an OOM kill.
    `GET /healthz` reports this so the k8s probes restart the pod, which is both cleaner and
    visible in `kubectl get pods`.
    """


def _init_worker(model_ref: str, kwargs: dict, model_name: str) -> None:
    # Pin threads per worker (K workers each spawning many BLAS/OMP threads would thrash the
    # pod). Default 1, override with LUME_WORKER_THREADS.
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "TORCH_NUM_THREADS"):
        os.environ.setdefault(var, os.environ.get("LUME_WORKER_THREADS", "1"))
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    # K instances may share a read-only design-beam HDF5, and HDF5's default file lock
    # rejects the concurrent open ([Errno 11]). Disable locking (before HDF5 loads) and
    # give each worker its own cwd so any file it *writes* cannot collide with a sibling.
    os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
    import tempfile

    os.chdir(tempfile.mkdtemp(prefix="lume-worker-"))
    # spawn re-imports rather than inheriting memory, so import by absolute module path.
    from lume_model_api.model.introspect import describe
    from lume_model_api.model.loader import build_model, resolve

    factory_path, resolved_kwargs, _ = resolve(model_ref, kwargs)

    global _MODEL, _INFO
    _MODEL = build_model(factory_path, resolved_kwargs)
    # Described under the URL name, not the factory reference, so `info.model` is what a
    # client sees in the path and in every response's `model` field.
    _INFO = describe(_MODEL, name=model_name)


def _worker_info():
    return _INFO


def _worker_evaluate(
    inputs: dict,
    outputs: list[str],
    max_particles,
    smooth_sigma_px,
    frame_index: int,
) -> dict:
    from lume_model_api.api.serialize import result_to_wire
    from lume_model_api.model.evaluate import evaluate

    result = evaluate(
        _MODEL,
        _INFO,
        inputs=inputs,
        outputs=outputs,
        max_particles=max_particles,
        smooth_sigma_px=smooth_sigma_px,
    )
    return result_to_wire(result, frame_index=frame_index)


def _worker_ping() -> bool:
    return _MODEL is not None


class ModelPool:
    """One pool, one model. A pod hosting M models runs M of these, each with its own K."""

    def __init__(
        self, model_ref: str, kwargs: dict, workers: int, max_inflight: int, model_name: str
    ):
        self.model_ref = model_ref
        self.model_name = model_name
        self.workers = workers
        self.max_inflight = max_inflight
        self.info = None  # populated by warmup()
        # Latches once a worker is lost. Read by GET /healthz, which is what the k8s probes
        # target, so a bricked pool becomes a pod restart rather than an endless 500 loop.
        self.dead = False
        self._inflight = 0
        self._ctx = mp.get_context("spawn")
        self._ex = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=self._ctx,
            initializer=_init_worker,
            initargs=(model_ref, dict(kwargs or {}), model_name),
        )
        metrics.POOL_WORKERS.labels(model=model_name).set(workers)
        metrics.POOL_MAX_INFLIGHT.labels(model=model_name).set(max_inflight)
        metrics.POOL_INFLIGHT.labels(model=model_name).set(0)
        metrics.POOL_DEAD.labels(model=model_name).set(0)

    async def warmup(self):
        """Build all K models up front (in parallel) and fetch the model description.

        The description is identical in every worker, so one fetch is enough. It is a plain
        dataclass, which is what lets it cross the spawn boundary back to a main process that
        has never imported the model.
        """
        loop = asyncio.get_running_loop()
        # K submits are enough to spawn all K workers: `_idle_worker_semaphore` starts at 0 and
        # `run_in_executor` submits synchronously, so each of the first K submits finds no idle
        # worker and spawns one. The model is built by `initializer` at process start, not by
        # these pings, so a ping landing on an already-warm worker still costs nothing.
        tasks = [loop.run_in_executor(self._ex, _worker_ping) for _ in range(self.workers)]
        try:
            await asyncio.gather(*tasks)
            self.info = await loop.run_in_executor(self._ex, _worker_info)
        except BrokenProcessPool as exc:
            # Almost always the model factory raising, or being OOM killed, inside
            # `_init_worker`. The bare executor error names neither, so say where to look.
            self._mark_dead()
            raise PoolDead(
                f"model {self.model_name!r} could not be built in a worker process ({exc}). "
                "Check the factory reference, the model's own dependencies and any lattice "
                "path it needs."
            ) from exc
        return self.info

    async def _submit(self, kind: str, fn, *args) -> dict:
        # Fail fast rather than letting the executor raise BrokenProcessPool again per call,
        # so a bricked pool costs one cheap rejection instead of a submit round trip.
        if self.dead:
            raise PoolDead(f"model {self.model_name!r} lost a worker process and cannot recover")
        if self._inflight >= self.max_inflight:
            metrics.POOL_REJECTED_TOTAL.labels(model=self.model_name, kind=kind).inc()
            raise PoolFull(f"pool saturated ({self.max_inflight} in flight)")
        self._inflight += 1
        metrics.POOL_INFLIGHT.labels(model=self.model_name).set(self._inflight)
        start = time.perf_counter()
        outcome = "ok"
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._ex, fn, *args)
        except BrokenProcessPool as exc:
            outcome = "error"
            self._mark_dead()
            raise PoolDead(
                f"model {self.model_name!r} lost a worker process and cannot recover: {exc}"
            ) from exc
        except Exception:
            outcome = "error"
            raise
        finally:
            self._inflight -= 1
            metrics.POOL_INFLIGHT.labels(model=self.model_name).set(self._inflight)
            metrics.EVALUATE_SECONDS.labels(model=self.model_name, kind=kind).observe(
                time.perf_counter() - start
            )
            metrics.EVALUATE_TOTAL.labels(
                model=self.model_name, kind=kind, outcome=outcome
            ).inc()

    def _mark_dead(self) -> None:
        if self.dead:
            return
        self.dead = True
        metrics.POOL_DEAD.labels(model=self.model_name).set(1)
        # ERROR, not warning: nothing recovers this short of a pod restart, and the only other
        # trace is a 503 per request.
        logger.error(
            "Pool for model %r lost a worker process. Every evaluate for this model will now "
            "fail and GET /healthz reports unhealthy so the pod restarts.",
            self.model_name,
        )

    async def evaluate(
        self,
        inputs: dict,
        outputs: list[str],
        kind: str = "interactive",
        max_particles: int | None = None,
        smooth_sigma_px: float | None = None,
        frame_index: int = 0,
    ) -> dict:
        """The one evaluate path, serving the HTTP endpoint and the live stream.

        `kind` only labels metrics. Pass "live" from the SSE producer and "interactive" from
        the HTTP route so Prometheus can tell continuous stream load apart from user-driven
        load. KEDA scales on lume_pool_inflight summed by pod, which ignores `kind`, so this
        label is for observability only.
        """
        return await self._submit(
            kind,
            _worker_evaluate,
            inputs,
            list(outputs),
            max_particles,
            smooth_sigma_px,
            frame_index,
        )

    def shutdown(self) -> None:
        self._ex.shutdown(wait=False, cancel_futures=True)
