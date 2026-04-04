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

This app has two tabs.

- `Live monitoring` continuously reads the staged model input PVs from EPICS,
	pushes them through the model, and plots scalar histories versus time.
- `Interactive offline changes` replaces the EPICS input stream with manual
	sliders for the requested injector controls and keeps a fake time stream going
	even while the user is idle.

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

2. In a second terminal, point the EPICS client at the local IOC and start the app:

```bash
conda activate va-dev-2
export LCLS_LATTICE=/Users/smiskov/SLAC/lcls-lattice
export EPICS_CA_AUTO_ADDR_LIST=NO
export EPICS_CA_ADDR_LIST=127.0.0.1
marimo run lume_visualizations/live_stream_monitor.py
```

That leaves the marimo app unchanged; only the PV source changes.

## Docker image

The included `Dockerfile` builds a runnable image that:

- installs this package,
- installs the pinned `virtual_accelerator` dependency from GitHub,
- clones the `lcls-lattice` repository into `/opt/lcls-lattice`, and
- defaults to serving the live monitor on port `2719`.

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

This test mode automatically sets the EPICS client to use `127.0.0.1` inside the
container and launches `lume_visualizations/fake_epics_ioc.py` before starting
the app.

To run the quad scan app instead:

```bash
docker run --rm -p 2718:2718 lume-visualizations \
	lume-quad-scan --host 0.0.0.0 --port 2718 --headless
```

## Kubernetes

Kubernetes manifests live under `deploy/kubernetes`.

- `namespace.yaml` creates the `lume-visualizations` namespace.
- `configmap.yaml` provides the `LCLS_LATTICE` path used in the container.
- `quad-scan.yaml` deploys the quad scan app at `/quad-scan`.
- `live-monitor.yaml` deploys the live monitor app at `/live-monitor`.
- `ingress.yaml` routes both apps through a single hostname.
- `kustomization.yaml` applies the full stack.

Apply everything with:

```bash
kubectl apply -k deploy/kubernetes
```

Before deploying, update the image tag in the manifests and replace the ingress
host `lume-visualizations.example.org` with your real hostname.

## GitHub Actions

`.github/workflows/build-container.yml` builds the Docker image and pushes it to
GitHub Container Registry as `ghcr.io/slaclab/lume-visualizations` on pushes to
`main`, tags matching `v*`, and manual workflow dispatches.
