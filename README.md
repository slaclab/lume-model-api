# lume-visualizations

This repository contains two marimo dashboards built on top of the SLAC
`virtual_accelerator` staged model for the CU injector / HXR line.

## Apps

### Quad scan monitor

Path: `lume_visualizations/quad_scan_monitor.py`

This app runs a quadrupole scan on `QUAD:IN20:525:BCTRL` and lets you switch the
display between `OTR2`, `OTR3`, and `OTR4`.

- `OTR2` uses `input_beam` for the `x-px` phase-space plot and scalar PV outputs
	for `sigma_x`, `sigma_y`, `sigma_z`, `norm_emit_x`, and `norm_emit_y`.
- `OTR3` uses `OTR3_beam` and `OTRS:IN20:621:Image:ArrayData`.
- `OTR4` uses `OTR4_beam` and `OTRS:IN20:711:Image:ArrayData`.

### Live stream monitor

Path: `lume_visualizations/live_stream_monitor.py`

This app has two tabs:

- **Live monitoring** — continuously reads the staged model input PVs from EPICS,
  pushes them through the model, and plots scalar histories versus time. The
  timeseries x-axis shows Pacific time.
- **Interactive offline changes** — lets you set model inputs via number boxes
  and evaluate the model on demand. The dashboard updates reactively whenever
  you change a value. A **"Apply current machine values"** button fetches the
  current EPICS PV values and loads them into the input boxes.

The live and interactive tabs use **separate model instances** so they never
interfere with each other.

The EPICS input layer lives in `lume_visualizations/epics_controls.py` so it
can be swapped out without changing the marimo UI code.

### Fake EPICS IOC

Path: `lume_visualizations/fake_epics_ioc.py`

This is a standalone fake EPICS IOC for testing the live-streaming tab without
access to the real machine. It serves the same staged-model input PV names used
by the live monitor and updates them continuously with smooth synthetic motion.

## Local install

The existing local workflow uses the conda environment `va-dev-2`.

```bash
conda activate va-dev-2
export LCLS_LATTICE=/Users/smiskov/SLAC/lcls-lattice
pip install -e ../virtual-accelerator
pip install -e .
```

If you do not want to use a sibling checkout, `pyproject.toml` also pins a known
good `virtual_accelerator` revision from GitHub.

`caproto` is included so the fake IOC can be run from the same environment.

## Run locally

You can run the apps directly with marimo:

```bash
marimo run lume_visualizations/quad_scan_monitor.py
marimo run lume_visualizations/live_stream_monitor.py
```

Or via the packaged entry points:

```bash
lume-fake-epics-ioc --list-pvs
lume-quad-scan --host 127.0.0.1 --port 2718
lume-live-monitor --host 127.0.0.1 --port 2719
```

For the fake IOC specifically, there is also a repo-local launcher script:

```bash
./start-fake-epics-ioc.sh --update-period 0.5
```

By default, the fake IOC binds to `127.0.0.1` and disables CA beacon
broadcasting. This avoids caproto's fallback beacon target
`255.255.255.255`, which can fail on macOS and some locked-down networks.
If you need LAN-visible discovery, pass `--interfaces 0.0.0.0
--broadcast-auto-beacons` or set the `EPICS_CAS_*` server environment variables
explicitly.

## Manual fake-IOC test flow

To test the live streaming app against a local fake IOC instead of the machine:

1. Start the fake IOC in one terminal:

```bash
conda activate va-dev-2
./start-fake-epics-ioc.sh --update-period 0.5
```

2. In a second terminal, start the app:

```bash
conda activate va-dev-2
export LCLS_LATTICE=/Users/smiskov/SLAC/lcls-lattice
marimo run lume_visualizations/live_stream_monitor.py
```

The app automatically defaults to `EPICS_CA_ADDR_LIST=127.0.0.1` when no EPICS
environment variables are set, so no manual `export` is needed for a purely local
fake-IOC run. If you are in an environment that already has EPICS env vars
pointing elsewhere, override them explicitly before launching:

```bash
export EPICS_CA_AUTO_ADDR_LIST=NO
export EPICS_CA_ADDR_LIST=127.0.0.1
```

## Docker image

The included `Dockerfile` builds a runnable image that:

- installs this package,
- installs the pinned `virtual_accelerator` dependency from GitHub,
- clones the `lcls-lattice` repository into `/opt/lcls-lattice`, and
- defaults to serving the live monitor on port `2719` via `marimo run`.

Build and run it locally:

```bash
docker build -t lume-visualizations .
docker run --rm -p 2718:2718 -p 2719:2719 lume-visualizations
```

To run the live monitor in containerized test mode with the fake IOC started in
the same container:

