# Architecture

How the pieces fit, and why they are arranged this way. Read this before making a structural
change.

This file is the canonical home for the layering, the process pool, the live hub, sequential
warmup, baseline-merge and where the published metadata comes from. Other documents link here
rather than restating any of it. [`AGENTS.md`](../AGENTS.md) is a short index of the traps that
are invisible in the code and points back here.

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

A second consequence of the direction: the model layer cannot publish its own metrics. The live
input provider exposes a plain `names` attribute and returns only the ids it read, and the
api-layer caller (`main._read_live_inputs`) is what turns that into `lume_live_inputs_readable`
and `lume_live_inputs_total`.

## Request flow for an evaluate

```
POST /api/v1/models/{name}/evaluate
  main.evaluate_v1
    _hosted(name)                                      app.state.models lookup, 404 listing hosted names
    _resolve_outputs(info, req.outputs, req.screen)   expand `screen`, reject empty, 400 on unknown ids
    hosted.pool.evaluate(...)                          in-flight accounting, metrics, 503 when saturated or dead
      -> worker process (ProcessPoolExecutor, spawn)
           evaluate.evaluate(model, info, ...)         baseline-merge, model.set, model.get, convert by kind
           serialize.result_to_wire(result)            numpy -> base64 float32, one dict
      <- the wire dict comes back
    {**wire, "model": ..., "version": ..., "input_sources": ...}   the sender attaches these three
  FastAPI validates against EvaluateV1Response
```

The name lookup comes first, so an unknown model is a 404 before anything is parsed against a
model's variable set. Schema validation happens before any of it: `max_particles`,
`smooth_images_sigma_px` and every value in `inputs` are bounded and finite-checked by
`api/schemas.py`, so an out-of-range or `NaN` knob is a 422 that never reaches a worker. Screen
expansion, "asked for nothing" and unknown-id checks happen in `main` so a bad request never
occupies a worker either. `evaluate.py` re-checks unknown ids as well, because it is a public
function of the model layer and a notebook caller gets the same error there (`UnknownVariable`,
which `main` maps to 400).

`model` in the response is always the URL name, so the `{name}` from the path. `version` is `demo`
for the demo model hosted under the name `demo`, `<name> (demo)` for the demo factory hosted under
any other name, and the name itself otherwise.

