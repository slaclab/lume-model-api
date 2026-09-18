# Review and hardening, September 2026

A clarity, correctness and overengineering review of this package, and what was changed as a
result. Written as a record of intent: it says what was wrong, what was done, and what was
deliberately left alone. It is a snapshot, not a maintained document. The canonical homes for
the behaviour it describes are [ARCHITECTURE.md](ARCHITECTURE.md), [API.md](API.md),
[ADDING_A_MODEL.md](ADDING_A_MODEL.md), [DEPLOY.md](DEPLOY.md) and
[`../AGENTS.md`](../AGENTS.md).

Branch `generic-multi-model-api`. Tests went from 109 to 207. The committed `openapi.json` is
regenerated and carries no drift.

## What the review found

The package was in good shape structurally. The layering is real, the model-agnostic design
holds, and the demo model genuinely exercises the production path. The problems were in three
places: failure modes that were invisible rather than absent, a wire format that disagreed with
itself on the two paths, and documentation that had grown to roughly twice the size of the code
by repeating itself.

Three findings from the first pass were withdrawn or corrected under review, and they are
recorded here because the reasoning matters more than the conclusion.

- **`warmup()` was claimed to be unreliable. It is not.** The concern was that
  `ProcessPoolExecutor` skips spawning a worker when an idle one exists, so K instant pings
  might not build all K models. Measured: `_idle_worker_semaphore` starts at zero and
  `run_in_executor` submits synchronously, so each of the first K submits spawns. The
  docstring's guarantee holds. The redundant `workers * 3` pings were reduced to `workers` and
  the comment now states the actual mechanism.
- **A lock was the wrong fix for the shared input provider.** A lock around `read_inputs`
  serializes two streams' frame reads and couples their frame rates to the channel access
  timeout. Building the PV dictionary eagerly and never mutating it in place removes the same
  races with no coupling.
- **Adding `/healthz` was thought to be schema-neutral. It was not.** Route docstrings become
  OpenAPI descriptions, so editing one changes `openapi.json` even when no field changes. The
  committed schema had to be regenerated, which is now verified as part of the work.

## Correctness

**A crashed worker used to brick the pod silently.** `ProcessPoolExecutor` latches broken: one
lost worker means every later submit raises, forever. Nothing caught it, and all three k8s
probes targeted `GET /api/v1/models`, which serves cached dataclasses and answered 200
regardless. The pod served 500s indefinitely and was never restarted. Now the pool is marked
dead, evaluates return 503 with a message that says a restart is needed, `lume_pool_dead`
exposes it to Prometheus, and a new `GET /healthz` fails so the probes do their job. Verified by
sending `SIGKILL` to a real worker process.

The pool is deliberately **not** rebuilt. Spawning K replacements while the old ones tear down
would peak at twice the model memory, which on a 7Gi limit with two 2GB workers converts a
restart-recoverable failure into an OOM kill.

**`/healthz` is separate from `GET /api/v1/models` on purpose.** The model list is client-facing
discovery, and on a pod hosting several models it has to keep listing the models that still work
even when one pool has died. A probe wants the opposite answer. `/healthz` carries
`include_in_schema=False`, so it is not a contract consumers can pin.

**The live view could publish design values labelled as live.** This was the most consequential
finding for the people who will actually use the service. An unreadable PV was pruned
permanently on the first read, so one briefly unreachable gateway at startup, or an IOC down
during a rollout of the singleton live pod, left `read_inputs` returning nothing for the life of
the process. The caller overlays that on the model's baseline, so the stream and
`machine-snapshot` served design values with nothing on the wire marking them as such, after a
single warning line. Fixed in four parts: nothing is pruned permanently any more and an
unconnected PV recovers on the next frame, the unreadable set is logged only when it changes,
zero connected PVs raises rather than silently falling back, and the new `input_sources` and
`sources` response fields say per id whether a value came from the machine, the request or the
baseline.

**A hung evaluate was invisible to the probes.** `smooth_images_sigma_px` had no upper bound and
`gaussian_filter` cost grows with sigma: on a real sensor a large value runs for hours, pinning
a worker that cannot be cancelled because it lives in a subprocess. Sigma is now bounded to 50
pixels and `max_particles` to 200000, both 422s rather than clamps. The optional
`LUME_EVALUATE_TIMEOUT_S` (default disabled) marks the pool dead on expiry, since an
unreclaimable worker means a restart is the only recovery.

**The two paths disagreed about non-finite numbers.** NaN is routine here: a solver that did not
converge, `norm_emit_x` on a degenerate beam. The HTTP route rendered it as `null` through its
response model, while the SSE path handed it to `json.dumps`, which emits the literal `NaN`.
That is not JSON, so a browser's `JSON.parse` threw and the whole frame was lost rather than one
field. The SSE path now nulls non-finite floats so both paths agree byte for byte. The sanitize
deliberately does not live in `result_to_wire`, because that runs on both paths and
`ScalarOutput.value` is a required float, so feeding it `None` would turn a NaN into a 500.

Requests are the opposite: `NaN` in `inputs` was accepted with a 200 and passed to `model.set`,
where lume's range check silently passed because every comparison with NaN is false. It is now a
422. Making that work also required a validation error handler, because FastAPI's default one
echoes the rejected value and cannot itself serialize it.

