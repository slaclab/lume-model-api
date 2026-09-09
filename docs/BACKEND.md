# lume-model-api — the API contract and how to run it

Stateless `POST /api/v1/evaluate` for request/response callers, and read-only EPICS to
`/api/v1/evaluate` to **SSE** for anything that wants a live view. One URL for every user and
every UI (no per-user `wN` instance, no allocator, no WS-relay).

Evaluates run on a pool of K subprocess model instances, K in parallel with no lock. Every
request is history-independent because the baseline merge happens in the source, so any
replica can serve any request.

## Layout
- `lume_model_api/api/` — FastAPI app. `main.py` (endpoints), `pool.py` (subprocess pool),
  `source.py` (real vs mock factory), `mock_source.py` (synthetic frames), `schemas.py`,
  `serialize.py`, `live_hub.py` (SSE fan-out), `metrics.py`.
- `lume_model_api/model/` — model / config / EPICS layer. `beam_monitor.py` (the evaluate
  core), `registry.py` (per-model screens, inputs, baseline), `config.py`,
  `epics_controls.py`, `fake_epics_ioc.py`.
- `Dockerfile`, `deploy/kubernetes/` — API-only image + the eval pool and live singleton.
- `scripts/setup-dev-env.sh` — creates the pinned conda env for the real model.
- `scripts/dump_openapi.py`, `openapi.json` — the generated API contract. See "The generated
  contract" below before editing `api/schemas.py`.

## Endpoints
- `GET  /api/config` — screens, writable inputs (+ranges/defaults), scalars, scan magnet.
- `POST /api/v1/evaluate` — `{screen, inputs, include_*}` to a beam frame. **The one evaluate
  endpoint**, see below.
- `GET  /api/machine-snapshot` — current input PVs (read-only) to a dict.
- `GET  /api/live/stream?screen=` — SSE frame stream (read-only EPICS driver).
- `GET  /metrics` — Prometheus. KEDA autoscales the eval pool on `lume_pool_inflight`.

`LUME_ROLE` selects what a process serves: `eval` (EPICS-free, scalable), `live` (the
singleton EPICS reader), or `all` (both, the default for dev and mock).

## One evaluate endpoint for every caller

`POST /api/v1/evaluate` is called by every UI and by programmatic clients such as notebooks
and emittance GUIs. There is deliberately **no** UI-private evaluate endpoint. A second shape
would mean every new UI reimplemented the unit handling, which is the duplication this design
removes. A UI is just a client that opts into all the outputs.

Conventions worth knowing before you write a client:

- **Units travel with the data.** Particle positions are µm and momenta eV/c, matching the
  µm-based scalars (`xrms_um`, `norm_emit_x_um_rad`). Every response states its own units in
  `distribution.units`, so read them rather than hard-coding. The model works internally in
  metres and the conversion happens once, in `_extract_distribution`.
- **Heavy outputs are opt-in.** Scalars always come back. Set `include_image`,
  `include_distribution` and `include_twiss` for the rest. Opt-in fields are present and
  `null` when not requested, **never absent**, which is what lets clients type them as
  always-present.
- **`max_particles` defaults to 3000, never the whole beam.** An omitted value gets the cap,
  not a multi-megabyte payload.
- **`distribution.coords` includes `weight`** (particle charge, in C) for physics callers.
  It is not a phase-space axis, so a UI plotting phase space should filter it out. That costs
  the live stream roughly 17% extra payload for data such a UI discards. Accepted
  deliberately: a distribution without weights is incomplete for the physics callers this
  endpoint serves, and an `include_weight` flag would add API surface to save something we
  have not measured as a problem. If it ever bites, lower `max_particles` on the live path
  rather than adding a flag.

The shape was reshaped once, when the old UI-private `/api/evaluate` was merged into it,
while it still had no consumers. From that commit on it is frozen and additive only, which
`tests/test_api_contract.py` enforces.

## The generated contract, after changing `api/schemas.py`

`openapi.json` at the repo root is dumped from the app and committed. It is the artifact
consumers generate their client types from, so a rename here becomes a compile error in their
build instead of a runtime surprise.

After editing a route or `api/schemas.py`:

```bash
python scripts/dump_openapi.py     # writes openapi.json
```

Commit the regenerated file. `.github/workflows/contract.yml` regenerates it on every pull
request and fails if it is stale.

Note what this does and does not buy you now that consumers live in other repos. This repo
guarantees the file is current. Whether a given consumer has *picked up* a change is enforced
in that consumer's CI, against the ref it pins. So a breaking rename here is silent until each
consumer refetches, which is the argument for pinning a ref rather than tracking `main`.

