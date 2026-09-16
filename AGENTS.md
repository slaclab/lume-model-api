# Notes for agents working on this package

`README.md` says what this service is, `docs/API.md` documents the contract and `docs/DEPLOY.md`
the deploys. This file covers the things that are easy to break and are not obvious from reading
the code, mostly cases where the obvious cleanup is wrong.

## This repo was split out of another one, recently

The code lived in `slaclab/lume-visualizations` until 2026-09-09 and still does, unchanged, in
parallel. That repo keeps the React UI that was this API's first consumer.

Consequences:

- **`webapp`, `webapp.backend` and `lume_visualizations` are dead names here.** If you find one
  in a comment or doc, it is stale text that got missed. `webapp/backend/` became
  `lume_model_api/api/` and `lume_visualizations/` became `lume_model_api/model/`.
- **History is preserved but paths are not.** `git log --follow` and `git blame` work, and
  commits before `a923530` refer to the old paths. Use `--follow` or you will think a file has
  no history.
- **Nothing here has ever been pushed or deployed.** There is no GitHub remote and no image
  exists under the `ghcr.io/slaclab/lume-model-api` name, so `deploy/kubernetes` points at a
  tag that does not resolve yet.

## The service is model-agnostic. Keep it that way

The whole point of the current design is that no PV name, screen name, unit or lattice path
appears in this package. Everything a client learns comes from the hosted model's
`supported_variables`, read once per worker by `model/introspect.py`.

So: **do not add a per-model table.** Not a screen list, not an input allowlist, not a range
override file, not a units map. The previous version of this service had all of those, hand-typed
for one model, and they drifted from the model. If a model is missing metadata, the fix is
upstream in the model. See the derived-range section below.

The only model-aware code is `SHORTCUTS` in `model/loader.py`, which maps a friendly name to a
factory path plus default kwargs. That is a convenience, not a registry: any factory works with
the full `module.path:factory_function` reference and no code change.

## Several models, addressed by name in the URL

One process hosts every model named in `LUME_MODELS`, and the URL name is what distinguishes
them. `app.state.models` is a `dict[str, HostedModel]`, and each `HostedModel` owns its own
`ModelSetting`, `ModelPool`, `ModelInfo`, `LiveHub` and live input provider.

| Route | Notes |
| --- | --- |
| `GET /api/v1/models` | `[{name, description, version}]`, sorted by name. Client discovery, not the probe target. |
| `GET /healthz` | Probe target. 503 once any pool has warmed but lost a worker. Not in `openapi.json`. |
| `GET /api/v1/models/{name}/config` | Replaced `GET /api/config`. Same body. |
| `POST /api/v1/models/{name}/evaluate` | Replaced `POST /api/v1/evaluate`. Same request and response. |
| `GET /api/v1/models/{name}/live/stream` | Replaced `GET /api/live/stream`. Same query params and events. |
| `GET /api/v1/models/{name}/machine-snapshot` | Replaced `GET /api/machine-snapshot`. Same body. |

**Do not add an unprefixed default model route.** There is one scheme and no implicit default
model. The old unprefixed paths are gone and return 404 on purpose, and `tests/test_app_demo.py`
asserts that. Reintroducing `/api/config` as an alias for "the first model" would mean a client
could work by accident on a one-model pod and break the moment a second model is added, which is
exactly the ambiguity the name-in-the-path scheme removes. An unknown name is a 404 whose detail
lists the hosted names.

`LUME_MODELS` rules, in `model/loader.py`'s `models_from_env()`:

- A value starting with `{` is a JSON object keyed by URL name, each value optionally carrying
  `factory`, `kwargs`, `workers` and `max_inflight`. A parse error raises `ModelRefError`
  quoting the value, because a YAML value copied from a shell example can carry literal quotes.
- A key with no `factory` must be a shortcut name, and the shortcut's factory and default kwargs
  are used with `kwargs` merged over them. An explicit `factory` means the kwargs are exactly
  what is given, with no shortcut defaults even when the key happens to match a shortcut name.
- `workers` defaults to `LUME_POOL_WORKERS` (default 4) and `max_inflight` to
  `LUME_MAX_INFLIGHT` if set, else `4 * workers`. **Both are per-model defaults, not a pod
  budget.** Three models at the default is twelve worker processes, so roughly 24 GB for real
  models.
- A non-JSON value is a comma list of shortcut names and `name=module:function` items. A bare
  `module:function` is an error, since it would put a `:` in a URL, and duplicate names are an
  error.
