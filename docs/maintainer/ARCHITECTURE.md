# Architecture

lume-model-api is a FastAPI process that hosts one or more `LUMEModel` instances. Each model runs
in its own pool of worker subprocesses, and the web process never imports the model itself. This
doc covers how a request moves through the code and what each part is responsible for. The reasons
behind the less obvious choices are in [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md).

## Layout

```
lume_model_api/
  model/            builds, describes and evaluates a LUMEModel. No HTTP, no FastAPI, no Pydantic.
    loader.py         LUME_MODELS -> list of ModelSetting (URL name, factory path, kwargs, pool size)
    introspect.py     model instance -> ModelInfo (inputs, outputs, screens, baseline)
    evaluate.py       baseline merge, validation, model.set, model.get, conversion by variable kind
    live_inputs.py    live input values from EPICS or a synthetic source
    demo.py           a small real LUMEModel, used by LUME_MODELS=demo and by the tests
  api/              the FastAPI service. Imports model/, never the reverse.
    main.py           routes, lifespan, role selection, the optional static mount
    pool.py           one spawn subprocess pool per hosted model
    live_hub.py       one producer loop per distinct output set, fanned out over SSE
    schemas.py        Pydantic request and response models (the source of openapi.json)
    serialize.py      numpy -> base64 float32 wire dicts
    metrics.py        Prometheus gauges, counters and histograms
```

`api` imports `model`, and `model` never imports `api`. That keeps the model layer usable from a
notebook with no web server: `describe(build_model(...))` and `evaluate(model, info, ...)` are
plain functions over plain dataclasses. `tests/test_introspect.py`, `tests/test_evaluate_demo.py`
and `tests/test_wire_shape.py` test the pipeline this way, without a TestClient.

There's one exception in the other direction. `serialize.py` lives in `api/` but runs inside a
pool worker, so arrays cross the process boundary already encoded instead of as pickled numpy. It
imports nothing from FastAPI, so the worker only pays for numpy (and scipy on the opt-in smoothing
path).

### Lazy imports

Several imports sit inside functions instead of at the top of a module. Hoisting any of them breaks
CI, which runs `pytest` and `python scripts/dump_openapi.py` in a plain venv with no torch, pytao,
Bmad or EPICS.

| Import | Where | Reason it stays lazy |
| --- | --- | --- |
| The hosted model (`virtual_accelerator`, ...) | `model/loader.py`, `build_model`, via `importlib` | Not on PyPI, pulls in torch or pytao, and must never load in the main process. |
| `scipy.ndimage` | `api/serialize.py`, `_smooth` | Only the opt-in `smooth_images_sigma_px` path uses it, and every worker imports this module at startup. |
| `epics` (pyepics) | `model/live_inputs.py`, `EpicsInputProvider.__init__` | Optional `[epics]` extra. Also has to load after the CA environment variables are set, because channel access reads them once at import. |
| `lume.variables` | `model/introspect.py` `variable_kind`, `model/evaluate.py` `_coerce_control` and `_prevalidate` | Keeps the main process import cheap. lume-base is still a required dependency. |
| `beamphysics.ParticleGroup` | `model/demo.py`, `_beam` | Only needed when the demo model runs. |
| `LiveHub`, `TooManyStreams` | `api/main.py`, `lifespan` and `live_stream` | Only the `live` and `all` roles load them. |
| `describe`, `build_model`, `result_to_wire`, `evaluate`, `tempfile` | `api/pool.py`, `_init_worker` and `_worker_evaluate` | Worker side. `spawn` re-imports instead of inheriting memory, so these are imported by absolute module path. |

`ModelUnusable` is imported at the top of `api/pool.py` even though it comes from the same module
as the worker-side imports. `_submit` runs in the main process and needs the class to classify what
a worker raised, and `model/evaluate.py` pulls in nothing heavier than numpy at import.

## Request flow

