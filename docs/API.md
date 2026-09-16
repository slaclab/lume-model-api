# API reference

Everything a client needs to call this service. One process hosts one or more models, and every
model-specific path carries that model's URL name. The running examples host the in-repo `demo`
model under the name `demo`, started like this:

```bash
LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```

Every id, kind and unit in the examples belongs to the demo model. A different model publishes
different ones, and a different `LUME_MODELS` hosts different names, which is why the first call
any client makes is `GET /api/v1/models` and the second is
`GET /api/v1/models/{name}/config`.

## Base URL

The examples below use `http://localhost:8000`. The deployed service at SLAC is

```
https://ard-modeling-service.slac.stanford.edu/lume-model-api
```

so the model list is
`https://ard-modeling-service.slac.stanford.edu/lume-model-api/api/v1/models`, a config is
`https://ard-modeling-service.slac.stanford.edu/lume-model-api/api/v1/models/cu_hxr_staged/config`,
and the SSE stream is
`https://ard-modeling-service.slac.stanford.edu/lume-model-api/api/v1/models/cu_hxr_staged/live/stream?screen=...`.

Three things to know about that URL:

- **The `/lume-model-api` prefix is stripped by the ingress before the request reaches the app.**
  The OpenAPI paths therefore start at `/api` and carry no prefix, so configure the prefix as
  your client's base URL rather than expecting to find it in `openapi.json`. The pods do know
  the prefix, through `LUME_ROOT_PATH`, but only so that Swagger at
  `<base>/docs` can find the schema.
- **`/live-monitor` on the same host is a different service.** That prefix is the old monolith,
  the pre-split API plus the React UI, and it answers a different contract. Do not point a
  client at it.
- **The live endpoints are routed to a different pod than the rest,** transparently and on the
  same host. `api/v1/models/<name>/live/*` and `api/v1/models/<name>/machine-snapshot` go to the
  singleton live producer, everything else to the autoscaled eval pool. A client sees one origin.

TLS terminates on a load balancer in front of the cluster with the Stanford wildcard
certificate, and plain `http://` is redirected to `https://` there, so always use the `https`
form. Whether off-site access is refused depends on the ingress allowlist, see `docs/DEPLOY.md`.

## Conventions

**Nothing is hard-coded about the model, including which models exist.** Input ids, output ids,
output kinds, screen keys and units all come from the model instance, and the set of hosted names
comes from the service. Read `GET /api/v1/models` and then each model's config at startup rather
than compiling either in.

**Units travel with the data, in the model's native units.** There is no unit conversion
anywhere in this service. A generic host cannot tell a beam image from a lattice function, so
it reports the unit the model declared and leaves display scaling to the caller. Particle
distributions follow the openPMD-beamphysics conventions a `ParticleGroup` already uses:
positions in m, momenta in eV/c, weights in C, energies in eV.

**Every requested output id is present in the response**, keyed by the id you asked for. An id
is never silently omitted, so `outputs["OTR_B_beam"]` is safe once the request succeeded.

**Large numeric payloads are base64 little-endian float32.** Decode in Python with

```python
numpy.frombuffer(base64.b64decode(s), dtype="<f4")
```

and reshape array outputs to their declared `shape` (row-major). In the browser, decode the
base64 to bytes and wrap them in `new Float32Array(bytes.buffer)`.

**`max_particles` defaults to 3000, never the full beam.** An omitted value gets the cap, not a
multi-megabyte payload.

**Evaluates are stateless.** Inputs you send are overlaid on the model's baseline (its design
values), so `{}` means the design machine and a single knob means the design machine with that
knob moved. The effective post-merge values come back in the response's `inputs`.

**Models are independent.** Two models hosted by one process share nothing except the live input
source. An evaluate on one never affects the other, and each has its own worker pool, its own
in-flight budget and its own 503 threshold.

## GET /api/v1/models

Lists every model this process hosts. This is the first call a client makes and the source a
model dropdown reads.

**Response**: a list of entries sorted by `name`.

| Field | Meaning |
| --- | --- |
| `name` | The URL name. Everything else about this model lives under `/api/v1/models/{name}/`. Matches `^[A-Za-z0-9][A-Za-z0-9_-]*$`. |
| `description` | First line of the model class docstring, the same string as that model's config `description`. |
| `version` | `demo` for the demo model hosted under the name `demo`, `<name> (demo)` for the demo factory hosted under any other name, and the name itself otherwise. So a client can always tell a demo deployment from a real one. |