- Empty or unset falls back to `LUME_MODEL` (default `demo`) with `LUME_MODEL_KWARGS`. A full
  `module:function` there takes the function name as its URL name, and the startup log says so.
- `LUME_MODEL_KWARGS` set while `LUME_MODELS` is set is ignored with a warning naming both, so
  a half-migrated Deployment env is loud rather than silently wrong.
- A URL name must match `^[A-Za-z0-9][A-Za-z0-9_-]*$`. No dots, so there is no path-segment
  normalisation surprise.

**Warmup is sequential across models, deliberately.** `lifespan` builds every pool first, then
warms them one at a time. Warming M pools concurrently would have sum(K) model instances under
construction at the same moment, and neither the pod memory limits nor the `startupProbe`
`failureThreshold` were sized for that. Do not "speed up startup" by gathering the warmups.
Construction and warmup are wrapped so that a failure shuts down every pool already built,
because otherwise a bad model leaves orphaned spawn workers behind.

**Every metric carries a `model` label**: `lume_pool_inflight`, `lume_pool_max_inflight`,
`lume_pool_workers`, `lume_evaluate_seconds`, `lume_evaluate_total` and
`lume_pool_rejected_total`. Cardinality is M per pod, which is fine. A query that forgets to
aggregate over it is not: a plain `avg(lume_pool_inflight)` is diluted by M, which is why the
KEDA trigger uses `avg(sum by (pod) (lume_pool_inflight))`.

**`model` is always the URL name**, in `ConfigResponse`, in `EvaluateV1Response`, in the
`LiveHub(model=...)` argument and in the metrics label. Not the factory path and not the
shortcut. `version` is `demo` for the demo model under the key `demo`, `<name> (demo)` for the
demo factory hosted under any other key, and the name itself otherwise, so a client can always
tell a demo deployment from a real one.

**Live input providers are per model, but the live source is global.** Each model gets its own
`EpicsInputProvider` or `SyntheticInputProvider` built from its own `ModelInfo`, while
`LUME_LIVE_SOURCE` and `LUME_LIVE_INPUTS` stay process-wide. Two models that share PV names
therefore create duplicate `epics.PV` objects and read the same PV twice per frame. That cost is
accepted for now. Do not fix it with a shared per-PV cache keyed on name without checking that
the two models really do want the same value at the same moment.

The hub callbacks are bound with a default argument
(`lambda model=entry: _read_live_inputs(model)`). That is not stylistic. A closure
over the loop variable would give every hub the last model in the dict.

## `lume-base` is a hard dependency, not an extra

`lume-base` is in `pyproject.toml`'s required `dependencies` and must stay there. Two reasons.

The variable classes it defines (`ScalarVariable`, `NDVariable`, `ParticleGroupVariable` and
friends) are the mechanism by which this service discovers a model's inputs, outputs and screens.
`introspect.variable_kind` does `isinstance` checks against them, so without lume-base there is
no API at all, only a 500.

And the in-repo demo model subclasses `LUMEModel` directly, so `LUME_MODELS=demo`, the tests and
CI all need it. It is safe as a hard dependency because it is pure Python plus numpy, h5py and
openpmd-beamphysics, all of which are on PyPI and none of which need a compiler or an
accelerator stack.

The imports of it inside functions (`introspect.variable_kind`, `evaluate._coerce_control`,
`demo._beam`) are **not** about optionality. They are about keeping module import cheap in the
main process. Hoisting them would not break anything, but leave them where they are.

## The demo model is a real LUMEModel, so it exercises the production path

`model/demo.py` is not a mock of the API. It is a model the API hosts exactly like any other
one. `LUME_MODELS=demo` goes through the same `describe()`, the same `evaluate()`, the same
serializer and the same subprocess pool as `cu_hxr_staged`, which is what makes a green test run
mean something.

This replaced a mock that lived in the api layer and derived every screen from one knob, so
switching screens changed nothing and the bug was invisible until someone ran the real model.
Two rules follow.

**Do not add a mock mode.** There is no `LUME_MOCK`, and reintroducing one that short-circuits
the model layer would recreate exactly the trap that was removed.

**Keep the demo model exercising every branch.** It deliberately has an input with a
model-declared range, one with no range (so `range_source: "derived"` is covered), one constant,
two screens of which only one has an image, non-image arrays and a plain read-only scalar. If you
add a feature to the generic code, add whatever the demo model needs to cover it, because CI has
no other model.

