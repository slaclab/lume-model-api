# A worker whose model stopped running, reported as a client error

A reproducible failure in the deployed service, recorded here because every signal the service
emits about it is wrong: the status code blames the caller, the health check reports success, and
the error names a numpy reshape four layers below the actual cause.

This file is the acceptance test for the fix. Run the reproduction before and after.

## Symptom

One request destabilizes the model. Every later request to the same worker then fails, including
requests that set nothing at all:

```console
$ curl -s -X POST "$API/api/v1/models/cu_hxr_staged/evaluate" \
    -H 'Content-Type: application/json' -d '{"inputs":{},"outputs":["QUAD:IN20:525:BACT"]}'
{"detail":"Model rejected the inputs: cannot reshape array of size 0 into shape (180,)"}

$ curl -s "$API/healthz"
{"status":"ok","models":["cu_hxr_staged"]}
```

Three things are wrong with that pair.

**The status is 400.** That tells a client its request was invalid and not to retry. The request was
empty. Nothing about it was invalid. The correct answer is 503: this worker is broken, try again
elsewhere or later.

**`/healthz` reports ok.** The k8s probes target it, so nothing restarts the pod. `pool.dead` is
never set because no worker process died: they are alive and permanently useless. `PoolDead` exists
for exactly this and does not fire, because it keys on the process exiting.

**The message describes a symptom.** `cannot reshape array of size 0 into shape (180,)` is
`StatVariable._get` reshaping an empty `lat_list`. The lattice is empty because the orbit is
unstable. Nothing in the message says so.

With K workers, requests routed to healthy ones still succeed, so the failure presents as an
intermittent fraction rather than an outage, and looks like a flaky client.

## Reproduction

Needs a deployment you may break. **Set a bend, at any value, including its own default.** One set
poisons one worker, permanently and silently.

```bash
API=https://ad-accel-online-ml-dev.slac.stanford.edu/lume-model-api

# 1. Healthy. Succeeds, repeatably.
for i in $(seq 8); do curl -s -o /dev/null -w '%{http_code} ' -X POST \
  "$API/api/v1/models/cu_hxr_staged/evaluate" -H 'Content-Type: application/json' \
  -d '{"inputs":{},"outputs":["QUAD:IN20:525:BACT"]}'; done; echo

# 2. Poison one worker: set a bend. Returns 500 or 400 depending on where it fails.
curl -s -X POST "$API/api/v1/models/cu_hxr_staged/evaluate" -H 'Content-Type: application/json' \
  -d '{"inputs":{"BEND:LI21:215:BCTRL":0.209},"outputs":["QUAD:IN20:525:BACT"]}'

# 3. Repeat step 1. A FRACTION now 400s, though these requests set nothing.
#    Repeat step 2 and step 1 alternately: the failing fraction climbs as workers fall.

# 4. Still claims healthy, at any failing fraction.
curl -s "$API/healthz"
```

`scripts/check_poisoned_worker.py` automates this.

**The failing fraction is the tell, and it is why a single request proves nothing.** Measured on
dev with 2 pods of 2 workers, alternating one poisoning set with a batch of empty-input requests:

| workers poisoned | empty-input requests failing |
| --- | --- |
| 0 | 0 of 8 |
| 1 | 1 of 8 |
| 2 | 6 of 12 |
| 3 | 9 of 12 |

Discrete steps, monotonically upward, never recovering without a restart. That is a per-worker
latch: each poisoned worker fails every request routed to it forever, while healthy workers keep
answering, so the service degrades in quarters rather than failing outright. A client sees flakiness
that worsens over time and never heals.

Recovery is a pod restart: `kubectl rollout restart deploy/lume-model-api-eval -n lume-model-api`.

An out-of-range value is not required, but the trigger **is** value-dependent, and the threshold is
far tighter than the published range. Measured on fresh pods, one value per pod:

| `BEND:LI21:215:BCTRL` | vs default | poisons? |
| --- | --- | --- |
| 0.21999940654481862 (its own startup value) | 0% | no |
| 0.2195 | -0.2% | no |
| 0.215 | -2.3% | **yes** |
| 0.209 | -5.0% | **yes** |
| -0.5 (out of range) | — | **yes** |

