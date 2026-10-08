"""Prometheus metrics for the model pool.

The honest saturation signal for autoscaling is in-flight work, not CPU (thread
pinning makes CPU a poor proxy). These live in the main process, because the async
`_submit` wrapper runs there even though the model itself runs in worker
subprocesses. So the default single-process registry is correct and each pod
exposes its own /metrics that KEDA/Prometheus scrapes.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Every metric carries the URL name of the model it belongs to, because one pod hosts one
# pool per model. Cardinality is the number of hosted models per pod, so a handful.
#
# The label changes what an aggregate means: avg(lume_pool_inflight) now averages across
# models and so reads low when one model is saturated. The KEDA query sums by pod first, see
# deploy/kubernetes/keda-scaledobject.yaml.

# Current vs configured capacity. `inflight / max_inflight` is what KEDA scales on.
POOL_INFLIGHT = Gauge(
    "lume_pool_inflight", "Requests currently in flight on the pool", labelnames=("model",)
)
POOL_MAX_INFLIGHT = Gauge(
    "lume_pool_max_inflight", "Configured max in-flight before 503", labelnames=("model",)
)
POOL_WORKERS = Gauge(
    "lume_pool_workers", "Model worker subprocesses in the pool", labelnames=("model",)
)

# 1 once the pool can no longer serve an evaluate. Needed as its own series because a dead pool
# reports `lume_pool_inflight` 0, so the KEDA query in
# deploy/kubernetes/keda-scaledobject.yaml reads a bricked pod as idle and would scale down.
# Alert on this, do not infer it from the other gauges.
#
# `reason` is one of `worker_lost`, `unusable` or `timeout` (`pool.DEAD_REASONS`). All three end
# in the same pod restart, so an alert should ignore the label and sum over it, but they call for
# different investigations: a lost worker is an OOM kill or a segfault, `unusable` is a model that
# broke itself while applying inputs, and `timeout` is `LUME_EVALUATE_TIMEOUT_S` expiring.
POOL_DEAD = Gauge(
    "lume_pool_dead",
    "1 when the pool is unusable and the pod must restart",
    labelnames=("model", "reason"),
)

# How often a model broke itself and whether it came back, by `outcome`: `recovered`, `failed`, or
# `unavailable` for a model that defines no `recover()`. The terminal state is already in
# `lume_pool_dead`, so what this adds is the rate. A bend scan that poisons a worker on every pass
# looks identical in the gauge and obvious here.
POOL_RECOVERY_TOTAL = Counter(
    "lume_pool_recovery_total",
    "Attempts to recover a model that became unusable, by outcome",
    labelnames=("model", "outcome"),
)

# Per-evaluate latency (whole submit: queue wait + model run), by model and request kind.
EVALUATE_SECONDS = Histogram(
    "lume_evaluate_seconds",
    "Time to complete a pool evaluate",
    labelnames=("model", "kind"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16),
)

# Outcomes: ok / error (raised in worker), and rejections (503 when saturated).
EVALUATE_TOTAL = Counter(
    "lume_evaluate_total", "Pool evaluates by outcome", labelnames=("model", "kind", "outcome")
)
POOL_REJECTED_TOTAL = Counter(
    "lume_pool_rejected_total", "Evaluates rejected because the pool was saturated",
    labelnames=("model", "kind"),
)

# How much of the machine the live view is actually reading, set by `main._read_live_inputs`
# from what the input provider returned. The provider lives in the model layer and must not
# import this module, so the api-layer caller publishes the numbers instead. See the layering
# section of AGENTS.md.
#
# `readable` below `total` is not by itself a fault: a model input that is not a real PV (a bunch
# length knob, say) never reads, and that is the normal steady state for such a model. What is
# worth an alert is a *drop* from whatever this pod's own steady state is, because the ids that
# stopped reading are being served from the model's design values instead of from the machine.
# Zero readable with a non-zero total means channel access is broken, and the live routes are
# already reporting an error for it.
LIVE_INPUTS_READABLE = Gauge(
    "lume_live_inputs_readable",
    "Live input ids that returned a usable value on the last read",
    labelnames=("model",),
)
LIVE_INPUTS_TOTAL = Gauge(
    "lume_live_inputs_total",
    "Live input ids this model's provider attempts to read",
    labelnames=("model",),
)

# Distinct live producer loops running on this model's hub. Capped at the pool's max_inflight,
# since more loops than in-flight slots only trade every viewer's frame rate for PoolFull errors.
# At the cap a new distinct output set is refused with a 503, so alert on this sitting at
# `lume_pool_max_inflight`.
LIVE_STREAMS = Gauge(
    "lume_live_streams",
    "Distinct live output sets currently being produced",
    labelnames=("model",),
)