`input_sources` is the third sender-attached key. On this route every id is either one the caller
sent (`"request"`) or one the baseline merge filled in (`"baseline"`), which is knowledge the
serializer does not have. The live producer attaches the same key from its own knowledge. See
[API.md](API.md#post-apiv1modelsnameevaluate) for the wire meaning.

The `frame_index` in an HTTP response is always 0. It is meaningful only on the live stream.

Response values are the mirror image of request values: a non-finite result is rendered as JSON
`null` rather than rejected, because a solver that did not converge legitimately produces NaN and
one bad field should not cost the whole frame. Both paths do it, the HTTP route through its
`response_model` and the SSE path through `serialize.json_safe`.

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

The merge is lossy in one way that matters operationally: after it, a design value and a real
machine reading are the same number. That is why the wire carries `input_sources` on an evaluate
and `sources` on a snapshot.

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
returns 503 without touching another model's traffic. A pool that dies is per model too, but a
dead pool takes the whole pod down through `/healthz`, on purpose. See "When a worker dies".

Settings are read inside `lifespan` rather than at module import. That is what lets a test
`monkeypatch.setenv` before its `TestClient` enters the lifespan, and it removed the old
`MODEL_REF` and `POOL_WORKERS` module globals. `LUME_ROLE` is read and validated there too, so an
unknown value fails startup instead of silently degrading to the eval behaviour, and so is the
live configuration (`live_inputs.check_env`), so a typo in `LUME_LIVE_SOURCE` or malformed
`LUME_LIVE_INPUTS` JSON is one startup error rather than one error event per frame forever. Only
the static mount reads its setting at import, because it has to be mounted before the app serves
anything.

**Warmup is sequential across models, and that is deliberate.** `lifespan` constructs every pool
first, then warms them one at a time. Warming them concurrently would put `sum(workers)` model
instances under construction at the same moment, and neither the pod memory limit nor the
`startupProbe` `failureThreshold` was sized for that: M pools of K workers each would peak at M
times the memory a single-model pod was measured at. The cost is that a pod's startup time is the
sum over models rather than the maximum, which the probe budget has to cover. Do not speed up
startup by gathering the warmups.

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

`ModelPool.__init__` takes the model name and uses `.labels(model=...)` on every metric, so every
series is per model. Cardinality is M per pod. A query has to aggregate over the label, which is
why the per-pod saturation number is `sum by (pod) (lume_pool_inflight)`. The full metric list is
in [API.md](API.md#get-metrics).

Sizing is the model's business. A real model instance is a couple of GB and takes 25 to 30
seconds to build, so the worker count is set by pod memory rather than by CPU, and on a
multi-model pod it is the **sum** of the workers that has to fit. See
[`../deploy/kubernetes/CAPACITY.md`](../deploy/kubernetes/CAPACITY.md) for the measured numbers and
[DEPLOY.md](DEPLOY.md#sizing-a-pod) for the sizing arithmetic.

### When a worker dies, the pool is dead and the pod restarts

Three different faults end here, all of them unrecoverable in-process and all of them latching
`ModelPool.dead`. `dead_reason` records which, and it is the `reason` label on `lume_pool_dead`
and the parenthesis in the `/healthz` detail.

| `reason` | What happened | Detected as |
| --- | --- | --- |
| `worker_lost` | A worker process was lost abruptly: an OOM kill of a multi-GB model, or the segfault process isolation exists to contain. | `BrokenProcessPool` |
| `unusable` | A worker's model broke itself while applying inputs and could not be recovered. The process is alive and permanently useless. | `ModelPoisoned` |
| `timeout` | `LUME_EVALUATE_TIMEOUT_S` expired, so a worker is computing an abandoned frame forever. See below. |  `asyncio.TimeoutError` |

`ProcessPoolExecutor` latches broken, which is what makes the first one the loss of the whole pool
rather than of one request. Once a worker is lost, every later submit raises `BrokenProcessPool`
for the life of the executor.

Whichever the cause, `ModelPool` sets `dead`, sets `lume_pool_dead` to 1 and logs at ERROR. Every
evaluate for that model then fails fast with `PoolDead`, which is a 503 carrying `Retry-After`, and
`/healthz` reports unhealthy so the k8s probes restart the pod.

**The pool is deliberately not rebuilt.** Spawning K replacement workers while the old ones tear
down would peak at twice the model memory, which turns a restart-recoverable failure into an OOM
kill of the pod. A restart is both cheaper and visible in `kubectl get pods`.

Two operational consequences. A dead pool reports `lume_pool_inflight` 0, so a scaling query reads
a bricked pod as idle: alert on `lume_pool_dead` rather than inferring death from the other
gauges. And a live producer loop for a dead pool tells its subscribers once and stops, rather than
retrying at frame rate and spamming every viewer.

The same latch is why a model that cannot be built raises `PoolDead` from `warmup()` with a
message naming the factory reference, rather than a bare executor error.

### A model that breaks itself is `unusable`, and it is not the caller's fault

A model can fail leaving its instance alive and internally inconsistent, so every later request
to that worker fails too, including one that sets nothing at all. On `cu_hxr_staged` a bend moved
2 percent off its startup value does exactly this, permanently, to one worker of K. The failure,
its measured thresholds and the upstream cause are in
[POISONED_WORKER.md](POISONED_WORKER.md).

**Exception type cannot classify this, which is why pre-validation exists.** The model's failure
surfaces as a numpy `ValueError` and a `RuntimeError` from a Tao command, and lume raises
`ValueError` and `TypeError` for a genuinely bad input value, so the two are indistinguishable
after the fact. `model/evaluate.py` therefore runs lume's own validation loop itself, in
`_prevalidate`, before calling `model.set()`: the supported-name check, the `Variable` check,
`read_only`, and `variable.validate_value(...)`. `LUMEModel.set` completes that whole loop before
it calls `_set`, so a failure in `_prevalidate` is the complete set of faults attributable to the
request, and **anything escaping `model.set()` afterwards is by construction the model's own**.
That second case becomes `ModelUnusable`, a `RuntimeError` rather than a `ValueError` so it cannot
be swallowed by the arm that answers 400.

Three layers then follow, in order of cost.

1. `_prevalidate` rejects every genuine client fault as a 400, before the model runs.
2. Anything escaping `model.set()` is a server fault, with no drift comparison, no tolerances and
   no per-variable assumptions. A readback cannot substitute for this: the failing request raises
   *inside* `model.set()`, and by then the model has already refreshed its cached controls to the
   values just written, so drift on a broken worker is zero.
3. The worker tries in-process recovery once. If that works the request still fails, as a 503, but
   the worker keeps serving. If it does not, the worker latches `_UNUSABLE` and raises
   `ModelPoisoned`, which escalates to `PoolDead` and a pod restart.

Detection has to be worker-side. `ProcessPoolExecutor` exposes no worker identity to the main
process, which therefore cannot tell which worker served a request, cannot target one and cannot
replace one.

**`lume_pool_recovery_total` is what makes the rate visible**, labelled by `outcome`. The
terminal state is already in `lume_pool_dead`, so a bend scan poisoning a worker on every pass
looks identical in the gauge and obvious in the counter.

**The invariant that an evaluate is history-independent does not hold on an unusable worker.**
The baseline merge applies every non-constant knob on every request, which is what normally makes
any worker answer any request identically. A worker whose model broke itself answers differently
from its siblings until the pod restarts, which is why the failing fraction climbs in steps rather
than the service failing outright.

#### Recovery is a `recover()` protocol, and nothing implements it yet

`_attempt_recovery` calls `model.recover()` when the model defines one, and otherwise reports
`unavailable` and goes straight to latching. **No model declares `recover()` today, so recovery
is inert and every poisoning currently ends in a pod restart.** That is the intended state rather
than an oversight: layers 1 and 2 plus the latch are the value here, and they are what turn a
silent permanent degradation into a visible restart.

The measured cure for `cu_hxr_staged` is to disable lattice calculation, write every baseline
control back, restore `track_type`, re-enable calculation and refresh state. Disabling the
calculation first is what makes it safe, because Tao then accepts the intermediate writes without
evaluating an unstable orbit. Three of those five steps are Bmad commands, so the sequence belongs
in the model, upstream in lume-bmad or virtual-accelerator, and not in a host whose whole premise
is that it knows nothing model-specific (see the no-per-model-tables rule in
[AGENTS.md](../AGENTS.md)).

**`model.reset()` is not the recovery and must never be called on a Bmad model.** It looks like
the obvious method and it is destructive and permanent even on a healthy instance:
`LUMEBmadModel.__init__` snapshots `_initial_state` before `build_bmad_model` runs
`model.set({"track_type": "beam"})`, so `reset()` writes back `"single"`, which unregisters 24
outputs including all five `*_beam` screens, after which every evaluate fails with
`Variable 's' is not supported by the model`. This service has never called it. Keep it that way.

### `LUME_EVALUATE_TIMEOUT_S`

Off by default (`0` or unset). When set to a float number of seconds, an evaluate that exceeds it
raises `PoolDead` and marks the pool dead, exactly as a lost worker does.

That is deliberate rather than harsh. `run_in_executor` cannot cancel work already handed to a
subprocess, so the worker keeps computing the abandoned frame and can never be reclaimed. The
multi-GB model memory it holds is only released by a process restart, so the honest response to a
timeout is to fail the pod rather than to leak a worker per timeout.

Any value must therefore sit well above both the 25 to 30 second model build and the roughly 2.5
seconds a real evaluate takes. A timeout set near normal latency converts a slow frame into a pod
restart.

A client disconnecting mid-evaluate is different and is not fatal: the submit is recorded as
`outcome="cancelled"` in `lume_evaluate_total`, and because the worker keeps computing the
abandoned frame, real occupancy can briefly exceed `max_inflight` when many stream clients leave
at once.

## The live hub

`api/live_hub.py` runs one background asyncio task **per distinct output set**, keyed on the
sorted tuple of resolved output ids. Each loop reads the live inputs, evaluates through the same
pool, and fans the resulting frame out to every subscriber of that key. There is one hub per hosted
model, so the keying is per model.

Keying on the output set rather than on a screen name is what makes sharing correct. Two clients
that asked for the same ids by different routes, one via `screen` and one via `outputs`, share a
loop, and a client that asked for one extra id gets its own. So N viewers of one view cost one
evaluate loop, not N, which is what lets a singleton producer with a 2-worker pool serve many
browsers.

A loop starts on its first subscriber and is cancelled when its last one leaves. A new
subscriber is seeded immediately with that loop's most recent frame, so a UI paints without
waiting a whole evaluate.

**The number of distinct loops is capped**, by default at that model's `max_inflight`. The same
sharing that makes many viewers cheap also lets one client multiply the work by varying its output
set, and loops past the in-flight limit only collect `PoolFull` errors while every legitimate
viewer's frame rate falls as 1/P. At the cap a request for a new output set is refused with a 503
whose detail says to reuse a set already streaming, because retrying will not help until a viewer
disconnects. `lume_live_streams` publishes the current count, so alert on it sitting at
`lume_pool_max_inflight`. The check happens in the route, before `EventSourceResponse` starts,
because once the response has begun there is no status code left to send.

`frame_index` is one counter per hub rather than one per stream. A per-stream counter restarted at
0 whenever the last viewer of a set left and a new one arrived, so a reconnecting client saw the
index go backwards. Per hub it only ever increases, at the cost of gaps in any one stream where a
sibling stream produced a frame.

There is no poll period. The loop runs as fast as `await evaluate` allows, which paces it
naturally, because awaiting real work yields to the event loop. Only the error path sleeps
(0.5 s), to avoid a hot spin on repeated failures. A transient exception is broadcast as an
`error` event and the loop survives it.

**An `error` event carries a generic message, not the exception text.** Every subscriber is an
untrusted browser, and a solver or lattice traceback can name file paths, beamline elements and a
model's private structure. The detail goes to the pod log through `logger.exception`, which is now
the only place it exists. `PoolDead` is the one exception and keeps its specific message, because
it tells the operator to expect a pod restart and contains nothing worth hiding.

Each subscriber has a size-1 **drop-old** queue. When a subscriber is too slow to drain, the
producer discards that subscriber's stale frame and enqueues the new one. A slow client sees a
lower frame rate, never a growing backlog and never head-of-line blocking for the others.

The SSE path bypasses the HTTP endpoint, so `LiveHub._run` attaches `model`, `version` and
`input_sources` to each frame itself. Each hub is constructed with `model=` set to its own URL
name, so a frame from one model's stream can never be mistaken for another's. Without those
attachments a streamed frame would not be a complete `EvaluateV1Response`, which is exactly what
every client types the stream as. `tests/test_wire_shape.py` pins this, and
[API.md](API.md#changing-the-contract) explains why nothing else can catch a missing key.

Subscribing happens inside the event generator, in the same `try`/`finally` as the unsubscribe.
sse-starlette can cancel a response before it ever calls `__anext__`, and a generator that never
started does not run its `finally`, so a subscribe done outside would leave a queue in the set and
its producer loop evaluating forever with no viewer.

## Live inputs, and what "live" is allowed to mean

`model/live_inputs.py` provides the values the live view evaluates at. `LUME_LIVE_SOURCE` picks
`epics` (each non-constant input id read as a channel-access PV) or `synthetic` (a slow wiggle
derived from `ModelInfo` alone, so the stream is demonstrable with no control system in reach).
Either way the provider returns **only the ids it actually has a value for**, and
`main._read_live_inputs` overlays that on the model's baseline.

Channel access blocks, so the read runs on `asyncio.to_thread`, keeping the event loop that serves
the SSE clients free. Every producer loop for a model plus `machine-snapshot` share one provider
and land in `read_inputs` on different threads, which is why `InputProvider` documents it as safe
to call concurrently and why neither implementation takes a lock.

Three rules keep the overlay honest, because a design value served as though it came off the
machine is worse than an error.

**Nothing is ever pruned permanently.** An id that is not readable on a given frame is skipped for
that frame only and recovers by itself on a later one, since CA reconnects are automatic. An
earlier version dropped every id that failed its first read, so one briefly unreachable gateway at
startup, or an IOC down during a rollout of the singleton live pod, permanently degraded the
process for the life of the pod. An unconnected PV is never `get()` at all, so a model input that
is not a real PV costs nothing per frame and does not pace the loop at the CA timeout. A connected
record that reads back NaN or infinity counts as unreadable, because passing that to `model.set`
either raises in a worker or produces a NaN beam that looks like a physics result.

**Zero connected PVs raises.** No live machine has all of its PVs down, so zero connected means
channel access itself is broken. Raising makes the stream report an error and `machine-snapshot`
fail, which is far better than overlaying `{}` on the baseline and publishing the model's design
values labelled as live.

**The unreadable set is logged only when it changes.** At live-loop rates, one line per frame is
the difference between a usable pod log and megabytes of the same sentence, while a recovery or a
newly broken PV still shows up.

`lume_live_inputs_readable` and `lume_live_inputs_total` are the aggregate an alert fires on, and
`input_sources` / `sources` are the per-response answer to the same question. `readable` below
`total` is not by itself a fault, because a model input that is not a real PV never reads. A drop
from a pod's own steady state is.

## `LUME_ROLE` and the two deployments

One image, three roles.

| `LUME_ROLE` | Serves | Notes |
| --- | --- | --- |
| `eval` | `/api/v1/models`, `/api/v1/models/{name}/config`, `/api/v1/models/{name}/evaluate`, `/healthz`, `/metrics`, `/` | No hub, no EPICS. Scales freely. |
| `live` | everything above plus `/api/v1/models/{name}/live/stream` and `/api/v1/models/{name}/machine-snapshot` | The singleton EPICS reader. |
| `all` (default) | everything | Dev and single-pod deploys. |

The live-only endpoints return 503 with an explanatory detail under `LUME_ROLE=eval`, keyed off
`hosted.hub is None` in both routes so a role change cannot leave them disagreeing. `LiveHub` is
imported lazily so the eval role never loads it.

The split exists because the two workloads have opposite scaling properties. Evaluate is
stateless and CPU-bound, so more replicas is strictly better. The live producer must be a
singleton: N replicas would each independently read EPICS and evaluate, costing N times the
model work and showing different viewers divergent frames. So the eval pool is
`replicas: 2` and autoscalable, and the live producer is pinned at `replicas: 1` with a
`Recreate` strategy so a rollout never runs two EPICS readers. The ingress routes by regex,
sending `api/v1/models/<name>/live/*` and `api/v1/models/<name>/machine-snapshot` to the singleton
and everything else to the pool, so adding or removing a route changes no routing rule. The model
name sits inside that regex as `[^/]+`, so adding a model changes no routing rule either.
Splitting the models across pods later does add rules, and
[DEPLOY.md](DEPLOY.md#splitting-models-across-pods) has that sketch.

`GET /healthz` is what all three probes on both Deployments target, and it is deliberately outside
the `/api/v1` contract. `GET /api/v1/models` is client discovery and has to keep listing the
models that still work even when one pool has died, which is the opposite of what a probe wants.
Probe configuration is in [DEPLOY.md](DEPLOY.md#probes).

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

The rules that turn a model's `supported_variables` into that description are the model author's
contract and live in [ADDING_A_MODEL.md](ADDING_A_MODEL.md).

Consequences worth knowing:

- `GET /api/v1/models` and each config route are free after startup. They read cached dataclasses.
- A description is fixed for the process's lifetime. A model whose variable set changed at
  runtime would not be reflected, which is a trade the k8s deploy makes explicit: config is a
  pod-level property, and the pod restarts to change it. The same holds for the hosted model
  **set**: changing `LUME_MODELS` is a pod restart, not a runtime operation, so `GET /api/v1/models`
  answers from what the pod started with.
- `LUME_MODELS`, `LUME_POOL_WORKERS`, `LUME_MAX_INFLIGHT`, `LUME_ROLE` and the live settings are
  read inside `lifespan`, once, not per request. A pod's configuration does not change while it
  runs, and re-reading these would only invite drift between what a config route says and what the
  pool is doing.
- `describe()` runs once per worker rather than once per request, so introspecting a model may
  be as expensive as it needs to be.
