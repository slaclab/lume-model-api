# Notes for agents working on this package

An index of the traps that are invisible in the code, and where the explanations live. It is
deliberately short: every behaviour is documented in exactly one place, and duplicating an
explanation here is how the docs drifted the last time.

| Topic | Canonical home |
| --- | --- |
| What the service is, quickstart, env vars | [`README.md`](README.md) |
| The wire contract, every endpoint, the metrics | [`docs/API.md`](docs/API.md) |
| Layering, the pool, the live hub, warmup, baseline merge | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| The model author's contract, `LUME_MODELS` in full | [`docs/ADDING_A_MODEL.md`](docs/ADDING_A_MODEL.md) |
| Image build, apply, probes, sizing, scaling, packaging | [`docs/DEPLOY.md`](docs/DEPLOY.md) |
| A model that breaks itself, and why 503 rather than 400 | [`docs/POISONED_WORKER.md`](docs/POISONED_WORKER.md) |

## This repo was split out of another one, recently

The code lived in `slaclab/lume-visualizations` until 2026-09-09 and still does, unchanged, in
parallel. That repo keeps the React UI that was this API's first consumer.

- **`webapp`, `webapp.backend` and `lume_visualizations` are dead names here.** Any occurrence is
  stale text. `webapp/backend/` became `lume_model_api/api/`, `lume_visualizations/` became
  `lume_model_api/model/`.
- **History is preserved but paths are not.** Use `git log --follow`, or `git blame` will look like
  a file has no history. Commits before `a923530` refer to the old paths.
- **Nothing here has ever been pushed or deployed.** No GitHub remote, and no image exists under
  `ghcr.io/slaclab/lume-model-api`, so `deploy/kubernetes` points at a tag that does not resolve.

## Do not undo these

Each of these is a decision that looks like a bug or an oversight.

**A dead pool is never rebuilt.** `ProcessPoolExecutor` latches broken, so one lost worker means
the pool can never serve another evaluate. Rebuilding it would spawn K replacements while the old
ones tear down, peaking at twice the model memory and turning a restart-recoverable failure into an
OOM kill. It is marked dead instead, evaluates 503, and the pod restarts. A
`LUME_EVALUATE_TIMEOUT_S` expiry is fatal for the same reason: a subprocess evaluate cannot be
cancelled, so that worker is unreclaimable. A worker whose model broke itself takes the same route,
even though its process is alive, because nothing in this pod can rebuild that model either. The
three causes are distinguished by the `reason` label on `lume_pool_dead` and by `dead_reason`. See
ARCHITECTURE, "When a worker dies".

**A model can fail leaving the instance alive and inconsistent, and exception type alone cannot
tell that from a bad request.** That is why `model/evaluate.py` runs lume's own validation loop
itself in `_prevalidate` before calling `model.set()`, rather than inferring attribution from what
was raised. lume raises `ValueError` and `TypeError` for a bad value, and so do numpy and a broken
lattice, so a poisoned worker used to report a request that set nothing at all as the caller's
fault with a 400, which told the client not to retry and so escalated to nothing. Anything
escaping `model.set()` after pre-validation passes is the model's own failure and becomes
`ModelUnusable`, which must stay a `RuntimeError`: `UnknownVariable` and `InvalidInput` are both
`ValueError`s and `main.evaluate_v1` catches that pair to answer 400. Do not widen
`_prevalidate` past what `LUMEModel.set` checks either, because an over-eager classifier turns
every bad request into a latched worker and a pod restart. See ARCHITECTURE, "A model that breaks
itself is `unusable`", and POISONED_WORKER for the measurements.

**`model.reset()` must never be called on a Bmad model**, and this service never does. It is the
obvious-looking recovery and it is destructive and permanent even on a healthy instance:
`LUMEBmadModel.__init__` snapshots `_initial_state` before `track_type` is set to `"beam"`, so
`reset()` writes back `"single"` and unregisters 24 outputs including every screen. Reason
recorded in ARCHITECTURE so nobody reaches for it.

**`GET /healthz` exists so the probes can restart such a pod, and must stay out of
`openapi.json`.** `include_in_schema=False` is load-bearing: not a contract a consumer may pin, and
adding it needed no schema regeneration. Do not make `GET /api/v1/models` the probe target, since
that route has to keep listing the models that still work when one pool has died.

