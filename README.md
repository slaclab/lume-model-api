# lume-model-api

An HTTP service that hosts one or more models wrapped in `LUMEModel` (from
[`lume-base`](https://github.com/slaclab/lume-base)). You point it at one or more factory
functions with one environment variable, and it publishes each model's inputs, outputs and
diagnostic screens over JSON, evaluates them on demand, and streams a live view over
server-sent events. Every model is addressed by name in the URL, under
`/api/v1/models/{name}/`. Nothing in the service names a PV, a screen or a unit. Everything a
client learns comes from the model instance's `supported_variables`, so hosting a new model
needs no code change here.

This service ships no UI. It exists so that any number of UIs, notebooks and analysis
scripts can share one model service and one set of conventions instead of each reimplementing
them. Model instances live in a pool of worker subprocesses, so K evaluates run in parallel
and the web process never imports torch, Bmad or a lattice. **Every evaluate is stateless:
whatever inputs you omit are filled in from the model's design values, so any worker and any
replica answers a given request identically.**

## Quickstart (5 minutes, no accelerator software needed)

The repo ships a small real `LUMEModel` called `demo`. It depends only on numpy and
`lume-base`, and it goes through exactly the same introspection, evaluate and serialization
path as a production model.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```

Ask the service which models it is hosting. This is the first call any client makes, and it is
what a model dropdown reads:

```bash
curl -s localhost:8000/api/v1/models | python -m json.tool
```

```json
[
  {
    "name": "demo",
    "description": "Dependency-light demonstration beamline with two screens.",
    "version": "demo"
  }
]
```

Then ask what one of those models publishes:

```bash
curl -s localhost:8000/api/v1/models/demo/config | python -m json.tool
```

```json
{
  "model": "demo",
  "version": "demo",
  "description": "Dependency-light demonstration beamline with two screens.",
  "inputs": [
    {"id": "DEMO:QUAD:1:BCTRL", "unit": "kG", "default": 2.0, "min": -5.0, "max": 5.0,
     "range_source": "model", "constant": false}
  ],
  "outputs": [
    {"id": "DEMO:OTRB:Image:ArrayData", "kind": "array", "unit": "counts",
     "shape": [240, 320], "element_name": "OTR_B"}
  ],
  "screens": [
    {"key": "OTR_A", "particles": "OTR_A_beam", "image": null},
    {"key": "OTR_B", "particles": "OTR_B_beam", "image": "DEMO:OTRB:Image:ArrayData"}
  ]
}
```

Then run one evaluate. `screen` is a shorthand that asks for that screen's particle
distribution plus its image, and `inputs` only needs the knobs you want to move.

```bash
curl -s -X POST localhost:8000/api/v1/models/demo/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"screen": "OTR_B", "inputs": {"DEMO:QUAD:1:BCTRL": -1.5}, "max_particles": 500}'
```

The response has `outputs` keyed by the ids you asked for, each declaring its own `kind` and
units. Arrays and particle coordinates arrive as base64 little-endian float32. See
[`docs/API.md`](docs/API.md) for the decode one-liner and every endpoint in full.

## Local install for a VA model

Running a virtual-accelerator model such as `cu_hxr_staged` without the image needs three things.
The pip pins live in the `va` extra in `pyproject.toml`, which the Dockerfile installs too.
Bmad, pytao and the lattices are not pip packages, so they are set up separately.

```bash
conda create -n lume-model-api -c conda-forge python=3.12 bmad pytao
conda activate lume-model-api
pip install --index-url https://download.pytorch.org/whl/cpu torch  # optional, CPU-only wheel
pip install -e ".[va,epics,dev]"

git clone https://github.com/slaclab/lcls-lattice.git ~/SLAC/lcls-lattice
git clone https://github.com/slaclab/facet2-lattice.git ~/SLAC/facet2-lattice
export LCLS_LATTICE=~/SLAC/lcls-lattice FACET2_LATTICE=~/SLAC/facet2-lattice
```

Check out the lattice commits named by `LCLS_LATTICE_REF` and `FACET_LATTICE_REF` in the
Dockerfile to match the image. The `cu_hxr_*` models need `LCLS_LATTICE` and the `facet_*`
models need `FACET2_LATTICE`. Then run:

```bash
LUME_MODELS=cu_hxr_staged KMP_DUPLICATE_LIB_OK=TRUE uvicorn lume_model_api.api.main:app --port 8000
```

Then set one injector quad, ask for OTR3's particles and image, and read the quad's readback:

```bash
curl -s -X POST localhost:8000/api/v1/models/cu_hxr_staged/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"inputs": {"QUAD:IN20:525:BCTRL": -3.0}, "screen": "OTR3", "outputs": ["QUAD:IN20:525:BACT"], "max_particles": 1000}' \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print({k: v.get('value', v['kind']) for k, v in r['outputs'].items()}); print(r['input_sources']['QUAD:IN20:525:BCTRL'])"
```

## Selecting models

`LUME_MODELS` is the only model-specific setting. It names every model this process hosts and
the URL name each one answers on. A shortcut is a friendly name for a factory path plus default
kwargs.

| Shortcut | Factory | Default kwargs |
| --- | --- | --- |
| `demo` | `lume_model_api.model.demo:make_demo_model` | `{}` |
| `cu_hxr_staged` | `virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model` | `{"n_particles": 1000, "end_element": "TD11"}` |
| `cu_hxr_bmad` | `virtual_accelerator.models.cu_hxr:get_cu_hxr_bmad_model` | `{"track_beam": true, "end_element": "TD11"}` |
| `facet_staged` | `virtual_accelerator.models.facet2:get_facet_staged_model` | `{}` |
| `facet_bmad` | `virtual_accelerator.models.facet2:get_facet_bmad_model` | `{}` |

`cu_hxr_staged` is one real model you can select. The shortest form of `LUME_MODELS` is a comma
list of shortcut names, one URL name each:

```bash
LUME_MODELS=demo,cu_hxr_staged uvicorn lume_model_api.api.main:app
```

The JSON object form is the full one. Each key is the URL name, and each value may carry
`factory`, `kwargs`, `workers` and `max_inflight`:

```bash
LUME_MODELS='{
  "cu_hxr_staged": {"kwargs": {"n_particles": 2000, "end_element": "OTR3"}, "workers": 2},
  "my_line": {"factory": "my_package.models:make_my_line_model", "kwargs": {"gain": 2.0}}
}' uvicorn lume_model_api.api.main:app
```

Any factory reachable on `sys.path` works with a full `module.path:factory_function` reference
and no code change, given a URL name to answer on. An unset `LUME_MODELS` hosts `demo`, and
`LUME_MODELS=""` also hosts `demo` but logs a warning, since an empty value in a k8s env block is a
pod that looks configured and is not. The older `LUME_MODEL` and `LUME_MODEL_KWARGS` are retired and
setting either is now a startup error. See
[`docs/ADDING_A_MODEL.md`](docs/ADDING_A_MODEL.md#the-rules-in-full) for the complete rules.

A model's own dependencies and its lattice location are the model's business. `LCLS_LATTICE` or `FACET2_LATTICE` must be set for the models
that need them, and virtual-accelerator raises a clear error when one is missing.
[`docs/ADDING_A_MODEL.md`](docs/ADDING_A_MODEL.md) covers what a model must expose, how to add
a shortcut, and how to get its dependencies into the image.

## Endpoints

Every model-specific path carries the model's URL name. There are no unprefixed routes and no
implicit default model, so `/api/config` and the other old paths return 404.

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/models` | `[{name, description, version}]` for every model this process hosts, sorted by name. |
| `GET /api/v1/models/{name}/config` | Inputs with ranges, units and defaults, outputs with kinds, and screens. |
| `POST /api/v1/models/{name}/evaluate` | Stateless evaluation. Same for every caller. `input_sources` says which effective inputs you sent and which the baseline filled in. |
| `GET /api/v1/models/{name}/machine-snapshot` | The current live input values, read-only, with `sources` marking each one `live` or `baseline`. |
| `GET /api/v1/models/{name}/live/stream?screen=` or `?outputs=a,b` | SSE `frame` events with the same body as evaluate. 503 when the hub already runs its maximum number of distinct producer loops. |
| `GET /metrics` | Prometheus, every series labelled by `model`. KEDA autoscales the eval pool on `lume_pool_inflight`. |
| `GET /healthz` | What all three k8s probes target, on both Deployments. 503 until every pool has warmed, and again once any pool loses a worker. Deliberately outside the `/api/v1` contract and absent from `openapi.json`, so no client should pin it. |

