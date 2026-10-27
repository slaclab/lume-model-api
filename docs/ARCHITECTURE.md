# Architecture

How the pieces fit, and why they are arranged this way. Read this before making a structural
change. [`AGENTS.md`](../AGENTS.md) covers the individual traps.

## Two layers: `model/` and `api/`

```
lume_model_api/
  model/        loads, describes and evaluates a LUMEModel. No HTTP, no FastAPI, no Pydantic.
    loader.py       LUME_MODEL -> factory path + kwargs -> a built model instance
    introspect.py   a model instance -> ModelInfo (inputs, outputs, screens, baseline)
    evaluate.py     baseline-merge, set, get, convert by variable kind
    demo.py         a small real LUMEModel, used by LUME_MODEL=demo and by the tests
    live_inputs.py  live input values from EPICS or synthetically
  api/          the FastAPI service. Depends on model/, never the reverse.
    main.py         routes, lifespan, role selection, the optional static mount
    pool.py         the spawn subprocess pool of model instances
    live_hub.py     one producer loop per output set, SSE fan-out
    schemas.py      the Pydantic request/response contract
    serialize.py    numpy -> base64 float32 wire dicts
    metrics.py      Prometheus gauges, histograms and counters
```

`api` imports `model`. `model` never imports `api`. This follows:

The model layer is usable from a notebook with no web server. `describe(build_model(...))` and
`evaluate(model, info, ...)` are plain functions over plain dataclasses, which is how
`tests/test_introspect.py`, `tests/test_evaluate_demo.py` and `tests/test_wire_shape.py` test
the pipeline without a TestClient.

And the dependency weight is separated. The model layer is where the heavy software is, so
keeping FastAPI and Pydantic out of it keeps anything in `model/` from formatting return
values.

One documented exception in the other direction: `serialize.py` lives in `api/` but runs
**inside** a pool worker, because arrays should cross the process boundary already encoded
rather than as pickled numpy. It imports nothing from FastAPI, so this costs the worker only
numpy and (on the opt-in smoothing path) scipy.

## Request flow for an evaluate

```
POST /api/v1/evaluate
  main.evaluate_v1
    _resolve_outputs(info, req.outputs, req.screen)   expand `screen`, reject empty, 400 on unknown ids
    pool.evaluate(...)                                 in-flight accounting, metrics, 503 when saturated
      -> worker process (ProcessPoolExecutor, spawn)
           evaluate.evaluate(model, info, ...)         baseline-merge, model.set, model.get, convert by kind
           serialize.result_to_wire(result)            numpy -> base64 float32, one dict
      <- the wire dict comes back
    {**wire, "model": ..., "version": ...}             the sender attaches these two
  FastAPI validates against EvaluateV1Response
```

Screen expansion, "asked for nothing" and unknown-id checks happen in `main` so a bad request
never occupies a worker. `evaluate.py` re-checks unknown ids as well, because it is a public
function of the model layer and a notebook caller gets the same error there (`UnknownVariable`,
which `main` maps to 400).

The `frame_index` in an HTTP response is always 0. It is meaningful only on the live stream.

## Baseline merge and statelessness

`introspect.describe` builds a `baseline` dict of `{input id: default}` for every non-constant
input. Every evaluate computes its effective inputs as `{**baseline, **requested}`, restricted to
non-constant inputs, and applies that whole set with one `model.set()`.

This is what makes the service stateless in the way that matters. A model instance is
stateful by nature: `set()` mutates it. If a request only applied the knobs it named, the
result would depend on whatever the previous request left behind on that particular worker, so
two identical requests could get different answers depending on which of K workers picked them
up, and a replica restart would change results. Merging over the baseline removes that
entirely: a request fully determines the machine state, `{}` means the design machine, and any
worker in any replica answers identically.

Constants are dropped from the applied set, since setting a pinned value back is at best a
no-op and at worst a validation error. The effective set is echoed in the response's `inputs` so
a caller can see what the baseline filled in.

Particle subsampling is a deterministic `linspace` stride for the same reason. A random draw
would make two identical requests to two workers return different particles.

## The process pool

`api/pool.py` runs a `ProcessPoolExecutor` on a `spawn` multiprocessing context, with
`LUME_POOL_WORKERS` workers. Each worker holds exactly one model instance in a module global.
Three constraints force this shape.

Two model instances in one process segfault, because torch gets loaded twice, and pytao is not
thread-safe. So process isolation is required, and threads are not an
option. `fork` with torch and OpenMP is unsafe, hence `spawn`. And `spawn` re-imports rather
than inheriting memory, which is why `_init_worker` and `_worker_evaluate` import by absolute
module path and why a worker cannot rely on anything the parent imported.

`_init_worker` also does four things that are easy to miss:

- pins BLAS/OMP threads to `LUME_WORKER_THREADS` (default 1), because K workers each spawning
  many threads would thrash the pod
- sets `KMP_DUPLICATE_LIB_OK=TRUE`
- sets `HDF5_USE_FILE_LOCKING=FALSE` before HDF5 loads, since K workers opening one read-only
  design-beam file hit HDF5's default lock and get `[Errno 11]`
