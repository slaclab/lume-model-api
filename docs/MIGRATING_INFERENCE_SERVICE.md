# Migrating `inference-service` into this service

`inference-service` serves one MLflow-registered lume-torch model per pod, on unversioned and
unprefixed paths (`/predict`, `/predict/batch`, `/inputs`, `/outputs`, `/model/info`, `/health`).
This service hosts any number of `LUMEModel`s behind `/api/v1/models/{name}/...`. The decision is
to converge on one service: teach this one to load a model from the MLflow registry, serve the FEL
and CU-INJ surrogates from it, cut the consumers over in one move, and delete
`inference_service.py`.

There is deliberately no intermediate step where both services speak the same contract. Rewriting
`inference-service`'s routes against this contract and then merging the two codebases is more work
than absorbing directly, and a hard cut was chosen, so nothing needs a deprecation window.

> **This document is a dated snapshot of another repository.** Everything it says about
> `inference-service` and about its consumers describes those repos as they stood on 2026-09-17 and
> cannot be verified from here. File and symbol names are given without line numbers, because line
> numbers in another repo rot immediately. If a named file or symbol has moved, trust that repo.
> What is authoritative here is the **new** side of every mapping.

## Why this is cheap: three things that are already true

Checked against the checkout at `../lume-torch`:

- **`TorchScalarVariable` subclasses lume-base's `ScalarVariable`, and `TorchNDVariable` subclasses
  `NDVariable`**, both in `lume_torch/variables.py`. `variable_kind` in `model/introspect.py` does
  `isinstance` against the lume-base classes, so a torch model's scalars publish as
  `kind: "scalar"` and its ND outputs as `kind: "array"` with no change here. ND outputs are the
  ones `inference-service` cannot represent at all, because `clean_output_value` calls `.item()`,
  which raises on anything with more than one element.
- **`lume_torch.base.LUMETorchModel` already adapts a `LUMETorch` to the `LUMEModel` interface this
  service drives** (`supported_variables`, `set`, `get`). `inference-service` uses `TorchModel`
  directly, which is a `LUMETorch` and not a `LUMEModel`, which is why none of this was reusable
  before.