```bash
docker run --rm -e LUME_START_FAKE_EPICS=1 -p 2719:2719 lume-visualizations
```

This automatically sets the EPICS client to use `127.0.0.1` inside the container,
launches the fake IOC, waits up to 15 s for it to become ready, then starts the
app. No additional `EPICS_CA_*` flags are needed.

To run the quad scan app instead:

```bash
docker run --rm -p 2718:2718 lume-visualizations \
	lume-quad-scan --host 0.0.0.0 --port 2718 --headless
```

## Kubernetes deployment (pool branch)

Kubernetes manifests live under `deploy/kubernetes/`.

### Architecture

The live monitor runs as a **5-worker StatefulSet** behind a lightweight
**allocator** that assigns each browser session to a dedicated worker. This is
required because marimo in `run` mode cannot handle multiple concurrent
sessions (torch double-load causes segfaults).

```
Browser → /live-monitor/ → nginx-ingress → Allocator (307 redirect)
Browser → /live-monitor/wN/ → nginx-ingress → Worker N (marimo directly)
```

Each worker runs `marimo run` with `--base-url /live-monitor/wN`. The allocator
tracks worker occupancy via heartbeats sent from client-side JavaScript.

### ConfigMap strategy

Some files are mounted via ConfigMap to allow rapid iteration without rebuilding
the Docker image:

| File | In ConfigMap? | Why |
|------|:---:|-----|
| `live_stream_monitor.py` | Yes | UI/dashboard code changes frequently |
| `live_stream_monitor.css` | Yes | Styling tweaks |
| `live_stream_monitor.head.html` | Yes | Session JS, heartbeat, error-recovery |
| `dashboard.py` | Yes | Plot layout, timezone, axis labels |
| `live_monitor_allocator.py` | Yes | Allocator routing logic |
| `beam_monitor.py` | No | Stable model interface, in Docker image |
| `config.py` | No | PV definitions, in Docker image |
| `epics_controls.py` | No | EPICS reader, in Docker image |

**Trade-off:** ConfigMap-mounted files can be updated with `kubectl apply -k .`
without a Docker rebuild (seconds vs minutes). The downside is the source must
be kept in sync between `lume_visualizations/` and `deploy/kubernetes/live-monitor-ui/`.
Files that rarely change stay in the Docker image for simplicity.

### Manifest files

| File | Purpose |
|------|---------|
| `namespace.yaml` | Creates the `lume-visualizations` namespace |
| `configmap.yaml` | Provides the `LCLS_LATTICE` path |
| `configmap-epics-fake.yaml` | EPICS config for the in-pod fake IOC |
| `configmap-epics-real.yaml` | EPICS config for the real CA gateway |
| `live-monitor.yaml` | Allocator Deployment, per-pod Services (w0–w4), StatefulSet |
| `ingress.yaml` | Per-worker paths + catch-all to allocator |
| `quad-scan.yaml` | Quad scan app Deployment |
| `quad-scan-ingress.yaml` | Ingress for the quad scan app |
| `kustomization.yaml` | Applies everything, ConfigMapGenerator, image tag override |

### Performance tuning

The Docker image container sees all host CPUs but is cgroup-limited to 2 cores.
Without thread pinning, torch and OpenMP spawn too many threads and contend
heavily. The StatefulSet sets `OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`,
`OPENBLAS_NUM_THREADS=2`, and `TORCH_NUM_THREADS=2` to match the CPU limit.
The app also calls `torch.set_num_threads()` at import time. Without this fix,
model evaluation takes ~5–10 s per shot; with it, ~180 ms.

### Switching between fake and real EPICS

Open `kustomization.yaml` and change the EPICS ConfigMap resource:

```yaml
resources:
  - configmap-epics-fake.yaml   # in-pod fake IOC (local/staging)
  # - configmap-epics-real.yaml # real facility CA gateway
```

### Deploy

```bash
cd deploy/kubernetes
kubectl apply -k .
kubectl rollout status statefulset/lume-live-monitor-worker -n lume-visualizations
```

Clean up stale ConfigMaps after deploy:

```bash
kubectl get configmaps -n lume-visualizations | grep live-monitor-ui
kubectl delete configmap -n lume-visualizations <old-hash>
```

### Changing worker count

1. In `live-monitor.yaml`: change `LUME_WORKER_COUNT`, StatefulSet `replicas`,
   add/remove per-pod Service blocks.
2. In `ingress.yaml`: add/remove `/live-monitor/wN` path entries.
3. Apply: `kubectl apply -k .`

## GitHub Actions

`.github/workflows/build-container.yml` builds the Docker image and pushes it to
GitHub Container Registry as `ghcr.io/slaclab/lume-visualizations` on pushes to
`main`, tags matching `v*`, and manual workflow dispatches.
