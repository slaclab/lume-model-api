# Migrating `lume-visualizations` to this backend

`lume-visualizations` still carries the original backend under `webapp/backend/` and the
original model layer under `lume_visualizations/`, and its React UI was written against that
backend's API. This service replaced it with a model-agnostic API, and the shapes changed. This
document lists every difference, keyed to the files in `lume-visualizations` that depend on it,
so the UI and its pipeline can be ported in one pass.

Line numbers refer to `lume-visualizations` at the time of the split (2026-09-09). The frontend
lives under `webapp/frontend/src/`.

## What to delete from `lume-visualizations`

All of this now lives here and should not be maintained twice:

| Remove | Replaced by |
| --- | --- |
| `webapp/backend/` (FastAPI app, pool, hub, schemas, serializer, mock source) | `lume_model_api/api/` |
| `lume_visualizations/beam_monitor.py`, `config.py`, `registry.py`, `epics_controls.py` | `lume_model_api/model/` |
| `lume_visualizations/fake_epics_ioc.py`, `start-fake-epics-ioc.sh`, the `lume-fake-epics-ioc` console script in `pyproject.toml` line 20 | Nothing. `LUME_LIVE_SOURCE=synthetic` drives the live view without EPICS. |
| `webapp/scripts/dump_openapi.py`, `webapp/openapi.json` | `openapi.json` at this repo's root |
| `tests/test_api_contract.py`, `tests/test_wire_shape.py` (they import `webapp.backend`) | The same tests here |
| `webapp/Dockerfile` stage 2 (Python runtime) | This repo's `Dockerfile`. Keep only the Node build stage, see "Building the image" below. |
| `webapp/deploy/kubernetes/`, the backend parts | `deploy/kubernetes/` here, renamed to `lume-model-api-*` in namespace `lume-model-api` so both can run during the port. Keep the old manifests until the cut-over, then delete the backend Deployments and keep UI manifests of your own. |
| The `LUME_MOCK` env var everywhere (`webapp/README.md` line 95, `webapp/backend/source.py` line 20) | `LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic` |

The `lcls-lattice` path logic and `LCLS_LATTICE` default in `lume_visualizations/config.py` are
gone too. The model factory in virtual-accelerator reads `LCLS_LATTICE` itself and raises a
clear error when it is unset.

## Regenerating the TypeScript client

`webapp/frontend/package.json` line 9:

```
"gen:api": "npx -y openapi-typescript@7.13.0 ../openapi.json -o src/api/schema.d.ts"
```

Point it at this repo's `openapi.json` at a pinned git ref, not at a local sibling checkout:

```
"gen:api": "npx -y openapi-typescript@7.13.0 https://raw.githubusercontent.com/slaclab/lume-model-api/<sha>/openapi.json -o src/api/schema.d.ts"
```

Bump `<sha>` deliberately. The drift check in `.github/workflows/contract.yml` should then
compare against that same URL rather than regenerating a local backend.

After regenerating, the compiler will point at every place below.

## Every path now carries a model name

The new backend can host several models in one process, so every route is prefixed with the
model's URL name and there is no default model:

| Old path | New path |
| --- | --- |
| none | `GET /api/v1/models` |
| `GET /api/config` | `GET /api/v1/models/{name}/config` |
| `POST /api/v1/evaluate` | `POST /api/v1/models/{name}/evaluate` |
| `GET /api/machine-snapshot` | `GET /api/v1/models/{name}/machine-snapshot` |
| `GET /api/live/stream` | `GET /api/v1/models/{name}/live/stream` |

The old unprefixed paths return 404. `GET /api/v1/models` returns
`[{name, description, version}]` sorted by name and is what a model dropdown should read. The UI
then keeps the chosen `name` in state and prefixes every other call with it. `config.model` in
`/api/v1/models/{name}/config` echoes that same `name`, so it is the key to match the dropdown
against. Do not hard-code `cu_hxr_staged` anywhere: against a local demo server the only model
is `demo`, and the UI must work there unchanged.

In `client.ts` this means one new `listModels()` call at mount and a `modelName` argument (or a
module-level setting) on every existing call. The sections below describe each endpoint's body,
which changed independently of the path.

## `GET /api/v1/models/{name}/config` (was `GET /api/config`)

Called once at mount in `src/api/client.ts` line 66 and consumed in `App.tsx`,
`InteractiveTab.tsx` and `LiveTab.tsx`. The k8s probes target `GET /healthz`.

