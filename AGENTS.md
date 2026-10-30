# Notes for agents working on this package

A short list of things that are easy to break without noticing. Each entry links to where it's
explained. Don't repeat those explanations here, since two copies drift apart.

Start with the [Docs](README.md#docs) section of the README, which maps every topic to its doc.
Write docs following [docs/WRITING_DOCS.md](docs/WRITING_DOCS.md).

## Repo history

The code was split out of `slaclab/lume-visualizations` on 2026-09-09. That repo keeps the React
UI that first used this API.

- `webapp`, `webapp.backend` and `lume_visualizations` are old names. `webapp/backend/` became
  `lume_model_api/api/` and `lume_visualizations/` became `lume_model_api/model/`.
- History was kept but paths changed. Use `git log --follow`. Commits before `a923530` use the
  old paths.
- Nothing has been deployed yet, and no image exists under `ghcr.io/slaclab/lume-model-api`, so
  `deploy/kubernetes` points at a tag that doesn't resolve.

## Do not undo these

Each looks like a bug or an oversight and isn't. Details are in
[DESIGN_DECISIONS.md](docs/maintainer/DESIGN_DECISIONS.md) unless another link is given.

| Rule | Entry |
| --- | --- |
| A dead pool is never rebuilt. The pod restarts instead. | [Dead pools are not rebuilt](docs/maintainer/DESIGN_DECISIONS.md#dead-pools-are-not-rebuilt) |
| An evaluate timeout kills the pool. | [Evaluate timeouts are fatal](docs/maintainer/DESIGN_DECISIONS.md#evaluate-timeouts-are-fatal) |
| `_prevalidate` decides 400 vs 503. `ModelUnusable` stays a `RuntimeError`. Don't make `_prevalidate` stricter than `LUMEModel.set`. | [Model failures are 503, not 400](docs/maintainer/DESIGN_DECISIONS.md#model-failures-are-503-not-400) |
| Never call `model.reset()` on a Bmad model. Recovery is a model's own `recover()`. | [No model.reset() on Bmad models](docs/maintainer/DESIGN_DECISIONS.md#no-modelreset-on-bmad-models), [Recovery belongs in the model](docs/maintainer/DESIGN_DECISIONS.md#recovery-belongs-in-the-model) |
| `GET /healthz` is the probe target and stays out of `openapi.json` (`include_in_schema=False`). The model list is not a health check. | [/healthz is separate from the model list](docs/maintainer/DESIGN_DECISIONS.md#healthz-is-separate-from-the-model-list) |
| Unreadable live PVs are skipped for one frame, never dropped. Zero connected PVs raises. | [Live PVs are never dropped](docs/maintainer/DESIGN_DECISIONS.md#live-pvs-are-never-dropped) |
| NaN and Inf are rejected in requests (422) and sent as `null` in responses. | [NaN is rejected in and sent as null out](docs/maintainer/DESIGN_DECISIONS.md#nan-is-rejected-in-and-sent-as-null-out) |
| `model`, `version` and `input_sources` are added by both senders, `main.evaluate_v1` and `LiveHub._run`. `SENDER_ADDED` in `tests/test_wire_shape.py` pins the set. | [Sender-attached keys](docs/maintainer/DESIGN_DECISIONS.md#sender-attached-keys) |
| `result_to_wire` emits every key and every requested output id, every time. | [Every key on every response](docs/maintainer/CHANGING_THE_API.md#every-key-on-every-response) |
| Warmup is one model at a time. The baseline merge sets every non-constant input. | [Warmup is sequential](docs/maintainer/DESIGN_DECISIONS.md#warmup-is-sequential), [Baseline merge](docs/maintainer/ARCHITECTURE.md#baseline-merge) |
| One input per control. `introspect._resolve_aliases` demotes aliases like `BDES` next to `BCTRL`. | [One input per control](docs/maintainer/DESIGN_DECISIONS.md#one-input-per-control) |
| No per-model tables (screens, ranges, units). `ALIAS_PREFERENCE` is the only name list, and it holds id suffixes. | [No per-model tables](docs/maintainer/DESIGN_DECISIONS.md#no-per-model-tables) |
| No unprefixed default-model route. `/api/config` returns 404 on purpose. | [No default-model route](docs/maintainer/DESIGN_DECISIONS.md#no-default-model-route) |
| No mock mode. `model/demo.py` is a real `LUMEModel` and has to keep covering every branch, deterministically. | [No mock mode](docs/maintainer/DESIGN_DECISIONS.md#no-mock-mode) |
| `lume-base` is a required dependency. | [lume-base is a required dependency](docs/maintainer/DESIGN_DECISIONS.md#lume-base-is-a-required-dependency) |
| Particle `coords` includes `weight`. No flag to drop it. | [Particle coords include weight](docs/maintainer/DESIGN_DECISIONS.md#particle-coords-include-weight) |
| Image smoothing is off by default and doesn't renormalize. | [Image smoothing is off by default](docs/maintainer/DESIGN_DECISIONS.md#image-smoothing-is-off-by-default) |
| `output_beam` isn't a screen, and writable non-scalars aren't published. A beam variable missing `read_only=True` silently disappears. | [output_beam and writable non-scalars are not published](docs/maintainer/DESIGN_DECISIONS.md#output_beam-and-writable-non-scalars-are-not-published) |
| The live Deployment stays at `replicas: 1`. | [Live producer is a singleton](docs/maintainer/DESIGN_DECISIONS.md#live-producer-is-a-singleton) |

## Lazy imports

Several imports are inside functions on purpose. The table, and the reason for each, is in
[ARCHITECTURE.md, Layout](docs/maintainer/ARCHITECTURE.md#layout). The check is that `pytest` and
`python scripts/dump_openapi.py` run in a plain venv with no torch, pytao, Bmad or EPICS. Moving
one of these imports to module level breaks CI.

## Changing the response schema

Three things change together: `openapi.json` (regenerated under the fastapi and pydantic versions
pinned in `.github/workflows/contract.yml`), the field sets in `tests/test_api_contract.py`, and
the key emitted by the serializer or by both senders. Changes are additive only. Renaming or
removing a field, making an optional one required, or moving a path needs `/api/v2`. The
`BREAKING` message in `tests/test_api_contract.py` says the same thing and the two must agree.
See [CHANGING_THE_API.md](docs/maintainer/CHANGING_THE_API.md).

Field descriptions go in `Field(description=...)`, not `#` comments, because only the former
reaches `openapi.json` and ReDoc.

## Other things that look wrong and aren't

- **The lume stack is pinned in the `va` extra in `pyproject.toml`.** virtual-accelerator declares
  bare `lume-*` requirements. The Dockerfile and the README's local install both use that extra,
  so don't pin anywhere else. `conda install bmad pytao` is unpinned. See
  [Build the image](docs/maintainer/DEPLOY.md#build-the-image).
- **The k8s objects are new, not renamed.** `lume-model-api-*` objects in namespace
  `lume-model-api` run beside the old `lume-monitor-*` objects, which belong to
  lume-visualizations.
- **`lume_model_api/static/` is gitignored.** A UI build lands there. Never commit one.
- **`docs/archive/` is gitignored.** It holds old planning docs for local reference only.
- **`README.md` has to exist.** `pyproject.toml` declares it as the readme and the Dockerfile
  copies it, so the build fails without it.