```bash
curl -s localhost:8000/api/v1/models | python -m json.tool
```

```json
[
  {
    "name": "demo",
    "description": "Dependency-light demonstration beamline with two screens.",
    "version": "demo"
  }
]
```

**A UI reads this list and never hard-codes a model name.** Which models a pod hosts is a
deployment decision that changes without a client release, and a hard-coded name becomes a 404
the moment it changes. Populate the dropdown from this list, use the entry's `name` in every
subsequent URL, and match a fetched config back to the selected entry on `config.model`, which is
that same URL name.

```python
import requests

BASE = "http://localhost:8000"

models = requests.get(f"{BASE}/api/v1/models").json()
print([entry["name"] for entry in models])   # ['demo']

selected = models[0]["name"]
config = requests.get(f"{BASE}/api/v1/models/{selected}/config").json()
assert config["model"] == selected
```

An unknown name on any of the routes below is a 404 whose detail lists the hosted names.

## GET /api/v1/models/{name}/config

Describes one hosted model. Cheap and cached in the web process, so calling it per page load
costs nothing.

**Response fields**

| Field | Meaning |
| --- | --- |
| `model` | The URL name this model is hosted under, so the same string as the `{name}` in the path and as the `name` in the model list. Not the factory path. |
| `version` | As in the model list: `demo`, `<name> (demo)`, or the name. |
| `description` | First line of the model class docstring. |
| `inputs` | List of `InputInfo`, the writable scalar knobs. |
| `outputs` | List of `OutputInfo`, everything readable. |
| `screens` | List of `ScreenInfo`, the diagnostic locations. |