A client reads `GET /api/v1/models` and never hard-codes a model name. That route is pure client
discovery and is deliberately not the probe target, because a pod hosting several models keeps
listing the ones that still work when another model's pool has died. The `model` field in every
response body is the URL name, which is how a client matches a fetched config back to the entry it
selected.

There is deliberately no second, UI-private evaluate endpoint. A second shape would mean every
new UI reimplemented the unit handling, which is the duplication this service removes.

## Environment variables

| Variable | Default | What it does |
| --- | --- | --- |
| `LUME_MODELS` | unset (hosts `demo`) | Every model to host, keyed by URL name. A JSON object, or a comma list of shortcut names and `name=module:function` items. |
| `LUME_ROLE` | `all` | `eval` serves the list, config and evaluate, `live` serves the stream and snapshot, `all` serves both. |
| `LUME_POOL_WORKERS` | `4` | Default model instances (subprocesses) per model. Per-model, not a pod budget, so three models at the default means twelve workers. |
| `LUME_MAX_INFLIGHT` | `4 * workers` | Default in-flight evaluates per model before the service returns 503. Also per-model. |
| `LUME_EVALUATE_TIMEOUT_S` | `0` (disabled) | Float seconds. When set, an evaluate that overruns marks that model's pool dead, so `/healthz` fails and the pod restarts, because a subprocess evaluate cannot be cancelled. Any value must sit well above the 25 to 30 second model build and the roughly 2.5 seconds an evaluate takes. |
| `LUME_WORKER_THREADS` | `1` | Per-worker BLAS/OMP thread cap. |
| `LUME_LIVE_SOURCE` | `epics` | `epics` reads each input id as a PV, `synthetic` wiggles inputs around their defaults. |
| `LUME_LIVE_INPUTS` | all inputs | JSON list restricting which input ids the live source reads. |
| `LUME_DERIVED_RANGE_FRACTION` | `0.5` | Fraction of \|default\| used for a range the model does not declare. |
| `LUME_MAX_IMAGE_DIM` | `512` | 2-D array outputs are block-mean downsampled to this longest side. `0` disables. |
| `LUME_STATIC_DIR` | unset | Directory of a single-page app to serve at `/`. |
| `LUME_ROOT_PATH` | empty | The URL prefix an ingress strips before the request arrives, `/lume-model-api` in the cluster. Only affects generated links, so Swagger under the prefix finds `openapi.json`. Must be unset when regenerating `openapi.json`. |
| `EPICS_CA_ADDR_LIST`, `EPICS_CA_AUTO_ADDR_LIST` | localhost | Channel-access config for the live role. Set from a ConfigMap in k8s. |
| `LCLS_LATTICE`, `FACET2_LATTICE` | unset | Read by the hosted model, not by this service. |

