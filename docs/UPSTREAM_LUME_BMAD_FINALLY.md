# Upstream: `LUMEBmadModel._set` raises from a `finally`, leaving the model unusable

A bug report for lume-bmad (https://github.com/lume-science/lume-bmad), written from this service's
side because it is what makes a hosted Bmad model fail permanently rather than transiently. Nothing
here is actionable in `lume-model-api`. See [POISONED_WORKER.md](POISONED_WORKER.md) for the
service-side symptom and for the defects that are ours.

## The code

`lume_bmad/model.py`, in `LUMEBmadModel._set`, as installed in the deployed image:

```python
def _set(self, values: dict[str, Any]) -> None:
    # turn tao eager mode off to speed up setting multiple variables
    self.simulator.cmd("set global lattice_calc_on = F")
    try:
        super()._set(values)
    except Exception:
        logger.error("Error setting variables: %s")
        raise
    finally:
        # after setting all variables, turn eager mode back on
        self.simulator.cmd("set global lattice_calc_on = T")   # <-- raises here

    # track_type toggles the set of supported read-only outputs.
    self._refresh_dynamic_action_variables()
    # update state with new input / output values
    self.update_state()
```

## What goes wrong

`set global lattice_calc_on = T` makes Tao recompute. If the new settings give an unstable orbit,
pytao raises `TaoCommandError` from that command. Because the command is in a `finally`, three
things follow, and each is worse than the last.

**The original error is masked.** If `super()._set(values)` had already failed, its exception is
discarded and replaced by the one from the `finally`. The `except Exception: raise` above it becomes
unreachable in effect. A caller sees a complaint about `lattice_calc_on` rather than about the
variable that was actually bad.

**The two statements after the block never run.** `_refresh_dynamic_action_variables()` and
`update_state()` are outside the `try`, so a raise from the `finally` skips both. `self._state` keeps
pre-set values while Tao holds post-set ones.

**The model is left inconsistent, not merely failed.** The instance stays alive and accepts further
calls, but its cache no longer describes its simulator.

The instance then never recovers, and **the `finally` alone does not explain that.** Every later call
rewrites the same values through a `_set` whose `finally` succeeds and whose `update_state()` does
run, yet the instance stays broken indefinitely. So something latches that re-writing the original
values does not undo. What exactly is still under investigation; see "What is not yet established"
below. The `finally` is the trigger and the reason the first failure is misreported, but it is not
the whole mechanism.

## Why the next call fails too, and differently

`update_state()` reads **every** supported variable, including the `StatVariable`s backed by
`tao.lat_list`. On a lattice with an unstable orbit, Tao invalidates its datums and `lat_list`
returns an empty array, so `StatVariable._get` does the equivalent of:

```python
np.asarray([]).reshape((180,))
# ValueError: cannot reshape array of size 0 into shape (180,)
```

That happens *inside* the next `_set`, via its own `update_state()` call, on a request that may not
have changed anything at all. So a single bad set produces two different failures on later calls
depending on where the model is in its cycle:

| raised from | exception type |
| --- | --- |
| `lattice_calc_on = T` in the `finally` | `TaoCommandError`, a `RuntimeError` |
| `update_state()` reading a `StatVariable` | `ValueError` |

Consumers that classify exceptions by type see one bug as two unrelated ones. This service did: the
`ValueError` was caught as a bad-input error and reported to callers as HTTP 400, while the
`TaoCommandError` escaped as an unhandled 500. Same root cause, two status codes, neither correct.

## Reproduction

No API needed. Against `lcls-lattice` with `LCLS_LATTICE` set:

```python
from virtual_accelerator.models.cu_hxr import get_cu_hxr_bmad_model

model = get_cu_hxr_bmad_model(start_element="OTR2", end_element="TD11", track_beam=True)

model.set({"BEND:LI21:215:BCTRL": -0.5})       # TaoCommandError from the finally
model.set({"BEND:LI21:215:BCTRL": 0.219999})   # the magnet's own startup value
# the instance never recovers: _state and the lattice disagree from here on
```

Deliberately a bend, because `cu_hxr` bends do this at **any** value including their own startup
value, so no out-of-range input is needed to trigger it. Quads moved by comparable amounts do not.

Observed in the deployed image and reproduced in a notebook on `lume-bmad 0.1.1.dev8+gc4e72e814`
with `pytao 1.2.1`.

## What is not yet established

Stated plainly so nobody builds on it as if it were settled.

**Why the instance never recovers.** The `finally` explains the first failure and the masked error. It
does not explain permanence, for the reason above. A candidate, unverified: `_SBendFieldVariable`
in virtual-accelerator writes the Bmad `DG` attribute computed from the element's *current* `P0C`
(`dp = (value*1e9 - p0c)/p0c; dg = dp*g`) and reads back `p0c*(1 + dg/g)*1e-9`. If `P0C` shifts when
the energy or orbit changes, the write is path-dependent and writing the original BCTRL value back
would not restore the original `DG`. That would put the latch in virtual-accelerator's conversion
rather than in lume-bmad. Not yet measured either way.

**Whether the latch is in Tao or in the Python cache.** If Tao is healthy and only `_state` is stale,
recovery is cheap. If Tao itself is wedged, it is not. Untested.

**Whether `reset()` recovers an instance.** `LUMEBmadModel.reset()` re-sets the initial control state,
but if the latch is path-dependent as above, writing the same values again would not help. Untested.

## Suggested fix

Two independent changes. The first is the bug; the second limits the damage when Tao legitimately
rejects a configuration.

**1. Do not raise from the `finally`.** Re-enabling eager mode is cleanup, and cleanup that throws
destroys the exception it was cleaning up after. Let the original error surface, and let the caller
see that the lattice calculation could not be restored as a secondary fact:

```python
try:
    super()._set(values)
finally:
    try:
        self.simulator.cmd("set global lattice_calc_on = T")
    except Exception:
        logger.exception("Could not re-enable lattice_calc_on after setting %s", list(values))
        raise                      # only reached when super()._set did NOT raise
```

**2. Make the instance honest about its own state.** Whatever the failure, `_state` must not silently
describe a different lattice. Either refresh it on the way out even when the set failed, or mark the
instance as needing a refresh so the next `get` raises something meaningful rather than a numpy
reshape error from four frames down. A `reset()` path that a caller can use to recover the instance
without rebuilding it would also let hosts avoid a process restart, which is the only recovery
available today.

A narrower option, if neither is acceptable: raise a dedicated exception type for
"this instance's state is no longer valid". Consumers currently have to distinguish a rejected value
from a broken model by inspecting exception types that belong to numpy and pytao, which is not a
contract anyone can rely on.

**3. A `recover()` method, so a host does not have to restart a process.** This is the request with
the clearest payoff, and the sequence below is measured to work on the real `cu_hxr` staged model.

It needs a snapshot of the control values taken at construction, while the instance is still healthy.
A host cannot capture this itself: by the time it notices a failure the values are already gone, and
`_initial_state` is not a substitute because it is snapshotted before `build_bmad_model` sets
`track_type`, which is what makes `reset()` destructive.

```python
# in __init__, after the model is fully built
self._recovery_snapshot = {
    name: var._get(self.simulator)
    for name, var in self.supported_variables.items()
    if not getattr(var, "read_only", False)
}

def recover(self) -> None:
    """Restore the lattice to its construction-time control values."""
    # Calculation off is load-bearing: with it on, Tao evaluates each intermediate write and
    # re-raises the same unstable-orbit error partway through the restore.
    self.simulator.cmd("set global lattice_calc_on = F")
    for name, value in self._recovery_snapshot.items():
        self.supported_variables[name]._set(self.simulator, value)
    self.simulator.cmd("set global track_type = beam")
    self.simulator.cmd("set global lattice_calc_on = T")
    self._refresh_dynamic_action_variables()
    self.update_state()
```

Measured on `cu_hxr_staged`, 85 controls restored with no errors:

| | before recovery | after |
| --- | --- | --- |
| `BX11 DG` | `0.009196655569503566` | `-6.22914621624457e-17` |
| `len(lat_list("*", "ele.a.beta"))` | `0` | `180` |
| `model.set({})` | `ValueError`, the reshape | succeeds |

Two caveats for whoever implements it. `track_type` should come from the snapshot rather than being
hardcoded to `beam`, or the method is wrong for a single-particle model. And recovery restores the
construction-time configuration, not the caller's intent, so a client mid-scan loses its position:
that is worth documenting as "recovered to design state" rather than "the request was retried".

This service already calls `model.recover()` when a model defines it (`api/pool.py`,
`_attempt_recovery`) and counts the outcome, so the method activates with no changes on our side.
Note the lume-base ordering fix above is still the better fix: with it, the model self-heals and
`recover()` is only a backstop.

## Impact on this service

Each `LUMEBmadModel` lives in its own pool worker subprocess. One bad set poisons one worker, for the
life of that process, with no crash to detect. The service degrades in steps as workers fall (measured
0 to 25 to 75 percent of requests failing over three bad sets across four workers) while the health
check keeps reporting success, because no process died. Recovery is a pod restart.

The two fixes above are worth making regardless of what turns out to cause the permanence, because
they are what makes the failure *diagnosable*: today one root cause surfaces as two unrelated
exception types from two libraries, and the original error is discarded. Fixing the permanence may
well be a separate change in a different repository.