Two couplings are NOT covered by the generated schema:

- **The SSE stream.** `GET /api/live/stream` has no `response_model`, so OpenAPI says nothing
  about it. The event names `frame` and `error`, and the error payload `{"message": ...}`,
  have to be matched by hand in each client. What keeps a client's always-present assumption
  honest is that `frame_to_wire()` in `api/serialize.py` always emits every key, which
  `tests/test_wire_shape.py` enforces across every screen. **Keep it unconditional.** A
  dropped key does not change `openapi.json`, so no consumer can detect it by refetching.
- **`/api/v1/*`** itself. `tests/test_api_contract.py` asserts its field names by hand,
  because a regenerated snapshot would otherwise hide a breaking rename. Adding an optional
  field is fine. Renaming or removing one needs a `/api/v2` instead.

## Run — mock (no conda, no model)
The mock returns synthetic frames with the same schema. No torch or virtual-accelerator
needed, which is also why CI can run the full test suite.
```bash
pip install -e ".[dev]"
LUME_MOCK=1 uvicorn lume_model_api.api.main:app --port 8000
```

## Run — real model
```bash
bash scripts/setup-dev-env.sh                   # one-time: pinned conda env "lume-webapp"
conda run -n lume-webapp env \
  LCLS_LATTICE=$HOME/SLAC/lcls-lattice KMP_DUPLICATE_LIB_OK=TRUE \
  OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 TORCH_NUM_THREADS=2 \
  python -m uvicorn lume_model_api.api.main:app --port 8000
# Live stream against a fake IOC (no real EPICS needed), in the same env:
python -m lume_model_api.model.fake_epics_ioc
```
Model init takes ~25-30 s before `/api/config` returns 200.

## Build image / deploy
```bash
docker build -t ghcr.io/slaclab/lume-model-api:latest .    # context = repo root
kubectl apply -k deploy/kubernetes
```

Build for `linux/amd64` explicitly. A plain `docker build` on an arm64 Mac mislabels the
manifest and k8s rejects it with "no match for platform":

```bash
docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
  -t ghcr.io/slaclab/lume-model-api:<tag> --load .
docker image inspect ghcr.io/slaclab/lume-model-api:<tag> --format '{{.Os}}/{{.Architecture}}'
```

Then bump `newTag` in `deploy/kubernetes/kustomization.yaml` and re-apply. Rollback is the old
tag plus a re-apply. The live singleton has a ~30 s gap per deploy, the eval pool rolls with no
downtime.

Smoke-test the image before deploying, which catches model or env breakage without touching the
cluster:

```bash
docker run --rm --entrypoint python <img> -c \
  "from lume_model_api.api.source import get_source; s=get_source('cu_hxr_staged',mock=False); \
   f=s.snapshot(next(iter(s.screens))); print(f.xrms_um)"
```

> **Known-broken build, carried over from before the split.** The committed `VA_REF` is a
> virtual-accelerator revision that removed `virtual_accelerator.models.staged_model`, which
> `model/registry.py` still imports, so a from-scratch build does not produce a working image.
> The running production images were built from an earlier recipe (`VA_REF=77bbda8`,
> `LCLS_LATTICE_REF=52ad1a5`) plus pinned `bmad=20260731.0` and `pytao=1.2.1`, which is not in
> the repo. `conda install bmad pytao` here is also unpinned, so a fresh solve can pull a Bmad
> that rejects the `cu_hxr` `tao.init`. Fixing this means porting `registry.py` to the new
> virtual-accelerator API, and is deliberately separate from the repo split.

## Serving a UI

This image is API-only: `/` returns 404 unless a UI is supplied. Either bake one in, by
building `FROM ghcr.io/slaclab/lume-model-api:<tag>` and copying a build into
`/app/lume_model_api/static/`, or point `LUME_STATIC_DIR` at a directory at runtime. A UI repo
that bakes its own image should patch only the eval Deployment's image in a kustomize overlay,
leaving the live singleton on the plain API image.

Serving the UI from somewhere else entirely also works. CORS is open, which covers both `fetch`
and the `EventSource` stream, so a UI on another origin needs no change here.

The ingress routes by regex rather than by listing endpoints: the EPICS-touching paths
(`api/live/*`, `api/machine-snapshot`) go to the live singleton and everything else to the eval
pool. Adding or removing a route changes no routing rule.