Its physics is illustrative, not predictive, and deterministic given the inputs (seeded
per-screen generators). The determinism is load-bearing for the wire-shape and subsampling tests.

## Layering: `api` depends on `model`, never the reverse

`lume_model_api/model/` knows nothing about HTTP, FastAPI or Pydantic. It returns plain
dataclasses (`ModelInfo`, `EvaluateResult`). `lume_model_api/api/` turns those into wire dicts.
Keep that direction, because it is what lets the model layer be used from a notebook without
starting a web server, and it is what the model-layer tests rely on.

`api/serialize.py` is the deliberate oddity: it lives in `api/` but runs **inside** a pool
worker, so arrays cross the process boundary already base64-encoded rather than as pickled
numpy. It imports nothing from FastAPI, which is what keeps that legal.

Inside `model/` there are no import cycles now. `loader` imports nothing from the package,
`introspect` imports nothing from it either, and `evaluate` imports only `introspect`. Keep the
one-directional shape.

## Lazy imports are deliberate. Do not hoist them to module top

Several imports sit inside functions on purpose. Moving them to the top of the file is the most
likely way to break this package, and it breaks it in CI rather than at review time.

| Import | Where | Why it must stay lazy |
| --- | --- | --- |
| the hosted model (`virtual_accelerator`, ...) | `model/loader.py:92`, via `importlib.import_module` | Not on PyPI. Installed from a pinned git ref in the image only, and it pulls torch or pytao. Never imported in the main process. |
| `scipy.ndimage` | `api/serialize.py:56` | Only the opt-in `smooth_images_sigma_px` path needs it, and this module is imported by every worker on every startup. |
| `epics` (pyepics) | `model/live_inputs.py:106` | An `[epics]` extra, absent in CI. Must also be imported **after** the CA env vars are set, because channel access reads them once at import. |
| `lume.variables` | `model/introspect.py:110`, `model/evaluate.py:87` | Keeps main-process import cheap. Not optional, see the lume-base section. |
| `beamphysics.ParticleGroup` | `model/demo.py:147` | Only needed when the demo model actually runs. |
| `LiveHub` | `api/main.py:81` | Only the `live` and `all` roles need it. |
| `describe`, `build_model`, `resolve`, `result_to_wire`, `evaluate` | `api/pool.py:50,51,71,72` | Worker-side imports by absolute module path, see the pool section. |
| `tempfile` | `api/pool.py:46` | Worker-side only. |

The test that this still holds is simply that `pytest` and `python scripts/dump_openapi.py` run
in a plain venv with no torch, no pytao, no Bmad and no EPICS. If you hoist one of these, that
stops being true and CI fails on an unrelated pull request.

## The model pool is processes, not threads, and that is not negotiable

`api/pool.py` uses a `spawn` `ProcessPoolExecutor`. Three constraints drive this:

- Two model instances in one process segfault (torch double-load), and `pytao` is not
  thread-safe. So process isolation is required, not just preferred.
- `fork` with torch and OpenMP is unsafe, hence `spawn`.
- `spawn` means workers re-import the package rather than inheriting memory, which is why
  `_init_worker` and `_worker_evaluate` import by absolute module path.

**Workers `chdir` into a fresh temp directory** (`pool.py:48`), so K workers writing files cannot
collide. Anything in `model/` that resolves a path relative to the current directory will read
the wrong place inside a worker while working fine in a test. Use absolute paths or paths derived
from `__file__`.

`HDF5_USE_FILE_LOCKING=FALSE` is set before HDF5 loads because K workers may share a read-only
design-beam file and HDF5's default lock rejects the concurrent open with `[Errno 11]`.

**A model factory must be safe to call K times in K processes**, at roughly the same moment. One
that writes to a fixed path or grabs a fixed port fails intermittently under
`LUME_POOL_WORKERS > 1`.

**The main process never imports the model.** `warmup()` builds all K instances of one model in
parallel and fetches back the `ModelInfo` one worker built, which is how
`/api/v1/models/{name}/config` is served from a process with no torch, no Bmad and no lattice.
The parallelism is within one model only, since the pools are warmed one after another. See the
sequential-warmup rule above. `ModelInfo` and its members are plain dataclasses precisely so
they pickle across the spawn boundary. If you add a field to them, keep it plain: a numpy array
or a model object there breaks startup, not a request.

## Baseline merge is what makes evaluates stateless