| Old field | New field | Notes |
| --- | --- | --- |
| `model` | `model` | Now the URL name you asked for, so it matches the dropdown key. |
| `version` | `version` | Unchanged in meaning. The demo reports `demo`. |
| `screens[].key` | `screens[].key` | Unchanged. Derived from the model now: a `{key}_beam` `ParticleGroupVariable`. |
| `screens[].label` | none | Use `key`. The model has no separate label. |
| `screens[].has_image` | `screens[].image !== null` | `image` is the output id of the image array, or `null`. |
| `inputs[].id`, `.min`, `.max`, `.default`, `.unit` | Same names | `unit` is now the model's unit (`kG`, `kG*m`, `deg`), not `""`. |
| `inputs[].label` | none | Use `id`. |
| none | `inputs[].range_source` | `"model"` or `"derived"`. Bmad-side magnets report `derived`. A slider may want to show a derived range as a hint rather than a hard limit. |
| none | `inputs[].constant` | `true` for inputs the model pins. Do not render a slider for these. |
| `scalars[]` | none | There is no fixed scalar list. Beam statistics come back inside each `particles` output as `stats` (see evaluate). |
| `scan_pv` | none | The quad-scan magnet is no longer known to the backend. See "The quad scan" below. |
| none | `outputs[]` | Every read-only variable with `id`, `kind`, `unit`, `shape`, `element_name`. Use it to find Twiss arrays and any scalar PV the UI wants to plot. |
| none | `description` | Free text from the model. |

Screen selection seeding at `LiveTab.tsx` line 29 and `InteractiveTab.tsx` line 35 currently
does `config.screens.find((s) => s.has_image)?.key`. Change to `s.image !== null`.

## `POST /api/v1/models/{name}/evaluate` (was `POST /api/v1/evaluate`)

`src/api/client.ts` lines 71 to 92 send
`{screen, inputs, include_image: true, include_distribution: true, include_twiss: true, max_particles: 3000}`
and `unpackFrame` (lines 42 to 63) reads the response.

### Request