So roughly a 2 percent move off the startup value is enough. The published range for this input is
`[0.11, 0.33]`, or default +/- 50 percent, so **every poisoning value above is inside the range the
service advertises as valid.** Enforcing the published range would not filter any of them.

Two injector quads moved far within range (`QUAD:IN20:525:BCTRL=-6.5` with
`QUAD:IN20:361:BCTRL=-4.0`) do **not** poison anything, so this is specific to what a bend set does
to the lattice rather than to large excursions generally.

An earlier version of this file claimed a bend poisons at its own default value. That was wrong. It
was measured against a deployment that was already poisoned from a previous test, which makes every
subsequent observation meaningless. Re-tested on freshly restarted pods, four sets at the exact
default followed by eight empty-input probes: all twelve returned 200.

The status code on the poisoning request itself alternates between 500 and 400 across otherwise
identical requests, because it depends on whether that worker was already poisoned: a fresh worker
raises out of `lattice_calc_on = T` and surfaces as an unhandled 500, while an already-poisoned one
fails earlier on the stale-state reshape and surfaces as 400.

## Cause

Every poisoning value is inside the range the service publishes, so validating those ranges would not
prevent this. They are advisory anyway: for a Bmad magnet the model declares no `value_range`, so
`introspect._input_info` derives one as `default +/- 50%` from a value read live at warmup, and for a
bend that value is the element's reference momentum. A filter tight enough to catch a 2 percent move
would reject most legitimate tuning.

The mechanism is in lume-bmad. `LUMEBmadModel._set` disables lattice calculation for speed and
re-enables it in a `finally`:

```python
finally:
    self.simulator.cmd("set global lattice_calc_on = T")   # raises here
self._refresh_dynamic_action_variables()   # never runs
self.update_state()                        # never runs
```

Tao recomputes on re-enable, finds the orbit unstable, and raises from the `finally`. Two
consequences. The raise replaces whatever the `try` was doing, so the original error is masked. And
the two state-refresh calls below never run, so the model's cached `_state` holds pre-set values
while the lattice holds post-set ones.

Pod logs at the time show the origin, with no wake or particle-loss errors:

```
Command: 'set global lattice_calc_on = T' causes errors in the function(s): tao_set_invalid
Error in tao_set_invalid:
  UNSTABLE ORBIT AT EVALUATION POINT
  FOR DATUM: BC1.energy[2] with data_type: e_tot_ref
```

**What makes it permanent is a separate, worse problem, and it is not in lume-bmad.** The `finally`
explains the first failure. It does not explain why the instance never recovers, because every later
call rewrites the same baseline through a `_set` whose `finally` succeeds.

The latch is an ordering deadlock in `StagedModel._set` (lume-base `lume/staged_model.py`), which per
stage does:

```python
if i > 0 and incoming_particles is not None:
    model.initial_particles = incoming_particles   # (1) issues `set beam_init position_file`
if model_values:
    model.set(model_values)                        # (2) would restore the bad attribute
```

With the lattice left in its bad state, step (1) raises (`Reference particle lost while tracking
through: CQ11#2`), so step (2) never runs. The write that would heal the model is unreachable because
the broken model prevents reaching it. Measured in isolation: replayed five times, the bad attribute
stayed frozen; reversing the two steps on identical state heals it immediately.

What actually changes in Tao is exactly one attribute, `BX11 DG` (the element behind
`BEND:LI21:215:BCTRL`), going from `0.0` to `1.5049100941303610`. `G`, `P0C`, `E_TOT`, `B_FIELD`, `L`
and `ANGLE` are all unchanged, `lattice_calc_on` is still `True`, and `track_type` is still `beam`.
A path-dependence hypothesis, that writing the original value back could not restore `DG` because
`P0C` shifts, was tested and **refuted**: `P0C` never moves, and writing the original value back
restores `DG` to within float noise.