A model instance is stateful, since `set()` mutates it. Every evaluate applies
`{**info.baseline, **requested}` restricted to non-constant inputs, so a request fully determines
the machine state. Without that, a result would depend on whatever the previous request left on
that particular worker, and two identical requests could get different answers from K workers.

Do not "optimize" this by applying only the knobs a request named. Particle subsampling is a
deterministic `linspace` stride for the same reason, so do not make it a random draw.

## Changing the response schema: three things move together

Editing `api/schemas.py` or a route means all of these, or CI fails:

1. **Regenerate `openapi.json` with the pinned versions.** `fastapi==0.141.1` and
   `pydantic==2.13.4` generate the JSON schema, and a newer pair emits harmless but different
   output. Regenerating under whatever pip resolved produces a large spurious diff, and
   committing it breaks CI, which still uses the pins.

   ```bash
   pip install fastapi==0.141.1 pydantic==2.13.4
   python scripts/dump_openapi.py
   ```

   **`LUME_ROOT_PATH` must never be set when regenerating `openapi.json`.** The Deployments set
   it to `/lume-model-api` so Swagger works under the ingress prefix, and a set `root_path` makes
   FastAPI add a `servers` entry to the schema. That would tie the committed contract to one
   deployment, and every client generated from it would prefix its requests with that path.
   `scripts/dump_openapi.py` pops the variable before importing the app for exactly this reason,
   so regenerate through the script rather than by calling `app.openapi()` yourself.
   `tests/test_api_contract.py` asserts the schema has no `servers` key.

2. **Update the expected field sets in `tests/test_api_contract.py` by hand.** They are written
   out longhand on purpose, so that a regenerated snapshot cannot hide a rename.

3. **If you added a field to `EvaluateV1Response`, add it to the dict in
   `serialize.result_to_wire` too**, or to the sender-attached set in `main.evaluate_v1` and
   `LiveHub._run`. See the next section.

**Additive only, from the move to `/api/v1/models/{name}/...` onward.** The response shape was
redesigned in place while the service still had no consumers. The v1 paths then moved once more,
to `/api/v1/models/{name}/...`, while there was still no deployed consumer and the UI port was
in progress (see `docs/MIGRATING_LUME_VISUALIZATIONS.md`). That window is now closed. Adding an
optional field is fine. Renaming or removing one, making an optional field required, or moving a
path again, needs `/api/v2`. Consumers in other repos pin a git ref, so a rename here is silent
for them until they refetch. The `BREAKING` string in `tests/test_api_contract.py` says the same
thing, and the two must agree.

## `result_to_wire` must emit every key unconditionally, and every requested output id

The HTTP endpoint has a `response_model`, so FastAPI fills in anything the serializer omits. The
SSE stream does not: `api/main.py live_stream` hands the dict straight to `json.dumps`. A key made
conditional there reaches clients genuinely absent, and because clients type the stream from
`EvaluateV1Response` they assume it is present.

The generic host adds a second half to this. `outputs` is keyed by the caller's own ids, so
"omitted" now means a client's `outputs["OTR_B_beam"]` is a `KeyError` rather than a null it can
render around. **Every requested id must be present**, always.

The trap is that neither failure changes `openapi.json`, so no consumer can detect it by
refetching the schema, and there is no type error anywhere. `tests/test_wire_shape.py` is the only
thing standing between a conditional key and a broken client, which is why it is parametrized
over both demo screens: a key conditional on image data passes on `OTR_B` and fails only on
`OTR_A`.

`model` and `version` are the exception, attached by the sender rather than the serializer,
because the SSE path bypasses the HTTP endpoint. Both senders must attach them, and
`test_sender_added_fields_are_exactly_what_live_hub_attaches` pins that.

## The derived-range heuristic is a stopgap. The fix belongs in the model

A writable scalar with no `value_range` gets `default +/- LUME_DERIVED_RANGE_FRACTION * |default|`
(or `+/- fraction` when the default is 0), flagged `range_source: "derived"` so a UI can render
it as a suggestion rather than a limit.

This exists only because some virtual-accelerator controls carry just `unit` and `read_only`, and
a service that knows nothing about the machine still has to put something on a slider. **The
right fix for a bad range is `value_range` on that variable in the model.** Same for a missing
`unit`, a missing `default_value`, and a screen reporting `image: null` because its image
variable lacks `element_name`. All of those are model metadata, and fixing them upstream fixes
them for every consumer of that model.