**Smaller correctness fixes.** Image downsampling silently cropped up to `factor - 1` rows and
columns off the trailing edge, so a beam or defect sitting there vanished; every pixel now
reaches the output. A particles output whose coordinates all failed to read shipped a
valid-looking empty beam that passed the wire-shape tests and rendered as an empty scatter plot;
it now raises. `LiveHub.shutdown` cancelled its producers without waiting for them while the
pool was torn down underneath; it now waits, bounded, so a wedged producer cannot hold the
process past the termination grace period. Producer loops are capped, since a client could
multiply the singleton's work simply by varying its output set. `frame_index` no longer restarts
at zero when the last viewer of a set leaves, which the API docs already promised. SSE error
events carry a generic message with the detail in the pod log, because every subscriber is an
untrusted browser. Cancelled evaluates are no longer counted as successes. An unknown
`LUME_ROLE`, a bad `LUME_LIVE_SOURCE`, malformed `LUME_LIVE_INPUTS` and a missing pyepics all
now fail startup instead of once per frame forever.

## Clarity and overengineering

**`LUME_MODEL` and `LUME_MODEL_KWARGS` are retired.** They were a third syntax for what
`LUME_MODELS` already expressed, costing three parsers in the loader plus a precedence warning.
Setting either is now a hard startup error naming the variable and showing the equivalent, which
matters because the still-deployed pre-split monolith read `LUME_MODEL`: an operator porting a
manifest across would otherwise have silently got whatever `LUME_MODELS` defaulted to.
`LUME_MODELS=""` still hosts the demo model but now warns, because a green pod quietly serving a
fake model is worse than a loud one.

**Dead code and duplication removed.** A write-only `ModelPool.info`; a hand-rolled `_dedupe`
that reimplemented `dict.fromkeys`; a lazy `import json` justified as an optimization in a module
that loads after two others that import it unconditionally; a factory path resolved a second
time inside every worker; a defensive branch for a state that cannot occur. `elapsed` was
threaded through four layers for one demo-only sine phase, and removing it also fixed
`machine-snapshot` reporting frozen synthetic values while the stream moved. The module-level
`SERVE_LIVE` global that forced a test to monkeypatch internals is gone, and the two different
"does this process do live" checks are now one.

**Documentation.** The same explanation appeared four to six times. The `result_to_wire`
every-key rule was in five places verbatim; baseline-merge, sequential warmup, per-model
`workers`, the spawn rationale, the packaging trap and the derived-range stopgap were similar.
That is why the docs had already drifted: `AGENTS.md`'s lazy-import table cited nine line
numbers of which six were wrong. Each topic now has one home and every other file cross-
references it. All line numbers are stripped from prose, including those pointing into another
repository. `AGENTS.md` went from 375 lines to 140 by becoming an index of traps rather than a
fourth copy of each explanation. `CAPACITY.md` no longer leads with numbers it then retracts.
`DEPLOY.md` lost about 130 lines designing a pod split that has not happened.

## Deliberately not done

- **Writable bool, enum and string variables still do not become inputs.** Supporting them means
  non-float values in `EvaluateV1Response.inputs` and `SnapshotResponse.inputs`, which breaks
  every client typed as a number map. That is a v2 change. They are now logged at INFO when
  dropped, so a missing knob is at least explicable.
- **The abandoned worker after a cancelled evaluate.** `ProcessPoolExecutor` cannot cancel work
  already running in a subprocess, so real occupancy can briefly exceed `max_inflight`. Inherent
  to the design, documented rather than fixed.
- **A worker's temporary directory is never cleaned up.** One empty directory per worker per pod
  start, in ephemeral storage.
- **Two models sharing a PV still read it twice per frame.** Deduplicating means a pod-wide
  cache that has to reason about whether two models want the same value at the same instant.
- **`config`'s `shape` and `evaluate`'s `shape` differ for downsampled images.** Already
  documented in API.md, which tells clients to reshape to the shape in the response.
- **`MIGRATING_LUME_VISUALIZATIONS.md` was kept**, with its other-repo line numbers stripped and
  marked as a dated snapshot. It is the UI port's only migration reference until that port lands.

## Verification

Beyond the 207 tests, the following were exercised against a running server rather than a
`TestClient`, because they are the paths tests cover least well.

| Check | Result |
| --- | --- |
| `SIGKILL` a real model worker | evaluate 503 with a clear message, `/healthz` 503, model list still 200, `lume_pool_dead` 1 |
| SSE stream on a two-model pod | frames parse, carry `model`, `version` and `input_sources`, monotonic `frame_index` |
| `machine-snapshot` over time | values move, and agree with the stream's phase |
| `NaN` in a request | 422 with a parseable body naming the field |
| `smooth_images_sigma_px: 20000` | 422 instead of pinning a worker |
| `openapi.json` regenerated under the CI pins | no drift |

`.venv` is the authoritative environment: it holds fastapi 0.141.1 and pydantic 2.13.4, which
are the pins CI uses to generate the schema. Note that a `python` on `PATH` may resolve to a
different environment. One test asserts that lume-base classifies `IntVariable` as a scalar, and
it correctly fails on lume-base 0.4.4, which is below the declared `>=0.5` and silently drops
every writable integer knob.
