# lume-model-api

HTTP API over the SLAC `virtual_accelerator` staged model (LCLS CU injector / HXR line).
Send control-knob values, get beam output back: scalars always, plus an optional screen
image, particle distribution and Twiss.

This service ships **no UI**. It exists so that any number of UIs, notebooks and analysis
scripts can share one model service and one set of unit conventions, instead of each
reimplementing them. It was extracted from
[`lume-visualizations`](https://github.com/slaclab/lume-visualizations), which keeps the
React monitor that was its first consumer.

## Layout

- `lume_model_api/api/` — the FastAPI app. Endpoints, the subprocess model pool, the SSE
  broadcast hub, the wire serializer and the Prometheus metrics.
- `lume_model_api/model/` — the model / config / EPICS layer. Owns the physics, the screen
  and PV definitions, and the fake IOC. Imports torch and `virtual_accelerator` lazily, so
  mock mode and the tests need neither.
- `deploy/kubernetes/` — the eval pool, the live singleton, service, ingress and KEDA.
- `docs/BACKEND.md` — the API contract, run instructions and deploy notes. **Read this
  before writing a client.**

## The one evaluate endpoint

- `GET  /api/config` — screens, writable inputs with ranges and defaults, scalars, scan magnet.
- `POST /api/v1/evaluate` — `{screen, inputs, include_*}` to a beam frame.
- `GET  /api/machine-snapshot` — current input PVs, read-only.
- `GET  /api/live/stream?screen=` — SSE frame stream driven by live EPICS reads.
- `GET  /metrics` — Prometheus. KEDA autoscales the eval pool on `lume_pool_inflight`.

There is deliberately no second, UI-private evaluate endpoint. A second shape would mean
every new UI reimplemented the unit handling, which is the duplication this service removes.

Three conventions worth knowing before you write a client, all covered in detail in
`docs/BACKEND.md`:

- **Units travel with the data.** Positions are µm and momenta eV/c, matching the µm-based
  scalars. Every response states its own units in `distribution.units`, so read them rather
  than hard-coding.
- **Heavy outputs are opt-in and never absent.** Scalars always come back. `image`,
  `distribution` and `twiss` are `null` unless requested, never missing, which is what lets
  clients type them as always-present.
- **`max_particles` defaults to 3000,** never the whole beam.

`openapi.json` is committed at the repo root. Generate your client types from it at a pinned
git ref rather than hand-copying the shapes. CI here fails if that file goes stale.

## Run it

Mock mode needs no model, no torch and no EPICS, and returns synthetic frames with the same
schema. It is the right way to develop a client.

```bash
pip install -e ".[dev]"
LUME_MOCK=1 uvicorn lume_model_api.api.main:app --port 8000
```

For the real model, `bash scripts/setup-dev-env.sh` builds the pinned conda env, then see
`docs/BACKEND.md`. Model init takes 25 to 30 seconds before `/api/config` returns 200.

To exercise the live stream without the real machine, run the fake IOC in the same env:

```bash
lume-model-api-fake-ioc --list-pvs
./start-fake-epics-ioc.sh --update-period 0.5
```

It binds `127.0.0.1` and disables CA beacon broadcast by default, which avoids caproto's
`255.255.255.255` fallback failing on macOS and locked-down networks. For LAN-visible
discovery: `--interfaces 0.0.0.0 --broadcast-auto-beacons`.

## Serving a UI from this image

The image is API-only and `/` returns 404. Two ways to put a UI in front of it:

1. **Derive an image.** Build `FROM ghcr.io/slaclab/lume-model-api:<tag>` and copy your
   build into `/app/lume_model_api/static/`. The pinned base tag is an explicit contract.
2. **Point `LUME_STATIC_DIR`** at any directory at runtime.

Or serve the UI wherever you like and call the API cross-origin. CORS is already open.