`LUME_MODELS` in full, with per-model worker counts and one model built from an explicit
factory:

```bash
LUME_MODELS='{"cu_hxr_staged": {"kwargs": {"end_element": "OTR3"}, "workers": 2, "max_inflight": 8},
              "demo": {"workers": 1}}'
```

`LUME_LIVE_SOURCE` and `LUME_LIVE_INPUTS` are global rather than per-model, so every hosted
model reads its live inputs from the same source.

## Deployed URL

The model list at SLAC is

```
https://ard-modeling-service.slac.stanford.edu/lume-model-api/api/v1/models
```

from `deploy/kubernetes/ingress.yaml`. Clients use everything up to and including
`/lume-model-api` as their base URL and append `/api/v1/models/...`, because the ingress strips
the prefix before the request reaches the app.

The `/live-monitor` prefix on the same host is the old monolith, which serves the pre-split API
and the React UI out of the `lume-visualizations` namespace. It is not this service.

TLS terminates in front of the cluster, on an F5 BigIP holding the `*.slac.stanford.edu`
certificate, which also redirects plain HTTP to HTTPS. That is why `ingress.yaml` carries no TLS
block. The Ingress carries an IP allowlist covering SLAC and a few other ranges, verified to
evaluate the real client address behind that load balancer, so the service is not reachable from
elsewhere. See [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Repo layout

- `lume_model_api/model/` is the model layer. `loader.py` turns `LUME_MODELS` into a
  `ModelSetting` per model and builds the instances, `introspect.py` derives inputs, outputs,
  screens and the baseline from each one,
  `evaluate.py` is the one evaluate path, `demo.py` is the in-repo demo model, and
  `live_inputs.py` supplies live input values from EPICS or synthetically.
- `lume_model_api/api/` is the HTTP layer. `main.py` (routes, and one `HostedModel` per hosted
  name), `pool.py` (the subprocess pool, one per model), `live_hub.py` (SSE fan-out, one hub per
  model), `schemas.py`, `serialize.py` and `metrics.py`.
- `tests/` runs the whole pipeline against the demo model, with no torch, pytao or EPICS.
- `deploy/kubernetes/` holds the eval pool, the live singleton, service, ingress and KEDA.
- `scripts/dump_openapi.py` regenerates the committed contract.

## Docs

One canonical home per topic. Every other file cross-references rather than repeating.

- [`docs/API.md`](docs/API.md) is the endpoint reference and the wire contract, with curl and
  Python examples, the output kinds, the units convention, the SSE details and the metrics. Read it
  before writing a client.
- [`docs/ADDING_A_MODEL.md`](docs/ADDING_A_MODEL.md) is the model author's contract: what a model
  must expose for each feature to appear, and the full `LUME_MODELS` rules.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) explains the layering, the request flow, the
  process pool, the live hub, the baseline merge and where the published metadata comes from.
