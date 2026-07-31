# LUME Live Stream Monitor — webapp (M1)

React/Vite/TS UI + FastAPI backend that replaces the marimo live-stream monitor.
Stateless `POST /api/evaluate` for the Interactive tab; read-only EPICS → `/evaluate`
→ **SSE** for the Live tab. One URL for all users (no `wN`, no allocator, no WS-relay).

M1 is single-user (one model instance, one asyncio lock). Concurrency/scale is M2+.

## Layout
- `backend/` — FastAPI app. `main.py` (endpoints), `source.py` (real vs mock factory),
  `mock_source.py` (synthetic frames), `schemas.py`, `serialize.py`.
- `frontend/` — React 19 + Vite + TS. Canvas beam image + phase-space scatter; uPlot
  timeseries + Twiss. `src/tabs/{LiveTab,InteractiveTab}.tsx`.
- `Dockerfile`, `deploy/kubernetes/` — image + single Deployment/Service/Ingress.
- `scripts/setup-dev-env.sh` — creates the pinned conda env for the real model.

## Endpoints
- `GET  /api/config` — screens, writable inputs (+ranges/defaults), scalars, version.
- `POST /api/evaluate` — `{screen, inputs}` → beam frame (image, scatter, scalars, Twiss).
- `GET  /api/machine-snapshot` — current input PVs (read-only) → dict.
- `GET  /api/live/stream?screen&period` — SSE frame stream (read-only EPICS driver).

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
Load the app at the trailing-slash URL: `https://<host>/lume-monitor/`.

> Deprecated by this app (retire once browser parity is confirmed):
> `lume_visualizations/live_stream_monitor.py`, `live_monitor_pool.py`,
> `deploy/kubernetes/live-monitor-ui/*`, `deploy/kubernetes/live-monitor.yaml`.
