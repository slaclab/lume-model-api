# lume-model-api

An HTTP service that hosts simulation models built on `LUMEModel` from
[lume-base](https://github.com/slaclab/lume-base). Each model's inputs, outputs and diagnostic
screens are published as JSON, models are evaluated on request, and a live view of the machine
is streamed over server-sent events. Models are addressed by name under `/api/v1/models/{name}/`.

The service has no knowledge of any particular PV, screen or unit. Everything it publishes comes
from the model's own `supported_variables`, so hosting a new model is a configuration change. It
ships no UI. UIs, notebooks and scripts all use the same API.

## Quickstart

The repo includes a small demo model that needs only numpy and lume-base. It runs through the
same code path as a real model.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```

`LUME_LIVE_SOURCE=synthetic` drives the live stream from the model's defaults instead of EPICS.

List the hosted models, then read one model's config:

```bash
curl -s localhost:8000/api/v1/models
curl -s localhost:8000/api/v1/models/demo/config
```

Evaluate it with one input changed and one screen requested:

```bash
curl -s -X POST localhost:8000/api/v1/models/demo/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"inputs": {"DEMO:QUAD:1:BCTRL": 3.0}, "screen": "OTR_B"}'
```

Interactive API docs are at <http://localhost:8000/docs> (Swagger) and
<http://localhost:8000/redoc>.

To run a virtual-accelerator model such as `cu_hxr_staged` locally, see
[Local install for a VA model](#local-install-for-a-va-model).

## Deployments

| Environment | Cluster | Base URL |
| --- | --- | --- |
| Production | `ad-accel-online-ml` | `https://ard-modeling-service.slac.stanford.edu/lume-model-api` |
| Development | `ad-accel-online-ml-dev` (vcluster) | `https://ad-accel-online-ml-dev.slac.stanford.edu/lume-model-api` |

Clients append `/api/v1/models/...` to the base URL. Both are reachable from the SLAC network only.
Nothing from this repo is deployed yet. The manifests in `deploy/kubernetes/` target the dev
host. See [URLs](docs/maintainer/DEPLOY.md#urls) for routing, TLS and the allowlist.

## Docs

**Using the API** (frontend, TypeScript and React):

- [Frontend quickstart](docs/frontend/QUICKSTART.md): every call a UI makes, in order, with
  `fetch` and `EventSource`
- [Concepts](docs/frontend/CONCEPTS.md): baseline merge, `input_sources`, screens, output kinds,
  the live stream and errors
- [TypeScript client](docs/frontend/TYPESCRIPT_CLIENT.md): generating and pinning types from
  `openapi.json`
- [API reference](https://slaclab.github.io/lume-model-api/): every field, generated from
  `openapi.json`

**Working on the service:**

- [Architecture](docs/maintainer/ARCHITECTURE.md): layers, the process pool, the live hub, roles
- [Design decisions](docs/maintainer/DESIGN_DECISIONS.md): choices that look like bugs and
  aren't
- [Adding a model](docs/maintainer/ADDING_A_MODEL.md): hosting a virtual-accelerator model or any
  other `LUMEModel`
- [Deploying](docs/maintainer/DEPLOY.md): image, Kubernetes, sizing, scaling, probes
- [Changing the API](docs/maintainer/CHANGING_THE_API.md): the additive-only rule and how to
  regenerate `openapi.json`
- [Writing docs](docs/WRITING_DOCS.md): the style these docs follow
- [AGENTS.md](AGENTS.md): short list of traps, for people and coding agents

## Configuration

The main settings. The full list with defaults is in
[Deploying](docs/maintainer/DEPLOY.md#configuration).

| Variable | Default | Purpose |
| --- | --- | --- |
| `LUME_MODELS` | `demo` | Models to host. See [Adding a model](docs/maintainer/ADDING_A_MODEL.md#lume_models-reference). |
| `LUME_ROLE` | `all` | `eval` serves config and evaluate, `live` adds the live stream and snapshot, `all` serves everything. |
| `LUME_POOL_WORKERS` | `4` | Worker processes per model. |
| `LUME_LIVE_SOURCE` | `epics` | Where live inputs come from: `epics` or `synthetic`. |
| `LCLS_LATTICE`, `FACET2_LATTICE` | unset | Lattice paths, read by virtual-accelerator models. |

## Local install for a VA model

Bmad and pytao come from conda-forge. The pinned lume stack and virtual-accelerator are in the
`va` extra in `pyproject.toml`, which the Dockerfile also installs.

```bash
conda create -n lume-model-api -c conda-forge python=3.12 bmad pytao
conda activate lume-model-api
pip install --index-url https://download.pytorch.org/whl/cpu torch  # optional, CPU-only wheel
pip install -e ".[va,epics,dev]"

git clone https://github.com/slaclab/lcls-lattice.git ~/lcls-lattice
git clone https://github.com/slaclab/facet2-lattice.git ~/facet2-lattice
export LCLS_LATTICE=~/lcls-lattice FACET2_LATTICE=~/facet2-lattice
```

To match the image, check out the lattice commits named by `LCLS_LATTICE_REF` and
`FACET_LATTICE_REF` in the Dockerfile. Then run:

```bash
LUME_MODELS=cu_hxr_staged LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```

Each worker takes 25 to 30 seconds to build the model. Then set an injector quad and request
OTR3's particles and image:

```bash
curl -s -X POST localhost:8000/api/v1/models/cu_hxr_staged/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"inputs": {"QUAD:IN20:525:BCTRL": -3.0}, "screen": "OTR3", "max_particles": 1000}'
```

## Repo layout

| Path | Contents |
| --- | --- |
| `lume_model_api/model/` | Loading, describing and evaluating a model. No HTTP. |
| `lume_model_api/api/` | The FastAPI app, worker pool, live hub, schemas and metrics. |
| `tests/` | Tests against the demo model. No torch, Bmad or EPICS needed. |
| `deploy/kubernetes/` | Manifests for the eval and live Deployments, Service and Ingress. |
| `scripts/` | `dump_openapi.py` regenerates `openapi.json`. `quad_scan_demo.py` and `check_poisoned_worker.py` exercise a running deployment. |
| `openapi.json` | The committed API contract that clients generate types from. |