Two consequences for anyone reading the 400 message: this is an ordering problem, and a Bmad-only
model (`get_cu_hxr_bmad_model`) **self-heals**, because it has no `initial_particles` step and so
always reaches step (2). Only the staged model latches.

The 400 itself comes from `update_state()`, which `_set` calls and which reads **every** supported
variable including the `tao.lat_list`-backed `StatVariable`s. When one of those returns an empty
array, `StatVariable._get` does the equivalent of `np.asarray([]).reshape((180,))`, a genuine
`ValueError`, which `evaluate.py` catches as `InvalidInput` and `main.py` maps to 400.

That last step is this repo's defect. `evaluate` cannot distinguish "the model rejected your value"
from "the model is broken" by exception type, because both arrive as `ValueError`. It assumes the
former. It no longer has to guess: see "What a fixed service must do" below.

**Not established:** what leaves `lat_list` empty. A dedicated investigation against the real lattice
could not reproduce an empty `lat_list` by any route, including driving `DG` directly to the bad
value, forcing a recalc, and switching elements off. It stayed at 180 elements throughout, and every
Tao query kept working. So the 400 message is reproducible on dev but its immediate cause is not yet
pinned down, and the chain above is inferred from the exception type and the traceback rather than
observed. Do not treat it as confirmed.

## What a fixed service must do

The reproduction above, after the fix:

1. Step 2 returns **503**, not 400 and not 500, with a detail naming the ids the request sent and
   the class of failure.
2. Step 3 returns **503**, not 400. A request that sets nothing must never be reported as a client
   error.
3. Step 4 returns **503**, so the k8s probes restart the pod.
4. The pod restarts unattended, and step 1 succeeds again afterwards with no manual action. Today
   this state persists until a human notices.
5. `lume_pool_dead{reason="unusable"}` and `lume_pool_recovery_total` appear in `/metrics`, so the
   cause and the rate are distinguishable from an OOM-killed worker.
6. A genuinely invalid value, a string where a float belongs or a read-only id, still returns
   **400** and does **not** restart the pod.

The detection is that the request was valid. `LUMEModel.set` runs its whole validation loop before
it calls `_set`, so validating each value against its own variable first, in `evaluate._prevalidate`,
leaves nothing attributable to the caller for the model to raise afterwards. Anything escaping
`model.set()` is then a server fault by construction, with no exception type to interpret. How it
escalates from there is in
[ARCHITECTURE.md](ARCHITECTURE.md#a-model-that-breaks-itself-is-unusable-and-it-is-not-the-callers-fault).

**A readback cannot do this job, which two earlier drafts of the fix assumed it could.** Every
failing request raises *inside* `model.set()`, so there is nothing to read back on success. Moving
the readback to the failure path does not rescue it either: `update_state()` iterates
`supported_variables` in registration order with controls first, so every control is refreshed to
the value just written before the loop reaches the variable that raises, and drift on a broken
worker is therefore exactly zero. Worse, comparing a write against a readback is not comparing
like with like: `_SBendFieldVariable` writes `DG` and reads `p0c*(1 + DG/G)*1e-9`, returning a
literal `0` when `g == 0`, and 24 of 54 published inputs default to exactly 0, so a strict
comparison would latch the pool dead on a healthy first evaluate and CrashLoopBackOff the pod.

## Related but not the cause

- `compute_covariance_matrix` in virtual-accelerator applies `* 1e-6` to a `sigma_z` already in
  metres, and `beam_output.py` then multiplies that slot by `c` because the documented layout is
  `[m, eV/c, m, eV/c, s, eV/c]`. The bunch handed to Bmad is 138 mm where 0.46 mm is physical.
  Real, and worth fixing upstream, but not this: the failure reproduces with `get_cu_hxr_bmad_model`,
  which has no injector surrogate and never runs that code.
- OTR11 and OTR12 report `sigma_x` around 280 m while OTR2, OTR3 and OTR4 are physical. Both sit
  downstream of the injector-to-Bmad handoff and may share a cause with the `sigma_z` bug.