Do not add an override mechanism here. That is precisely how the previous version ended up with a
hand-typed copy of one model's PV table.

## Verifying that a packaging change actually works

Two failure modes here look like success. Both have bitten this repo and both are guarded in the
Dockerfile comments, but if you are changing packaging, test it properly.

**`pip install -e .` with the package directory absent exits 0.** It reports
`Successfully installed lume-model-api-0.1.0`, finds no packages, and then every import fails
with `ModuleNotFoundError` even after the code arrives. This is why the `Dockerfile` copies
`lume_model_api/` *before* installing. Reversing those two lines produces a broken image with a
green build. `pyproject.toml` also declares `readme = "README.md"`, so the build hard-fails
without the README copied in.

**`uvicorn` always puts the current directory on `sys.path`.** Its CLI defaults `--app-dir` to
`""` and does `sys.path.insert(0, app_dir)` unconditionally. So launching from the repo root
imports the source tree whether or not the package is installed, and in the container
`WORKDIR /app` does the same. To actually test an install:

```bash
cd /tmp && LUME_MODELS=demo /path/to/.venv/bin/uvicorn --app-dir /nonexistent \
  lume_model_api.api.main:app --port 8001
```

Also check the wheel, because an editable install masks a bad `packages.find`:

```bash
pip wheel --no-deps . -w /tmp/wh && unzip -l /tmp/wh/*.whl | grep lume_model_api/
```

Both `lume_model_api/api/` and `lume_model_api/model/` must appear. The
`include = ["lume_model_api*"]` trailing `*` in `pyproject.toml` is load-bearing. Without it
setuptools matches the name exactly and silently drops both subpackages.

## Things that look broken and should be left alone

- **The Docker build is unverified since the `VA_REF` bump to `043a2f0`.** The Dockerfile and
  `scripts/setup-dev-env.sh` were ported to the current virtual-accelerator API and the
  lume-cheetah / lume-bmad / lume-torch force pins were dropped, because the new VA resolves its
  own. No from-scratch build has been run. `conda install bmad pytao` is also unpinned, so a fresh
  solve can pull a Bmad that rejects a model's `tao.init`. Do not bump `newTag` in
  `deploy/kubernetes/kustomization.yaml` until a build is confirmed. Details in
  `docs/DEPLOY.md`.
- **The k8s objects are `lume-model-api-*` in namespace `lume-model-api`, serving the
  `/lume-model-api` prefix, on purpose.** They are deliberately a *new* set of objects rather than
  a rename of the old ones, so applying `deploy/kubernetes` runs this service beside the old
  monolith instead of replacing it. The `lume-monitor-*` objects in `lume-visualizations` belong to
  the lume-visualizations repo, still serve `/live-monitor` with the old API and the old UI, and
  are deleted only at the cut-over described in `docs/DEPLOY.md`. Nothing here should treat
  `/live-monitor` as this API's path.
- **Particle `coords` ships a `weight` array** that phase-space plots do not use, costing roughly
  17% extra payload. Deliberate: a distribution without per-particle charge is incomplete for
  physics callers. Do not add an `include_weight` flag. If payload size ever bites, lower
  `max_particles` on the live path.
- **Particle `stats` are computed on the full beam, before subsampling.** That is the point: a
  small `max_particles` should thin the scatter plot without corrupting the numbers next to it.
- **`smooth_images_sigma_px` is off by default and does not renormalize.** Both are
  deliberate. A blur on every 2-D array is wrong for a generic host, which cannot tell a screen
  image from a response matrix, and a rescale would silently invalidate the declared unit.
- **`output_beam` is excluded from screens** in `introspect.NON_SCREEN_PARTICLE_VARIABLES`. A
  screen is a place you can point a camera at, so the generic "beam at the end" is not one.
- **Writable non-scalar variables appear neither as inputs nor as outputs.** A writable
  `input_beam` is not drivable through a JSON knob API, and listing it as an output would promise
  a `get()` many models do not support on their own controls.
- **`lume_model_api/static/` is gitignored.** It is where a UI repo's build lands. Never commit
  one here.
- **The live producer must stay `replicas: 1`.** N replicas would each read EPICS and evaluate
  independently, costing N times the model work and showing viewers divergent frames.

## House style

No emojis, no em dashes and no semicolons in prose, in docs or in comments. Complete sentences. Short
paragraphs, and tables only for facts that actually enumerate. Comments should say *why*, not
restate the code.
