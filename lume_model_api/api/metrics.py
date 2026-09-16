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

# 1 once a worker died abruptly and the executor can no longer be submitted to. Needed as its
# own series because a dead pool reports `lume_pool_inflight` 0, so the KEDA query in
# deploy/kubernetes/keda-scaledobject.yaml reads a bricked pod as idle and would scale down.
# Alert on this, do not infer it from the other gauges.
POOL_DEAD = Gauge(
    "lume_pool_dead", "1 when the pool is unusable and the pod must restart", labelnames=("model",)
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
