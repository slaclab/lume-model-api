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

# Not one of the worker-side lazy imports below: `_submit` runs in the main process and has to
# name this class to count a recovered failure. It costs that process nothing new, because
# `api/main.py` already imports its siblings from the same module to map them onto status
# codes, and the module itself pulls in nothing heavier than numpy.
from lume_model_api.model.evaluate import ModelUnusable

from . import metrics

logger = logging.getLogger(__name__)

# Per-worker globals: the one model instance for this process, and the description built
# from it once at startup rather than on every request.
_MODEL = None
_INFO = None
# Worker-side latch: None while this worker's model is healthy, otherwise why it is not.
# Per worker rather than per pool because `ProcessPoolExecutor` exposes no worker identity to
# the main process, which therefore cannot tell which worker served a request, cannot target
# one and cannot replace one. Detection has to happen where the model is.
_UNUSABLE: str | None = None

# `outcome` label values on `lume_pool_recovery_total`. A model that defines no `recover()` is
# "unavailable", which is every model today: see the recovery section of
# docs/ARCHITECTURE.md.
RECOVERY_UNAVAILABLE = "unavailable"
RECOVERY_OK = "recovered"
RECOVERY_FAILED = "failed"
RECOVERY_OUTCOMES = (RECOVERY_UNAVAILABLE, RECOVERY_OK, RECOVERY_FAILED)

# `reason` label values on `lume_pool_dead`, so the rate of each cause is visible rather than
# only the terminal state they share.
DEAD_WORKER_LOST = "worker_lost"
DEAD_UNUSABLE = "unusable"
DEAD_TIMEOUT = "timeout"
DEAD_REASONS = (DEAD_WORKER_LOST, DEAD_UNUSABLE, DEAD_TIMEOUT)


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


class ModelPoisoned(RuntimeError):
    """This worker's model is unusable and in-process recovery did not fix it.

    Raised in the worker, so it crosses the process boundary by pickling. The diagnostic goes
    in the message rather than in an attribute: an attribute does survive that pickling, but
    the message is what reaches the pod log and the client, which is where it is useful.

    `_submit` converts this to `PoolDead`, which is why the escalation needs nothing new: a
    poisoned worker reaches the same 503 on evaluate, the same unhealthy `/healthz` and the
    same pod restart as a lost one. See docs/ARCHITECTURE.md, "When a worker dies".
    """


def _init_worker(factory_path: str, kwargs: dict, model_name: str) -> None:
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
    # factory_path is already resolved by the main process before this worker starts.
    # Calling resolve() again here would be a no-op (a factory path always contains ':'
    # and passes through unchanged), but importing it just to discard its output would be
    # misleading about where resolution actually happens.
    from lume_model_api.model.introspect import describe
    from lume_model_api.model.loader import build_model

    global _MODEL, _INFO
    _MODEL = build_model(factory_path, kwargs)
    # Described under the URL name, not the factory reference, so `info.model` is what a
    # client sees in the path and in every response's `model` field.
    _INFO = describe(_MODEL, name=model_name)


def _worker_info():
    return _INFO


def _requested_label(inputs: dict) -> str:
    """Name the ids a failing request sent, for an error message a browser may see.

    Abbreviated past a handful because the live producer sends the whole merged input set, so
    the honest version of that is a count rather than 54 ids in one error event. The caller's
    own ids are the useful half: on the HTTP route they are exactly the knobs it moved.
    """
    names = sorted(inputs or {})
    if not names:
        return "no inputs at all, so only the model's own baseline"
    if len(names) > 5:
        return f"{len(names)} inputs"
    return ", ".join(names)


def _attempt_recovery(model) -> str:
    """Give the model one chance to put itself back together. Returns a recovery outcome.

    A model may define `recover()` to undo whatever a failed `set()` left behind, restoring
    the state its own `set()` assumes. Nothing declares it today, so this returns
    "unavailable" for every model this package currently hosts and the caller latches
    immediately. That is the whole protocol on purpose: the one measured cure is a sequence of
    Bmad commands, and a model-agnostic host has no business knowing them. See
    docs/ARCHITECTURE.md for the sequence and where it belongs.

    `model.reset()` is emphatically not this, and must never be called on a Bmad model: it
    writes back a `_initial_state` snapshotted before `track_type` was set, which unregisters
    every beam output and breaks the instance permanently even when it was healthy.
    """
    recover = getattr(model, "recover", None)
    if not callable(recover):
        return RECOVERY_UNAVAILABLE
    try:
        recover()
    except BaseException:
        # The model was already unusable, so a failed recovery only means the worker stays
        # that way. The traceback is the only record of why the cure did not work, and the
        # caller's message says nothing about it.
        logger.exception("recover() failed on the model in this worker.")
        return RECOVERY_FAILED
    return RECOVERY_OK


