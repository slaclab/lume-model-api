# Notes for agents working on this package

`README.md` says what this service is and `docs/BACKEND.md` documents the API contract and
deploys. This file covers the things that are easy to break and are not obvious from reading
the code, mostly cases where the obvious cleanup is wrong.

## This repo was split out of another one, recently

The code lived in `slaclab/lume-visualizations` until 2026-09-09 and still does, unchanged, in
parallel. That repo keeps the React UI that was this API's first consumer.

Consequences:

- **`webapp`, `webapp.backend` and `lume_visualizations` are dead names here.** If you find one
  in a comment or doc, it is stale text that got missed. `webapp/backend/` became
  `lume_model_api/api/` and `lume_visualizations/` became `lume_model_api/model/`.
- **History is preserved but paths are not.** `git log --follow` and `git blame` work, and
  commits before `a923530` refer to the old paths. Use `--follow` or you will think a file has
  no history.
- **Nothing here has ever been pushed or deployed.** There is no GitHub remote and no image
  exists under the `ghcr.io/slaclab/lume-model-api` name, so `deploy/kubernetes` points at a
  tag that does not resolve yet.

## Layering: `api` depends on `model`, never the reverse

`lume_model_api/model/` knows nothing about HTTP, FastAPI or Pydantic. It returns `BeamFrame`
dataclasses. `lume_model_api/api/` turns those into wire dicts. Keep that direction, because it
is what lets the model layer be used from a notebook without starting a web server.

Inside `model/` there is one more rule, already noted in `registry.py`: `registry` must not
import `beam_monitor`, because `beam_monitor` imports the registry.

## Lazy imports are deliberate. Do not hoist them to module top

Several imports sit inside functions on purpose. Moving them to the top of the file is the most
likely way to break this package, and it breaks it in CI rather than at review time.

| Import | Where | Why it must stay lazy |
| --- | --- | --- |
| `virtual_accelerator` | `model/registry.py:30` | Not on PyPI. Installed from a pinned git ref in the image only. |
| `scipy.ndimage` | `model/beam_monitor.py:49` | Only the real screen PSF needs it. |
| `epics` (pyepics) | `model/epics_controls.py:39,44` | An `[epics]` extra, absent in CI. Also must be imported *after* the CA env vars are set. |
| `ModelImageSource` | `api/source.py:30` | Pulls in torch. Mock mode must never load it. |
| `LiveHub` | `api/main.py:89` | Only the live role needs it. |

The test that this still holds is simply that `pytest` and `python scripts/dump_openapi.py` run
in a plain venv with no torch, no pytao, no Bmad and no EPICS. If you hoist one of these, that
stops being true and CI fails on an unrelated pull request.

`caproto` is the exception: it is imported at module top in `model/fake_epics_ioc.py`, which
`api/main.py` imports, so it loads on every startup and is a required dependency. That is why it
is not an extra.

## The model pool is processes, not threads, and that is not negotiable

`api/pool.py` uses a `spawn` `ProcessPoolExecutor`. Three constraints drive this:

- Two model instances in one process segfault (torch double-load), and `pytao` is not
  thread-safe. So process isolation is required, not just preferred.
- `fork` with torch and OpenMP is unsafe, hence `spawn`.
- `spawn` means workers re-import the package rather than inheriting memory, which is why
  `_init_worker` and `_worker_evaluate` import by absolute module path.

**Workers `chdir` into a fresh temp directory** (`pool.py:43`), so K workers writing files cannot
collide. Anything in `model/` that resolves a path relative to the current directory will read
the wrong place inside a worker while working fine in a test. Use absolute paths or paths derived
from `__file__`.

`HDF5_USE_FILE_LOCKING=FALSE` is set before HDF5 loads because K workers share the read-only
design-beam file and HDF5's default lock rejects the concurrent open with `[Errno 11]`.

## Mock mode does not vary output by screen

`LUME_MOCK=1` is the right way to develop and is what CI uses, but know its one sharp edge:
`MockImageSource.snapshot` derives every beam value from a single `knob` computed from two input
PVs (`SOLN:IN20:121:BCTRL` and `QUAD:IN20:525:BCTRL`). `screen_key` only selects the label and
whether an image exists at all.

So in mock mode **OTR3 and OTR4 return identical beam values**, and only OTR2 differs, by having
no image. If you are chasing "switching screens does not change the output", that is the mock,
not a bug. The real model genuinely differs per screen because each screen has its own
`particle_source`. Verify screen-dependent behaviour against the real model or not at all.

