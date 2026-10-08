"""Model-side layer: load a `LUMEModel`, describe it, evaluate it, read its live inputs.

Nothing here knows about HTTP, FastAPI or Pydantic, so all of it is usable from a notebook
without starting a web server. See ../api for the service that wraps it.

- ``loader``       turns `LUME_MODELS` into per-model settings and builds each model
- ``introspect``   derives inputs, outputs, screens and the baseline from the instance
- ``evaluate``     baseline-merge, set, get, and conversion by variable kind
- ``demo``         a small in-repo `LUMEModel` used by `LUME_MODELS=demo` and by the tests
- ``live_inputs``  EPICS or synthetic input values for the live view
"""
