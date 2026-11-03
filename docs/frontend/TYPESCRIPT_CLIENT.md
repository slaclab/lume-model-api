# TypeScript client

`openapi.json` at the repo root is the API contract. It's generated from the app and committed,
and a UI generates its TypeScript types from it with
[openapi-typescript](https://openapi-ts.dev/). No client library is published.

## Generate types

Add a script to the UI's `package.json`:

```json
"scripts": {
  "gen:api": "npx -y openapi-typescript@7.13.0 https://raw.githubusercontent.com/slaclab/lume-model-api/<sha>/openapi.json -o src/api/schema.d.ts"
}
```

Then import the schema types by name:

```ts
import type { components } from "./api/schema";

type ConfigResponse = components["schemas"]["ConfigResponse"];
type EvaluateRequest = components["schemas"]["EvaluateV1Request"];
type EvaluateResponse = components["schemas"]["EvaluateV1Response"];
type Output = EvaluateResponse["outputs"][string];
```

`Output` is a union of `ScalarOutput`, `ArrayOutput`, `ParticlesOutput` and `ValueOutput`, and
TypeScript narrows it with `switch (output.kind)`.

Field descriptions from the API end up as JSDoc comments in the generated file, so they show up
on hover in the editor.

To generate against a local checkout instead, point the script at the file path, for example
`../lume-model-api/openapi.json`.

## Pin a version

Replace `<sha>` with a commit hash from this repo, not `main`. The generated types then only
change when someone bumps the hash on purpose, and a field that was renamed shows up as a
compile error at that point.

The `/api/v1` contract is additive only. New optional fields can appear, but existing fields
and paths aren't renamed or removed. A breaking change would go to a new `/api/v2`. So bumping
the hash should only ever add things, and an older pinned version keeps working against a newer
server.

## Known gaps

**The live stream's event names aren't typed.** OpenAPI has no standard way to describe a
stream of named events. The schema declares the stream route as `text/event-stream` with
`EvaluateV1Response` as the payload, which is the data of a `frame` event. The event names and
the `error` payload are typed by hand:

```ts
type FrameEvent = EvaluateResponse; // data of a "frame" event
type ErrorEvent = { message: string }; // data of an "error" event
```

**`stats` keys can be missing.** A particles output's `stats` omits any statistic the beam can't
provide, so code reads each one as possibly `undefined`.

**`input_sources` and `sources` values are typed `string`.** In practice they are always
`"request"`, `"baseline"` or `"live"`. A narrower type can be declared locally:

```ts
type InputSource = "request" | "baseline" | "live";
```

**Fields with defaults are generated as required.** openapi-typescript marks response fields
that have a default value (such as `unit` or `input_sources`) as always present, which matches
what the server sends.