**Live input PVs are never pruned permanently.** An unreadable id is skipped for that frame only.
An earlier version dropped every id that failed its first read, so one unreachable CA gateway at
startup permanently degraded the pod and the stream served design values labelled as live for the
rest of its life. Zero connected PVs raises rather than returning `{}`, for the same reason.

**Non-finite numbers are rejected inbound and rendered `null` outbound, on purpose.** A NaN knob
passes lume's range check silently (every comparison against NaN is False) and produces a NaN beam
that looks like a physics result, so a request carrying one is a 422. A NaN *result* is legitimate
physics, so it becomes JSON `null` on both paths rather than costing the frame.

**`model`, `version` and `input_sources` are attached by the sender, not the serializer**, and both
senders (`main.evaluate_v1` and `LiveHub._run`) must attach all three. Only the sender knows
provenance. `SENDER_ADDED` in `tests/test_wire_shape.py` pins the set.

**`result_to_wire` must emit every key unconditionally, and every requested output id.** The SSE
path has no `response_model`, so a conditional key reaches a client genuinely missing and does not
change `openapi.json`, which means no consumer can detect it. Explained in
[API.md](docs/API.md#result_to_wire-emits-every-key-on-every-call-and-every-requested-output-id).

**Warmup is sequential across models** and the baseline merge applies every non-constant knob.
Neither is an optimization opportunity. See ARCHITECTURE.

**The baseline merge is only correct while no two inputs alias one control**, and that is what
`introspect._resolve_aliases` enforces. Because the merge writes every non-constant knob in a single
`model.set()`, two writable handles on one underlying control are both written and the one applied
last wins. virtual-accelerator publishes exactly that for every magnet, mapping `BCTRL` and `BDES`
to the same variable class, so setting only `BCTRL` was a silent no-op: the response echoed the
caller's value with `source: "request"` while the magnet held its default, and a full-range quad
scan came back perfectly flat. One handle per control is published as an input and the rest become
read-only outputs carrying `alias_of`. Do not "simplify" this away by trusting ids to be distinct.

**No per-model tables.** No screen list, no input allowlist, no range override file, no units map.
The previous version had all of those, hand-typed for one model, and they drifted. A missing range
or unit is fixed upstream in the model. See ADDING_A_MODEL, "Pushing metadata upstream".
`introspect.ALIAS_PREFERENCE` is the one near-exception and is deliberately not a table: it holds
final id segments, not variable ids, and it only breaks a tie the model itself leaves ambiguous. It
carries a `REVISIT_ALIASES` note for when a non-Bmad backend arrives.

**No unprefixed default-model route.** `/api/config` and friends are gone and 404 on purpose, and
`tests/test_app_demo.py` asserts it. An alias for "the first model" would work by accident on a
one-model pod and break when a second is added.

**No mock mode.** The demo model in `model/demo.py` is a real `LUMEModel` on the same describe,
evaluate, serialize and pool path as `cu_hxr_staged`, which is what makes a green test run mean
anything. It replaced a mock in the api layer that derived every screen from one knob, so switching
screens changed nothing and the bug was invisible until someone ran a real model. Keep it
exercising every branch (a model range, a derived range, a constant, two screens with one image
between them, non-image arrays, a plain scalar), because CI has no other model, and keep it
deterministic, which the wire-shape and subsampling tests rely on.

**`lume-base` is a required dependency, not an extra.** Its variable classes are how the service
discovers inputs, outputs and screens (`introspect.variable_kind` does `isinstance` against them),
and the demo model subclasses `LUMEModel`. Safe as a hard dependency: pure Python plus numpy, h5py
and openpmd-beamphysics.

## Lazy imports are deliberate. Do not hoist them

| Import | Where | Why it must stay lazy |
| --- | --- | --- |
| the hosted model (`virtual_accelerator`, ...) | `model/loader.py`, `build_model` via `importlib` | Not on PyPI, installed from a pinned git ref in the image only, and it pulls torch or pytao. Never imported in the main process. |
| `scipy.ndimage` | `api/serialize.py`, `_smooth` | Only the opt-in `smooth_images_sigma_px` path needs it, and this module is imported by every worker on every startup. |
| `epics` (pyepics) | `model/live_inputs.py`, `EpicsInputProvider.__init__` | An `[epics]` extra, absent in CI. Must also be imported **after** the CA env vars are set, because channel access reads them once at import. |
| `lume.variables` | `model/introspect.py` `variable_kind`, `model/evaluate.py` `_coerce_control` | Keeps main-process import cheap. Not optional, see above. |
| `beamphysics.ParticleGroup` | `model/demo.py`, `_beam` | Only needed when the demo model actually runs. |
| `LiveHub`, `TooManyStreams` | `api/main.py`, `lifespan` and `live_stream` | Only the `live` and `all` roles may load them. |
| `describe`, `build_model`, `result_to_wire`, `evaluate`, `tempfile` | `api/pool.py`, `_init_worker` and `_worker_evaluate` | Worker-side, imported by absolute module path because `spawn` re-imports rather than inheriting memory. `ModelUnusable` from the same module is imported at the top instead, because `_submit` runs in the main process and has to name it to classify what a worker raised. |

The test that this still holds is that `pytest` and `python scripts/dump_openapi.py` run in a plain
venv with no torch, no pytao, no Bmad and no EPICS. Hoisting one of these fails CI on an unrelated
pull request.

## Changing the response schema: three things move together

Regenerating `openapi.json` under the pinned fastapi and pydantic, updating the longhand field sets
in `tests/test_api_contract.py`, and emitting the new key from the serializer or from both senders.
The procedure is in [API.md](docs/API.md#changing-the-contract).

**Additive only, from the move to `/api/v1/models/{name}/...` onward.** Renaming or removing a
field, making an optional one required, or moving a path again needs `/api/v2`, because consumers
pin a git ref and a rename here is silent for them until they refetch. The `BREAKING` string in
`tests/test_api_contract.py` says the same thing and the two must agree.

## Smaller things that look broken and are not

- **The lume stack is pinned in the Dockerfile on purpose, and `LUME_BMAD_REF` must stay a git
  ref.** virtual-accelerator declares bare `lume-*` requirements, and the released lume-bmad drops
  every beam variable, for the reason in the `output_beam` bullet below.
  `scripts/setup-dev-env.sh` mirrors all of those pins,
  so the two move together. `conda install bmad pytao` is still unpinned, so a fresh solve can pull
  a Bmad that rejects a model's `tao.init`. See DEPLOY, "Building the image".
- **The k8s objects are a new set, not a rename.** `lume-model-api-*` in namespace
  `lume-model-api` on the `/lume-model-api` prefix run beside the old monolith's `lume-monitor-*`
  objects, which belong to the lume-visualizations repo. Nothing here treats `/live-monitor` as
  this API's path.
- **Particle `coords` ships a `weight` array** that phase-space plots do not use, for about 17%
  extra payload. A distribution without per-particle charge is incomplete for physics callers, so
  do not add an `include_weight` flag. `stats` are computed on the full beam, before subsampling.
- **`smooth_images_sigma_px` is off by default and does not renormalize.** Blurring every 2-D array
  is wrong for a generic host, and a rescale would invalidate the declared unit.
- **`output_beam` is excluded from screens** (a screen is somewhere you can point a camera), and
  writable non-scalar variables are published nowhere, logged at INFO so a missing knob is
  explainable. That rule is right and stays, but know its failure mode: a variable that is read-only
  by contract can still arrive with `read_only=False`, because lume's `ReadOnlyActionMixin` enforces
  the flag with a pydantic validator and pydantic does not validate defaults unless the model sets
  `validate_default`, which `lume.variables.Variable` does not. A model author who mixes in
  `ReadOnlyActionMixin` and leaves the flag alone gets a variable this package silently drops. That
  is exactly how every `<ele>_beam` on `cu_hxr_staged` disappeared, leaving screen images with no
  particles and `screens: []`. Nothing here checks for it. The Dockerfile pin plus its build-time
  assertion is what keeps it from recurring, so do not treat either as redundant.
- **`lume_model_api/static/` is gitignored.** It is where a UI repo's build lands. Never commit one.
- **The live producer must stay `replicas: 1`.** N replicas would each read EPICS and evaluate,
  costing N times the work and showing viewers divergent frames.
- **`README.md` must keep existing.** `pyproject.toml` declares `readme = "README.md"` and the
  Dockerfile copies it, so the build fails without it.

## House style

No emojis, no em dashes and no semicolons in prose, in docs or in comments. Complete sentences.
Short paragraphs, and tables only for facts that actually enumerate. Comments and docs say *why*,
not what. No line numbers in prose: a file path plus a function or symbol name does not rot.
