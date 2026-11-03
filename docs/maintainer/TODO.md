# To do

Open work on the service, ordered by what blocks the next deploy first. Each item says what's
left and how to confirm it's done. Remove an item when its change merges.

## Demo deploy (in progress, 2026-11-03)

The goal is a dev deployment that streams without the live pod restarting. `cu_hxr_staged`
can't run to `TD11` on live machine values until virtual-accelerator fixes the bend PVs (next
section), so the demo tracks a shorter beamline.

1. **Bump and pin the Python stack.** Done 2026-11-03. `pyproject.toml`'s `va` extra pins
   virtual-accelerator `9e65670`, lume-bmad `0.1.2` and torch `2.15.0`. The Dockerfile pins
   lcls-lattice `48c907d` and the same torch. The surrogate packages can't be pinned (see
   [Unpinned dependencies](#unpinned-dependencies)). bmad, pytao, fastapi and pydantic stay at
   their tested versions, pinned in the Dockerfile (see
   [Deferred pin bumps](#deferred-pin-bumps)). The stack installed cleanly in a fresh environment
   on bmad `20261023.0` and pytao `1.3.3`.
2. **End the model at `OTR3`.** `LUME_MODELS` in both `deploy/kubernetes/deployment.yaml` and
   `deployment-live.yaml` sets `end_element` to `OTR3`. The model then has 11 inputs and no bend
   in range, so the live pod reads no `BEND:*` PV. The first bend past `OTR3` is
   `BEND:IN20:661` (BX01), between `OTR3` and `OTR4`. Its live `BCTRL` (0.000166 GeV/c, against
   a design of 0.135) moves the beam about 158 mm off `OTR4`, so any span past `OTR3` also needs
   `LUME_LIVE_INPUTS` set to every input except `BEND:IN20:661:BCTRL`. At `OTR3` that id isn't a
   model input, and `LUME_LIVE_INPUTS` would only log it as unknown at startup, so the manifests
   don't set it. Done in the manifests 2026-11-03, and live once
   `GET /api/v1/models/cu_hxr_staged/config` lists screens `OTR2` and `OTR3`.
3. **Verify locally before building.** Run `describe` and `evaluate` on the pinned stack with
   live values from `GET .../machine-snapshot` (that route reads PVs and doesn't run the model).
   Run `pytest` and `python scripts/dump_openapi.py` in a plain venv, as CI does. Done
   2026-11-03: `describe` at `OTR3` gives 11 inputs and screens `OTR2` and `OTR3`, an evaluate on
   the live values returned all 1000 particles at `OTR3`, 233 tests pass, and `openapi.json` is
   unchanged.
4. **Build, push, deploy and smoke-test.** Build for amd64, run the container locally, and
   check `/config` and `/evaluate` (see [Build the image](DEPLOY.md#build-the-image)). Push a
   dated tag as well as `dev`, so a rollback is a tag change. The image deployed before this bump
   is digest `sha256:cf5a641b590f7329bb4363fecf63e394e9ff3c56299e563bdc3da27153c26785`. Apply
   both Deployments and stream for several minutes. Done when the live pod stays `1/1` with no
   new restarts.

## Blocked on virtual-accelerator

- **Bend PVs.** virtual-accelerator maps both `BEND:*:BCTRL` and `BEND:*:BDES` to
  `SBendBCTRLVariable` and treats the value as momentum in GeV/c. On the machine `BCTRL` is a
  "B-Perturb" trim that is stale on four of the six bends, and the BC1 bends (`BEND:LI21:*`) are
  in kG-m. Any live bend value makes Bmad fail with `UNSTABLE ORBIT` at BC1, which latches the
  worker and restarts the pod. The maintainer plans to make every bend PV read-only and set the
  bends internally by phase matching. Once that lands, bump the pin and check that
  `GET .../config` lists no `BEND:*` id under `inputs`. Each read-only class has to declare
  `read_only: bool = True` itself, because mixing in `ReadOnlyActionMixin` alone leaves the flag
  `False` (see
  [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#output_beam-and-writable-non-scalars-are-not-published)).
- **Restore the full beamline.** After the bend fix, set `end_element` back to `TD11` in both
  Deployments. Bend ids leave the input list, so clients that send them in `evaluate` get
  a 400.
- **L1S klystron PVs.** `KLYS:LI21:11:PDES` ("Desired PAD SLED Phase") and
  `KLYS:LI21:11:ENLD` ("Accepted no load energy gain") drive the L1S overlay `K21_1`. Live they
  read -24.5 deg and 213.9 MeV against a design of 0 deg and 111.6 MeV, which gives the wrong
  energy into BC1 without raising an error. The beam-relevant setpoints may be
  `ACCL:LI21:1:L1S_PDES` and `ACCL:LI21:1:L1S_ADES` (not verified). This also matters for phase
  matching, which needs the right L1S settings to set the bends.
- **Quad and corrector `BCTRL`.** `QUAD:LI21:301:BCTRL` also has DESC "B-Perturb". Live quad and
  corrector `BCTRL` values are close to their defaults today, so nothing fails, but they haven't
  been compared against `BDES`.

## Deferred pin bumps

- **bmad and pytao.** The Dockerfile pins bmad `20261023.0` and pytao `1.3.3`, the pair tested on
  2026-11-03. The image deployed before this bump has bmad `20260904.1` and pytao `1.2.4`. Latest
  on 2026-11-03 is bmad `20261031.0` and pytao `1.4.0`. Bump both build args after testing the new
  pair against the `va` extra.
- **fastapi and pydantic.** CI and the Dockerfile pin fastapi `0.141.1` and pydantic `2.13.4`.
  Latest on 2026-11-03 is `0.162.0` and `2.14.2`. Bumping them can change `openapi.json` and the
  contract tests, which the frontend pins (see [CHANGING_THE_API.md](CHANGING_THE_API.md)), and
  both files have to move together.

## Unpinned dependencies

- **Injector surrogate packages.** virtual-accelerator requires `lcls-cu-inj-model` and
  `facet2-inj-ml-model` as git URLs with no commit, and both require `lume-torch` and `distgen`
  the same way. pip treats a pinned URL for one of those packages as a conflict, so they install
  from their default branch at build time. On 2026-11-03 that resolved to lcls-cu-inj-model
  `95b052b`, facet2-inj-ml-model `fc07f06`, lume-torch `3a37796` (tag `v3.0.0`) and distgen
  `d856bc1`. The Dockerfile's `pip freeze` line prints them on each build. Pinning them needs
  commit pins in virtual-accelerator and the surrogate repos, or a constraints file that pins the
  same URLs.
- **The rest of the API stack.** uvicorn, sse-starlette and starlette aren't pinned anywhere, so
  a rebuild installs the latest. On 2026-11-03 that was uvicorn `0.54.0` and sse-starlette `3.6.0`,
  against `0.53.0` and `3.4.11` in the image deployed before this bump.

## Deployment config

- **One `LUME_MODELS` for both Deployments.** `deployment.yaml` and `deployment-live.yaml` each
  set `LUME_MODELS`, and nothing checks they match. The eval pods serve `/config` and the live pod
  serves the stream, so if they drift `/config` can list a screen that `/live/stream` rejects with
  a 400. Move the value into a ConfigMap that both reference (as `configmap-epics.yaml` does for
  the EPICS settings), or add a test that loads both manifests and compares the two values.