- `chdir`s into a fresh temp directory, so files a worker writes cannot collide with a sibling's

Backpressure is a plain in-flight counter. At `LUME_MAX_INFLIGHT` (default `4 * workers`) a
submit raises `PoolFull`, which becomes a 503 and increments `lume_pool_rejected_total`. There
is no queue beyond the executor's own, on purpose: a deep queue turns saturation into unbounded
latency, and a UI would rather be told to back off.

Sizing is the model's business. A real model instance is a couple of GB and takes 25 to 30
seconds to build, so K is set by pod memory rather than by CPU. See
[`../deploy/kubernetes/CAPACITY.md`](../deploy/kubernetes/CAPACITY.md).

## The live hub

`api/live_hub.py` runs one background asyncio task **per distinct output set**, keyed on the
sorted tuple of resolved output ids. Each loop reads the live inputs, evaluates through the same
pool, and fans the resulting frame out to every subscriber of that key.

Keying on the output set rather than on a screen name is what makes sharing correct. Two clients
that asked for the same ids by different routes, one via `screen` and one via `outputs`, share a
loop, and a client that asked for one extra id gets its own. So N viewers of one view cost one
evaluate loop, not N, which is what lets a singleton producer with a 2-worker pool serve many
browsers.

A loop starts on its first subscriber and is cancelled when its last one leaves. A new
subscriber is seeded immediately with that loop's most recent frame, so a UI paints without
waiting a whole evaluate.

There is no poll period. The loop runs as fast as `await evaluate` allows, which paces it
naturally, because awaiting real work yields to the event loop. Only the error path sleeps
(0.5 s), to avoid a hot spin on repeated failures. An exception is broadcast as an `error` event
and the loop survives it.

Each subscriber has a size-1 **drop-old** queue. When a subscriber is too slow to drain, the
producer discards that subscriber's stale frame and enqueues the new one. A slow client sees a
lower frame rate, never a growing backlog and never head-of-line blocking for the others.

The SSE path bypasses the HTTP endpoint, so `LiveHub._run` attaches `model` and `version` to
each frame itself. Without that a streamed frame would not be a complete
`EvaluateV1Response`, which is exactly what every client types the stream as.
`tests/test_wire_shape.py` pins this.

Channel access blocks, so `main._read_live_inputs` runs the provider on
`asyncio.to_thread`, keeping the event loop that serves the SSE clients free.

## `LUME_ROLE` and the two deployments

One image, three roles.

| `LUME_ROLE` | Serves | Notes |
| --- | --- | --- |
| `eval` | `/api/config`, `/api/v1/evaluate`, `/metrics`, `/` | No hub, no EPICS. Scales freely. |
| `live` | everything above plus `/api/live/stream` and `/api/machine-snapshot` | The singleton EPICS reader. |
| `all` (default) | everything | Dev and single-pod deploys. |

The live-only endpoints return 503 with an explanatory detail under `LUME_ROLE=eval`, and
`LiveHub` is imported lazily so the eval role never loads it.

The split exists because the two workloads have opposite scaling properties. Evaluate is
stateless and CPU-bound, so more replicas is strictly better. The live producer must be a
singleton: N replicas would each independently read EPICS and evaluate, costing N times the
model work and showing different viewers divergent frames. So the eval pool is
`replicas: 2` and autoscalable, and the live producer is pinned at `replicas: 1` with a
`Recreate` strategy so a rollout never runs two EPICS readers. The ingress routes by regex,
sending `api/live/*` and `api/machine-snapshot` to the singleton and everything else to the
pool, so adding or removing a route changes no routing rule.

## Where metadata comes from

The main process **never imports the model**. That is a hard rule, and it is what keeps the web
process free of torch, Bmad and a lattice.

So the flow is: at startup, `lifespan` resolves `LUME_MODEL` (import-free, so a bad value fails
with one clear error rather than K worker tracebacks), constructs the pool, and calls
`pool.warmup()`. Warmup submits enough concurrent no-op pings that the executor has to spin up
all K workers, which builds all K model instances in parallel, then fetches the `ModelInfo` one
worker already built.

`ModelInfo` and its `InputInfo` / `OutputInfo` / `ScreenInfo` members are plain dataclasses
with no numpy or model objects in them, deliberately, because they have to pickle across the
spawn boundary back into a process that has never imported the model. The result is cached on
`app.state.info` and is what `/api/config` is served from, what `_resolve_outputs` validates
against, and what the live input provider is built from.

Consequences worth knowing:

- `/api/config` is free after startup. It reads a cached dataclass.
- The description is fixed for the process's lifetime. A model whose variable set changed at
  runtime would not be reflected, which is a trade the k8s deploy makes explicit: config is a
  pod-level property, and the pod restarts to change it.
- `LUME_MODEL`, `LUME_POOL_WORKERS`, `LUME_MAX_INFLIGHT` and `LUME_ROLE` are read at module
  import in `main.py`, not per request. A pod's configuration does not change while it runs, and
  re-reading them would only invite drift between what `/api/config` says and what the pool is
  doing.
- `describe()` runs once per worker rather than once per request, so introspecting a model may
  be as expensive as it needs to be.
