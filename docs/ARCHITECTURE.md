# Architecture

How the pieces fit, and why they are arranged this way. Read this before making a structural
change. [`AGENTS.md`](../AGENTS.md) covers the individual traps.

## Two layers: `model/` and `api/`

```
lume_model_api/
  model/        loads, describes and evaluates a LUMEModel. No HTTP, no FastAPI, no Pydantic.
    loader.py       LUME_MODELS -> one ModelSetting per model -> built model instances
    introspect.py   a model instance -> ModelInfo (inputs, outputs, screens, baseline)
    evaluate.py     baseline-merge, set, get, convert by variable kind
    demo.py         a small real LUMEModel, used by LUME_MODELS=demo and by the tests
    live_inputs.py  live input values from EPICS or synthetically
  api/          the FastAPI service. Depends on model/, never the reverse.
    main.py         routes, lifespan, role selection, one HostedModel per hosted name,
                    the optional static mount
    pool.py         the spawn subprocess pool of model instances, one pool per model
    live_hub.py     one producer loop per output set, SSE fan-out, one hub per model
    schemas.py      the Pydantic request/response contract
    serialize.py    numpy -> base64 float32 wire dicts
    metrics.py      Prometheus gauges, histograms and counters, all labelled by model
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
POST /api/v1/models/{name}/evaluate
  main.evaluate_v1
    _hosted(name)                                      app.state.models lookup, 404 listing hosted names
    _resolve_outputs(info, req.outputs, req.screen)   expand `screen`, reject empty, 400 on unknown ids
    hosted.pool.evaluate(...)                          in-flight accounting, metrics, 503 when saturated
      -> worker process (ProcessPoolExecutor, spawn)
           evaluate.evaluate(model, info, ...)         baseline-merge, model.set, model.get, convert by kind
           serialize.result_to_wire(result)            numpy -> base64 float32, one dict
      <- the wire dict comes back
    {**wire, "model": ..., "version": ...}             the sender attaches these two
  FastAPI validates against EvaluateV1Response
```

The name lookup comes first, so an unknown model is a 404 before anything is parsed against a
model's variable set. Screen expansion, "asked for nothing" and unknown-id checks happen in `main`
so a bad request never occupies a worker. `evaluate.py` re-checks unknown ids as well, because it
is a public function of the model layer and a notebook caller gets the same error there
(`UnknownVariable`, which `main` maps to 400).

`model` in the response is always the URL name, so the `{name}` from the path. `version` is `demo`
for the demo model hosted under the name `demo`, `<name> (demo)` for the demo factory hosted under
any other name, and the name itself otherwise.

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

## Several models in one process

A process hosts every model named in `LUME_MODELS`, and the URL name is the only thing that
distinguishes them. `app.state.models` is a `dict[str, HostedModel]` keyed on that name, and
`HostedModel` is a dataclass holding everything one model owns:

| Field | What it is |
| --- | --- |
| `setting` | The `ModelSetting` from `loader.models_from_env()`: name, factory path, kwargs, workers, max_inflight, is_demo. |
| `pool` | That model's own `ModelPool`, sized by its own `workers` and `max_inflight`. |
| `info` | The `ModelInfo` one of its workers built and shipped back. |
| `hub` | Its `LiveHub`, in the `live` and `all` roles, else `None`. |
| `provider` | Its live input provider, built from its own `ModelInfo`, else `None`. |
| `version` | Derived: `demo` when the name is `demo`, `<name> (demo)` for the demo factory under another name, else the name. |

Nothing is shared between two hosted models except the process, the live input source setting and
the CPU. Each has its own pool, its own in-flight budget, its own cached description and its own
producer loops, so an evaluate on one cannot affect the other's results and a saturated model
returns 503 without touching another model's traffic.

Settings are read inside `lifespan` rather than at module import. That is what lets a test
`monkeypatch.setenv` before its `TestClient` enters the lifespan, and it removed the old
`MODEL_REF` and `POOL_WORKERS` module globals. `LUME_ROLE` is read there too, and validated, so
an unknown value fails startup instead of silently degrading to the eval behaviour. Only the
static mount still reads its setting at import, because it has to be mounted before the app
serves anything.

**Warmup is sequential across models, and that is deliberate.** `lifespan` constructs every pool
first, then warms them one at a time. Warming them concurrently would put `sum(workers)` model
instances under construction at the same moment, and neither the pod memory limit nor the
`startupProbe` `failureThreshold` was sized for that: M pools of K workers each would peak at M
times the memory a single-model pod was measured at. The cost is that a pod's startup time is the
sum over models rather than the maximum, which the probe budget has to cover.

Construction and warmup are wrapped so that a failure shuts down every pool already built before
re-raising. Without that, a model that fails to build halfway through leaves the earlier models'
spawn workers orphaned, and the pod restarts on top of them.

In the `live` and `all` roles there is one `LiveHub` per model, and each hub's read callback is
bound with a default argument (`lambda model=entry: _read_live_inputs(model)`). A
closure over the loop variable would give every hub the last model in the dict, which is the kind
of bug that only shows up on a two-model pod. Shutdown iterates every hub and then every pool.

Live input providers are per model, built from each model's own `ModelInfo`, but
`LUME_LIVE_SOURCE` and `LUME_LIVE_INPUTS` stay global. Two models that share PV names therefore
create duplicate `epics.PV` objects and read the same PV twice per frame. Accepted for now: a
shared per-PV cache would have to reason about whether two models want the same value at the same
instant, which is a bigger change than the duplicate read costs.

## The process pool

`api/pool.py` runs a `ProcessPoolExecutor` on a `spawn` multiprocessing context, with one pool per
hosted model and that model's `workers` count in each. `LUME_POOL_WORKERS` is the per-model
default, not a pod total, so a pod's worker count is the sum over models. Each worker holds
exactly one model instance in a module global. Three constraints force this shape.