def _worker_evaluate(
    inputs: dict,
    outputs: list[str],
    max_particles,
    smooth_sigma_px,
    frame_index: int,
) -> dict:
    from lume_model_api.api.serialize import result_to_wire
    from lume_model_api.model.evaluate import evaluate

    global _UNUSABLE
    if _UNUSABLE is not None:
        # Only reachable by a request that raced in before `_mark_dead()` took effect in the
        # main process, since a dead pool refuses every submit. Kept anyway, because that race
        # is exactly how a poisoned worker used to answer with a 400 blaming the caller.
        raise ModelPoisoned(_UNUSABLE)

    try:
        result = evaluate(
            _MODEL,
            _INFO,
            inputs=inputs,
            outputs=outputs,
            max_particles=max_particles,
            smooth_sigma_px=smooth_sigma_px,
        )
    except ModelUnusable as exc:
        # The full text, with whatever the model said about lattice elements and measured
        # values, exists only here. `_UNUSABLE` below reaches a browser through the live
        # stream, which does not sanitize a `PoolDead` message, so that one stays at the
        # failure class and the ids the caller sent.
        logger.exception(
            "The model in this worker failed while applying %s.", sorted(inputs or {})
        )
        outcome = _attempt_recovery(_MODEL)
        if outcome == RECOVERY_OK:
            # Recovered, so this request still failed but the worker keeps serving. Raised as
            # itself rather than as `ModelPoisoned`, which is what stops `_submit` from
            # killing a pool that is once again healthy.
            # The metrics registry lives in the main process, so the outcome has to travel
            # back on the exception for `_submit` to count it. An attribute, unlike the
            # diagnostic, because no human reads this one and a label value buried in a
            # message would have to be parsed back out.
            exc.recovery_outcome = outcome
            raise
        cause = exc.__cause__ or exc
        _UNUSABLE = (
            f"model {_INFO.model!r} became unusable in this worker. The request that broke it "
            f"sent {_requested_label(inputs)}, and it failed with "
            f"{type(cause).__name__}. Recovery: {outcome}. The pod log has the model's own "
            "message."
        )
        poisoned = ModelPoisoned(_UNUSABLE)
        poisoned.recovery_outcome = outcome
        raise poisoned from exc
    return result_to_wire(result, frame_index=frame_index)


def _worker_ping() -> bool:
    return _MODEL is not None