- [`docs/DEPLOY.md`](docs/DEPLOY.md) covers the image build, the kustomize apply, the probes, pod
  sizing, scaling and the packaging traps.
- [`docs/MIGRATING_LUME_VISUALIZATIONS.md`](docs/MIGRATING_LUME_VISUALIZATIONS.md) maps the
  old backend's API onto this one, field by field, for porting the original React UI.
- [`docs/MIGRATING_INFERENCE_SERVICE.md`](docs/MIGRATING_INFERENCE_SERVICE.md) is the step-by-step
  plan for absorbing the MLflow-backed `inference-service` into this one, written for someone who
  knows that service and not this one.
- [`AGENTS.md`](AGENTS.md) is a short index of the sharp edges that are invisible in the code. Read
  it before changing anything structural, human or agent.

## The contract

`openapi.json` is committed at the repo root and is the artifact consumers generate their
client types from. Fetch it at a pinned git ref rather than hand-copying the shapes.
`.github/workflows/contract.yml` regenerates it on every pull request and fails if it is
stale, and `tests/test_api_contract.py` asserts the `/api/v1/*` field names by hand so a
rename cannot hide behind a regenerated snapshot.

The response shape was redesigned once, when the service became model-agnostic and while it
still had no consumers. The v1 paths then moved once more, to `/api/v1/models/{name}/...`, while
there was still no deployed consumer and the UI port was in progress (see
[`docs/MIGRATING_LUME_VISUALIZATIONS.md`](docs/MIGRATING_LUME_VISUALIZATIONS.md)). From that
change onward, additive only. Adding an optional field is fine. Renaming or removing one, or
moving a path again, needs `/api/v2`.