| Old | New |
| --- | --- |
| `screen` | `screen` (still a shorthand, appends that screen's particles and image ids) |
| `inputs` | `inputs`, numbers only |
| `include_image`, `include_distribution` | Gone. Asking for a `screen` returns its particles and image. |
| `include_twiss` | Gone. Add the Twiss array ids to `outputs`, found in `config.outputs` by `kind === "array"` and `shape.length === 1`. For the LCLS Bmad models they are `s`, `x.beta`, `y.beta`. |
| `max_particles` | `max_particles`, same default of 3000 |
| none | `outputs: string[]`, any output ids. Combine freely with `screen`. |
| none | `smooth_images_sigma_px`. The old backend always applied a 1 px Gaussian to screen images (`beam_monitor.py` lines 45 to 53). That is now opt-in. Send `1.0` to keep the old look. |

So the old call becomes

```ts
{
  screen,
  inputs,
  outputs: twissIds,          // from config.outputs, e.g. ["s", "x.beta", "y.beta"]
  max_particles: 3000,
  smooth_images_sigma_px: 1.0,
}
```

### Response

The response is `{model, version, timestamp, frame_index, inputs, outputs}` where `outputs` is
keyed by the ids you asked for and each entry declares a `kind`. Mapping onto the old `Frame`:

| Old field | Where it is now |
| --- | --- |
| `screen` | Not echoed. You know which screen you asked for. |
| `screen_label` | Gone, use the key. |
| `frame_index`, `timestamp` | Unchanged, top level. |
| `image_message`, `image_caption` | Gone. When a screen has `image: null` in config there is no image output at all, and the UI decides what to say. `image_caption` was an echo of the request. |
| `image.shape`, `image.dtype`, `image.data_b64` | `outputs[config.screen.image]`, an `array` kind with the same three fields plus `unit`. Present only when the screen has an image. |
| `distribution.coords`, `distribution.units`, `distribution.n` | `outputs[config.screen.particles]`, a `particles` kind with `coords`, `units`, `n`. Same base64 float32 encoding. `weight` is still present and still not a plot axis, so keep the `NON_AXIS_COORDS` filter at `client.ts` line 40. |
| `scalars.xrms_um` | `outputs[particles].stats.sigma_x`, in metres |
| `scalars.yrms_um` | `stats.sigma_y`, in metres |
| `scalars.sigma_z_um` | `stats.sigma_z`, in metres |
| `scalars.norm_emit_x_um_rad` | `stats.norm_emit_x`, in metres (m rad) |
| `scalars.norm_emit_y_um_rad` | `stats.norm_emit_y`, in metres |
| none | `stats.mean_energy` (eV), `stats.charge` (C), and `stats_units` naming every unit |
| `twiss.s`, `twiss.beta_x`, `twiss.beta_y` | `outputs["s"]`, `outputs["x.beta"]`, `outputs["y.beta"]`, each an `array` kind. They are base64 float32 now rather than JSON lists, so decode them like the image. |
| none | `inputs`: the effective post-baseline-merge control values. Useful for showing what the baseline filled in. |

### Units changed from µm to metres

The old backend converted positions to µm server side (`beam_monitor.py` lines 24 to 31 and
253 to 258) and named the scalars `*_um`. The new one ships the model's native units, which for
a `ParticleGroup` are metres and eV/c, and declares them in `units` and `stats_units`. The UI
now owns the display conversion. Places to change:

- `ScalarTimeseries.tsx` lines 65 to 66 hard-code the axis labels `'RMS size (µm)'` and
  `'Norm. emit (µm·rad)'`. Either scale the incoming metres by 1e6 and keep the labels, or build
  the labels from `stats_units`.
- The `Scalars` type keys in `types.ts` (`xrms_um`, ...) go away. Rename to the `stats` keys.
- `BeamImage.tsx` line 113 mentions µm pixel pitch in a tooltip. The image array itself is
  unchanged in meaning (counts per pixel), only the pixel size text is a UI constant.
- Scatter-plot axis labels already come from `distribution.units` at runtime and need no change
  beyond reading `outputs[particles].units`.

Note that `test_distribution_positions_are_micrometres` in the old wire test was checking for a
conversion that no longer exists.

### Image scaling

`imageScale.ts` assumes float32 images with arbitrary non-negative values, which is still true.
Two things changed underneath it:

- The old backend peak-normalized every image to 1.0 after the PSF blur. The new one does not,
  with or without `smooth_images_sigma_px`. `robust` and `fixed` modes are unaffected. If `auto`
  mode assumed a [0, 1] range, it now sees raw counts.
- Downsampling to `LUME_MAX_IMAGE_DIM` (512) still happens, so `shape` is the shape after
  downsampling, as before.

## `GET /api/v1/models/{name}/machine-snapshot` (was `GET /api/machine-snapshot`)

`client.ts` lines 94 to 99 read `data.inputs` and merge it into slider state at
`InteractiveTab.tsx` line 94. The shape is unchanged: `{inputs: {id: number}}`. Two behaviour
changes:

- It now returns every non-constant input, overlaid on the baseline, so an input with no PV
  behind it reports its default instead of being absent.
- In demo mode (`LUME_LIVE_SOURCE=synthetic`) the values wiggle around the defaults rather than
  sitting at them.

## `GET /api/v1/models/{name}/live/stream` (was `GET /api/live/stream`)

`client.ts` lines 101 to 123 open an `EventSource` on `?screen=`, listen for `frame` and
`error`, and parse `frame` data as the evaluate response. All of that still holds, with these
changes:

- The path now includes the model name, so the `EventSource` URL is built from the chosen
  model like every other call.
- The `screen` query default of `OTR4` (`main.py` line 199) is gone. `screen` or `outputs` is
  required and the request is a 400 without one.
- To get Twiss in the stream, add `&outputs=s,x.beta,y.beta` (comma-separated).
- A `frame` event's data is the new response shape, so pass it through the same unpack function
  as the HTTP evaluate.
- The `error` payload is still `{"message": string}`.
- Screen images in the stream are not smoothed. There is no per-stream smoothing option, so a UI
  that wants the old look should apply the blur client side or accept raw counts.

## The quad scan

`InteractiveTab.tsx` line 107 reads `config.scan_pv` to pick the magnet to sweep, and the old
backend hard-coded it as `QUAD:IN20:525:BCTRL` in `registry.py` line 54. The new backend has
no concept of a scan magnet. Options, in order of preference:

1. Let the user pick any input from `config.inputs` as the scan variable, defaulting to the
   first one whose `id` starts with `QUAD:` if present.
2. Make it a frontend build-time or runtime setting (`VITE_SCAN_PV`).

Do not reintroduce it into the backend. It is a UI concern about one accelerator.

## Building the image and deploying

The old `webapp/Dockerfile` built the frontend in stage 1 and copied `dist/` into the Python
image at `webapp/backend/static/` (line 89). The Python stage is gone, and the UI should not be
baked into this repo's image either. **Make the UI its own static nginx Deployment serving
`dist/`.** Keep only the Node build stage of the old Dockerfile and copy its output into an nginx
base. That way a UI release does not restart the model pods, the UI does not have to track an API
tag, and the two repos deploy independently. `docs/DEPLOY.md` sketches the Deployment and the
Ingress rule under "Cutting over from the old monolith".

The API's own image is `ghcr.io/slaclab/lume-model-api` (`kustomization.yaml` pinned
`ghcr.io/slaclab/lume-monitor:n8` before the split), and no tag exists under the new name yet:
the build is unverified since the virtual-accelerator ref was bumped (see `docs/DEPLOY.md`).

