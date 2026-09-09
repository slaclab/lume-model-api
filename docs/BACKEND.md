# LUME Live Stream Monitor — webapp (M1)

React/Vite/TS UI + FastAPI backend that replaces the marimo live-stream monitor.
Stateless `POST /api/v1/evaluate` for the Interactive tab, and read-only EPICS →
`/api/v1/evaluate` → **SSE** for the Live tab. One URL for all users (no `wN`, no
allocator, no WS-relay).

M1 is single-user (one model instance, one asyncio lock). Concurrency/scale is M2+.

## Layout
- `backend/` — FastAPI app. `main.py` (endpoints), `source.py` (real vs mock factory),
  `mock_source.py` (synthetic frames), `schemas.py`, `serialize.py`.
- `frontend/` — React 19 + Vite + TS. Canvas beam image + phase-space scatter; uPlot
  timeseries + Twiss. `src/tabs/{LiveTab,InteractiveTab}.tsx`.
- `Dockerfile`, `deploy/kubernetes/` — image + single Deployment/Service/Ingress.
- `scripts/setup-dev-env.sh` — creates the pinned conda env for the real model.
- `scripts/dump_openapi.py`, `openapi.json` — the generated API contract. See "Generated
  types" below before editing `backend/schemas.py`.

## Endpoints
- `GET  /api/config` — screens, writable inputs (+ranges/defaults), scalars, scan magnet.
- `POST /api/v1/evaluate` — `{screen, inputs, include_*}` → beam frame. **The one evaluate
  endpoint**, see below.
- `GET  /api/machine-snapshot` — current input PVs (read-only) → dict.
- `GET  /api/live/stream?screen&period` — SSE frame stream (read-only EPICS driver).

## One evaluate endpoint for every caller

`POST /api/v1/evaluate` is called by this app's own web UI, by any other UI, and by
programmatic clients such as notebooks and emittance GUIs. There is deliberately **no**
UI-private evaluate endpoint. A second shape would mean every new UI reimplemented the
unit handling, which is the duplication this design removes. The UI is just a client that
opts into all the outputs.

Conventions worth knowing before you write a client:

- **Units travel with the data.** Particle positions are µm and momenta eV/c, matching the
  µm-based scalars (`xrms_um`, `norm_emit_x_um_rad`). Every response states its own units in
  `distribution.units`, so read them rather than hard-coding. The model works internally in
  metres and the conversion happens once, in `_extract_distribution`.
- **Heavy outputs are opt-in.** Scalars always come back. Set `include_image`,
  `include_distribution` and `include_twiss` for the rest. Opt-in fields are present and
  `null` when not requested, never absent, which is what lets the frontend type them as
  `Required<>`.
- **`max_particles` defaults to 3000, never the whole beam.** An omitted value gets the cap,
  not a multi-megabyte payload.
- **`distribution.coords` includes `weight`** (particle charge, in C) for physics callers.
  It is not a phase-space axis, so the UI strips it in `unpackFrame` before the scatter
  plot. That costs the live stream roughly 17% extra payload for data the UI discards.
  Accepted deliberately: a distribution without weights is incomplete for the physics
  callers this endpoint serves, and an `include_weight` flag would add API surface to save
  something we have not measured as a problem. If it ever bites, lower `max_particles` on
  the live path rather than adding a flag.

The shape was reshaped once, when the old UI-private `/api/evaluate` was merged into it,
while it still had no consumers. From that commit on it is frozen and additive only, which
`tests/test_api_contract.py` enforces.

## Generated types, after changing `backend/schemas.py`

The frontend no longer hand-copies the backend wire shapes. `webapp/openapi.json` is dumped
from the app, and `frontend/src/api/schema.d.ts` is generated from that, so renaming a backend
field becomes a TypeScript compile error instead of a runtime surprise in the browser. Both
files are committed, because Dockerfile stage 1 builds the frontend with Node only and has no
Python available to generate them.

After editing a route or `backend/schemas.py`:

```bash
python webapp/scripts/dump_openapi.py    # writes webapp/openapi.json
cd webapp/frontend && npm run gen:api    # writes src/api/schema.d.ts
npm run build                            # tsc reports anything that no longer fits
```

Commit both regenerated files. `.github/workflows/contract.yml` regenerates them on every
pull request and fails if they are stale.

Two couplings are NOT covered by the generated types:

- **The SSE stream.** `GET /api/live/stream` has no `response_model`, so OpenAPI says nothing
  about it. The event names `frame` and `error`, and the error payload `{"message": ...}`, are
  matched by hand in `frontend/src/api/client.ts`. What keeps the frontend's
  `Required<EvaluateV1Response>` honest on that path is that `frame_to_wire()` in
  `backend/serialize.py` always emits every key, which `tests/test_wire_shape.py` enforces
  across every screen. Keep it unconditional.
- **`/api/v1/*`**, the contract for the UI and for notebooks and GUIs alike.
  `tests/test_api_contract.py` asserts its field names by hand, because a regenerated snapshot
  would otherwise hide a breaking rename. Adding an optional field is fine. Renaming or
  removing one needs a `/api/v2` instead.

## Run — mock (no conda, fast UI iteration)
The mock returns synthetic frames with the same schema; no torch/VA needed.
```bash
# backend on :8000
LUME_MOCK=1 python -m uvicorn webapp.backend.main:app --port 8000
# frontend dev server (proxies /api → :8000)
cd webapp/frontend && npm install && npm run dev
```

## Run — real model
```bash
bash webapp/scripts/setup-dev-env.sh            # one-time: pinned conda env "lume-webapp"
conda run -n lume-webapp env \
  LCLS_LATTICE=$HOME/SLAC/lcls-lattice KMP_DUPLICATE_LIB_OK=TRUE \
  OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 TORCH_NUM_THREADS=2 \
  python -m uvicorn webapp.backend.main:app --port 8000
# Live tab against a fake IOC (no real EPICS needed):
python -m lume_visualizations.fake_epics_ioc            # in the same env
```
Model init takes ~25-30 s before `/api/config` returns 200.

## Build image / deploy
```bash
docker build -f webapp/Dockerfile -t ghcr.io/slaclab/lume-monitor:latest .   # context = repo root
kubectl apply -k webapp/deploy/kubernetes                                      # single URL, no wN
```
Load the app at the trailing-slash URL: `https://<host>/live-monitor/`.

## Deploying the frontend separately

The image serves the SPA itself, but nothing requires that. `VITE_API_URL` points the build at
any backend host, CORS is already open (which covers both `fetch` and the `EventSource`
stream), and the backend serves no static files when neither `backend/static/` nor
`frontend/dist/` exists, so it runs as a pure API.

```bash
cd webapp/frontend
VITE_API_URL=https://host/live-monitor npm run build   # dist/ is now standalone
```

Leave `VITE_API_URL` unset to get the default `'.'`, which is the same-origin case the image
uses today.

> Deprecated by this app (retire once browser parity is confirmed):
> `lume_visualizations/live_stream_monitor.py`, `live_monitor_pool.py`,
> `deploy/kubernetes/live-monitor-ui/*`, `deploy/kubernetes/live-monitor.yaml`.
