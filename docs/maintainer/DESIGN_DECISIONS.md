# Design decisions

Choices in this codebase that look like bugs or missed optimizations but aren't. Each entry says
what the code does and what goes wrong if it's changed. [ARCHITECTURE.md](ARCHITECTURE.md)
describes how the pieces fit together.

## Model failures are 503, not 400

A model can fail while applying inputs and leave its instance alive but inconsistent, so every
later request to that worker fails too, even one that sets nothing. The exception types don't help
tell this apart from a bad request: lume raises `ValueError` and `TypeError` for a bad value, and
numpy and a broken lattice raise the same types. An earlier version classified by exception type
and answered 400, which told clients not to retry and left the broken worker in place with
`/healthz` still green.

`evaluate._prevalidate` now runs lume's own checks before `model.set()`, so every fault that's the
caller's is a 400 (`InvalidInput`) before the model runs. Anything that escapes `model.set()` after
that is the model's failure and becomes `ModelUnusable`, which leads to a 503 and, unless the model
recovers, a pod restart. `ModelUnusable` has to stay a `RuntimeError`, because `main.evaluate_v1`
catches `UnknownVariable` and `InvalidInput` (both `ValueError`s) to answer 400.

`_prevalidate` shouldn't check more than `LUMEModel.set` does. A stricter check would turn ordinary
bad requests into latched workers and pod restarts. `scripts/check_poisoned_worker.py` checks a
deployment end to end and breaks the deployment it runs against.

## Dead pools are not rebuilt

`ProcessPoolExecutor` stays broken after losing one worker, so the pool can never serve another
evaluate. Rebuilding it would spawn K new workers while the old ones tear down, which can double the
pod's memory and turn a restart into an OOM kill. The pool is marked dead instead, evaluates return
503, `/healthz` fails, and the probes restart the pod. A restart also shows up in
`kubectl get pods`, which an in-process rebuild wouldn't.

## Evaluate timeouts are fatal

A subprocess evaluate can't be cancelled. When `LUME_EVALUATE_TIMEOUT_S` expires, the worker keeps
computing the abandoned frame and holds its model memory for good. The only way to get that memory
back is a process restart, so a timeout marks the pool dead with reason `timeout`. The setting is
off by default, and any value has to be well above the model build time and normal evaluate latency,
or a slow frame becomes a pod restart.

## Recovery belongs in the model

`pool._attempt_recovery` calls `model.recover()` when a model defines one, and otherwise latches
the worker. No model defines it yet, so every `unusable` failure ends in a pod restart. The fix
for `cu_hxr_staged` was measured on the dev cluster: disable lattice calculation, write every
baseline control back, restore `track_type`, re-enable calculation, then refresh state. Turning
calculation off first lets Tao accept the intermediate writes without evaluating an unstable
orbit. Three of those five steps are Bmad commands, so the sequence belongs in lume-bmad or
virtual-accelerator as a `recover()` method, not here. <!-- VERIFY-VA -->

## No model.reset() on Bmad models

`model.reset()` looks like the obvious recovery, and it's destructive even on a healthy Bmad model.
`LUMEBmadModel.__init__` snapshots `_initial_state` before `track_type` is set to `"beam"`, so
`reset()` writes back `"single"` and unregisters 24 outputs, including every screen. After that,
every evaluate fails. This service never calls it, and `pool._attempt_recovery` only calls a
model's own `recover()`. <!-- VERIFY-VA -->

## One input per control

The baseline merge writes every non-constant input in one `model.set()`. If two inputs are handles
on the same underlying control, both get written and whichever is applied last wins.
virtual-accelerator publishes both `BCTRL` and `BDES` for every magnet, mapped to the same
variable. Setting only `BCTRL` was a silent no-op: the response echoed the requested value with
`source: "request"` while the magnet stayed at its default, and a full-range quad scan came back
flat. <!-- VERIFY-VA -->

`introspect._resolve_aliases` keeps one input per control and publishes the others as read-only
outputs with `alias_of` set. Two inputs count as aliases when they share the id prefix before the
last `:`, the unit and the default. `ALIAS_PREFERENCE` (`BCTRL`, then `BDES`) breaks the tie. It
lists final id segments, not variable ids, so it isn't a per-model table. The `REVISIT_ALIASES` note
in `introspect.py` covers what to check when a non-Bmad backend arrives.

## Live PVs are never dropped

An unreadable PV is skipped for that frame only and stays in the provider's set, so it's read again
once it reconnects. An earlier version dropped every PV that failed its first read. One unreachable
CA gateway at startup then left the pod permanently degraded, streaming design values labelled as
live. For the same reason, zero connected PVs raises an error instead of returning `{}`, which would
have streamed the baseline as if it were the machine.

## NaN is rejected in and sent as null out

A NaN input passes lume's range check, because every comparison with NaN is false. The model then
runs and returns a NaN beam that looks like a physics result. `schemas.ControlValue` sets
`allow_inf_nan=False`, so a request with NaN or Inf is a 422, and `main.validation_error_handler`
renders the rejected value as `null` so the 422 itself can be encoded.