`InputInfo` has `id`, `unit`, `default`, `min`, `max`, `range_source` and `constant`.
`range_source` is `"model"` when the model declared a `value_range` and `"derived"` when the
range was inferred from the default (see
[ADDING_A_MODEL.md](ADDING_A_MODEL.md#missing-ranges-are-derived)). A UI should treat a derived
range as a suggestion rather than a limit. `constant` is true when the model pins the input to
a single value, in which case it is excluded from the baseline, from evaluates and from the
live input reads.

`OutputInfo` has `id`, `kind` (one of `scalar`, `array`, `particles`, `value`), `unit`, `shape`
(for array outputs, else null) and `element_name` (the beamline element the variable belongs
to, when the model declares one, else null).

`ScreenInfo` has `key`, `particles` (the output id of that screen's particle distribution) and
`image` (the output id of its image, or null when the model publishes none).

```bash
curl -s localhost:8000/api/v1/models/demo/config | python -m json.tool
```

```python
import requests

config = requests.get("http://localhost:8000/api/v1/models/demo/config").json()

knobs = {item["id"]: item for item in config["inputs"] if not item["constant"]}
kinds = {item["id"]: item["kind"] for item in config["outputs"]}
screens = {item["key"]: item for item in config["screens"]}

print(sorted(knobs))            # ['DEMO:QUAD:1:BCTRL', 'DEMO:SOLN:1:BCTRL', 'DEMO:XCOR:1:BCTRL']
print(screens["OTR_B"])         # {'key': 'OTR_B', 'particles': 'OTR_B_beam', 'image': 'DEMO:OTRB:Image:ArrayData'}
print(screens["OTR_A"]["image"])  # None: this screen has particles but no image
```

## POST /api/v1/models/{name}/evaluate

Runs the named model at the given inputs and returns the requested outputs. This is the one
evaluate endpoint for every caller, UI and notebook alike.

**Request fields**

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `inputs` | object of id to number | `{}` | Control values, overlaid on the model's baseline. Send only what you want to move. |
| `outputs` | list of id | `[]` | Output ids to return. |
| `screen` | string or null | null | Shorthand that appends this screen's `particles` id and, when it has one, its `image` id. |
| `max_particles` | integer or null | null (means 3000) | Cap on particles per coordinate. |
| `smooth_images_sigma_px` | number or null | null | Opt-in Gaussian blur in pixels, applied to 2-D array outputs only. |

`outputs` and `screen` may be combined, and the union is deduplicated, so `screen` plus one of
that screen's ids does not evaluate anything twice. At least one of the two is required.

**Response fields**

| Field | Meaning |
| --- | --- |
| `model` | The URL name, so the `{name}` from the path. |
| `version` | As in the model list. |
| `timestamp` | Unix seconds when the frame was serialized. |
| `frame_index` | 0 for HTTP calls, a monotonically increasing counter on the live stream. |
| `inputs` | The effective post-merge control values that were applied. |
| `outputs` | Object of output id to an `Output`, discriminated on `kind`. |

### The screen shorthand versus explicit outputs

`{"screen": "OTR_B"}` and `{"outputs": ["OTR_B_beam", "DEMO:OTRB:Image:ArrayData"]}` produce
the same result for the demo model. Prefer `screen` when you are showing a diagnostic location
and want whatever that location publishes, since a screen with no image simply returns one
fewer output rather than 400. Prefer explicit `outputs` when you want a specific set, for
example particles without the image, or Twiss arrays and a scalar together.

### Output kinds

A response's `outputs` values are one of four shapes, told apart by `kind`.

`scalar`

| Field | Type |
| --- | --- |
| `kind` | `"scalar"` |
| `value` | number |
| `unit` | string, the model's unit |

`array`

| Field | Type |
| --- | --- |
| `kind` | `"array"` |
| `shape` | list of int, row-major |
| `dtype` | `"float32"` |
| `data_b64` | base64 little-endian float32 |
| `unit` | string |

2-D arrays are block-mean downsampled so the longest side is at most `LUME_MAX_IMAGE_DIM`
(default 512), which is why `shape` here can be smaller than the `shape` in the config.
Always reshape to the `shape` in the response. Block-mean averaging preserves the intensity
distribution, so a client's own scaling is unaffected and only the resolution drops. Arrays of
any other rank ship at full resolution.

`particles`

| Field | Type |
| --- | --- |
| `kind` | `"particles"` |
| `n` | int, particles per coordinate after subsampling |
| `units` | object of coordinate name to unit, for example `{"x": "m", "px": "eV/c", "weight": "C"}` |
| `coords` | object of coordinate name to base64 little-endian float32, each of length `n` |
| `stats` | object of statistic name to number |
| `stats_units` | object of statistic name to unit |

Coordinates are `x`, `px`, `y`, `py`, `z`, `pz` and `weight`, subject to the beam actually
carrying them. Subsampling is a deterministic `linspace` stride, not a random draw, so two
identical requests to two different pool workers return the same particles. `weight` is
included deliberately: a distribution without per-particle charge is incomplete for a physics
caller, and it is only about 17% of the payload. A UI plotting phase space should filter it
out client-side.

`stats` covers `sigma_x`, `sigma_y`, `sigma_z`, `norm_emit_x`, `norm_emit_y`, `mean_energy` and
`charge`, minus any the beam object cannot compute. They are computed on the **full** beam
before subsampling, so a small `max_particles` thins the scatter plot without corrupting the
numbers next to it.

`value`

| Field | Type |
| --- | --- |
| `kind` | `"value"` |
| `value` | any JSON value |

This is the fallback for anything that is not a number, an array or a beam: enums, strings,
flags.

### `smooth_images_sigma_px`

Off by default. When set, each 2-D array output is convolved with a Gaussian of that sigma in
pixels, before downsampling. The filter conserves the array's sum, so a smoothed image is
still in its declared unit and your own intensity scaling keeps working.

The reason it exists: a screen image built from a thousand or so macroparticles is single-count
noise at pixel resolution, and convolving with something like the detector point-spread
function is what makes it look like a camera frame. It is opt-in because blurring every 2-D
array is wrong for a generic host, which cannot tell an image from a response matrix.

### Examples

```bash
curl -s -X POST localhost:8000/api/v1/models/demo/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"screen": "OTR_B", "inputs": {"DEMO:QUAD:1:BCTRL": -1.5}, "max_particles": 500}'
```

```bash
curl -s -X POST localhost:8000/api/v1/models/demo/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"outputs": ["DEMO:OTRA:XRMS", "s", "x.beta"]}'
```

```python
import base64

import numpy as np
import requests

BASE = "http://localhost:8000/api/v1/models/demo"


def decode(b64: str) -> np.ndarray:
    """Every base64 payload in this API is little-endian float32."""
    return np.frombuffer(base64.b64decode(b64), dtype="<f4")


body = requests.post(
    f"{BASE}/evaluate",
    json={
        "screen": "OTR_B",
        "inputs": {"DEMO:QUAD:1:BCTRL": -1.5},
        "max_particles": 500,
    },
).json()

print(body["inputs"])  # the effective values, including the two the baseline filled in

beam = body["outputs"]["OTR_B_beam"]
x = decode(beam["coords"]["x"])          # (500,) in metres
px = decode(beam["coords"]["px"])        # (500,) in eV/c
print(beam["n"], beam["units"]["x"], beam["stats"]["norm_emit_x"], beam["stats_units"]["norm_emit_x"])

image = body["outputs"]["DEMO:OTRB:Image:ArrayData"]
frame = decode(image["data_b64"]).reshape(image["shape"])   # (240, 320) here
print(frame.shape, image["unit"])

# A scalar and a 1-D array in one call.
curves = requests.post(
    f"{BASE}/evaluate", json={"outputs": ["DEMO:OTRA:XRMS", "s", "x.beta"]}
).json()["outputs"]
print(curves["DEMO:OTRA:XRMS"]["value"], curves["DEMO:OTRA:XRMS"]["unit"])
s = decode(curves["s"]["data_b64"]).reshape(curves["s"]["shape"])
beta_x = decode(curves["x.beta"]["data_b64"]).reshape(curves["x.beta"]["shape"])
```

A generic decoder that handles any model's output set:

```python
def decode_output(entry: dict):
    kind = entry["kind"]
    if kind == "scalar":
        return entry["value"]
    if kind == "array":
        return decode(entry["data_b64"]).reshape(entry["shape"])
    if kind == "particles":
        return {name: decode(b64) for name, b64 in entry["coords"].items()}
    return entry["value"]  # kind == "value"
```

### Error codes

| Status | When |
| --- | --- |
| 400 | Neither `outputs` nor `screen` was given, so the request asked for nothing. A generic host has no sensible default output set. |
| 400 | An unknown output id, an unknown screen key or an unknown input id. The detail names the offenders and points at this model's config. |
| 400 | The model itself rejected an input value, for example one outside its `value_range` on a model that validates strictly. The detail carries the model's message. |
| 404 | The `{name}` in the path is not a model this process hosts. The detail lists the hosted names. `GET /api/v1/models` is the authoritative list. |
| 422 | The body failed schema validation, for example `inputs` was not an object or a value was not a number. FastAPI generates this. |
| 503 | This model's pool is saturated: its `max_inflight` evaluates are already running. Retry with backoff. Each model has its own budget, so another model may still be answering. |

```bash
$ curl -s -X POST localhost:8000/api/v1/models/demo/evaluate -H 'Content-Type: application/json' -d '{}'
{"detail":"Nothing requested: send `outputs` (a list of output ids) and/or `screen`. GET /api/v1/models/demo/config lists every input, output and screen this model publishes."}

$ curl -s -X POST localhost:8000/api/v1/models/demo/evaluate -H 'Content-Type: application/json' -d '{"outputs":["NOPE"]}'
{"detail":"Unknown output id(s): NOPE. GET /api/v1/models/demo/config lists every input, output and screen this model publishes."}

$ curl -s -X POST localhost:8000/api/v1/models/demo/evaluate -H 'Content-Type: application/json' -d '{"screen":"NOPE"}'
{"detail":"Unknown screen 'NOPE'. This model has: OTR_A, OTR_B."}
```

The old unprefixed paths (`/api/config`, `/api/v1/evaluate`, `/api/live/stream`,
`/api/machine-snapshot`) are gone and return 404. There is no implicit default model, so a client
always names one.

A 503 also comes back from `/api/v1/models/{name}/live/stream` and
`/api/v1/models/{name}/machine-snapshot` when the process was started with `LUME_ROLE=eval`,
because those two are served only by the live role. The detail says
`live view not served by this instance` or `machine-snapshot not served by this instance`. That is
a routing mistake rather than a load problem, so do not retry it.

## GET /api/v1/models/{name}/live/stream

A server-sent event stream of frames, driven by the live input source rather than by the
caller. Requires `screen` or `outputs` (comma-separated ids), or both, resolved exactly as in
evaluate, so the same 400s apply. Served only by the `live` and `all` roles.

Two event names:

- `frame`, whose `data` is the same JSON body as a `POST /api/v1/models/{name}/evaluate`
  response, including `model`, `version`, `timestamp`, `frame_index`, `inputs` and `outputs`.
  `frame_index` increments per frame on this stream.
- `error`, whose `data` is `{"message": "..."}`. The loop keeps running after an error and
  retries about twice a second, so an `error` event is not the end of the stream.

Frames arrive as fast as the model can be evaluated, with no fixed poll period. There is one
producer loop per distinct output set per model, so many viewers of the same set on the same
model cost one evaluate loop. A new subscriber is seeded immediately with the last frame that
loop produced, so a UI paints without waiting a whole evaluate. Each subscriber has a size-1
drop-old queue, so a slow client always gets the newest frame and never a backlog.

```bash
curl -s -N "localhost:8000/api/v1/models/demo/live/stream?outputs=DEMO:OTRA:XRMS"
```

```
event: frame
data: {"timestamp": 1789014229.307307, "frame_index": 0, "inputs": {"DEMO:QUAD:1:BCTRL": 2.0034, ...}, "outputs": {"DEMO:OTRA:XRMS": {"kind": "scalar", "value": 0.00010936, "unit": "m"}}, "model": "demo", "version": "demo"}
```

From Python, with `requests` streaming and no extra dependency:

```python
import json

import requests

with requests.get(
    "http://localhost:8000/api/v1/models/demo/live/stream",
    params={"screen": "OTR_B"},
    stream=True,
    timeout=None,
) as response:
    response.raise_for_status()
    event = None
    for line in response.iter_lines(decode_unicode=True):
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = json.loads(line.split(":", 1)[1])
            if event == "error":
                print("stream error:", payload["message"])
            else:
                print(payload["frame_index"], payload["outputs"]["OTR_B_beam"]["n"])
        # a blank line terminates an event, so nothing to do for it
```

Or with `sseclient-py`, which does the framing for you:

```python
import json

import requests
import sseclient  # pip install sseclient-py

response = requests.get(
    "http://localhost:8000/api/v1/models/demo/live/stream?screen=OTR_B",
    stream=True,
    timeout=None,
)
for event in sseclient.SSEClient(response).events():
    payload = json.loads(event.data)
    if event.event == "error":
        print("stream error:", payload["message"])
    else:
        print(payload["frame_index"], payload["inputs"])
```

From JavaScript, `EventSource` handles reconnection itself. CORS is open, so a UI on another
origin needs no change here.

```js
const source = new EventSource(
  "http://localhost:8000/api/v1/models/demo/live/stream?screen=OTR_B",
);

source.addEventListener("frame", (event) => {
  const frame = JSON.parse(event.data);
  const image = frame.outputs["DEMO:OTRB:Image:ArrayData"];
  const bytes = Uint8Array.from(atob(image.data_b64), (c) => c.charCodeAt(0));
  const pixels = new Float32Array(bytes.buffer); // reshape with image.shape
});

source.addEventListener("error", (event) => {
  // A named "error" event from the server carries a payload. A transport-level
  // failure fires the same handler with no data, and EventSource retries on its own.
  if (event.data) console.warn(JSON.parse(event.data).message);
});
```

Closing the connection unsubscribes, and the producer loop stops once its last viewer leaves.

## GET /api/v1/models/{name}/machine-snapshot

The current live input values for that model's inputs, read-only. Served only by the `live` and
`all` roles, and 503 otherwise.

The response is `{"inputs": {id: float}}` covering every non-constant input of the named model.
Ids the live source cannot read report their baseline value instead, so the key set is stable.
With `LUME_LIVE_SOURCE=epics` the values come from channel access, and with `synthetic` they are
the demo wiggle. The live source is a process-wide setting rather than a per-model one, so every
hosted model reads from the same source.

```bash
curl -s localhost:8000/api/v1/models/demo/machine-snapshot
```

```json
{"inputs": {"DEMO:QUAD:1:BCTRL": 2.0, "DEMO:SOLN:1:BCTRL": 0.4983163265428268, "DEMO:XCOR:1:BCTRL": 0.24636243249711504}}
```

```python
import requests

BASE = "http://localhost:8000/api/v1/models/demo"

live = requests.get(f"{BASE}/machine-snapshot").json()["inputs"]
# Evaluate the model at the machine's current state, then at a perturbed one.
body = requests.post(
    f"{BASE}/evaluate",
    json={"inputs": live, "screen": "OTR_B"},
).json()
```

Constant inputs are absent from the snapshot, because the model pins them and there is nothing
to read.

## GET /metrics

Prometheus text format, one registry per pod. Scrape it per pod rather than through the
service, since the numbers are per-process.

**Every series carries a `model` label** holding the URL name, so a pod hosting M models emits M
label values per series. Cardinality is M per pod, which is small, but a query has to aggregate
over the label: a plain `avg(lume_pool_inflight)` is diluted by M.

| Metric | Type | Meaning |
| --- | --- | --- |
| `lume_pool_inflight` | gauge, label `model` | Evaluates currently in flight for that model. KEDA scales on the per-pod sum. |
| `lume_pool_max_inflight` | gauge, label `model` | That model's configured `max_inflight`. |
| `lume_pool_workers` | gauge, label `model` | Model subprocesses in that model's pool. |
| `lume_evaluate_seconds` | histogram, labels `model`, `kind` | Whole submit time (queue wait plus model run). `kind` is `interactive` for HTTP calls and `live` for stream frames. |
| `lume_evaluate_total` | counter, labels `model`, `kind`, `outcome` | Completed evaluates, `outcome` in `ok` or `error`. |
| `lume_pool_rejected_total` | counter, labels `model`, `kind` | Evaluates rejected with 503 because that model's pool was saturated. |

```bash
curl -s localhost:8000/metrics | grep '^lume_pool'
```

The honest per-pod saturation signal is therefore `sum by (pod) (lume_pool_inflight)`. See
[`../deploy/kubernetes/SCALING.md`](../deploy/kubernetes/SCALING.md) for the KEDA trigger that
uses it.

## Changing the contract

`openapi.json` at the repo root is dumped from the app and committed. Consumers generate their
client types from it at a pinned git ref, so a rename here becomes a compile error in their
build instead of a runtime surprise.

After editing a route or `lume_model_api/api/schemas.py`:

1. Regenerate with the pinned versions. `fastapi==0.141.1` and `pydantic==2.13.4` are what CI
   uses to generate the JSON schema, and a newer pair emits harmless but different output.
   Regenerating under whatever pip happened to resolve produces a large spurious diff that
   fails CI.

   ```bash
   pip install fastapi==0.141.1 pydantic==2.13.4
   python scripts/dump_openapi.py     # writes openapi.json
   ```

2. Update the expected field sets in `tests/test_api_contract.py` by hand. They are written out
   longhand on purpose, so that a regenerated snapshot cannot hide a rename.

3. Keep `serialize.result_to_wire` emitting every key unconditionally, and make sure every
   requested output id is present. The SSE path has no `response_model`, so a conditionally
   omitted key reaches clients genuinely absent and does not show up in `openapi.json` at all.
   `tests/test_wire_shape.py` enforces this over both demo screens.

**Additive only, from the move to `/api/v1/models/{name}/...` onward.** The response shape was
redesigned once while the service still had no consumers, and the v1 paths then moved once more,
to `/api/v1/models/{name}/...`, while there was still no deployed consumer and the UI port was in
progress (see [MIGRATING_LUME_VISUALIZATIONS.md](MIGRATING_LUME_VISUALIZATIONS.md)). That window
is now closed. Adding an optional field is fine. Renaming a field, removing one, making an
optional field required, or moving a path again needs `/api/v2` instead, because consumers pin a
git ref and a rename here is silent for them until they refetch.

Two couplings the generated schema does not cover. The SSE stream has no `response_model`, so
OpenAPI says nothing about the `frame` and `error` event names or the `{"message": ...}` error
payload, and each client has to match them by hand. And `/api/v1/*` field names themselves are
only guarded by the hand-written test above.