Two model instances in one process segfault, because torch gets loaded twice, and pytao is not
thread-safe. So process isolation is required, not merely preferred, and threads are not an
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

Backpressure is a plain in-flight counter, per pool. At that model's `max_inflight` (default
`4 * workers`) a submit raises `PoolFull`, which becomes a 503 and increments
`lume_pool_rejected_total` for that model. There is no queue beyond the executor's own, on
purpose: a deep queue turns saturation into unbounded latency, and a UI would rather be told to
back off.

`ModelPool.__init__` takes the model name and uses `.labels(model=...)` on every metric, so all of
`lume_pool_inflight`, `lume_pool_max_inflight`, `lume_pool_workers`, `lume_evaluate_seconds`,
`lume_evaluate_total` and `lume_pool_rejected_total` are per-model series. Cardinality is M per
pod. A query has to aggregate over the label, which is why the per-pod saturation number is
`sum by (pod) (lume_pool_inflight)`.

Sizing is the model's business. A real model instance is a couple of GB and takes 25 to 30
seconds to build, so the worker count is set by pod memory rather than by CPU, and on a
multi-model pod it is the **sum** of the workers that has to fit. See
[`../deploy/kubernetes/CAPACITY.md`](../deploy/kubernetes/CAPACITY.md) and the split-pod recipe in
[DEPLOY.md](DEPLOY.md).

## The live hub

`api/live_hub.py` runs one background asyncio task **per distinct output set**, keyed on the
sorted tuple of resolved output ids. Each loop reads the live inputs, evaluates through the same
pool, and fans the resulting frame out to every subscriber of that key. There is one hub per hosted
model, so the keying is per model and a pod hosting M models can run up to M times the number of
distinct views in producer loops.

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
each frame itself. Each hub is constructed with `model=` set to its own URL name, so a frame from
one model's stream can never be mistaken for another's. Without that attachment a streamed frame
would not be a complete `EvaluateV1Response`, which is exactly what every client types the stream
as. `tests/test_wire_shape.py` pins this.

Channel access blocks, so `main._read_live_inputs` runs the provider on
`asyncio.to_thread`, keeping the event loop that serves the SSE clients free.

## `LUME_ROLE` and the two deployments

One image, three roles.

| `LUME_ROLE` | Serves | Notes |
| --- | --- | --- |
| `eval` | `/api/v1/models`, `/api/v1/models/{name}/config`, `/api/v1/models/{name}/evaluate`, `/metrics`, `/` | No hub, no EPICS. Scales freely. |
| `live` | everything above plus `/api/v1/models/{name}/live/stream` and `/api/v1/models/{name}/machine-snapshot` | The singleton EPICS reader. |
| `all` (default) | everything | Dev and single-pod deploys. |

The live-only endpoints return 503 with an explanatory detail under `LUME_ROLE=eval`, and
`LiveHub` is imported lazily so the eval role never loads it.

The split exists because the two workloads have opposite scaling properties. Evaluate is
stateless and CPU-bound, so more replicas is strictly better. The live producer must be a
singleton: N replicas would each independently read EPICS and evaluate, costing N times the
model work and showing different viewers divergent frames. So the eval pool is
`replicas: 2` and autoscalable, and the live producer is pinned at `replicas: 1` with a
`Recreate` strategy so a rollout never runs two EPICS readers. The ingress routes by regex,
sending `api/v1/models/<name>/live/*` and `api/v1/models/<name>/machine-snapshot` to the singleton
and everything else to the pool, so adding or removing a route changes no routing rule. The model
name sits inside that regex as `[^/]+`, so adding a model changes no routing rule either. Splitting
the models across pods later does add rules, and [DEPLOY.md](DEPLOY.md) has that recipe.

## Where metadata comes from

The main process **never imports the model**. That is a hard rule, and it is what keeps the web
process free of torch, Bmad and a lattice.

So the flow is: at startup, `lifespan` resolves `LUME_MODELS` into one `ModelSetting` per model
(import-free, so a bad value fails with one clear error rather than K worker tracebacks),
constructs a pool per model, and calls `pool.warmup()` on each in turn. Warmup submits enough
concurrent no-op pings that the executor has to spin up all K workers of that pool, which builds
all K instances of that one model in parallel, then fetches the `ModelInfo` one worker already
built. The parallelism is within a model, never across them.

`ModelInfo` and its `InputInfo` / `OutputInfo` / `ScreenInfo` members are plain dataclasses
with no numpy or model objects in them, deliberately, because they have to pickle across the
spawn boundary back into a process that has never imported the model. Each result is cached on its
`HostedModel.info` and is what that model's config route is served from, what `_resolve_outputs`
validates against, and what that model's live input provider is built from.

Consequences worth knowing:

- `GET /api/v1/models` and each config route are free after startup. They read cached dataclasses.
- A description is fixed for the process's lifetime. A model whose variable set changed at
  runtime would not be reflected, which is a trade the k8s deploy makes explicit: config is a
  pod-level property, and the pod restarts to change it. The same holds for the hosted model
  **set**: changing `LUME_MODELS` is a pod restart, not a runtime operation, so `GET /api/v1/models`
  answers from what the pod started with.
- `LUME_MODELS`, `LUME_POOL_WORKERS` and `LUME_MAX_INFLIGHT` are read inside `lifespan`, once, not
  per request. `LUME_ROLE` is read at module import, because the route set and the static mount
  depend on it and not on the model list. A pod's configuration does not change while it runs, and
  re-reading these would only invite drift between what a config route says and what the pool is
  doing.
- `describe()` runs once per worker rather than once per request, so introspecting a model may
  be as expensive as it needs to be.