```
POST /api/v1/models/{name}/evaluate
  main.evaluate_v1
    _hosted(name)                                  404 if this process doesn't host {name}
    _resolve_outputs(info, req.outputs, req.screen)  expand `screen`, 400 on nothing or unknown ids
    pool.evaluate(...)                             in-flight count, metrics, 503 when saturated or dead
      -> worker process (ProcessPoolExecutor, spawn)
           evaluate.evaluate(model, info, ...)     merge, prevalidate, model.set, model.get, convert
           serialize.result_to_wire(result)        numpy -> base64 float32, one dict
      <- wire dict
    {**wire, model, version, input_sources}        attached by the sender
  FastAPI validates against EvaluateV1Response
```

Screen expansion and unknown-id checks run in `main`, so a bad request never takes up a worker.
`evaluate.py` checks unknown ids again because it's also a public model-layer function, and a
notebook caller gets the same `UnknownVariable` error there.

`model`, `version` and `input_sources` are added by the sender because only the sender knows them.
There are two senders, `main.evaluate_v1` and `LiveHub._run`, and both have to attach all three.
`SENDER_ADDED` in `tests/test_wire_shape.py` pins the set.

`frame_index` is always 0 on the HTTP route. It only counts on the live stream.

## Baseline merge

Every evaluate sets all non-constant inputs, not just the ones in the request. `introspect.describe`
builds `ModelInfo.baseline`, a dict of `{input id: default}` for every non-constant input, once per
worker. `evaluate.evaluate` merges the request over it (`{**baseline, **requested}`), drops
constants, and applies the result in one `model.set()`.

A model instance keeps whatever values were last set on it, and each model runs on several
workers. Applying only the requested inputs would make the result depend on what the previous
request left on that worker. With the merge, the same request gives the same result on any worker
in any replica, and an empty `inputs` evaluates the design machine.

The merged set comes back in the response's `inputs`, so a caller can see what the baseline filled
in. Particle subsampling uses a fixed `linspace` stride instead of a random sample, so two workers
return the same particles.