- **`LUMETorchModel` publishes output variables as shallow copies with `read_only=True`**, which is
  exactly what `introspect.describe` keys off to split inputs from outputs. See
  [ADDING_A_MODEL.md](ADDING_A_MODEL.md#what-the-model-must-expose-for-each-feature-to-appear).

So the model-side work is a factory function, not an integration.

## Orientation: where each `inference-service` concept lives here

| In `inference-service` | Here | Read |
| --- | --- | --- |
| `MODEL_NAME` and `MODEL_VERSION`, one model per pod, required at startup | `LUME_MODELS`, any number per pod, keyed by the URL name each answers on | [ADDING_A_MODEL.md](ADDING_A_MODEL.md#three-ways-to-run-a-model) |
| A `TorchModel` loaded once in the web process, shared across `asyncio.to_thread` calls | A `LUMEModel` built independently in each of K pool subprocesses | [ARCHITECTURE.md](ARCHITECTURE.md#the-process-pool) |
| `POST /predict` | `POST /api/v1/models/{name}/evaluate` | [API.md](API.md#post-apiv1modelsnameevaluate) |
| `POST /predict/batch` | New in step 3 below | |
| `GET /inputs`, `GET /outputs`, `GET /inputs/types`, `GET /model/info` | `GET /api/v1/models/{name}/config`, one call. The Python class names `/inputs/types` returns are replaced by the four wire kinds | [API.md](API.md#get-apiv1modelsnameconfig) |
| Nothing | `GET /api/v1/models`, the first call every client makes | [API.md](API.md#get-apiv1models) |
| `GET /health`, in the schema, polled by consumers | `GET /healthz`, out of the schema on purpose. "Can you serve me" is `GET /api/v1/models` | [API.md](API.md#conventions) |
| `GET /` endpoint map and `GET /debug/state` | Nothing. `openapi.json` is the discovery document | [API.md](API.md#changing-the-contract) |
| A hard 30 second timeout per request | `max_inflight` backpressure returning 503, and `LUME_EVALUATE_TIMEOUT_S` which is off by default | [ARCHITECTURE.md](ARCHITECTURE.md#lume_evaluate_timeout_s) |
| Model defaults silently fill missing inputs | Baseline merge, echoed back in `inputs` alongside `input_sources` | [ARCHITECTURE.md](ARCHITECTURE.md#baseline-merge-and-statelessness) |
| Nothing | `GET /metrics`, and KEDA scaling on it | [API.md](API.md#get-metrics) |

Two things have no counterpart in `inference-service` and can be ignored for a surrogate: screens
and the live SSE stream. A torch surrogate publishes no `ParticleGroupVariable`, so its config
reports `screens: []` and the `screen` shorthand is simply unusable. Run surrogate pods with
`LUME_ROLE=eval` and give them no live pod. See
[ARCHITECTURE.md](ARCHITECTURE.md#lume_role-and-the-two-deployments).

## Step 1: load a model from the MLflow registry

**Add `lume_model_api/model/mlflow_source.py`** with three pieces:

- `resolve(name, version)` returning the concrete registry version and run id via `MlflowClient`.
  Port the version, stage and `latest` handling from `inference-service`'s
  `download_model_artifacts`.
- `download(name, version)` returning a `ModelSource` dataclass carrying `kind="mlflow"`, the
  registry name, the resolved version, the run id, and the absolute `config_path` of the YAML found
  in the artifacts. Port `find_yaml_config` as is.
- `make_model(config_path)` returning `LUMETorchModel(TorchModel(config_path))`. This is the
  factory a pool worker calls, and it is the whole model-side adapter.

**In `model/loader.py`**, accept `mlflow:<registry-name>[:<version>]` as a model reference in
`resolve`, mapping it to the factory path `lume_model_api.model.mlflow_source:make_model`. Add a
`source_ref` field to `ModelSetting` to carry the reference, and a `materialize(setting)` function
that performs the download and returns the setting with `kwargs["config_path"]` filled in plus the
`ModelSource`.

**In `api/main.py`'s `lifespan`**, call `loader.materialize(setting)` for each setting before
constructing its `ModelPool`, store the returned `ModelSource` on `HostedModel`, and remove the
downloaded tree in the lifespan's `finally` beside `pool.shutdown()`.

**Check the `mlflow:` branch before the `":" in model_ref` branch in `resolve`.** That branch
currently treats any reference containing a colon as a `module.path:factory` path, so an
`mlflow:...` reference would be handed to `importlib` and fail with a confusing message.

**Download in the main process, exactly once.** Each of the K workers calls the factory
independently, in its own process, at roughly the same moment, so a factory that talks to MLflow
itself would fetch the artifact K times and race on the temp directory. K workers reading one
read-only YAML tree is explicitly fine. See
[ADDING_A_MODEL.md](ADDING_A_MODEL.md#what-your-factory-must-tolerate).

**Keep `import mlflow` inside a function, and add a row to the lazy-import table in `AGENTS.md`.**
mlflow is a new `[mlflow]` extra that CI does not install, and `pytest` plus
`python scripts/dump_openapi.py` have to keep running in a plain venv. Importing the mlflow client
in the main process is a deliberate exception to the rule that models are only imported in workers,
justified because the client pulls no torch and because only the main process knows the resolved
version. `resolve` itself must stay import-free, which is why the download lives in `materialize`.

**Pass an absolute `config_path`.** A worker's working directory is a fresh temp directory, so a
relative path resolves to somewhere useless inside a worker while working fine in a test run from
the repo root.

**Pin the version. Never deploy `latest`.** An unpinned reference means a pod restart can silently
pick up a different model, and `materialize` is the only place that would notice. Log the resolved
version and run id there. `deployments/fel-model/deployment.yaml` in the old repo already pins `22`,
so keep doing that in `LUME_MODELS`.

**MLflow becomes a startup dependency.** The lifespan warms every pool before it yields, so an
MLflow outage means the pod fails to start rather than serving 503s. `inference-service` has the
same exposure today, because its lifespan re-raises. Pinned versions are what make a restart
reproducible. If restarts during an MLflow outage matter, cache the artifacts on a volume.

**Verify** with the FEL model, and compare against the old service field by field:

```bash
MLFLOW_TRACKING_URI=<uri> \
LUME_MODELS='{"lcls-fel-surrogate": {"factory": "mlflow:lcls-fel-surrogate:22", "workers": 2}}' \
LUME_ROLE=eval uvicorn lume_model_api.api.main:app --port 8000

curl -s localhost:8000/api/v1/models/lcls-fel-surrogate/config | python -m json.tool
```

**Then check no input went missing.** `introspect._input_info` falls back to `model.get([name])`
for a variable that declares no `default_value`, and `LUMETorchModel._get` raises `KeyError` before
the first `set`, which `_current_value` catches, logs and turns into a dropped input. The knob is
then absent from the config with only an INFO line to say so. Compare the `inputs` ids in the
config against `TorchModel.input_names`, and expect `range_source: "derived"` on any input the
model declares no `value_range` for, which is fixed upstream in the model rather than here. See
[ADDING_A_MODEL.md](ADDING_A_MODEL.md#missing-ranges-are-derived).

## Step 2: make `model/evaluate.py` tolerate torch tensors

Both changes are in the output conversion loop of `evaluate`.

**The array branch calls `np.asarray(value)`, which raises on a tensor that still requires grad.**
`inference-service`'s `clean_output_value` does `.detach().cpu()` for exactly this reason. Add a
helper that applies `detach().cpu().numpy()` when the value exposes `detach`, and use it in the
array branch and in `_plain`.

**Duck-type on `hasattr(value, "detach")` rather than importing torch.** No module in this package
may import torch, which is what lets the tests and the schema dump run in a plain venv.

**Leave the scalar branch's `float(value)` raising.** It handles a 0-d or single-element tensor and
raises on anything larger, which is correct: a variable published as `kind: "scalar"` that returns
many values is a model bug worth surfacing. It is also the trap the batch route in step 3 must not
walk into.

**Verify** with a unit test using a stub object that exposes `detach`, `cpu` and `numpy`, so the
test still runs without torch, plus one real evaluate of the FEL model.

## Step 3: three additions to the `/api/v1` contract

One commit, one schema regeneration. Follow
[API.md](API.md#changing-the-contract) exactly: regenerate under the pinned `fastapi` and
`pydantic`, then update the longhand field sets in `tests/test_api_contract.py` by hand. All three
additions are optional fields or new routes, so they stay inside the additive-only rule.

### `all_outputs: bool = False` on `EvaluateV1Request`

`POST /predict` returns every output, and every existing consumer depends on that. This service
400s a request that asks for nothing, because a generic host has no sensible default output set.
`all_outputs: true` expands to every id in the model's config and counts as a third way to satisfy
"at least one of `outputs` or `screen`".

**A field, not an `outputs: ["*"]` sentinel.** A sentinel needs no schema change, which sounds
attractive, but it would be invisible in `openapi.json`, and being detectable by fetching the
schema is the whole argument behind the wire rules in
[API.md](API.md#result_to_wire-emits-every-key-on-every-call-and-every-requested-output-id).

**Guard the expansion.** On a Bmad model "everything" is every screen's beam and every image. Refuse
with 400 above a configured output count, naming the count, rather than assembling a response no
client can use.

### `POST /api/v1/models/{name}/evaluate/batch`

One output spec for the whole batch plus N input dicts, which is both what `/predict/batch` accepts
and how a vectorized torch call works:

```
{ "inputs_list": [{...}, {...}], "outputs": [...], "screen": null,
  "all_outputs": false, "max_particles": null, "smooth_images_sigma_px": null }

-> { "model": ..., "version": ..., "results": [ <EvaluateV1Response>, ... ] }
```

`results` is positionally aligned with `inputs_list`, and each entry is a full evaluate response so
a client reuses one decoder.

**Keep `frame_index` at 0 on every result.** Reinterpreting it as the batch index would break its
documented meaning as a monotonic per-hub counter on the SSE path. The array order carries the
index.

**Add `_worker_evaluate_batch` to `api/pool.py` and route it through `ModelPool._submit`.** One
worker task looping over the items is one IPC round trip and one unit of `max_inflight`, and
`_submit` is what keeps the inflight gauge, the metrics and the dead-pool check honest.

**Do not port the vectorized-then-sequential fallback.** `inference-service`'s `predict_batch`
wraps the vectorized attempt in a bare `except Exception`, so a genuine input error is swallowed,
the whole batch runs a second time sequentially, and the second failure is what gets reported. A
missing input surfaces as a 500 after twice the work. Validate every id up front and fail the whole
batch on a model rejection. Vectorization can come later, behind an explicit per-model capability
and a test that a batched call and N single calls agree.

**Cap `len(inputs_list)` as a 422**, and size the cap against the evaluate timeout. A batch holds
one worker for N evaluates and a subprocess evaluate cannot be cancelled. With
`LUME_EVALUATE_TIMEOUT_S` unset, which is the default and a deliberate one, that only means a long
request. If anyone sets that timeout it applies to the whole batch submit, so N times the per-item
latency has to stay well under it or a large batch marks the pool dead and restarts the pod. See
[DEPLOY.md](DEPLOY.md#lume_evaluate_timeout_s-is-unset-and-that-is-a-real-choice). A client that
disconnects mid-batch also leaves the worker computing all N.

**Label batch submits `kind="batch"`** in `lume_evaluate_seconds` and `lume_evaluate_total`, so they
do not dilute interactive latency, and add the value to the metrics table in API.md.

### `source` on `ConfigResponse`

Optional, null when unknown:

```json
{"kind": "mlflow", "name": "lcls-fel-surrogate", "version": "22", "run_id": "..."}
{"kind": "factory", "factory": "virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model"}
```

This is missing from both services today. `inference-service` computes the resolved version and run
id in `download_model_artifacts` and discards them, so `/model/info` reports whatever was requested,
which is `null` or `"latest"`, and no consumer can record what it actually ran.

**Attach it in the route from `HostedModel`, not in `describe`.** `ModelInfo` is built inside a
worker that has no idea where its config file came from, and it crosses the spawn boundary as a
pickled dataclass. Provenance is known only in the main process, which is the same reason `model`,
`version` and `input_sources` are attached by the sender. See
[ARCHITECTURE.md](ARCHITECTURE.md#where-metadata-comes-from).

**Do not redefine `version`.** Anyone arriving from `inference-service` will expect `version` to
mean `MODEL_VERSION`. Here it is documented as `demo`, `<name> (demo)` or the URL name, and the
lume-visualizations client reads it that way, so changing its meaning is a silent break and an
`/api/v2` matter. The registry version goes in `source.version`.

**Update the docs in the same commit**, because every behaviour has exactly one canonical home:
API.md gets the batch section, the `all_outputs` and `source` rows and the new metrics label, and
README.md's endpoint list gets the batch route.

## Step 4: packaging and the image

**Add an `[mlflow]` extra** holding `mlflow` and `lume-torch`, following the `[epics]` pattern in
`pyproject.toml`. It stays out of `dependencies` for the same reason a model's own stack does.

**Do not add mlflow to the existing image.** That image installs miniforge, `bmad`, `pytao`, two
lattices and virtual-accelerator, so pointing surrogate pods at it means pulling a multi-GB Bmad
image to run a small torch model. Add a second, slim build on `python:3.12-slim` installing the CPU
torch wheel, `lume-torch` and `.[mlflow]`, which is close to what `inference-service`'s own
Dockerfile already does. Two images, one package. See
[DEPLOY.md](DEPLOY.md#building-the-image) and
[ADDING_A_MODEL.md](ADDING_A_MODEL.md#getting-the-models-dependencies-into-the-image).

**Start at `LUME_POOL_WORKERS=1` or `2`.** The default of 4 means four torch processes for a model
that runs in milliseconds. See [DEPLOY.md](DEPLOY.md#sizing-a-pod).

**Measure before tuning the execution model.** Whether subprocess IPC per evaluate is significant
for a millisecond model is an open question. Threads would be safe on a torch-only pod, because
`TorchModel._evaluate` does not mutate the model, but that contradicts the pool's reason to exist
on a mixed pod, so treat it as a later optimization with a measurement behind it and not as part of
this migration. See [ARCHITECTURE.md](ARCHITECTURE.md#the-process-pool).

## Step 5: deploy alongside, then diff a sweep

One Deployment per model, as today, which keeps MLflow versions, scaling and blast radius
independent. The ingress routes `/api/v1/models/<name>/*` by name to the owning Deployment. Both
registry names work unchanged as URL segments, because `loader.NAME_PATTERN` allows dashes and
underscores and rejects only dots. See
[DEPLOY.md](DEPLOY.md#splitting-models-across-pods) and
[DEPLOY.md](DEPLOY.md#the-deployed-url).

**`GET /api/v1/models` needs a single owner.** It is the first call every client makes, and each pod
knows only its own models. Route that one path to a static catalog served from a ConfigMap listing
the whole fleet, and add a CI check that every catalog entry resolves and every Deployment appears
in it. A hand-maintained fleet list drifts, and a missing entry is invisible: clients simply never
see the model.

**Probe `/healthz`, not `/health`.** See [DEPLOY.md](DEPLOY.md#probes).

**Keep `/fel` and `/cuinj` serving until step 6, and diff a sweep across both origins before
cutting.** This is not optional. The two evaluate paths differ in validation strictness, in inbound
non-finite handling and in what they echo back, so same model and same inputs is not obviously the
same numbers. Sweep each input across its range and compare outputs to a tolerance. The rest of the
install check is in
[DEPLOY.md](DEPLOY.md#verifying-an-install-actually-works).

## Step 6: cut the consumers

All in one move, since there are no shims.

| Consumer | What to change |
| --- | --- |
| `fel-surrogate-mcp-skill` | `src/fel_mcp_server.py` maps one tool per old path and holds `DEFAULT_API_BASE`. Also the two skills under `.claude/skills/` and their `references/examples.md`, which carry worked `curl` examples. |
| `lume-model-visual` | `src/model.py` holds both base URLs, and `src/state.py` touches the same API. |
| `ai-lab`, `snd-sase-online-model`, `virtual-accelerator/examples/staged_model_demo.ipynb` | Mention the host in docs or a notebook and may need only a URL change. Grep for `ard-modeling-service`. |
| `inference-service` itself | `client.py`, `test_client.py`, `test_validation.py`, `test_local.py` and `tests/` go away with the service in step 7. |

Behaviour changes a consumer will notice, beyond the paths:

| | Before | After |
| --- | --- | --- |
| An input outside its declared range | Logged and evaluated anyway, because `load_lume_model` sets `input_validation_config` to `warn` for every input | 400 carrying the model's own message |
| `NaN` or `Infinity` in `inputs` | Accepted, 200. Verified against the pinned `fastapi` 0.141.1 and `pydantic` 2.13.4: Starlette's `json.loads` accepts the literal and the float field allows it | 422 |
| Asking for outputs | `/predict` returns all of them | List ids in `outputs` or set `all_outputs: true`. An empty request is 400 |
| The inputs actually applied | Not reported | Echoed in `inputs`, with `input_sources` saying which came from the request and which from the baseline |
| An ND output | 500, because `clean_output_value` calls `.item()` | `kind: "array"`, base64 little-endian float32, with `shape` |
| A non-finite output | Already `null`, which pydantic v2 does by default | `null`, and documented as such on both paths |
| The resolved model version | `/model/info` reports what was requested, so `null` or `"latest"` | `source.version` and `source.run_id`, resolved at startup |
| Health | `GET /health` | `GET /healthz` for probes, `GET /api/v1/models` to ask whether the service can serve you |

**Decide the first row deliberately.** Keeping the 400 is correct and is what this service's
contract says, but check whether the MCP skill or the trame app relies on pushing a knob past a
declared range before you cut, because it will start failing.

## Step 7: delete the old service

Delete `inference_service.py`, `client.py`, `Dockerfile`, `Dockerfile.client`, the four top-level
test files, `tests/`, and the `/fel` and `/cuinj` ingresses. Drop `/debug/state` with them: it
publishes `dir(app.state)`.

Keep `copier-template-k8s` and `model-configs`, retargeted at the new image and at `LUME_MODELS`
instead of `MODEL_NAME` and `MODEL_VERSION`. That template is the reason a new model needs no code
change, and it is worth more than the service it currently generates. Leave a README in the old repo
pointing here.

## Done when

- [ ] `GET /api/v1/models/lcls-fel-surrogate/config` lists every name in `TorchModel.input_names`
      and every output, with no dropped input in the INFO log.
- [ ] A sweep over both origins agrees to a tolerance, recorded somewhere.
- [ ] `pytest` and `python scripts/dump_openapi.py` still pass in a plain venv with no mlflow, no
      torch, no pytao and no EPICS.
- [ ] `openapi.json` regenerated under the pinned versions, with the longhand field sets in
      `tests/test_api_contract.py` updated by hand.
- [ ] API.md and README.md describe the batch route, `all_outputs`, `source` and the `batch` metrics
      label. AGENTS.md's lazy-import table has a row for mlflow.
- [ ] The catalog ConfigMap lists the whole fleet and CI checks it against the Deployments.
- [ ] No consumer references `ard-modeling-service.slac.stanford.edu/fel` or `/cuinj`.
