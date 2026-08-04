"""Prometheus metrics for the model pool (N3).

The honest saturation signal for autoscaling is in-flight work, not CPU (thread
pinning makes CPU a poor proxy). These live in the main process — the async
`_submit` wrapper runs there even though the model itself runs in worker
subprocesses — so the default single-process registry is correct; each pod
exposes its own /metrics that KEDA/Prometheus scrapes.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Current vs configured capacity. `inflight / max_inflight` is what KEDA scales on.
POOL_INFLIGHT = Gauge("lume_pool_inflight", "Requests currently in flight on the pool")
POOL_MAX_INFLIGHT = Gauge("lume_pool_max_inflight", "Configured max in-flight before 503")
POOL_WORKERS = Gauge("lume_pool_workers", "Model worker subprocesses in the pool")

# Per-evaluate latency (whole submit: queue wait + model run), by request kind.
EVALUATE_SECONDS = Histogram(
    "lume_evaluate_seconds",
    "Time to complete a pool evaluate",
    labelnames=("kind",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16),
)

# Outcomes: ok / error (raised in worker), and rejections (503 when saturated).
EVALUATE_TOTAL = Counter(
    "lume_evaluate_total", "Pool evaluates by outcome", labelnames=("kind", "outcome")
)
POOL_REJECTED_TOTAL = Counter(
    "lume_pool_rejected_total", "Evaluates rejected because the pool was saturated",
    labelnames=("kind",),
)
