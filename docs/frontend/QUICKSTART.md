# Frontend quickstart

This page walks through the calls a TypeScript UI makes, in the order it makes them: list the
models, read one model's config, evaluate it, and stream live frames. The examples use plain
`fetch` and `EventSource` with types generated from `openapi.json` (see
[TYPESCRIPT_CLIENT.md](TYPESCRIPT_CLIENT.md)).

Every id below belongs to the `demo` model, which ships with this repo. A real model publishes
different ids, so a UI reads them from the config at runtime instead of hard-coding them.

![Wiring a UI to lume-model-api](ui-data-flow.svg)

[ui-backend-communication.svg](ui-backend-communication.svg) shows where each request goes
inside the deployment.

The full field reference is in ReDoc, published at <https://slaclab.github.io/lume-model-api/>
and served at `/redoc` (or Swagger at `/docs`) on a running server.

## Base URL

For local development, start the API with the demo model:

```bash
pip install -e ".[dev]"
LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```

`LUME_LIVE_SOURCE=synthetic` makes the live stream wiggle the inputs around their defaults, so
the stream works without EPICS.

The SLAC deployments are:

| Environment | Base URL |
| --- | --- |
| Production (not deployed yet) | `https://ard-modeling-service.slac.stanford.edu/lume-model-api` |
| Development | `https://ad-accel-online-ml-dev.slac.stanford.edu/lume-model-api` |