## Changing the response schema: three things move together

Editing `api/schemas.py` or a route means all of these, or CI fails:

1. `python scripts/dump_openapi.py`, and commit `openapi.json`.
2. Update the expected field sets in `tests/test_api_contract.py`. They are written out by hand
   on purpose, so that a regenerated snapshot cannot hide a rename.
3. If you added a field to `EvaluateV1Response`, add it to the dict in `frame_to_wire` too. See
   the next section.

**Regenerate `openapi.json` with the pinned versions.** `fastapi==0.141.1` and
`pydantic==2.13.4` generate the JSON schema, and a newer pair emits harmless but different
output. Regenerating under whatever pip resolved produces a large spurious diff, and committing
it breaks CI, which still uses the pins.

**Renaming or removing a v1 field needs `/api/v2`.** Adding an optional field is fine. Consumers
in other repos pin a git ref, so a rename here is silent for them until they refetch.

## `frame_to_wire` must emit every key unconditionally

The HTTP endpoint has a `response_model`, so FastAPI fills in anything the serializer omits. The
SSE stream does not: `api/main.py live_stream` hands the dict straight to `json.dumps`. A key
made conditional there reaches clients genuinely absent, and because clients type the stream
from `EvaluateV1Response` they assume it is present.

The trap is that this does not change `openapi.json`, so no consumer can detect it by refetching
the schema, and there is no type error anywhere. `tests/test_wire_shape.py` is the only thing
standing between a conditional key and a broken client, which is why it is parametrized over
every screen: a key conditional on image data passes on OTR3 and OTR4 and fails only on OTR2.

## Verifying that a packaging change actually works

Two failure modes here look like success. Both have bitten this repo and both are guarded in the
Dockerfile comments, but if you are changing packaging, test it properly.

**`pip install -e .` with the package directory absent exits 0.** It reports
`Successfully installed lume-model-api-0.1.0`, finds no packages, and then every import fails
with `ModuleNotFoundError` even after the code arrives. This is why the `Dockerfile` copies
`lume_model_api/` *before* installing. Reversing those two lines produces a broken image with a
green build.

**`uvicorn` always puts the current directory on `sys.path`.** Its CLI defaults `--app-dir` to
`""` and does `sys.path.insert(0, app_dir)` unconditionally. So launching from the repo root
imports the source tree whether or not the package is installed, and in the container
`WORKDIR /app` does the same. To actually test an install:

```bash
cd /tmp && LUME_MOCK=1 /path/to/.venv/bin/uvicorn --app-dir /nonexistent \
  lume_model_api.api.main:app --port 8001
```

Also check the wheel, because an editable install masks a bad `packages.find`:

```bash
pip wheel --no-deps . -w /tmp/wh && unzip -l /tmp/wh/*.whl | grep lume_model_api/
```

The `include = ["lume_model_api*"]` trailing `*` in `pyproject.toml` is load-bearing. Without it
setuptools matches the name exactly and silently drops both subpackages.

## Things that look broken and should be left alone

- **The image does not build from scratch.** The committed `VA_REF` points at a
  virtual-accelerator revision that removed `virtual_accelerator.models.staged_model`, which
  `model/registry.py:30` imports. Production runs an older recipe that is not in the repo. This
  predates the repo split and fixing it means porting `registry.py` to the new
  virtual-accelerator API. Do not bundle that with unrelated work. Details in
  `docs/BACKEND.md`.
- **k8s objects are still named `lume-monitor-*` in namespace `lume-visualizations`.** Only the
  image name was updated. Renaming the Deployments, Services or namespace would orphan the
  running production service rather than update it.
- **`distribution.coords` ships a `weight` array** that phase-space plots do not use, costing
  roughly 17% extra payload. Deliberate: a distribution without weights is incomplete for
  physics callers. Reasoning in `docs/BACKEND.md`. Do not add an `include_weight` flag.
- **`lume_model_api/static/` is gitignored.** It is where a UI repo's build lands. Never commit
  one here.
- **The `[project.scripts]` entry is `lume-model-api-fake-ioc`,** not `lume-fake-epics-ioc`. The
  old name still exists in `lume-visualizations`, and two packages claiming one script name means
  whichever installed last wins with no warning.

## House style

No em dashes and no semicolons in prose, in docs or in comments. Comments should say *why*, not
restate the code.