class ModelPool:
    """One pool, one model. A pod hosting M models runs M of these, each with its own K."""

    def __init__(
        self, factory_path: str, kwargs: dict, workers: int, max_inflight: int, model_name: str
    ):
        self.factory_path = factory_path
        self.model_name = model_name
        self.workers = workers
        self.max_inflight = max_inflight
        # Latches once a worker is lost or its model becomes unusable. Read by GET /healthz,
        # which is what the k8s probes target, so a bricked pool becomes a pod restart rather
        # than an endless 500 loop. `dead_reason` is one of DEAD_REASONS, so a 503 can say which
        # of the three happened without an operator having to read the pod log first.
        self.dead = False
        self.dead_reason = ""
        self._inflight = 0
        # 0 / unset means no timeout. Real models take 25-30s to build and ~2.5s per
        # evaluate, so any non-zero value must be well above both of those numbers.
        _raw_timeout = os.environ.get("LUME_EVALUATE_TIMEOUT_S", "").strip()
        self._timeout_s: float = float(_raw_timeout) if _raw_timeout else 0.0
        self._ctx = mp.get_context("spawn")
        self._ex = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=self._ctx,
            initializer=_init_worker,
            initargs=(factory_path, dict(kwargs or {}), model_name),
        )
        metrics.POOL_WORKERS.labels(model=model_name).set(workers)
        metrics.POOL_MAX_INFLIGHT.labels(model=model_name).set(max_inflight)
        metrics.POOL_INFLIGHT.labels(model=model_name).set(0)
        # Every label combination is published at 0 here, for the same reason the other series
        # are: a Prometheus series that only appears once it is non-zero breaks any alert or
        # query written with `absent()`, and a pod that has never been poisoned is exactly the
        # pod whose numbers an operator wants to compare against.
        for reason in DEAD_REASONS:
            metrics.POOL_DEAD.labels(model=model_name, reason=reason).set(0)
        for outcome in RECOVERY_OUTCOMES:
            metrics.POOL_RECOVERY_TOTAL.labels(model=model_name, outcome=outcome).inc(0)

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
        info = None
        try:
            await asyncio.gather(*tasks)
            info = await loop.run_in_executor(self._ex, _worker_info)
        except BrokenProcessPool as exc:
            # Almost always the model factory raising, or being OOM killed, inside
            # `_init_worker`. The bare executor error names neither, so say where to look.
            self._mark_dead(DEAD_WORKER_LOST)
            raise PoolDead(
                f"model {self.model_name!r} could not be built in a worker process ({exc}). "
                "Check the factory reference, the model's own dependencies and any lattice "
                "path it needs."
            ) from exc
        return info

    async def _submit(self, kind: str, fn, *args) -> dict:
        # Fail fast rather than letting the executor raise BrokenProcessPool again per call,
        # so a bricked pool costs one cheap rejection instead of a submit round trip.
        if self.dead:
            raise PoolDead(
                f"model {self.model_name!r} cannot recover in this pod ({self.dead_reason}) and "
                "cannot serve another evaluate"
            )
        if self._inflight >= self.max_inflight:
            metrics.POOL_REJECTED_TOTAL.labels(model=self.model_name, kind=kind).inc()
            raise PoolFull(f"pool saturated ({self.max_inflight} in flight)")
        self._inflight += 1
        metrics.POOL_INFLIGHT.labels(model=self.model_name).set(self._inflight)
        start = time.perf_counter()
        outcome = "ok"
        try:
            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(self._ex, fn, *args)
            if self._timeout_s:
                try:
                    return await asyncio.wait_for(fut, timeout=self._timeout_s)
                except asyncio.TimeoutError as exc:
                    # run_in_executor cannot cancel work already submitted to a subprocess.
                    # The worker keeps computing the abandoned frame and can never be
                    # reclaimed, so the pool must die and the pod must restart to release
                    # the multi-GB model memory held in that worker process.
                    self._mark_dead(DEAD_TIMEOUT)
                    raise PoolDead(
                        f"model {self.model_name!r}: worker did not return within "
                        f"{self._timeout_s}s. The worker process cannot be reclaimed "
                        "and the pod must restart."
                    ) from exc
            return await fut
        except BrokenProcessPool as exc:
            outcome = "error"
            self._mark_dead(DEAD_WORKER_LOST)
            raise PoolDead(
                f"model {self.model_name!r} lost a worker process and cannot recover: {exc}"
            ) from exc
        except ModelPoisoned as exc:
            # The worker process is alive and useless, which is why nothing above catches this:
            # `ProcessPoolExecutor` only latches on a process it lost. Escalated through the
            # same chain anyway, because the consequence is identical. A restart is the only
            # thing that rebuilds the model, and `PoolDead` is what makes `/healthz` ask for
            # one.
            outcome = "error"
            self._count_recovery(getattr(exc, "recovery_outcome", RECOVERY_UNAVAILABLE))
            self._mark_dead(DEAD_UNUSABLE)
            raise PoolDead(
                f"model {self.model_name!r} became unusable in a worker process and could not "
                f"be recovered, so the pod must restart: {exc}"
            ) from exc
        except ModelUnusable as exc:
            # Reached only when the worker recovered, so the pool stays alive and the caller
            # gets a 503 naming this one failed request. Counted here rather than in the worker
            # because the metrics registry lives in this process.
            outcome = "error"
            self._count_recovery(getattr(exc, "recovery_outcome", RECOVERY_OK))
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            # The worker keeps computing the abandoned frame, so real occupancy can briefly
            # exceed max_inflight when many live-stream clients disconnect at once. This is
            # inherent to ProcessPoolExecutor rather than something to fix here.
            raise
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

    def _count_recovery(self, outcome: str) -> None:
        if outcome not in RECOVERY_OUTCOMES:
            # An unrecognized outcome would be a new label value appearing in the middle of a
            # failure, so it is folded into the one that describes what happened next.
            outcome = RECOVERY_FAILED
        metrics.POOL_RECOVERY_TOTAL.labels(model=self.model_name, outcome=outcome).inc()

    def _mark_dead(self, reason: str) -> None:
        if self.dead:
            return
        self.dead = True
        self.dead_reason = reason
        metrics.POOL_DEAD.labels(model=self.model_name, reason=reason).set(1)
        # ERROR, not warning: nothing recovers this short of a pod restart, and the only other
        # trace is a 503 per request.
        logger.error(
            "Pool for model %r is dead (%s). Every evaluate for this model will now fail and "
            "GET /healthz reports unhealthy so the pod restarts.",
            self.model_name,
            reason,
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
