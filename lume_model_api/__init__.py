"""LUME model API: a stateless HTTP service over the LCLS virtual-accelerator staged model.

Two subpackages, kept apart because they have different dependency weights:

- ``model``  the model / config / EPICS layer. Owns the physics and the PV names, and lazily
  imports torch and virtual_accelerator so nothing heavy loads until a real evaluate runs.
- ``api``    the FastAPI app. Serves ``POST /api/v1/evaluate`` plus a read-only SSE live
  stream, and never touches the model except through the subprocess pool.

Run it with ``uvicorn lume_model_api.api.main:app``.
"""
