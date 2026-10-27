"""LUME model API: a stateless HTTP service that hosts any `LUMEModel`.

`LUME_MODELS` is the only model-specific setting. Everything a client learns about a model
(its inputs with ranges and units, its outputs, its screens) is derived from the instance's
`supported_variables`, so hosting a new model needs no code change here.

One process can host several models at once, each addressed by the name it is configured
under: ``GET /api/v1/models`` lists them and the rest of the routes live under
``/api/v1/models/{name}/``.

Two subpackages, kept apart because they have different dependency weights:

- ``model``  loads, describes and evaluates the models. Imports the heavy simulation stack
  lazily, so nothing beyond numpy and lume-base loads until a real evaluate runs.
- ``api``    the FastAPI app. Serves ``GET /api/v1/models``,
  ``GET /api/v1/models/{name}/config``, ``POST /api/v1/models/{name}/evaluate`` and a
  read-only SSE live stream, and never touches a model except through its subprocess pool.

Run it with ``uvicorn lume_model_api.api.main:app``.
"""