The merge is only correct while no two inputs control the same thing, which is what
`introspect._resolve_aliases` enforces. See [One input per control](DESIGN_DECISIONS.md#one-input-per-control).

### Validation before `model.set()`

Before calling `model.set()`, `evaluate._prevalidate` runs the same checks `LUMEModel.set` runs:
the name is supported, the variable is a `Variable`, it isn't `read_only`, and
`variable.validate_value()` accepts the value. A failure there raises `InvalidInput`, which is a
400. Anything raised from inside `model.set()` after that is the model's own failure and becomes
`ModelUnusable`, a 503. See [Model failures are 503, not 400](DESIGN_DECISIONS.md#model-failures-are-503-not-400).

## Process pool

`api/pool.py` runs one `ProcessPoolExecutor` per hosted model, on a `spawn` context. Each worker
holds one model instance in a module global (`_MODEL`) and the `ModelInfo` built from it (`_INFO`).

The shape comes from three constraints:

- Two model instances in one process segfault (torch loaded twice), and pytao isn't thread-safe,
  so each instance needs its own process.
- `fork` with torch and OpenMP is unsafe, so the pool uses `spawn`.
- `spawn` re-imports modules instead of inheriting memory, so `_init_worker` and
  `_worker_evaluate` import everything they need by absolute path.

`_init_worker` also sets up the worker process:

- pins `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `OPENBLAS_NUM_THREADS` and `TORCH_NUM_THREADS` to
  `LUME_WORKER_THREADS` (default 1), so K workers don't each spawn a full thread pool
- sets `KMP_DUPLICATE_LIB_OK=TRUE`
- sets `HDF5_USE_FILE_LOCKING=FALSE` before HDF5 loads, because K workers opening one read-only
  design-beam file otherwise fail with `[Errno 11]`
- `chdir`s into a fresh temp directory, so files a worker writes can't collide with a sibling's

Pool size comes from `LUME_MODELS` per model, falling back to `LUME_POOL_WORKERS` (default 4).
That's a per-model number, so a pod's total worker count is the sum over its models.

Backpressure is a plain in-flight counter per pool. When a model has `max_inflight` evaluates
running (default `4 * workers`, or `LUME_MAX_INFLIGHT`), the next submit raises `PoolFull`, which is
a 503 with `Retry-After: 2`. There's no queue beyond the executor's own, so saturation shows up as a
fast 503 instead of growing latency.

`LUME_EVALUATE_TIMEOUT_S` is off by default. When it's set, an evaluate that runs longer kills the
pool (see below), so any value has to sit well above the model build time and the normal evaluate
latency.

A client that disconnects mid-evaluate is recorded as `outcome="cancelled"`. The worker keeps
computing the abandoned frame, so real occupancy can briefly exceed `max_inflight` when many live
viewers leave at once.

## When a worker dies

Three faults can't be recovered inside the pod. Each one latches `ModelPool.dead` and records
`ModelPool.dead_reason`:

| `dead_reason` | Cause | Detected as |
| --- | --- | --- |
| `worker_lost` | A worker process died, usually an OOM kill or a segfault. | `BrokenProcessPool` |
| `unusable` | A worker's model broke itself while applying inputs and recovery failed or wasn't available. The process is alive but useless. | `ModelPoisoned` from the worker |
| `timeout` | `LUME_EVALUATE_TIMEOUT_S` expired. The subprocess can't be cancelled, so that worker is stuck. | `asyncio.TimeoutError` |

Once dead, the pool sets `lume_pool_dead{reason=...}` to 1, logs at ERROR, and fails every later
evaluate fast with `PoolDead` (503, `Retry-After: 30`). `GET /healthz` returns 503 with the reason,
so the probes restart the pod. The pool is never rebuilt in place, because spawning replacements
while the old workers tear down can double the pod's memory. See
[Dead pools are not rebuilt](DESIGN_DECISIONS.md#dead-pools-are-not-rebuilt).

`ProcessPoolExecutor` latches broken after losing one worker, so `worker_lost` takes out the whole
pool, not just one request. A model that fails to build in `_init_worker` surfaces the same way,
as `PoolDead` from `warmup()` with a message naming the factory.

The `unusable` case is detected in the worker, because `ProcessPoolExecutor` gives the main process
no way to tell which worker served a request. When `ModelUnusable` escapes `evaluate`,
`_worker_evaluate` logs the model's own message and calls `_attempt_recovery`:

- If the model defines `recover()` and it succeeds, the request still fails (503, `Retry-After: 2`)
  but the worker keeps serving.
- If `recover()` raises, or the model doesn't define it, the worker sets its `_UNUSABLE` latch and
  raises `ModelPoisoned`. `_submit` turns that into `PoolDead` with reason `unusable`.

No model defines `recover()` today, so every `unusable` failure currently ends in a pod restart.
`lume_pool_recovery_total{outcome=...}` counts the attempts (`recovered`, `failed`, `unavailable`).
`model.reset()` is never used as recovery. See
[No model.reset() on Bmad models](DESIGN_DECISIONS.md#no-modelreset-on-bmad-models).

A worker whose model broke itself answers differently from its siblings until the pod restarts, so
the baseline merge's "same answer on any worker" guarantee doesn't hold for it.

## Live hub

`api/live_hub.py` has one `LiveHub` per model in the `live` and `all` roles. It runs one asyncio
task per distinct output set, keyed on the sorted tuple of resolved output ids. Each loop reads the
live inputs, evaluates through the same pool as HTTP requests (with `kind="live"`), and sends the
frame to every subscriber of that key.

Keying on the output set means two clients asking for the same ids share a loop, even if one used
`screen` and the other listed `outputs`. N viewers of one view cost one evaluate loop, so a
single live pod with a 2-worker pool can serve many browsers.

How a loop behaves:

- It starts on its first subscriber and is cancelled when the last one leaves.
- A new subscriber gets the loop's latest frame immediately, so a UI paints without waiting a full
  evaluate.
- There's no poll period. The loop runs as fast as `await pool.evaluate` returns. Only the error
  path sleeps (`ERROR_BACKOFF_S`, 0.5 s).
- A transient exception is logged with its traceback and sent to viewers as an `error` event with
  `GENERIC_ERROR_MESSAGE`, and the loop keeps going. The real message stays in the pod log because
  it can contain file paths and model internals.
- `PoolDead` is sent once, with its real message, and the loop stops and forgets the stream.
- Each subscriber has a size-1 queue that drops the old frame when a new one arrives. A slow client
  sees a lower frame rate, never a backlog, and doesn't hold up anyone else.

The number of distinct loops per model is capped at the pool's `max_inflight`. More loops than
in-flight slots would only produce `PoolFull` errors. A request for a new output set past the cap
is a 503 from `check_capacity`, raised before the SSE response starts so the client gets a real
status code.

`frame_index` is one counter per hub, not per stream, so it never goes backwards when a stream
restarts. It has gaps where sibling streams produced frames.

The SSE path has no `response_model`, so `LiveHub._run` attaches `model`, `version` and
`input_sources` itself and runs `serialize.json_safe` to turn NaN and Inf into `null`. Without that,
`json.dumps` would emit `NaN`, which isn't valid JSON, and the browser would drop the whole frame.

### Live inputs

`main._read_live_inputs` calls the model's input provider on `asyncio.to_thread`, because channel
access blocks. It returns two dicts: the values merged over the baseline, and the subset actually
read from the machine. The second one is what fills `input_sources` on a frame and `sources` on
`machine-snapshot`.

`LUME_LIVE_SOURCE` picks the provider in `model/live_inputs.py`:

- `epics` (default) reads every non-constant input id as a PV. On the first read it waits up to
  `connection_timeout` (3 s) for all PVs together. An unconnected or non-finite PV is skipped for
  that frame and stays in the set, so it's picked up again when it reconnects. If no PV at all is
  connected, the read raises instead of returning design values labelled as live.
- `synthetic` moves each input around its default on a 6 s cycle, for demos with no control system.

`LUME_LIVE_INPUTS` (a JSON list) restricts which ids are read. `live_inputs.check_env` validates
both settings, and the presence of pyepics, at startup.

Each model has its own provider. Two models sharing PV names read each PV once per model per frame.

## Roles

One image runs in one of three roles, set by `LUME_ROLE`:

| `LUME_ROLE` | Serves | Notes |
| --- | --- | --- |
| `eval` | `/api/v1/models`, `.../config`, `.../evaluate`, `/healthz`, `/metrics` | No live hub, no EPICS. Runs as several replicas. |
| `live` | everything above plus `.../live/stream` and `.../machine-snapshot` | The single EPICS reader. |
| `all` (default) | everything | Local dev and single-pod setups. |

An unknown value fails startup. On `eval`, the two live routes return 503 with
`live view not served by this instance` or `machine-snapshot not served by this instance`.

Evaluate is stateless and CPU-bound, so the eval Deployment can run more replicas. The live
Deployment has to stay at `replicas: 1` with a `Recreate` strategy, because each replica would read
EPICS and run its own loops. See
[Live producer is a singleton](DESIGN_DECISIONS.md#live-producer-is-a-singleton).

The ingress in `deploy/kubernetes/ingress.yaml` routes by regex. Paths matching
`api/v1/models/<name>/(live/...|machine-snapshot)` go to the live Service, and everything else
goes to the eval Service, so adding a route or a model needs no new rule.

## Model metadata

The main process never imports the model. Everything it knows about a model comes back from a
worker as a `ModelInfo` dataclass.

At startup, `lifespan` in `api/main.py`:

1. Reads `LUME_MODELS` with `loader.models_from_env()`. Resolution doesn't import anything, so a
   bad value fails once with a clear message instead of K worker tracebacks. The retired
   `LUME_MODEL` and `LUME_MODEL_KWARGS` also fail startup, with a suggested replacement.
2. Reads `LUME_ROLE` and, for live roles, checks the live settings.
3. Creates one `ModelPool` per model.
4. Warms the pools one model at a time. `pool.warmup()` submits K pings so all K workers spawn and
   build their models, then fetches `ModelInfo` from one of them. See
   [Warmup is sequential](DESIGN_DECISIONS.md#warmup-is-sequential).
5. In live roles, creates a `LiveHub` per model.

If any model fails to build, every pool already created is shut down before the error propagates,
so no spawn workers are left behind.

`ModelInfo` and its `InputInfo`, `OutputInfo` and `ScreenInfo` members hold no numpy or model
objects, so they pickle across the spawn boundary into a process that has never imported the
model. They're stored on `app.state.models[name].info` and used for the config route, for
`_resolve_outputs`, and to build the live input provider.

This has a few consequences:

- The config route reads a cached dataclass, so it costs nothing after startup.
- A model's description is fixed for the life of the process. Changing models means restarting the
  pod.
- `describe()` runs once per worker, not per request, so it can be slow without affecting requests.
- Settings are read inside `lifespan`, not at import, so tests can set the environment before
  their TestClient starts. `LUME_ROOT_PATH` and `LUME_STATIC_DIR` are the exceptions, because the
  app and the static mount are created at import.

### How variables become inputs, outputs and screens

`introspect.describe` walks `model.supported_variables` in id order:

- A `read_only` variable becomes an output, with `kind` from `variable_kind` (`scalar`, `array`,
  `particles` or `value`).
- A writable `ScalarVariable` (including `IntVariable`) becomes an input. Its default is
  `default_value`, or `model.get()` if there's none. If that read fails too, the input is skipped
  with a warning.
- A writable variable of any other type is left out and logged at INFO.

An input without `value_range` gets `default ± LUME_DERIVED_RANGE_FRACTION * |default|` (0.5 by
default, or an absolute span of 0.5 when the default is 0), marked `range_source: "derived"`. An
input whose range has equal bounds is `constant` and is left out of the baseline and the live read.

`_resolve_aliases` then groups inputs by id prefix, unit and default. In a group with more than one
member, one input stays (ranked by `ALIAS_PREFERENCE`, then a model-declared range, then id) and the
others become read-only outputs with `alias_of` set.

Screens come from `particles` outputs. Each one except `output_beam` becomes a screen, keyed by the
id with any `_beam` suffix removed. An `array` output whose id ends in `:Image:ArrayData` and whose
`element_name` matches that key becomes the screen's image. When two outputs strip to the same key,
the second is logged and skipped.

## Metrics

`api/metrics.py` uses the default single-process Prometheus registry in the main process. `_submit`
runs there, so that's where the counts are. Each pod exposes its own `/metrics`, and every series
has a `model` label.

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `lume_pool_inflight` | gauge | `model` | Evaluates in flight |
| `lume_pool_max_inflight` | gauge | `model` | Configured `max_inflight` |
| `lume_pool_workers` | gauge | `model` | Worker processes in the pool |
| `lume_pool_dead` | gauge | `model`, `reason` | 1 once the pool can't serve again. `reason` is `worker_lost`, `unusable` or `timeout`. |
| `lume_pool_recovery_total` | counter | `model`, `outcome` | Recovery attempts: `recovered`, `failed`, `unavailable` |
| `lume_evaluate_seconds` | histogram | `model`, `kind` | Whole submit time, queue wait plus model run. `kind` is `interactive` or `live`. |
| `lume_evaluate_total` | counter | `model`, `kind`, `outcome` | Completed evaluates: `ok`, `error`, `cancelled` |
| `lume_pool_rejected_total` | counter | `model`, `kind` | 503s from a saturated pool |
| `lume_live_inputs_total` | gauge | `model` | Input ids the live provider tries to read |
| `lume_live_inputs_readable` | gauge | `model` | Ids that returned a usable value on the last read |
| `lume_live_streams` | gauge | `model` | Distinct live output sets running |

Every label combination of `lume_pool_dead` and `lume_pool_recovery_total` is published at 0 when
the pool is created, so `absent()` queries and comparisons with healthy pods work.

Things to watch when scaling by hand or alerting:

- Per-pod saturation is `sum by (pod) (lume_pool_inflight)`. A plain average across series is
  diluted by the number of models.
- A dead pool reports 0 in flight and looks idle. Watch `lume_pool_dead`, summed over `reason`.
- `lume_live_inputs_readable` below `lume_live_inputs_total` is normal for a model whose inputs
  aren't all real PVs. A drop from the pod's usual level means some inputs are being served from
  design values.
- `lume_live_streams` at `lume_pool_max_inflight` means new output sets are being refused.

The live input gauges are set by `main._read_live_inputs`, not by the provider, because the
provider lives in the model layer and can't import `api/metrics.py`.