A NaN result is legitimate (a solver that didn't converge, emittance of a degenerate beam). The
HTTP `response_model` and `serialize.json_safe` on the SSE path both send it as `null`, so the frame
is still valid JSON. `json_safe` can't move into `result_to_wire`, because `ScalarOutput.value` is a
required `float` and a `None` there would make the HTTP route return a 500.

## Warmup is sequential

`lifespan` warms one model's pool at a time. Warming all of them together would build every model
instance on the pod at once, and the pod's memory limit and `startupProbe` budget weren't sized for
that peak. Startup takes the sum of the model build times instead of the longest one, and the probe
budget has to cover that.

## No per-model tables

Nothing in this service names a screen, a PV, a range or a unit for a specific model. An earlier
version had a screen list, an input allowlist, a range override file and a units map, typed by hand
for one model, and they drifted from the model. A missing range or unit gets fixed upstream in the
model. `loader.SHORTCUTS` maps short names to factory paths and default kwargs only, and
`ALIAS_PREFERENCE` holds id segments, not ids.

## No default-model route

Every model route includes the model name. `/api/config`, `/api/v1/evaluate`,
`/api/machine-snapshot` and `/api/live/stream` return 404, and `tests/test_app_demo.py` checks
that. A route for "the first model" would work on a one-model pod and quietly point clients at the
wrong model once a second one is added.

## No mock mode

`model/demo.py` is a real `LUMEModel` that goes through the same describe, evaluate, serialize and
pool path as a production model. It replaced a mock in the API layer that derived every screen from
one knob, so switching screens changed nothing and the bug only showed up on a real model. CI has no
other model, so the demo has to keep covering every branch: a model-declared range, a derived range,
a constant, two screens with only one image between them, non-image arrays, and a plain scalar. It
also has to stay deterministic, because the wire-shape and subsampling tests depend on it.

## lume-base is a required dependency

`introspect.variable_kind` uses `isinstance` checks against lume's variable classes, and the demo
model subclasses `LUMEModel`, so lume-base can't be an optional extra. It's pure Python plus numpy,
h5py and openpmd-beamphysics, so it's safe as a hard dependency. It's still imported lazily in a few
places to keep the main process cheap to import.

## Particle coords include weight

Particle payloads include a `weight` coordinate (per-particle charge, in C) that phase-space plots
don't use. It adds about 17% to the payload. A distribution without charge is incomplete for physics
callers, so there's no flag to leave it out. `stats` are computed on the full beam before
subsampling.

## Image smoothing is off by default

`smooth_images_sigma_px` blurs every 2-D array output. That's right for screen images built from a
few thousand macroparticles and wrong for other 2-D arrays, so it's opt-in. The Gaussian filter
preserves the array's sum and isn't renormalized, so values stay in the declared unit. The cap of 50
pixels exists because the kernel is about `8 * sigma` wide, and since a subprocess evaluate can't be
cancelled, a huge sigma would tie up a worker for hours.

## output_beam and writable non-scalars are not published

`output_beam` isn't a screen, because a screen is a place a camera can point at. Writable
variables that aren't scalars (an `input_beam`, for example) aren't published as inputs or outputs,
and `describe` logs them at INFO so a missing knob can be explained.

This rule has a failure mode. A variable meant to be read-only can arrive with `read_only=False`.
lume's `ReadOnlyActionMixin` enforces the flag with a pydantic validator, and pydantic doesn't
validate defaults unless the model sets `validate_default`, which `lume.variables.Variable` doesn't.
A model that mixes in `ReadOnlyActionMixin` without setting the flag gets its variable silently
dropped here. That's how every `<ele>_beam` on `cu_hxr_staged` disappeared, leaving `screens: []`.
Nothing in this service checks for it. The lume-bmad pin in `pyproject.toml` and the build-time
assertion in the Dockerfile are what prevent it. <!-- VERIFY-VA -->

## Live producer is a singleton

The live Deployment runs `replicas: 1` with a `Recreate` strategy. Every replica would read EPICS
and run its own evaluate loops, costing N times the work and showing viewers frames that disagree
with each other. Evaluate load goes to the eval Deployment, which can run several replicas.

## /healthz is separate from the model list

The probes target `GET /healthz`, which fails once any pool on the pod is dead. `GET /api/v1/models`
keeps listing every model, including the ones that still work on a multi-model pod, so it can't be
the probe target. `/healthz` is registered with `include_in_schema=False` so it isn't part of the
contract and isn't in `openapi.json`.

## Sender-attached keys

`model`, `version` and `input_sources` are added by the code that sends the response, not by
`result_to_wire`, because only the sender knows the URL name and where each input came from. There
are two senders, `main.evaluate_v1` and `LiveHub._run`, and both have to attach all three.
`SENDER_ADDED` in `tests/test_wire_shape.py` pins the set.
