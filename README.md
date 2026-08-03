# lume-visualizations

Live-stream monitor for the SLAC `virtual_accelerator` staged model (LCLS CU
injector / HXR line).

- **The app** lives in [`webapp/`](webapp/README.md) — a React/Vite + FastAPI
  single-URL service (Live + Interactive tabs). Run/build/deploy instructions are in
  `webapp/README.md`.
- **This package (`lume_visualizations/`)** is the model/config/EPICS layer the webapp
  imports:
  - `beam_monitor.py` — `ModelImageSource.snapshot()` → `BeamFrame` (the evaluate core).
  - `registry.py` — `ModelSpec` / `MODEL_REGISTRY` (per-model screens, inputs, baseline).
  - `config.py` — screen + input PV definitions.
  - `epics_controls.py` — read-only EPICS input reader.
  - `fake_epics_ioc.py` — synthetic IOC for local testing without the real machine.

> **History:** the earlier **marimo** live-stream monitor and **quad-scan** apps — and
> their heartbeat allocator + per-worker `wN` Kubernetes deploy — were retired in favor
> of the webapp. See the git history if you need them.

## Install (working on the model layer)

The heavy model (`virtual_accelerator` + torch + Bmad) is installed separately — see
`webapp/scripts/setup-dev-env.sh` for the pinned env used to run the real model. For
just this package:

```bash
export LCLS_LATTICE=/path/to/lcls-lattice
pip install -e .
```

`caproto` (fake IOC), `numpy`, and `pyepics` come with it.

## Fake EPICS IOC

Serves the staged-model input PV names with smooth synthetic motion, so the webapp's
**Live** tab can be exercised without the real machine:

```bash
lume-fake-epics-ioc --list-pvs
./start-fake-epics-ioc.sh --update-period 0.5
```

It binds `127.0.0.1` and disables CA beacon broadcast by default (avoids caproto's
`255.255.255.255` fallback, which fails on macOS / locked-down networks). For
LAN-visible discovery: `--interfaces 0.0.0.0 --broadcast-auto-beacons`.
