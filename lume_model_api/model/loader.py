"""Turn the `LUME_MODELS` setting into the list of models this process will host.

The only model-aware code in this package is the shortcut table below, and even that is a
convenience: any factory reachable on `sys.path` works as `module.path:factory_function`
with no code change here.

`LUME_MODELS` names one or more models, each keyed by the URL name it answers on at
`/api/v1/models/<name>/...`. `models_from_env` is the single place those rules live, so the
API layer only ever sees a list of `ModelSetting`.

Lattice locations (`LCLS_LATTICE`, `FACET2_LATTICE`) are the model's business, not this
service's. virtual-accelerator raises a clear error when one is missing, so nothing here
guesses a path.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# The factory that the `demo` shortcut resolves to. Named so `is_demo` does not have to
# string-match the shortcut, which a caller can bypass by giving the full path.
DEMO_FACTORY = "lume_model_api.model.demo:make_demo_model"

DEFAULT_MODEL = "demo"

# A URL name is one path segment, so no dots and no slashes. Excluding dots keeps `.` and
# `..` out of the segment, which is what would otherwise need normalising before lookup.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

DEFAULT_WORKERS = 4

# name -> (factory path, default kwargs). The kwargs are defaults only: a per-model `kwargs`
# is merged over them, so a shortcut never blocks an override.
SHORTCUTS: dict[str, tuple[str, dict]] = {
    "demo": (DEMO_FACTORY, {}),
    "cu_hxr_staged": (
        "virtual_accelerator.models.cu_hxr:get_cu_hxr_staged_model",
        {"n_particles": 1000, "end_element": "TD11"},
    ),
    "cu_hxr_bmad": (
        "virtual_accelerator.models.cu_hxr:get_cu_hxr_bmad_model",
        {"track_beam": True, "end_element": "TD11"},
    ),
    "facet_staged": ("virtual_accelerator.models.facet2:get_facet_staged_model", {}),
    "facet_bmad": ("virtual_accelerator.models.facet2:get_facet_bmad_model", {}),
}


class ModelRefError(ValueError):
    """A model reference is neither a known shortcut nor a `module:factory` reference."""


@dataclass
class ModelSetting:
    """One hosted model: the URL name plus everything needed to build and pool it."""

    name: str
    factory_path: str
    kwargs: dict = field(default_factory=dict)
    workers: int = DEFAULT_WORKERS
    max_inflight: int = DEFAULT_WORKERS * 4
    is_demo: bool = False


def model_ref_from_env() -> str:
    return os.environ.get("LUME_MODEL") or DEFAULT_MODEL


def kwargs_from_env() -> dict:
    """Parse `LUME_MODEL_KWARGS`, a JSON object merged over the shortcut's defaults."""
    raw = os.environ.get("LUME_MODEL_KWARGS", "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ModelRefError(f"LUME_MODEL_KWARGS must be a JSON object, got {type(parsed).__name__}")
    return parsed


def resolve(model_ref: str, overrides: dict | None = None) -> tuple[str, dict, bool]:
    """Resolve a shortcut name or `module:factory` path.

    Returns `(factory_path, kwargs, is_demo)`. Resolution is deliberately import-free so
    the main process can decide what a worker will build without loading torch or Bmad.
    """
    shortcut = SHORTCUTS.get(model_ref)
    if shortcut is not None:
        factory_path, kwargs = shortcut[0], dict(shortcut[1])
    elif ":" in model_ref:
        factory_path, kwargs = model_ref, {}
    else:
        known = ", ".join(sorted(SHORTCUTS))
        raise ModelRefError(
            f"Unknown model reference {model_ref!r}. Use one of: {known}, "
            "or a full reference like 'my_package.models:make_model'."
        )
    kwargs.update(overrides or {})
    return factory_path, kwargs, factory_path == DEMO_FACTORY


def build_model(factory_path: str, kwargs: dict | None = None):
    """Import and call a `module:factory` reference.

    Called in a pool worker, never in the main process: importing a real model pulls in
    torch / pytao, and two of those in one process is the segfault pool.py exists to avoid.
    """
    module_path, _, attribute = factory_path.partition(":")
    if not module_path or not attribute:
        raise ModelRefError(
            f"Model reference {factory_path!r} must be 'module.path:factory_function'."
        )
    module = importlib.import_module(module_path)
    try:
        factory = getattr(module, attribute)
    except AttributeError as exc:
        raise ModelRefError(f"{module_path} has no attribute {attribute!r}") from exc
    return factory(**(kwargs or {}))


def _pool_defaults() -> tuple[int, int | None]:
    """Pod-wide fallbacks for `workers` and `max_inflight`.

    These are per-model defaults, not a pod budget. Three models at the default of four
    workers each is twelve model subprocesses, so raising the pod's memory is part of adding
    a model.
    """
    workers = int(os.environ.get("LUME_POOL_WORKERS", str(DEFAULT_WORKERS)))
    raw = os.environ.get("LUME_MAX_INFLIGHT", "").strip()
    return workers, int(raw) if raw else None


def _check_name(name: str) -> None:
    if not NAME_PATTERN.match(name):
        raise ModelRefError(
            f"Model name {name!r} is not usable as a URL segment. Names must match "
            f"{NAME_PATTERN.pattern} (letters, digits, underscore and dash, no dots)."
        )


def _setting(
    name: str,
    factory_path: str,
    kwargs: dict,
    is_demo: bool,
    workers: int | None = None,
    max_inflight: int | None = None,
) -> ModelSetting:
    default_workers, default_max_inflight = _pool_defaults()
    resolved_workers = int(workers if workers is not None else default_workers)
    if max_inflight is not None:
        resolved_max_inflight = int(max_inflight)
    elif default_max_inflight is not None:
        resolved_max_inflight = default_max_inflight
    else:
        resolved_max_inflight = 4 * resolved_workers
    return ModelSetting(
        name=name,
        factory_path=factory_path,
        kwargs=kwargs,
        workers=resolved_workers,
        max_inflight=resolved_max_inflight,
        is_demo=is_demo,
    )


def _models_from_json(raw: str) -> list[ModelSetting]:
    """`LUME_MODELS` as a JSON object keyed by URL name."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Quote the value: a YAML string copied from a shell example carries literal quotes,
        # which is the usual cause and is invisible in the bare parser message.
        raise ModelRefError(f"LUME_MODELS is not valid JSON ({exc}). Value was: {raw!r}") from exc
    if not isinstance(parsed, dict):
        raise ModelRefError(
            f"LUME_MODELS must be a JSON object keyed by model name, got {type(parsed).__name__}."
        )
    settings = []
    for name, spec in parsed.items():
        _check_name(name)
        if not isinstance(spec, dict):
            raise ModelRefError(
                f"LUME_MODELS[{name!r}] must be an object with optional 'factory', 'kwargs', "
                f"'workers' and 'max_inflight', got {type(spec).__name__}."
            )
        kwargs = dict(spec.get("kwargs") or {})
        factory = spec.get("factory")
        if factory:
            # An explicit factory means the kwargs are exactly what was given, so the key's
            # shortcut defaults never apply even when the key happens to name a shortcut.
            factory_path, resolved_kwargs, is_demo = resolve(str(factory), kwargs)
        else:
            if name not in SHORTCUTS:
                known = ", ".join(sorted(SHORTCUTS))
                raise ModelRefError(
                    f"LUME_MODELS[{name!r}] has no 'factory', so {name!r} must be a shortcut. "
                    f"Known shortcuts: {known}."
                )
            factory_path, resolved_kwargs, is_demo = resolve(name, kwargs)
        settings.append(
            _setting(
                name,
                factory_path,
                resolved_kwargs,
                is_demo,
                workers=spec.get("workers"),
                max_inflight=spec.get("max_inflight"),
            )
        )
    return settings


def _models_from_list(raw: str) -> list[ModelSetting]:
    """`LUME_MODELS` as a comma list of shortcuts and `name=module:function` items."""
    settings = []
    seen: set[str] = set()
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        name, separator, reference = item.partition("=")
        name = name.strip()
        if separator:
            reference = reference.strip()
        elif ":" in name:
            raise ModelRefError(
                f"LUME_MODELS entry {item!r} needs a URL name: write 'name={item}'. A bare "
                "'module:function' would put ':' in the URL."
            )
        else:
            reference = name
        _check_name(name)
        if name in seen:
            raise ModelRefError(f"LUME_MODELS names {name!r} twice. Each URL name must be unique.")
        seen.add(name)
        factory_path, kwargs, is_demo = resolve(reference)
        settings.append(_setting(name, factory_path, kwargs, is_demo))
    if not settings:
        raise ModelRefError(f"LUME_MODELS names no models. Value was: {raw!r}")
    return settings


def _model_from_single() -> list[ModelSetting]:
    """The one-model fallback: `LUME_MODEL` plus `LUME_MODEL_KWARGS`."""
    reference = model_ref_from_env()
    factory_path, kwargs, is_demo = resolve(reference, kwargs_from_env())
    if reference in SHORTCUTS:
        name = reference
    else:
        # A full reference has no URL name of its own, so take the factory function's. Logged
        # because the resulting URL is not something the operator typed anywhere.
        name = factory_path.partition(":")[2]
        if not NAME_PATTERN.match(name):
            raise ModelRefError(
                f"LUME_MODEL {reference!r} gives the URL name {name!r}, which is not usable as "
                'a URL segment. Set LUME_MODELS instead, as {"myname": {"factory": "..."}}.'
            )
        logger.info(
            "LUME_MODEL %s has no URL name of its own, hosting it as %r after its factory "
            "function. Set LUME_MODELS to choose the name.",
            reference,
            name,
        )
    return [_setting(name, factory_path, kwargs, is_demo)]


def models_from_env() -> list[ModelSetting]:
    """Every model this process should host, in the order `LUME_MODELS` gave them."""
    raw = os.environ.get("LUME_MODELS", "").strip()
    if raw:
        # The single-model variables are silently dead once LUME_MODELS is set, and the image
        # ships a LUME_MODELS default, so a pod that overrides only LUME_MODEL would otherwise
        # look configured and serve something else.
        for ignored in ("LUME_MODEL", "LUME_MODEL_KWARGS"):
            if os.environ.get(ignored, "").strip():
                logger.warning(
                    "%s is set but LUME_MODELS takes precedence, so %s is ignored. Move it "
                    "into LUME_MODELS.",
                    ignored,
                    ignored,
                )
        settings = _models_from_json(raw) if raw.startswith("{") else _models_from_list(raw)
    else:
        settings = _model_from_single()
    for setting in settings:
        logger.info(
            "hosting %s -> %s workers=%d at /api/v1/models/%s",
            setting.name,
            setting.factory_path,
            setting.workers,
            setting.name,
        )
    return settings
