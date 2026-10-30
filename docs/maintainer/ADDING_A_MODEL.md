# Adding a model

The service hosts any `LUMEModel` from [lume-base](https://github.com/slaclab/lume-base). Nothing
is registered and no schema is written by hand. At startup each worker builds the model, reads
its `supported_variables`, and the API's inputs, outputs and screens all come from that.

There are two cases. A model that already lives in
[virtual-accelerator](https://github.com/slaclab/virtual-accelerator) needs only an environment
variable. A `LUMEModel` from any other package also needs that package installed in the image.

## Host a virtual-accelerator model

1. **Pick the model.** These shortcuts are defined in `SHORTCUTS` in
   `lume_model_api/model/loader.py`:

   | Shortcut | Factory | Default kwargs |
   | --- | --- | --- |
   | `demo` | `lume_model_api.model.demo:make_demo_model` | none |
   | `cu_hxr_staged` | `virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model` | `n_particles=1000`, `end_element="TD11"` |
   | `cu_hxr_bmad` | `virtual_accelerator.models.cu_hxr:get_cu_hxr_bmad_model` | `track_beam=True`, `end_element="TD11"` |
   | `facet_staged` | `virtual_accelerator.models.facet2:get_facet_staged_model` | none |
   | `facet_bmad` | `virtual_accelerator.models.facet2:get_facet_bmad_model` | none |

   Any other virtual-accelerator factory works too, by its full path (see step 2).

2. **Set `LUME_MODELS`.** The shortest form is a shortcut name:

   ```bash
   LUME_MODELS=cu_hxr_staged
   ```

   To change kwargs, use the JSON form. With no `factory`, the key has to be a shortcut, and the
   kwargs given are merged over the shortcut's defaults:

   ```bash
   LUME_MODELS='{"cu_hxr_staged": {"kwargs": {"end_element": "OTR3"}}}'
   # builds get_cu_hxr_staged_model(n_particles=1000, end_element="OTR3")
   ```

   For a factory with no shortcut, give it a URL name and a `factory`. Shortcut defaults are not
   applied when `factory` is set, even if the key matches a shortcut:

   ```bash
   LUME_MODELS='{"cu_hxr_otr3": {"factory": "virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model",
                                 "kwargs": {"n_particles": 2000, "end_element": "OTR3"}}}'
   ```

3. **Point the model at its lattice.** virtual-accelerator reads `LCLS_LATTICE` (LCLS models) or
   `FACET2_LATTICE` (FACET-II models), and raises if it's missing. The image already sets both.
   For a local run, clone [lcls-lattice](https://github.com/slaclab/lcls-lattice) or
   [facet2-lattice](https://github.com/slaclab/facet2-lattice) and export the path.

4. **Run it.** A local install needs Bmad and pytao from conda-forge plus the `va` extra. See
   [Local install for a VA model](../../README.md#local-install-for-a-va-model) in the README.

   ```bash
   LUME_MODELS=cu_hxr_staged LUME_LIVE_SOURCE=synthetic LCLS_LATTICE=$HOME/lcls-lattice \
     uvicorn lume_model_api.api.main:app --port 8000
   ```

   `cu_hxr_staged` takes 25 to 30 seconds per worker to build. `facet_staged` at its default
   10000 particles takes several minutes, so lower `n_particles` or raise the startup probe
   budget before deploying it. The log prints one line per hosted model once it has resolved
   `LUME_MODELS`:

   ```
   hosting cu_hxr_staged -> virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model workers=4 at /api/v1/models/cu_hxr_staged
   ```

5. **Check it.** Follow [Smoke test](#smoke-test).

To make a new model available by name in deployments, add a line to `SHORTCUTS`. Nothing else in
the package needs to know about it.

```python
SHORTCUTS: dict[str, tuple[str, dict]] = {
    ...
    "cu_hxr_otr3": (
        "virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model",
        {"n_particles": 1000, "end_element": "OTR3"},
    ),
}
```

## Host another LUMEModel

A model from another package works the same way once that package is importable in the worker.
The service calls a factory function, so the package has to expose one that returns the model
instance.

1. **Check the model's variables.** The API is built entirely from `supported_variables`, so a
   model that runs fine in a notebook can still publish nothing useful. Read
   [How variables map to the API](#how-variables-map-to-the-api) first. The short version:

   - knobs are writable `ScalarVariable`s with a `default_value`, a `value_range` and a `unit`
   - results are variables with `read_only=True`
   - a screen is a read-only `ParticleGroupVariable` named `<key>_beam`

2. **Expose a factory.** Any `module.path:function` that returns the model works. It's called
   with the kwargs from `LUME_MODELS`.

   ```python
   # my_package/models.py
   def make_my_line_model(gain: float = 1.0) -> MyLine:
       return MyLine(gain=gain)
   ```

   Each worker calls the factory in its own process, so the factory has to be safe to run
   several times at once. See [What a factory has to tolerate](#what-a-factory-has-to-tolerate).

3. **Install it locally and run it.**

   ```bash
   pip install -e ../my-package
   LUME_MODELS='{"my_line": {"factory": "my_package.models:make_my_line_model", "kwargs": {"gain": 2.0}}}' \
   LUME_LIVE_SOURCE=synthetic \
     uvicorn lume_model_api.api.main:app --port 8000
   ```

4. **Run the [smoke test](#smoke-test).**

5. **Add it to the image.** Install the package in the `Dockerfile`, after the step that installs
   the `pyproject.toml` requirements and before `COPY lume_model_api/`, pinned to a commit:

   ```dockerfile
   ARG MY_PACKAGE_REF=<commit sha>
   RUN python -m pip install "my-package @ git+https://github.com/my-org/my-package@${MY_PACKAGE_REF}"
   ```

   Check that it doesn't pull in a different fastapi or pydantic, since the last install wins. If
   the model needs data files, clone them the way the lattices are cloned and set the env var the
   model reads in the `ENV` block. Bmad, pytao and CPU torch are already in the image.

6. **Deploy it.** Set `LUME_MODELS` in `deploy/kubernetes/deployment.yaml` and
   `deployment-live.yaml`, and check pod memory against
   [Sizing](DEPLOY.md#sizing). Optionally add a shortcut to `SHORTCUTS`.

### A minimal model

This is enough to publish two inputs, a scalar output and an array output. `lume_model_api/model/demo.py`
is a fuller example with screens, particles, images and a constant input.

```python
# my_package/models.py
import numpy as np
from lume.model import LUMEModel
from lume.variables import NDVariable, ScalarVariable

PROFILE_POINTS = 128


class MyLine(LUMEModel):
    """A two-magnet line with one profile monitor."""  # becomes the config's `description`

    def __init__(self, gain: float = 1.0) -> None:
        self.gain = gain
        self._variables = {
            # Writable, with a declared range: range_source is "model".
            "MY:QUAD:1:BCTRL": ScalarVariable(
                name="MY:QUAD:1:BCTRL", default_value=1.0, value_range=(-4.0, 4.0), unit="kG"
            ),
            # Writable, no range: the service derives one and sets range_source to "derived".
            "MY:XCOR:1:BCTRL": ScalarVariable(
                name="MY:XCOR:1:BCTRL", default_value=0.0, unit="kG-m"
            ),
            # Read-only, so these are outputs.
            "MY:SCREEN:XRMS": ScalarVariable(name="MY:SCREEN:XRMS", read_only=True, unit="m"),
            "MY:SCREEN:PROFILE": NDVariable(
                name="MY:SCREEN:PROFILE", read_only=True, shape=(PROFILE_POINTS,), unit="counts"
            ),
        }
        self.reset()

    @property
    def supported_variables(self) -> dict:
        return self._variables

    def reset(self) -> None:
        self._controls = {
            name: float(var.default_value)
            for name, var in self._variables.items()
            if not var.read_only
        }
        self._run()

    def _set(self, values: dict) -> None:
        self._controls.update({name: float(value) for name, value in values.items()})
        self._run()

    def _get(self, names: list[str]) -> dict:
        return {name: self._state[name] for name in names}

    def _run(self) -> None:
        sigma = 1e-4 * self.gain * (1.0 + 0.1 * abs(self._controls["MY:QUAD:1:BCTRL"]))
        centre = 40.0 * self._controls["MY:XCOR:1:BCTRL"]
        axis = np.arange(PROFILE_POINTS, dtype=float) - PROFILE_POINTS / 2
        self._state = {
            "MY:SCREEN:XRMS": float(sigma),
            "MY:SCREEN:PROFILE": np.exp(-0.5 * ((axis - centre) / 8.0) ** 2),
        }


def make_my_line_model(gain: float = 1.0) -> MyLine:
    return MyLine(gain=gain)
```

`LUMEModel` requires `supported_variables`, `_get`, `_set` and `reset`. The public `set()` and
`get()` validate against `supported_variables` and then call `_set` and `_get`. The service calls
`set()` then `get()` on every evaluate, so the simulation can run in either one. This service
never calls `reset()` itself.

The config for this model has `MY:QUAD:1:BCTRL` with range -4 to 4 (`"model"`),
`MY:XCOR:1:BCTRL` with range -0.5 to 0.5 (`"derived"`), two outputs and no screens.

To add a screen, add a read-only `ParticleGroupVariable` named `MY_SCREEN_beam`. It shows up as
screen `MY_SCREEN`. For an image on that screen, add a read-only `NDVariable` subclass with an
`element_name` field set to `"MY_SCREEN"`, and a name ending in `:Image:ArrayData`. The demo
model's `ElementNDVariable` shows how.

## Host several models

One pod can host several models. Each gets its own worker pool and its own URL name.

```bash
LUME_MODELS='{
  "cu_hxr_staged": {"kwargs": {"end_element": "OTR3"}, "workers": 2, "max_inflight": 8},
  "facet_staged":  {"workers": 1},
  "my_line":       {"factory": "my_package.models:make_my_line_model"}
}'
```

Without kwargs or pool settings, a comma list is shorter:

```bash
LUME_MODELS=demo,my_line=my_package.models:make_my_line_model
```

Things to plan for:

- **Memory adds up.** `workers` is per model, not per pod. Three models at the default of 4 workers
  is 12 model processes, at roughly 2 GB each for a real model. See [Sizing](DEPLOY.md#sizing).
- **Startup adds up.** Models are warmed one at a time, so a pod with two real models takes about a
  minute to become ready. The `startupProbe` has to allow for it.
- **Busy limits are separate, CPU isn't.** Each model has its own `max_inflight`, so a busy model
  doesn't cause 503s on another one. They do share the pod's CPU.
- **Live input settings are shared.** `LUME_LIVE_SOURCE` and `LUME_LIVE_INPUTS` apply to every
  model in the pod.

If any model fails to build, the pools already built are shut down and the pod exits.

## Smoke test

Build the model and describe it, the same way a worker does at startup:

```bash
python -c "
from lume_model_api.model.introspect import describe
from lume_model_api.model.loader import build_model, resolve

path, kwargs, _ = resolve('my_package.models:make_my_line_model')
info = describe(build_model(path, kwargs), name='my_line')
print(len(info.inputs), 'inputs,', len(info.outputs), 'outputs')
print('screens:', [s.key for s in info.screens])
print('derived ranges:', [i.id for i in info.inputs if i.range_source == 'derived'])
print('aliases:', [o.id for o in info.outputs if o.alias_of])
"
```

`resolve` accepts a shortcut name or a `module:function` path. Watch the log while it runs. It
reports writable variables that were skipped, screen-key collisions and alias groups.

Then start the service and call the API:

```bash
curl -s localhost:8000/api/v1/models
curl -s localhost:8000/api/v1/models/my_line/config
curl -s -X POST localhost:8000/api/v1/models/my_line/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"inputs": {"MY:XCOR:1:BCTRL": 0.5}, "outputs": ["MY:SCREEN:XRMS", "MY:SCREEN:PROFILE"]}'
```

For a model with screens, run one evaluate per screen with `{"screen": "<key>"}`.

In the image, the same Python check runs with
`docker run --rm --entrypoint python <image> -c "..."`.

## How variables map to the API

`lume_model_api/model/introspect.py` reads `supported_variables` once per worker. Each variable
lands in one place:

| Variable | Published as |
| --- | --- |
| `read_only=False`, `ScalarVariable` or `IntVariable` | Input |
| `read_only=False`, any other class | Nothing. Logged at INFO with its id and kind. |
| `read_only=True`, `ScalarVariable` or `IntVariable` | Output, kind `scalar` |
| `read_only=True`, `NDVariable` | Output, kind `array` |
| `read_only=True`, `ParticleGroupVariable` | Output, kind `particles`, and a screen unless it's named `output_beam` |
| `read_only=True`, anything else (`BoolVariable`, `StrVariable`, `EnumVariable`, ...) | Output, kind `value` |

### Inputs

For each input, three fields on the variable matter:

- `default_value` is the design value and goes into the baseline. Without it, the service reads
  the current value with `model.get()` at startup. If that also fails, the input is skipped with a
  warning.
- `value_range` sets `min` and `max`, with `range_source: "model"`. Without it, the range is the
  default ± `LUME_DERIVED_RANGE_FRACTION` (0.5) times its absolute value, or ± 0.5 when the
  default is 0, with `range_source: "derived"`.
- `unit` is passed through unchanged.

A `value_range` with equal bounds marks the input `constant: true`. Constants are listed in the
config but are never set, never part of the baseline and never read from the machine.

### Aliased inputs

Some models publish two writable ids for one control. virtual-accelerator does this for every
magnet, with `BCTRL` and `BDES`. Every evaluate writes the whole baseline, so with both published
the one written last would win and setting the other would silently do nothing.

The service keeps one id per control as the input. The rest become read-only outputs with
`alias_of` naming the input. Two inputs are treated as aliases when they share everything before
the last `:` in their id, the same unit and the same default. The input kept is chosen by
`introspect.ALIAS_PREFERENCE` (`BCTRL`, then `BDES`), then by having a model-declared range, then
by id. Each group is logged at WARNING. Ids with no `:` are never grouped.

This has only been tried on Bmad models through virtual-accelerator. A backend with a different
naming scheme may need a different rule. See `REVISIT_ALIASES` in `introspect.py`. One known gap:
the FACET-II models publish a quad with an empty device name (`:BCTRL` and `:BDES`), which has no
prefix, so both stay inputs.

### Screens

Each read-only `ParticleGroupVariable` except `output_beam` becomes a screen. The key is the
variable name with a trailing `_beam` removed, so `OTR_B_beam` becomes `OTR_B`. A name without
`_beam` is used as is. If two variables produce the same key, only the first becomes a screen and
the second is logged at WARNING.

A screen gets an `image` when a read-only `NDVariable` has `element_name` equal to the screen key
and a name ending in `:Image:ArrayData`. Otherwise `image` is null and the screen still returns
particles. `element_name` isn't a field on lume's `NDVariable`, so the model needs a subclass that
adds it.

A `particles` output has to yield at least one of `x`, `px`, `y`, `py`, `z`, `pz` or `weight`, or
the evaluate fails. An empty `ParticleGroup` is fine and returns `n: 0`.

### The read_only trap

lume's `ReadOnlyActionMixin` enforces `read_only` with a pydantic validator, and lume's
`Variable` doesn't validate defaults. A variable that mixes in `ReadOnlyActionMixin` without
passing `read_only=True` therefore arrives writable. If it's a `ParticleGroupVariable`, the
service drops it as a writable non-scalar and the screen disappears. Pass `read_only=True`
explicitly.

### What a factory has to tolerate

- **Several copies at once.** Each worker calls the factory in its own process at about the same
  time. Writing to a fixed path or binding a fixed port fails intermittently once
  `LUME_POOL_WORKERS` is above 1. Reading shared files is fine.
- **A temporary working directory.** Each worker starts in a fresh temp directory, so relative
  paths resolve somewhere unexpected. Use absolute paths, env vars, or paths built from
  `__file__`.
- **No HDF5 file locking.** Workers set `HDF5_USE_FILE_LOCKING=FALSE` so they can open the same
  read-only file. A model that writes HDF5 should write under its working directory.
- **One thread.** BLAS, OpenMP and torch are pinned to `LUME_WORKER_THREADS` threads (default 1)
  per worker.
- **No cancellation.** An evaluate running in a worker can't be stopped. If
  `LUME_EVALUATE_TIMEOUT_S` is set and an evaluate exceeds it, the pool is marked dead and the pod
  restarts. It's unset by default.
- **A failed `set()` ends the worker.** If `set()` raises after lume's own validation passed, the
  model is treated as broken and the pod restarts. A model can define a `recover()` method to
  repair itself instead. See [When a worker dies](ARCHITECTURE.md#when-a-worker-dies).

## LUME_MODELS reference

The parser is `models_from_env` in `lume_model_api/model/loader.py`.

- A value starting with `{` is a JSON object. Each key is a URL name and each value is an object
  with optional `factory`, `kwargs`, `workers` and `max_inflight`. Invalid JSON fails startup and
  the error quotes the value, which helps spot stray quotes copied from a shell example.
- A key without `factory` has to be a shortcut. Its kwargs are merged over the shortcut's
  defaults.
- A key with `factory` uses exactly the kwargs given.
- Any other value is a comma list of shortcut names and `name=module:function` items. A bare
  `module:function` is an error because `:` can't go in a URL, and so is a repeated name.
- `workers` defaults to `LUME_POOL_WORKERS` (4). `max_inflight` defaults to `LUME_MAX_INFLIGHT`
  if set, otherwise 4 × `workers`. Both are per model.
- URL names match `^[A-Za-z0-9][A-Za-z0-9_-]*$`.
- Unset hosts `demo`. Set to an empty string also hosts `demo`, and logs a warning, since that's
  usually a k8s env entry with a missing value.
- `LUME_MODEL` and `LUME_MODEL_KWARGS` are retired. Setting either fails startup with the
  equivalent `LUME_MODELS` value in the message.

Names are resolved in the main process without importing anything, so a typo fails once with a
clear message instead of once per worker.

## Fixing metadata upstream

When an input shows `range_source: "derived"`, the fix is a `value_range` on the variable in the
model. The same goes for a missing unit or default, a screen without an image because the image
variable has no `element_name`, and screen-key collisions. This repo has no override files for
ranges, units or screens, on purpose. The version before it kept a hand-typed copy of one model's
PV table, and it drifted from the model. Fixing the model fixes it for everyone who uses it.
