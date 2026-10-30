# API concepts

What the response fields mean and how the service behaves, beyond what the schema says. Read
[QUICKSTART.md](QUICKSTART.md) first. Field types are in ReDoc, at
<https://slaclab.github.io/lume-model-api/> or `/redoc` on a running server.

Examples use the `demo` model that ships with this repo.

## Baseline merge

Each model has a baseline, which is its set of design values (the `default` of each input in
the config). Every evaluate starts from the baseline and applies the request's `inputs` on top:

```json
// request
{"inputs": {"DEMO:QUAD:1:BCTRL": -1.5}, "outputs": ["DEMO:OTRA:XRMS"]}

// response
"inputs": {"DEMO:QUAD:1:BCTRL": -1.5, "DEMO:SOLN:1:BCTRL": 0.45, "DEMO:XCOR:1:BCTRL": 0.0}
```

So each request fully describes the machine state it asks about. Two identical requests give
the same result regardless of what ran before them, and `{"inputs": {}}` always evaluates the
design machine.

This also means a UI that wants to move two knobs has to send both in the same request. Sending
one knob, then the other, gives two results that each have only one knob moved.

Constant inputs (`constant: true` in the config) are never set and don't appear in the
response's `inputs`.

## input_sources

`input_sources` has the same keys as `inputs` and records where each value came from:

| Value | Source |
| --- | --- |
| `request` | Sent in the request body |
| `baseline` | The model's design value |
| `live` | Read from the machine. Live stream only. |

On the evaluate route a value is only ever `request` or `baseline`.

On the live stream, a PV that can't be read falls back to its design value and is marked
`baseline`. The number looks like any other reading, so this field is the only way to show a
user which inputs aren't coming from the machine.

`machine-snapshot` returns the same information in a field called `sources`, with values `live`
or `baseline`.

## Screens and outputs

A screen is a diagnostic location, such as a beam profile monitor. In the config each screen
names the output id of its particle distribution and, if there is one, its camera image:

```json
{"key": "OTR_B", "particles": "OTR_B_beam", "image": "DEMO:OTRB:Image:ArrayData"}
{"key": "OTR_A", "particles": "OTR_A_beam", "image": null}
```

These two requests return the same outputs:

```json
{"screen": "OTR_B"}
{"outputs": ["OTR_B_beam", "DEMO:OTRB:Image:ArrayData"]}
```

`screen` is the simpler choice for a view that shows whatever a screen publishes. A screen with
no image just returns one fewer output. `outputs` is the choice for a specific set, such as
particles without the image, or Twiss arrays alongside a scalar. The two can be combined, and
duplicate ids are only evaluated once.

At least one of `screen` and `outputs` is required. A request with neither is a 400.

## Output kinds

Each value in `outputs` has a `kind`, which decides its fields. TypeScript narrows on it:

| `kind` | Fields | Typical use |
| --- | --- | --- |
| `scalar` | `value`, `unit` | A single number |
| `array` | `shape`, `dtype`, `data_b64`, `unit` | Images, Twiss functions, profiles |
| `particles` | `n`, `coords`, `units`, `stats`, `stats_units` | Beam distributions |
| `value` | `value` | Enums, strings, flags |

Every kind also has `variable_class`, the name of the lume variable class behind it (for example
`ScalarVariable`, `ElementNDVariable` or `EnumVariable`). It's finer than `kind` but rarely
needed. The main use is telling an enum from a string, since both are `kind: "value"`.