Both Deployments now need `LUME_MODELS` (a JSON object naming the hosted models, already set to
`cu_hxr_staged` in this repo's manifests), and the live one needs `LUME_LIVE_SOURCE=epics`.
Adding a model to the dropdown is adding a key to that JSON on both Deployments and raising
their memory, see `docs/DEPLOY.md`. The EPICS ConfigMap is unchanged in content and renamed to
`lume-model-api-epics-config` along with everything else in `deploy/kubernetes/`.

## The URLs after the cut-over

The UI keeps its public path. The API moves to its own.

| What | Where |
| --- | --- |
| The ported UI | `https://ard-modeling-service.slac.stanford.edu/live-monitor/` |
| This API | `https://ard-modeling-service.slac.stanford.edu/lume-model-api` |
| The model list | `https://ard-modeling-service.slac.stanford.edu/lume-model-api/api/v1/models` |

So **`VITE_API_URL` must be the absolute path `/lume-model-api`**, not the `'.'` (same origin) it
defaults to in `client.ts` line 9 and not a full URL. Same host, so no CORS is involved at all,
and an absolute path means the UI's own prefix cannot leak into its API calls the way a relative
base would. The client appends `/api/v1/models/...` to it as before.

The API objects live in their own namespace with their own names, so this API and the old
monolith can run at once. `/live-monitor` keeps serving the old API and the old UI throughout the
port, and `GET /live-monitor/api/config` still returns `scalars` and `has_image` and no
`outputs` until the old backend pods are deleted. Develop the port against `LUME_MODELS=demo`
locally, deploy the new API first, verify `/lume-model-api/api/v1/models`, then swap the UI, then
delete the old backend Deployments. `docs/DEPLOY.md` has the full sequence and the rollback.

## Local development

Old: `LUME_MOCK=1 python -m uvicorn webapp.backend.main:app --port 8000` plus optionally the
fake IOC.

New, from a checkout of this repo:

```bash
pip install -e ".[dev]"
LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```

`GET /api/v1/models` then lists one entry, `demo`. To exercise the dropdown with two entries,
host the demo twice under different names:

```bash
LUME_MODELS='{"demo": {"workers": 1}, "alt": {"factory": "lume_model_api.model.demo:make_demo_model", "workers": 1}}' \
  LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```

The demo model has screens `OTR_A` (no image) and `OTR_B` (with image), inputs
`DEMO:QUAD:1:BCTRL`, `DEMO:SOLN:1:BCTRL`, `DEMO:XCOR:1:BCTRL` (derived range) and
`DEMO:CHARGE` (constant), and Twiss arrays `s`, `x.beta`, `y.beta`. Because everything the UI
needs is read from `/api/v1/models` and `/api/v1/models/{name}/config`, a UI that works against the demo works against
`cu_hxr_staged` unchanged. That is the test: no `OTR`, `IN20` or `µm` literal should remain in
the frontend logic once the port is done.

## Checklist

1. Delete the backend, model layer, fake IOC and their tests from `lume-visualizations`.
2. Point `gen:api` at this repo's `openapi.json` at a pinned ref and regenerate `schema.d.ts`.
3. `client.ts`: call `GET /api/v1/models` at mount, keep the chosen name in state, and prefix
   every other path with `/api/v1/models/{name}`. Add a model dropdown fed by that list.
4. `client.ts`: new request body (`outputs`, `smooth_images_sigma_px`, no `include_*`), new
   `unpackFrame` reading `outputs[...]` by kind, stream URL with `outputs=` for Twiss.
5. `types.ts`: replace `Scalars` with the `stats` keys, drop `screen_label`, `image_message`,
   `image_caption`, `has_image`, `scan_pv`.
6. Convert metres to µm in the UI (or label from `stats_units`), fix `ScalarTimeseries.tsx`.
7. `has_image` to `image !== null` in `LiveTab.tsx` and `InteractiveTab.tsx`.
8. Replace `config.scan_pv` with a user choice or a UI setting.
9. Skip inputs with `constant: true` when building sliders, and consider marking
   `range_source: "derived"` ranges.
10. Build the UI as its own nginx image serving `dist/`, set `VITE_API_URL=/lume-model-api`, and
   give it its own Deployment, Service and Ingress rule at `/live-monitor/`. Drop the derived
   image `FROM ghcr.io/slaclab/lume-model-api` and the eval-Deployment overlay entirely.
11. Run the UI against `LUME_MODELS=demo` and confirm nothing model-specific is hard-coded.
12. After the new API answers `/lume-model-api/api/v1/models` and the ported UI is serving
   `/live-monitor/`, delete the old backend Deployments from `lume-visualizations`.
