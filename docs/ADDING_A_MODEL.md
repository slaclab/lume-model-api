# Hosting a different model

This service hosts any object that satisfies `lume.model.LUMEModel`. There is no registration
step and no schema to write. The service reads the model's `supported_variables` once per worker
and derives the whole API from it, so what shows up over HTTP is a direct function of how the
model declares its variables.

Read this file when you want to point the service at a model it has not hosted before, whether
that is a factory in [virtual-accelerator](https://github.com/slaclab/virtual-accelerator) or
something you are writing yourself. It is the canonical home for two things: the model author's
contract, and the `LUME_MODELS` rules in full.

## What the model must expose for each feature to appear

`LUMEModel` requires four things: a `supported_variables` property returning a dict of name to
`lume.variables.Variable`, `_get(names)`, `_set(values)` and `reset()`. The public `get()` and
`set()` wrappers validate names and values against `supported_variables` for you.

Introspection (`lume_model_api/model/introspect.py`) then walks that dict. Each rule below is
the whole story for one API feature.

### Writable ScalarVariables become inputs

A variable that is **not** `read_only` and **is** a `ScalarVariable` (or `IntVariable`, which
subclasses it) becomes an entry in `GET /api/v1/models/{name}/config`'s `inputs` and a knob a
caller may send in `inputs` on evaluate.

Writable variables of any other class appear nowhere. A writable `ParticleGroupVariable` (an
`input_beam`, say) is not drivable through a JSON knob API, and it is not a measurement either,
so listing it as an output would promise a `get()` that many models do not support on their own
controls. Every one of those dropped variables is logged at INFO once per `describe` call, with its
id and kind, so an operator can see why a knob is missing from the published inputs rather than
guessing.

### `default_value`, `value_range` and `unit` feed the config endpoint

| Model declares | Becomes |
| --- | --- |
| `default_value` | `default`, and the value used for this input in the baseline |
| `value_range=(low, high)` | `min`, `max`, and `range_source: "model"` |
| `unit` | `unit` on the input and, for read-only variables, on every output payload |

If a writable scalar has no `default_value`, the service falls back to `model.get([name])` to
read the control's current value. If that read raises, the input is skipped with a warning
rather than failing startup, which is how a model that refuses to read back some controls still
comes up.

### Missing ranges are derived

A writable scalar with no `value_range` still gets a usable slider. The service derives
`min = default - span` and `max = default + span` with
`span = LUME_DERIVED_RANGE_FRACTION * abs(default)`, or `span = LUME_DERIVED_RANGE_FRACTION`
when the default is 0 (a proportional span would collapse to a point). The fraction defaults to
0.5. Such an input is flagged `range_source: "derived"` so a UI can render it as a suggestion
rather than a limit.

This is a stopgap for models whose Bmad-side controls carry only `unit` and `read_only`. See
[Pushing metadata upstream](#pushing-metadata-upstream) below.

### `min == max` means constant

When the model's own `value_range` has `low == high`, the input is reported with
`constant: true`. Constants are excluded from the baseline, never set during an evaluate, and
never read by the live input source, because setting them back is at best a no-op and at worst a
validation error.

### Two writable ids for one control: only one stays settable

If your model publishes several writable handles on the same underlying control, the service
publishes one of them as an input and demotes the rest to read-only outputs carrying `alias_of`.
The wire behaviour is documented in [API.md](API.md#aliased-controls-one-settable-handle-per-knob).

It has to, because every evaluate applies the whole baseline so a request is history-independent.
Two handles on one control would both be written inside one `set()`, the last one applied would
win, and setting the other would be a silent no-op that still echoed back as `source: "request"`.

A group is detected when two writable inputs share an id prefix (everything before the final
`:`-delimited segment), a unit and a default value. Two handles on one attribute read back the same
value at startup, so knobs on the same device with different defaults are left alone. The survivor
is chosen by `introspect.ALIAS_PREFERENCE`, then by preferring a model-declared range over a
derived one, then by id. Every group is logged at WARNING during introspection.

If your model publishes no aliases, none of this applies and nothing is demoted: a group of one
passes straight through, and an id with no `:` in it is never grouped at all.

> **Revisit this when a second simulator backend arrives.** Alias detection is inferred from
> published metadata rather than declared by the model, and it has only been exercised against Bmad
> models through virtual-accelerator, where controls are EPICS PV names in a `DEVICE:AREA:UNIT:ATTR`
> hierarchy. Two things need rechecking for a Cheetah, Impact or surrogate backend that names
> controls differently. First, whether an id prefix plus unit plus default still identifies an
> alias, since a backend using flat names like `k1` and `q2` gives every input an empty prefix and
> leaves only the unit and default to separate them (which is why an id with no `:` is currently
> skipped outright). Second, whether `ALIAS_PREFERENCE` should give way to something the model
> declares, since comparing each input's own variable class is the rigorous test that this
> approximates: virtual-accelerator maps `BCTRL` and `BDES` to one class, and that is the fact the
> heuristic is standing in for. See `REVISIT_ALIASES` in `lume_model_api/model/introspect.py`.

### Read-only variables become outputs, by kind

A variable with `read_only=True` becomes an output. Its `kind` is decided by its class, and
that same `kind` is what the evaluate payload uses, so the kind a caller reads from the config
endpoint is the kind it gets back.

| Variable class | `kind` | Payload |
| --- | --- | --- |
| `ScalarVariable`, `IntVariable` | `scalar` | `{value, unit}` |
| `NDVariable` | `array` | `{shape, dtype, data_b64, unit}` |
| `ParticleGroupVariable` | `particles` | `{n, units, coords, stats, stats_units}` |
| anything else (`BoolVariable`, `StrVariable`, `EnumVariable`) | `value` | `{value}` |

`shape` on an `NDVariable` and `element_name`, when the variable class has one, are published in
the config too.

A `particles` output must actually yield coordinates. If **none** of `x`, `px`, `y`, `py`, `z`,
`pz` or `weight` can be read from the object the model returned, the evaluate raises instead of
shipping a valid-looking beam with `n: 0`. A genuinely empty `ParticleGroup` still yields
zero-length arrays, so the real-but-empty case stays distinguishable from a variable that is
`None` or of an unexpected type.

### `{ele}_beam` ParticleGroupVariables become screens

Every read-only `ParticleGroupVariable` becomes a screen, with one exception: a variable named
`output_beam` is treated as the model's plain final output rather than a diagnostic. A screen is
a place you can point a camera at, so "the beam at the end" is not one.

The screen `key` is the variable name with a trailing `_beam` stripped, so `OTR_B_beam` gives
key `OTR_B`. A variable that does not end in `_beam` becomes a screen keyed by its full name.

Two variables can therefore collide on one key, for example `OTR_B_beam` and `OTR_B`. Only the
first is reachable, and the second is logged as a WARNING naming both ids and the key it wants.
That is a warning rather than a startup failure so the pod still serves the screens that do work.
The fix is to rename one of the variables upstream in the model.

### An NDVariable named `{...}:Image:ArrayData` with a matching `element_name` becomes that screen's image

A screen gets an `image` when there is a read-only `NDVariable` whose `element_name` equals the
screen key **and** whose name ends in `:Image:ArrayData`. Both conditions are required. If
either is missing the screen reports `image: null`, which is a degraded result rather than a
failure: the screen's particles still work, and `POST /api/v1/models/{name}/evaluate` with that
`screen` just returns one fewer output.

`element_name` is not a field on `lume.variables.NDVariable` itself. Current
virtual-accelerator image variables carry it on a subclass, and the demo model does the same
(`ElementNDVariable` in `lume_model_api/model/demo.py`). If you host a model whose pinned
lume-base or virtual-accelerator predates that attribute, expect `image: null` everywhere.

## Three ways to run a model

`LUME_MODELS` names every model the process hosts, keyed by the name each one answers on in the
URL. It is the only model-specific setting. The three forms below cover a shortcut, a full factory
path and a new shortcut.

### 1. A shortcut name

```bash
LUME_MODELS=cu_hxr_staged LCLS_LATTICE=$HOME/SLAC/lcls-lattice \
  uvicorn lume_model_api.api.main:app --port 8000
```

That serves the model at `/api/v1/models/cu_hxr_staged/`. The shortcut table lives in
`lume_model_api/model/loader.py` and is the only model-aware code in the package. Each entry maps
a name to a factory path plus default kwargs. `README.md` lists the current names.

To override a shortcut's kwargs, use the JSON object form. A key with no `factory` must be a
shortcut name, and the `kwargs` you give are merged **over** that shortcut's defaults, so a
shortcut never blocks an override:

```bash
LUME_MODELS='{"cu_hxr_staged": {"kwargs": {"end_element": "OTR3"}}}' ...
# builds get_cu_hxr_staged_model(n_particles=1000, end_element="OTR3")
```

### 2. A dotted factory path, with kwargs

Any factory reachable on `sys.path` works with no code change at all. In the JSON form, give the
URL name as the key and the reference as `factory`:

```bash
LUME_MODELS='{"cu_hxr": {"factory": "virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model",
                         "kwargs": {"n_particles": 2000, "end_element": "OTR3"}}}' \
LCLS_LATTICE=$HOME/SLAC/lcls-lattice \
  uvicorn lume_model_api.api.main:app --port 8000
```

The reference is `module.path:factory_function`, and the factory is called with `**kwargs`. An
explicit `factory` means the kwargs are **exactly** what you gave, with no shortcut defaults
merged in, even when the key happens to match a shortcut name. That way a key can shadow a
shortcut without silently inheriting its kwargs.

Without kwargs, the comma form is shorter. Each item is a shortcut name or `name=module:function`:

```bash
LUME_MODELS=demo,cu_hxr=virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model \
  uvicorn lume_model_api.api.main:app --port 8000
```

A bare `module:function` with no `name=` is an error, because a `:` cannot go in a URL path
segment. **You always choose the URL name.** Nothing derives one from a factory function's name.
A name that is neither a known shortcut nor carries a `factory` fails at startup with a message
listing the known shortcuts. Resolution happens in the main process and is deliberately
import-free, so a typo fails fast with one clear error instead of K identical worker tracebacks.

### 3. Add a shortcut

Worth doing for a model that gets deployed, so the Deployment env and the docs can name it. Add
one entry to `SHORTCUTS` in `lume_model_api/model/loader.py`:

```python
SHORTCUTS: dict[str, tuple[str, dict]] = {
    ...
    "my_line": ("my_package.models:make_my_line_model", {"n_particles": 1000}),
}
```

That is the whole change. Nothing else in the package needs to know the name exists.

## Hosting several models

One process hosts every model in `LUME_MODELS`, and the URL name is what tells them apart. The
list at `GET /api/v1/models` is what a client reads to populate a model dropdown, and each
model's own inputs, outputs, screens and live stream sit under `/api/v1/models/{name}/`.

```bash
LUME_MODELS='{
  "cu_hxr_staged": {"kwargs": {"end_element": "OTR3"}, "workers": 2, "max_inflight": 8},
  "facet_staged":  {"workers": 1},
  "my_line":       {"factory": "my_package.models:make_my_line_model", "kwargs": {"gain": 2.0}}
}' uvicorn lume_model_api.api.main:app --port 8000
```

At startup the service logs one line per model, which is the quickest way to confirm what a pod
resolved:

```
hosting cu_hxr_staged -> virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model workers=2 at /api/v1/models/cu_hxr_staged
```

### The rules in full

1. A value starting with `{` is parsed as a JSON object. Each key is a URL name and each value is
   an object with optional `factory`, `kwargs`, `workers` and `max_inflight`. A parse error fails
   startup with the offending value quoted, which matters because a YAML value copied out of a
   shell example can carry literal quotes.
2. A key with **no** `factory` must be a shortcut name. The shortcut's factory and default kwargs
   are used, with your `kwargs` merged over them.
3. A key **with** `factory` gets exactly the kwargs you gave, and no shortcut defaults.
4. `workers` defaults to `LUME_POOL_WORKERS` (default 4). `max_inflight` defaults to
   `LUME_MAX_INFLIGHT` when that is set, else to `4 * workers`. Both are **per model, not a pod
   budget**, so three models at the defaults is twelve worker subprocesses. Sizing a pod for that
   is in [DEPLOY.md](DEPLOY.md#sizing-a-pod).
5. A non-JSON value is a comma list. Each item is a shortcut name or `name=module:function`. A
   bare `module:function` is an error, and so is a duplicate name.
6. An **unset** `LUME_MODELS` hosts `demo`, which is the local-dev default. `LUME_MODELS=""` also
   hosts `demo` but logs a WARNING, because an empty value in a k8s env block is a pod that looks
   configured and is not.
7. `LUME_MODEL` and `LUME_MODEL_KWARGS` are **retired**. Setting either is a hard startup error
   naming the variable and printing the `LUME_MODELS` equivalent. The pre-split monolith read
   them, so an operator porting a manifest across would otherwise silently serve the default model
   instead of the one they configured.
8. A URL name must match `^[A-Za-z0-9][A-Za-z0-9_-]*$`. No dots, so there is no path-segment
   normalisation surprise, and no characters that need escaping in a URL.

### What a second model costs

`workers` and `max_inflight` being per model is the number that surprises people. Two
consequences, both of which are deployment decisions rather than config ones:

- Model memory is roughly 2 GB per real worker, summed across the pod's models. See
  [DEPLOY.md](DEPLOY.md#sizing-a-pod) for the arithmetic and when to split models across pods.
- Startup is the **sum** of the models' build times, not the maximum, because pools are warmed one
  model at a time. Two real models at 25 to 30 seconds each is about a minute before the pod is
  ready, and the `startupProbe` budget has to cover it. See
  [ARCHITECTURE.md](ARCHITECTURE.md#several-models-in-one-process) for why the warmup is sequential.

`max_inflight` being per model means each model has its own 503 threshold and a saturated model does
not reject another model's evaluates. What they do share is the pod's CPU, so a hot model can still
slow a quiet one down.

If any model fails to build or warm, every pool already built is shut down before the error
propagates, so a bad entry does not leave orphaned worker processes behind.

Live input providers are per model, built from each model's own `ModelInfo`, but
`LUME_LIVE_SOURCE` and `LUME_LIVE_INPUTS` are process-wide, so every hosted model reads its live
values from the same source.

## A worked example: a minimal LUMEModel

`lume_model_api/model/demo.py` is the template to copy. It is deliberately small, depends only
on numpy and `lume-base`, and exercises every branch of the generic code (a model-declared
range, a derived range, a constant, two screens with one image between them, non-image arrays
and a plain scalar).

A stripped-down version, enough to light up inputs, one scalar output and one array output:

```python
# my_package/models.py
import numpy as np
from lume.model import LUMEModel
from lume.variables import NDVariable, ScalarVariable

PROFILE_POINTS = 128


class MyLine(LUMEModel):
    """One-sentence description. This line becomes the config's `description`."""

    def __init__(self, gain: float = 1.0) -> None:
        self.gain = gain
        self._variables = {
            # Writable scalar with a model-declared range: range_source == "model".
            "MY:QUAD:1:BCTRL": ScalarVariable(
                name="MY:QUAD:1:BCTRL", default_value=1.0, value_range=(-4.0, 4.0), unit="kG"
            ),
            # No value_range, so the service derives one: range_source == "derived".
            "MY:XCOR:1:BCTRL": ScalarVariable(
                name="MY:XCOR:1:BCTRL", default_value=0.0, unit="kG-m"
            ),
            # Read-only, so these are outputs rather than inputs.
            "MY:SCREEN:XRMS": ScalarVariable(
                name="MY:SCREEN:XRMS", read_only=True, unit="m"
            ),
            "MY:SCREEN:PROFILE": NDVariable(
                name="MY:SCREEN:PROFILE", read_only=True, shape=(PROFILE_POINTS,), unit="counts"
            ),
        }
        self._controls: dict[str, float] = {}
        self.reset()

    @property
    def supported_variables(self) -> dict:
        return self._variables

    def reset(self) -> None:
        self._controls = {
            name: float(variable.default_value)
            for name, variable in self._variables.items()
            if not variable.read_only
        }
        self._run()

    def _set(self, values: dict) -> None:
        self._controls.update({name: float(value) for name, value in values.items()})
        self._run()

    def _get(self, names: list[str]) -> dict:
        return {name: self._state[name] for name in names}

    def _run(self) -> None:
        """Whatever your simulation is. Called on every set, results cached in _state."""
        sigma = 1e-4 * self.gain * (1.0 + 0.1 * abs(self._controls["MY:QUAD:1:BCTRL"]))
        centre = 40.0 * self._controls["MY:XCOR:1:BCTRL"]
        axis = np.arange(PROFILE_POINTS, dtype=float) - PROFILE_POINTS / 2
        self._state = {
            "MY:SCREEN:XRMS": float(sigma),
            "MY:SCREEN:PROFILE": np.exp(-0.5 * ((axis - centre) / 8.0) ** 2),
        }


def make_my_line_model(gain: float = 1.0) -> MyLine:
    """The factory a LUME_MODELS entry points at."""
    return MyLine(gain=gain)
```

The `_set` then `_get` split matters: `set()` is where the simulation runs and caches, and
`_get` only reads that cache. This service always calls `set()` before `get()` in a single
evaluate, so a model that runs lazily inside `_get` also works, just with different timing.

Run it and call it:

```bash
LUME_MODELS='{"my_line": {"factory": "my_package.models:make_my_line_model",
                          "kwargs": {"gain": 2.0}}}' \
LUME_LIVE_SOURCE=synthetic \
  uvicorn lume_model_api.api.main:app --port 8000

curl -s localhost:8000/api/v1/models | python -m json.tool
curl -s localhost:8000/api/v1/models/my_line/config | python -m json.tool
curl -s -X POST localhost:8000/api/v1/models/my_line/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"inputs": {"MY:XCOR:1:BCTRL": 0.5}, "outputs": ["MY:SCREEN:XRMS", "MY:SCREEN:PROFILE"]}'
```

The config will show two inputs, one with `range_source: "model"` and one with
`"derived"`, two outputs of kinds `scalar` and `array`, and an empty `screens` list, because
this model declares no `ParticleGroupVariable`. Add one named `MY_SCREEN_beam` and a screen
appears with key `MY_SCREEN`. See `demo.py` for how to build a `ParticleGroup` and a paired
image.

To add screens, follow the demo model: a read-only `ParticleGroupVariable` named `{key}_beam`,
and optionally an `NDVariable` subclass carrying `element_name={key}` whose name ends in
`:Image:ArrayData`.

## Getting the model's dependencies into the image

`pyproject.toml` covers only this service's own dependencies (fastapi, uvicorn, sse-starlette,
pydantic, prometheus-client, numpy, scipy, lume-base). A hosted model's stack is deliberately
absent, because none of it is on PyPI at the revisions a real model needs. It is pinned in the
`Dockerfile` and in `scripts/setup-dev-env.sh` instead.

If your model is a plain pip-installable package, add it next to the existing install step in
the `Dockerfile`:

```dockerfile
ARG MY_MODEL_REF=<commit sha>
RUN git clone https://github.com/my-org/my-package.git /opt/my-package \
    && cd /opt/my-package && git checkout ${MY_MODEL_REF} \
    && python -m pip install -e .
```

Keep three things in mind. Pin a commit SHA, not a branch, so a rebuild is reproducible. Put
the install **before** the `COPY pyproject.toml` / `pip install -e .` lines only if it does not
pull a conflicting fastapi or pydantic, since the last install wins. And if the model needs a
lattice or data directory, clone it the way the existing `LCLS_LATTICE` and `FACET2_LATTICE`
steps do and set the env var the model reads in the image `ENV` block.

Models that need Bmad or pytao already have what they need: the image installs `bmad` and
`pytao` from conda-forge and patches `libtao.so`. Models that need torch get the CPU wheel.

Then set the default model the image serves, which the k8s Deployments override per workload. Use
the comma form here, so the `ENV` line needs no quote handling:

```dockerfile
ENV LUME_MODELS=my_line
```

## Smoke-testing a model

The cheapest real check is to build the model in a worker and describe it, exactly as a pod
does at startup:

```bash
python -c "
from lume_model_api.model.introspect import describe
from lume_model_api.model.loader import build_model, resolve

path, kwargs, _ = resolve('my_line')
info = describe(build_model(path, kwargs), name='my_line')
print(info.model, len(info.inputs), 'inputs', len(info.outputs), 'outputs')
print([item.key for item in info.screens])
print([(item.id, item.range_source, item.constant) for item in info.inputs])
"
```

In a container, the same thing with the model's env in place:

```bash
docker run --rm -e LUME_MODELS=my_line --entrypoint python <image> -c "
from lume_model_api.model.introspect import describe
from lume_model_api.model.loader import build_model, resolve
path, kwargs, _ = resolve('my_line')
info = describe(build_model(path, kwargs), name='my_line')
print(info.model, [item.key for item in info.screens])
"
```

If that prints, the model imports, builds and introspects, which is everything the service does
before serving its first request. `resolve` and `build_model` are the same functions
`models_from_env()` feeds, so this checks the real path. Watch the log while it runs: the INFO line
about dropped writable non-scalars and any screen-key collision warning both appear here. Then
start the app and curl `/api/v1/models`, `/api/v1/models/my_line/config` and one evaluate per
screen, following [API.md](API.md) literally.

## What your factory must tolerate

Each of the K workers in a model's pool calls your factory independently, in its own process, at
roughly the same moment. [ARCHITECTURE.md](ARCHITECTURE.md#the-process-pool) explains why the pool
is processes rather than threads. What that costs a model author:

**A factory must be safe to call K times in K processes.** One that writes to a shared fixed path,
grabs a fixed port, or assumes it is the only instance on the machine will fail intermittently
under `LUME_POOL_WORKERS > 1`. Read-only shared input files are fine.

**A worker's working directory is a fresh temp directory,** so K workers writing files cannot
collide. Any path your model resolves relative to the current directory will point somewhere
useless inside a worker while working fine in a test run from the repo root. Use absolute paths,
paths from an env var, or paths derived from `__file__`.

**HDF5 file locking is disabled in workers,** because K workers opening one read-only design-beam
file hit HDF5's default lock and get `[Errno 11]`. If your model writes HDF5, write it under the
worker's own cwd.

**Nothing your model imports may be imported at this package's module top.** No module in
`lume_model_api` imports a model, torch, pytao or pyepics at import time, which is what lets
`pytest` and `python scripts/dump_openapi.py` run in a plain venv and what keeps the web process
free of torch. Your model is imported dynamically by `loader.build_model` inside a worker.

**Lattice and data locations belong to the model.** This service sets no lattice env var and
guesses no path. Set `LCLS_LATTICE`, `FACET2_LATTICE` or whatever your model reads, in the shell,
the Dockerfile `ENV` block or the Deployment. virtual-accelerator raises a clear error when one is
missing, which is a better failure than a wrong default.

**One evaluate that never returns can cost the pod.** A subprocess evaluate cannot be cancelled,
so if `LUME_EVALUATE_TIMEOUT_S` is set and your model exceeds it, the pool is marked dead and the
pod restarts. A model that occasionally takes minutes needs either a timeout well above that or
none at all.

## Pushing metadata upstream

When an input shows up with `range_source: "derived"`, the right fix is a `value_range` on that
variable **in the model**, not an override in this repo. The derived-range heuristic exists
because some virtual-accelerator controls carry only `unit` and `read_only`, and a service that
knows nothing about the machine has to put *something* on a slider. It is a stopgap.

The same goes for a missing `unit`, a missing `default_value`, a screen with no image because
its variable lacks `element_name`, a screen-key collision, and a knob whose real limits are tighter
than the derived ones. All of those are model metadata. Fixing them upstream fixes them for every
consumer of that model, and it keeps this repo free of the per-model tables it was rewritten to
delete.

Deliberately not offered here: an env var or config file of per-input range overrides. That is
how the previous version of this service ended up with a hand-typed copy of one model's PV
table, which then drifted from the model.