A scalar's `value` and the numbers in `stats` can be `null` when the model produced NaN or
infinity, for example a degenerate beam with no meaningful emittance. The generated types say
`number`, so code that reads them should handle `null` anyway (see
[Known gaps](TYPESCRIPT_CLIENT.md#known-gaps)).

Particles are subsampled to `max_particles` (default 3000, maximum 200000). The subsample is a
fixed stride through the beam rather than a random draw, so the same request always returns
the same particles.

## Units

Values come in the model's own units, and the service doesn't convert anything. Every input has
a `unit` in the config, and every output payload carries its unit (`unit` on scalars and arrays,
`units` and `stats_units` on particles).

Particle distributions use the openPMD-beamphysics conventions: positions in m, momenta in eV/c,
`weight` in C, energies in eV. So `sigma_x` is in metres, and a UI that displays micrometres
multiplies by 1e6.

Units can differ between models, and between inputs of the same model (the demo has `kG`,
`kG*m` and `kG-m`), so labels should come from the payload.

## Images

A 2-D array output is treated as an image. Images are downsampled on the server so the longest
side is at most 512 pixels (the server's `LUME_MAX_IMAGE_DIM` setting). Each output pixel is the
mean of a block of input pixels, so intensities keep their meaning and only the resolution
drops. A 1040x1392 sensor arrives as 347x464.

That's why the `shape` in an evaluate response can be smaller than the `shape` in the config.
Always reshape with the response's `shape`.

Arrays of any other rank, such as 1-D Twiss functions, arrive at full resolution.

Images are raw values in the declared unit, not normalized to 0..1. Color scaling is up to the
UI.

`smooth_images_sigma_px` applies a Gaussian blur of that width, in pixels, to every 2-D array
before downsampling. It's off by default. A screen image built from about a thousand particles
looks like scattered single counts, and a blur of 1 or 2 pixels makes it look more like a
camera frame. The blur keeps the total intensity the same and doesn't rescale, so the unit still
holds. The maximum is 50. There is no smoothing option on the live stream.

## Aliased inputs

Some models publish two writable ids for the same control. LCLS magnets do this: `BCTRL` and
`BDES` on one magnet set the same value. Setting both in one evaluate would make one of them
silently win, so the service keeps one id as an input and turns the others into read-only
outputs with `alias_of` naming the input to use:

```json
{"id": "QUAD:IN20:525:BDES", "kind": "scalar", "unit": "kG", "alias_of": "QUAD:IN20:525:BCTRL"}
```

Reading the alias works and returns the same value as its input. Sending it in `inputs` is a 400
for an unknown input id. `alias_of` is always `null` on an input. The demo model has no aliases.

## Live stream behavior

`GET /api/v1/models/{name}/live/stream` evaluates the model over and over at the machine's
current inputs and sends each result to every connected viewer.

**Shared loops.** The server runs one evaluation loop per model per distinct set of output ids.
Viewers asking for the same set share it, however they asked (by `screen`, by `outputs`, or
both). A UI that settles on a few fixed output sets costs the server a few loops no matter how
many people have it open.

**Loop limit.** The number of distinct output sets per model is capped at the model's
in-flight limit, which the Kubernetes manifests set to 8 on the live pod. A new set beyond the cap is
a 503 with a message saying so. Joining a set that's already streaming always works. Retrying
the same new set won't help until another viewer disconnects.

**Pacing.** There's no fixed frame rate. Frames go out as fast as the model runs, which is a few
seconds per frame for a real model. A new viewer gets the latest frame straight away.

**Slow clients.** Each viewer holds at most one pending frame. If the browser falls behind, the
older frame is dropped and the newest one kept, so a slow client sees fewer frames rather than a
growing delay.

**`frame_index`.** A counter shared by all loops of one model. It only goes up, including across
reconnects, but a stream sees gaps where another output set produced a frame. It works as a
frame id, not as a count of frames received. On the evaluate route it's always 0.

**`error` events.** The payload is `{"message": string}`. Most errors are temporary, and the loop
retries about twice a second. The message is a fixed generic sentence, because the real error
text can include server paths and model internals, so UI logic shouldn't depend on its wording.
The one exception is a dead pool, which sends a message saying the pod must restart and then
stops the stream. `EventSource` reconnects on its own once the pod is back.

**Errors before the stream opens** (a bad `screen`, an unknown id, the loop limit) are normal
HTTP errors. `EventSource` reports them only as a generic `error` event with no status.

**Live data.** The stream and `machine-snapshot` are served by a separate live pod in the
cluster. The ingress routes them there, so a UI sees one base URL. If the live data source can't
read any PV at all, the frame fails with an `error` event instead of reporting design values as
machine values.

## Errors

| Status | When | `Retry-After` | Retry? |
| --- | --- | --- | --- |
| 400 | No `outputs` or `screen` | | No |
| 400 | Unknown output id, screen key or input id (including an alias) | | No |
| 400 | The model rejected an input value, for example one outside a range it enforces | | No |
| 404 | Unknown model name. The detail lists the hosted ones. | | No |
| 422 | Body failed validation: not a number, NaN or infinity, `max_particles` outside 1 to 200000, `smooth_images_sigma_px` outside 0 to 50 | | No |
| 503 | The model's pool is busy. Each model has its own limit, so other models may still answer. | 2 | Yes, with backoff |
| 503 | A request broke the model, which then recovered. The worker is serving again. | 2 | Yes |
| 503 | The model's pool is dead. The pod restarts in about 30 seconds. | 30 | After the restart |
| 503 | Live route on a server that doesn't serve live data | | No. Check the URL. |
| 503 | Live stream loop limit reached | | No. Reuse a set that's already streaming. |

The `detail` string says which case it is.

A 503 means the server failed, not the request. That includes the case where an input value
broke the model: the request was valid, so it isn't a 400. In practice no model recovers today,
so that case ends in a dead pool.

Some error details from the demo:

```json
{"detail": "Nothing requested: send `outputs` (a list of output ids) and/or `screen`. GET /api/v1/models/demo/config lists every input, output and screen this model publishes."}
{"detail": "Unknown output id(s): NOPE. GET /api/v1/models/demo/config lists every input, output and screen this model publishes."}
{"detail": "Unknown input id(s): NOPE. GET /api/v1/models/demo/config lists every input this model accepts."}
```

There is no unprefixed default-model route. `/api/config`, `/api/v1/evaluate`,
`/api/live/stream` and `/api/machine-snapshot` return 404.

## Coming from lume-visualizations

The React UI in `slaclab/lume-visualizations` was written against the API before it moved to
this repo. Porting it mostly means these changes.

**Paths carry a model name.** Load the model list first, then put the chosen name in every call.

| Old path | New path |
| --- | --- |
| none | `GET /api/v1/models` |
| `GET /api/config` | `GET /api/v1/models/{name}/config` |
| `POST /api/v1/evaluate` | `POST /api/v1/models/{name}/evaluate` |
| `GET /api/machine-snapshot` | `GET /api/v1/models/{name}/machine-snapshot` |
| `GET /api/live/stream` | `GET /api/v1/models/{name}/live/stream` |

**Config changes.**

- `screens[].has_image` became `screens[].image !== null`. `image` is the image's output id.
- `screens[].label` and `inputs[].label` are gone. Use `key` and `id`.
- `scalars[]` is gone. Beam statistics are in each particles output's `stats`.
- `scan_pv` is gone. The scan magnet is a UI choice, for example any input from
  `config.inputs`, or a build-time setting.
- New: `outputs[]`, `inputs[].range_source`, `inputs[].constant` (don't render a slider for
  these), `description`.

**Evaluate request changes.**

- `include_image` and `include_distribution` are gone. `screen` returns both.
- `include_twiss` is gone. Add the Twiss ids to `outputs`. For the LCLS Bmad models they're `s`,
  `x.beta` and `y.beta`, and the stream takes them as `&outputs=s,x.beta,y.beta`.
- The old backend always blurred screen images by 1 pixel and normalized them to a peak of 1.
  Send `smooth_images_sigma_px: 1.0` for the blur. There's no normalization anymore.
- Out-of-range `max_particles` and non-finite inputs are now 422s instead of being clamped.

**Evaluate response changes.**

- Outputs are keyed by id in `outputs`. The image is `outputs[screen.image]` and the
  distribution is `outputs[screen.particles]`.
- `scalars.xrms_um` and friends became `stats.sigma_x`, `sigma_y`, `sigma_z`, `norm_emit_x` and
  `norm_emit_y`, in metres rather than micrometres. `ScalarTimeseries.tsx` labels and the
  `Scalars` type need updating.
- Twiss arrays are base64 float32 like images, not JSON lists.
- `screen`, `screen_label`, `image_message` and `image_caption` are gone.
- New: `inputs` and `input_sources`.

**Stream changes.** `screen` no longer defaults to `OTR4`, so `screen` or `outputs` is required.
`error` messages are generic. `frame_index` is shared across output sets. Opening a new output
set can return 503 (see [Live stream behavior](#live-stream-behavior)).

**Types.** Regenerate them from this repo's `openapi.json` (see
[TYPESCRIPT_CLIENT.md](TYPESCRIPT_CLIENT.md)).

**Local development.** `LUME_MOCK=1` is gone. Run the demo model instead:

```bash
LUME_MODELS=demo LUME_LIVE_SOURCE=synthetic uvicorn lume_model_api.api.main:app --port 8000
```
