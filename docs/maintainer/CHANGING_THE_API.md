# Changing the API

`openapi.json` at the repo root is generated from the app and committed. Consumers generate their
TypeScript types from it at a pinned git ref, so a renamed field shows up as a compile error in
their build. This doc covers what can change under `/api/v1` and the steps for changing it.

## Rules

`/api/v1` is additive only:

- Adding an optional field or a new route is fine. `input_sources`, `sources` and
  `variable_class` were added this way.
- Renaming or removing a field, making an optional field required, or moving a path needs
  `/api/v2`. Consumers pin a git ref, so a rename here breaks them silently until they regenerate.

The `BREAKING` message in `tests/test_api_contract.py` says the same thing, and the two should
stay in sync.

Description and docstring changes don't affect the contract, but they do change `openapi.json`, so
the schema still has to be regenerated.

## Procedure

After changing a route in `api/main.py` or a model in `api/schemas.py`:

1. Regenerate the schema with the same fastapi and pydantic versions CI uses. Other versions
   produce a different but equivalent schema, and the CI drift check fails on the diff.

   ```bash
   pip install fastapi==0.141.1 pydantic==2.13.4
   python scripts/dump_openapi.py
   ```

   `LUME_ROOT_PATH` must not be set while generating, because it adds a `servers` entry that ties
   the schema to one deployment. `scripts/dump_openapi.py` unsets it, and
   `tests/test_api_contract.py` checks that there's no `servers` key.

2. Update the expected field sets in `tests/test_api_contract.py` by hand. They're written out in
   full so a regenerated schema can't hide a rename.

3. Make sure the new key is actually sent. For a field in `EvaluateV1Response`, either
   `serialize.result_to_wire` emits it, or both senders (`main.evaluate_v1` and `LiveHub._run`)
   attach it. See the next section.

4. Run `pytest`. The `contract` workflow in `.github/workflows/contract.yml` runs the tests, then
   regenerates `openapi.json` and fails if it differs from the committed file.

Field descriptions come from `Field(description=...)` in `api/schemas.py`, and route text comes from
the route's `summary` and docstring. `#` comments don't reach `openapi.json`, so they're the place
for maintainer notes.

The published reference at https://slaclab.github.io/lume-model-api/ is built from the committed
`openapi.json` by `.github/workflows/docs.yml`.

## Every key on every response

The HTTP route has a `response_model`, so FastAPI fills in any key the serializer leaves out. The
live stream doesn't. `live_stream` sends the dict through `json.dumps` directly, so a key that
`result_to_wire` only emits sometimes is missing for streaming clients, even though their types
(generated from `EvaluateV1Response`) say it's there. Leaving the key out doesn't change
`openapi.json`, so no consumer can catch it by regenerating.

Two rules follow:

- `serialize.result_to_wire` and `serialize.serialize_output` emit every key on every call.
- Every requested output id appears in `outputs`. A missing id is a `KeyError` in the client.

`tests/test_wire_shape.py` checks both. It runs over both demo screens, because a key that depends
on image data passes on `OTR_B` and only fails on `OTR_A`, which has no image.

`model`, `version` and `input_sources` are the exceptions. The senders add them, and
`SENDER_ADDED` in `tests/test_wire_shape.py` lists them. Adding another sender-attached key means
updating both senders and that set.

OpenAPI doesn't describe the SSE stream at all. The `frame` and `error` event names and the
`{"message": ...}` error payload are only documented in the frontend docs, and changing any of them
breaks clients with no schema change.