Both are reachable from the SLAC network only. The README's
[Deployments](../../README.md#deployments) table has the current status.

The `/lume-model-api` prefix belongs in the base URL. The ingress strips it before the request
reaches the app, so the paths in `openapi.json` start at `/api` and don't include it.

```ts
const BASE = "http://localhost:8000";
```

CORS is open to any origin, so a dev server on another port needs no proxy.

## List models

```ts
import type { components } from "./schema";

type ModelListEntry = components["schemas"]["ModelListEntry"];

const models: ModelListEntry[] = await fetch(`${BASE}/api/v1/models`).then((r) => r.json());
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

`name` goes into the path of every other call. Which models a deployment hosts is set by its
config and can change without a UI release, so a model picker should be filled from this list.
A `version` of `"demo"` or `"<name> (demo)"` means the demo model is being served.

## Read a model's config

```ts
type ConfigResponse = components["schemas"]["ConfigResponse"];

const name = models[0].name;
const config: ConfigResponse = await fetch(`${BASE}/api/v1/models/${name}/config`).then((r) =>
  r.json(),
);
```

The config lists everything the model publishes. A shortened response from the demo model:

```json
{
  "model": "demo",
  "version": "demo",
  "inputs": [
    {"id": "DEMO:QUAD:1:BCTRL", "unit": "kG", "default": 2.0, "min": -5.0, "max": 5.0,
     "range_source": "model", "constant": false, "alias_of": null},
    {"id": "DEMO:XCOR:1:BCTRL", "unit": "kG-m", "default": 0.0, "min": -0.5, "max": 0.5,
     "range_source": "derived", "constant": false, "alias_of": null},
    {"id": "DEMO:CHARGE", "unit": "nC", "default": 0.25, "min": 0.25, "max": 0.25,
     "range_source": "model", "constant": true, "alias_of": null}
  ],
  "outputs": [
    {"id": "DEMO:OTRA:XRMS", "kind": "scalar", "unit": "m", "shape": null, ...},
    {"id": "DEMO:OTRB:Image:ArrayData", "kind": "array", "unit": "counts", "shape": [240, 320], ...},
    {"id": "OTR_B_beam", "kind": "particles", "unit": "", "shape": null, ...},
    {"id": "s", "kind": "array", "unit": "m", "shape": [64], ...}
  ],
  "screens": [
    {"key": "OTR_A", "particles": "OTR_A_beam", "image": null},
    {"key": "OTR_B", "particles": "OTR_B_beam", "image": "DEMO:OTRB:Image:ArrayData"}
  ]
}
```

A few things a UI usually does with it:

```ts
// Sliders: skip constants, which the model pins and never changes.
const knobs = config.inputs.filter((input) => !input.constant);

// Screen picker. A screen with image: null has particles but no camera image.
const screens = config.screens;

// Look up an output's kind and unit by id.
const outputsById = new Map(config.outputs.map((output) => [output.id, output]));
```

A `range_source` of `"derived"` means the model declared no limits and the service made up a
range of 50% either side of the default. It's a reasonable slider range, not a limit the model
enforces. More on this, and on `alias_of`, in [CONCEPTS.md](CONCEPTS.md).

The config is cached by the server, so it's cheap to fetch on every page load.

## Evaluate

```ts
type EvaluateRequest = components["schemas"]["EvaluateV1Request"];
type EvaluateResponse = components["schemas"]["EvaluateV1Response"];

async function evaluate(name: string, body: EvaluateRequest): Promise<EvaluateResponse> {
  const response = await fetch(`${BASE}/api/v1/models/${name}/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await response.json(), response.headers.get("Retry-After"));
  }
  return response.json();
}

const result = await evaluate("demo", {
  inputs: { "DEMO:QUAD:1:BCTRL": -1.5 },
  screen: "OTR_B",
  outputs: ["s", "x.beta"],
  max_particles: 500,
});
```

`ApiError` is defined in [Handle errors](#handle-errors).

Only the inputs being changed need to be sent. Every other input uses its design value, so `{}`
evaluates the design machine. The response's `inputs` has every value the model ran with, and
`input_sources` says which ones came from the request:

```json
"inputs": {"DEMO:QUAD:1:BCTRL": -1.5, "DEMO:SOLN:1:BCTRL": 0.45, "DEMO:XCOR:1:BCTRL": 0.0},
"input_sources": {"DEMO:QUAD:1:BCTRL": "request", "DEMO:SOLN:1:BCTRL": "baseline", "DEMO:XCOR:1:BCTRL": "baseline"}
```

Outputs are requested by id in `outputs`, by `screen`, or both. `screen: "OTR_B"` adds
`OTR_B_beam` and `DEMO:OTRB:Image:ArrayData`. Every requested id is present in the response's
`outputs`, so `result.outputs["OTR_B_beam"]` is safe once the request succeeded.

Each output has a `kind` that decides its shape:

```ts
type Output = EvaluateResponse["outputs"][string];

function describe(output: Output) {
  switch (output.kind) {
    case "scalar":
      return `${output.value} ${output.unit}`;
    case "array":
      return `array ${output.shape.join("x")} ${output.unit}`;
    case "particles":
      return `${output.n} particles`;
    case "value":
      return String(output.value);
  }
}
```

Input values must be finite numbers. `JSON.stringify` turns `NaN` and `Infinity` into `null`,
which the server rejects with a 422, so an empty or half-typed numeric field needs handling
before the request is sent.

Values outside an input's `min` and `max` are not rejected by default. Whether the model
refuses them depends on how the model validates its own variables, and the demo accepts them.

## Decode arrays and particles

Arrays and particle coordinates arrive as base64 strings of little-endian float32 values. This
helper turns one into a `Float32Array`:

```ts
function decodeFloat32(b64: string): Float32Array {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return new Float32Array(bytes.buffer);
}
```

`Float32Array` uses the platform's byte order, which is little-endian on every browser platform
in practice.

An array output is flat and row-major. Reshape it with `shape` from the response, not from the
config, because images can be downsampled (see [Images](CONCEPTS.md#images)):

```ts
const image = result.outputs["DEMO:OTRB:Image:ArrayData"];
if (image.kind === "array") {
  const [rows, cols] = image.shape; // [240, 320] for the demo
  const pixels = decodeFloat32(image.data_b64);
  const pixelAt = (row: number, col: number) => pixels[row * cols + col];
}
```

A particles output has one array per coordinate, each of length `n`:

```ts
const beam = result.outputs["OTR_B_beam"];
if (beam.kind === "particles") {
  const x = decodeFloat32(beam.coords.x); // metres, per beam.units.x
  const px = decodeFloat32(beam.coords.px); // eV/c
  console.log(beam.n, beam.stats.norm_emit_x, beam.stats_units.norm_emit_x);
}
```

`coords` has `x`, `px`, `y`, `py`, `z`, `pz` and `weight` (per-particle charge in C), when the
beam carries them. `weight` isn't a plot axis, so phase-space plots should leave it out.
`stats` (`sigma_x`, `norm_emit_x`, `mean_energy`, `charge` and so on) are computed on the full
beam before subsampling, so they don't change with `max_particles`.

## Stream live frames

The live stream runs the model at the machine's current input values and sends each result as
a server-sent event. It needs `screen`, `outputs` (comma-separated), or both:

```ts
const url = new URL(`${BASE}/api/v1/models/demo/live/stream`);
url.searchParams.set("screen", "OTR_B");
url.searchParams.set("outputs", ["s", "x.beta"].join(","));

const source = new EventSource(url);

source.addEventListener("frame", (event) => {
  const frame: EvaluateResponse = JSON.parse(event.data);
  const beam = frame.outputs["OTR_B_beam"];
  // draw here
});

source.addEventListener("error", (event) => {
  if (event instanceof MessageEvent && event.data) {
    // Sent by the server. The stream keeps running afterwards.
    console.warn(JSON.parse(event.data).message);
  }
  // Without data, the connection dropped and EventSource reconnects by itself.
});

// When the view unmounts:
source.close();
```

A `frame` has the same body as an evaluate response. On the stream, `input_sources` is `"live"`
for a value read from the machine and `"baseline"` for a PV that couldn't be read.

Frames arrive as fast as the model runs, with no fixed rate. A new subscriber gets the latest
frame straight away. The server keeps evaluating as long as any viewer of that output set is
connected, so close the source when the view goes away.

The stream isn't described in `openapi.json`, so the `frame` and `error` event names and the
`{"message": string}` error payload have to be typed by hand. See
[Live stream behavior](CONCEPTS.md#live-stream-behavior) for frame ordering, limits and errors.

## Handle errors

Errors come back with a JSON body of `{"detail": ...}`. For 400, 404 and 503, `detail` is a
string written for a person. For 422 it's FastAPI's list of validation errors.

```ts
class ApiError extends Error {
  constructor(
    public status: number,
    public body: { detail: unknown },
    public retryAfter: string | null,
  ) {
    super(typeof body.detail === "string" ? body.detail : `HTTP ${status}`);
  }
}
```

| Status | Cause | What to do |
| --- | --- | --- |
| 400 | No outputs requested, an unknown id, or a value the model rejected | Fix the request. Retrying won't help. |
| 404 | Unknown model name | Refresh the model list. |
| 422 | Malformed body, a non-finite number, or an out-of-bounds `max_particles` or `smooth_images_sigma_px` | Fix the request. |
| 503 with `Retry-After: 2` | The model's pool is busy | Retry after the delay. |
| 503 with `Retry-After: 30` | The model's pool is dead and the pod is restarting | Show a message and retry after the delay. |
| 503 with no `Retry-After` | Live route on a server that doesn't serve live data, or too many distinct live streams | Retrying won't help. See [Errors](CONCEPTS.md#errors). |

Some real responses from the demo:

```json
400 {"detail": "Unknown screen 'NOPE'. This model has: OTR_A, OTR_B."}
404 {"detail": "Unknown model 'nope'. This process hosts: demo. See GET /api/v1/models."}
422 {"detail": [{"type": "finite_number", "loc": ["body", "inputs", "DEMO:QUAD:1:BCTRL"],
                 "msg": "Input should be a finite number", "input": null}]}
```

`EventSource` never shows the HTTP status of a stream that failed to open. While developing,
open the stream URL in a browser tab or with `curl -N` to see the error.
