"""HTTP layer: the FastAPI app, its schemas, the subprocess model pool and the live hub.

Depends on ``lume_model_api.model``, never the reverse. This is the only place Pydantic and
FastAPI appear, and the only place model values become wire dicts.
"""
