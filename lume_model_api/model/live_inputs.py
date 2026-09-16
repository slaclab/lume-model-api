"""Where the live view's input values come from.

`LUME_LIVE_SOURCE` picks a provider:

- `epics` (default) reads each non-constant input id as a channel-access PV.
- `synthetic` wiggles each one around its default, from `ModelInfo` alone, so the live
  stream is demonstrable with no control system in reach.

Both return only the ids they actually have a value for. The caller overlays the result on
`ModelInfo.baseline`, so an id with no PV behind it keeps its design value instead of
stalling the loop or forcing a hardcoded exclusion list.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

EPICS = "epics"
SYNTHETIC = "synthetic"
DEFAULT_SOURCE = EPICS

# Period and phase spread of the synthetic wiggle. Different phases per input keep the
# scalars from all peaking at once, which would look like a single global knob.
SYNTHETIC_PERIOD_S = 6.0
SYNTHETIC_PHASE_STEP_RAD = 0.7
SYNTHETIC_AMPLITUDE_FRACTION = 0.25


def configure_epics_from_env() -> None:
    """Apply the channel-access address settings, defaulting to localhost.

    Must run before pyepics is imported: CA reads these variables once, at import, so
    setting them afterwards has no effect. In the container and in k8s they arrive from the
    lume-model-api-epics-config ConfigMap and this function is a no-op.
    """
    if "EPICS_CA_ADDR_LIST" not in os.environ and "EPICS_CA_AUTO_ADDR_LIST" not in os.environ:
        # No EPICS network config at all, so default to localhost rather than broadcasting
        # onto whatever subnet the pod happens to sit on.
        os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
        os.environ["EPICS_CA_ADDR_LIST"] = "127.0.0.1"


def live_source_from_env() -> str:
    return (os.environ.get("LUME_LIVE_SOURCE") or DEFAULT_SOURCE).strip().lower()


def live_input_ids_from_env() -> list[str] | None:
    """`LUME_LIVE_INPUTS`, a JSON list restricting which input ids are read."""
    raw = os.environ.get("LUME_LIVE_INPUTS", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LUME_LIVE_INPUTS is not valid JSON ({exc}). Value was: {raw!r}") from exc
    if not isinstance(parsed, list):
        raise ValueError(
            f"LUME_LIVE_INPUTS must be a JSON list of input ids, got {type(parsed).__name__}."
        )
    return [str(item) for item in parsed]


def check_env() -> None:
    """Validate the live settings at startup instead of on the first frame.

    Both are read when a provider is first built, which happens on the first live request. So
    without this a typo in `LUME_LIVE_SOURCE` or malformed `LUME_LIVE_INPUTS` JSON starts the
    pod green and then fails once per frame, forever, as an SSE `error` event and a 500 on
    `machine-snapshot`. Called from the app's lifespan in the live roles.
    """
    source = live_source_from_env()
    if source not in {EPICS, SYNTHETIC}:
        raise ValueError(f"Unknown LUME_LIVE_SOURCE {source!r}. Use {EPICS!r} or {SYNTHETIC!r}.")
    live_input_ids_from_env()


def _readable_ids(info, restrict_to: list[str] | None) -> list[str]:
    ids = [item.id for item in info.inputs if not item.constant]
    if restrict_to is None:
        return ids
    wanted = set(restrict_to)
    unknown = wanted - set(ids)
    if unknown:
        logger.warning("LUME_LIVE_INPUTS names ids this model has no writable input for: %s",
                       ", ".join(sorted(unknown)))
    return [name for name in ids if name in wanted]


class InputProvider(ABC):
    """Read the live value of a model's inputs.

    One provider per hosted model, shared by every live producer loop for that model and by
    `machine-snapshot`. Those run on different threads (channel access blocks, so the caller
    hands the read to `asyncio.to_thread`), so **`read_inputs` must be safe to call
    concurrently**. Neither implementation takes a lock: a lock around a read would serialize
    two streams' frames and couple their frame rates to the CA timeout. They avoid mutating
    shared state in place instead.
    """

    @abstractmethod
    def read_inputs(self) -> dict[str, float]:
        """Return the ids that currently have a value. Missing ids fall back to baseline."""


class EpicsInputProvider(InputProvider):
    """Reads the model's non-constant inputs over channel access.

    `PV` objects are created once, in the constructor, and reused. Creating them per read costs
    a fresh CA search every frame, which at live-loop rates is what makes the stream stutter.
    Building them eagerly also keeps `read_inputs` free of lazy initialization, which two
    concurrent first readers would otherwise both perform, leaving a whole duplicate set of CA
    channels subscribed. Construction itself stays lazy, in the caller, which is what keeps the
    pyepics import after the CA environment variables are set.

    Ids that never connect are dropped after the first read, with one warning. A model input
    that is not a real PV (a bunch length knob, say) would otherwise time out on every frame
    and pace the whole loop at the CA timeout.
    """

    def __init__(self, info, restrict_to: list[str] | None = None,
                 timeout: float = 2.0, connection_timeout: float = 3.0) -> None:
        self.names = _readable_ids(info, restrict_to)
        self.timeout = timeout
        self.connection_timeout = connection_timeout
        self._pruned = False
        configure_epics_from_env()
        try:
            import epics  # imported here so it lands after the CA env vars are set
        except ImportError as exc:
            # pyepics is the [epics] extra, so a default install reaching this line means the
            # live role is running without it. A bare ModuleNotFoundError on every frame says
            # nothing about which of the two fixes the operator wants.
            raise RuntimeError(
                "LUME_LIVE_SOURCE=epics needs pyepics, which is the optional [epics] extra. "
                'Install it with pip install "lume-model-api[epics]", or set '
                "LUME_LIVE_SOURCE=synthetic to drive the live view from the model's own "
                "input defaults."
            ) from exc
        # Rebound wholesale by the prune, never mutated in place, so a concurrent reader
        # iterating the old dict cannot hit "dictionary changed size during iteration".
        self._pvs: dict = {name: epics.PV(name) for name in self.names}

    def _wait_for_connections(self, pvs: dict) -> None:
        """Give every PV one shared `connection_timeout` to connect, not one each.

        CA searches run in the background for all PVs at once, so waiting on them one at a
        time would only serialize the timeouts: ten dead PVs would hold the first frame for
        ten times the timeout instead of once.
        """
        import epics

        deadline = time.monotonic() + self.connection_timeout
        while time.monotonic() < deadline:
            if all(pv.connected for pv in pvs.values()):
                return
            epics.ca.poll(evt=0.05, iot=0.05)

    def read_inputs(self) -> dict[str, float]:
        # Read the attribute once. Two concurrent first readers then iterate the same immutable
        # dict and compute the same prune, so whichever assigns last is still correct.
        pvs = self._pvs
        if not self._pruned:
            self._wait_for_connections(pvs)
        values: dict[str, float] = {}
        unreadable: list[str] = []
        for name, pv in pvs.items():
            raw = pv.get(timeout=self.timeout) if pv.connected else None
            try:
                values[name] = float(raw)
            except (TypeError, ValueError):
                unreadable.append(name)
        if not self._pruned:
            self._pruned = True
            if unreadable:
                logger.warning(
                    "Dropping %d input(s) with no readable PV, falling back to their baseline "
                    "values for the rest of this process: %s",
                    len(unreadable),
                    ", ".join(sorted(unreadable)),
                )
                dropped = set(unreadable)
                self._pvs = {name: pv for name, pv in pvs.items() if name not in dropped}
        return values


class SyntheticInputProvider(InputProvider):
    """Slowly-varying inputs derived from `ModelInfo`, for demos and for CI."""

    def __init__(self, info, restrict_to: list[str] | None = None) -> None:
        names = set(_readable_ids(info, restrict_to))
        self._inputs = [item for item in info.inputs if item.id in names]
        # The phase origin lives here rather than being passed in per read, so every reader of
        # one model sees the same synthetic machine at the same instant. Threaded in by the
        # caller it was per producer loop, which made two streams of one model disagree and
        # pinned `machine-snapshot` to a fixed elapsed=0 while the streams moved.
        self._started = time.monotonic()

    def read_inputs(self) -> dict[str, float]:
        elapsed = time.monotonic() - self._started
        values: dict[str, float] = {}
        for index, item in enumerate(self._inputs):
            low, high = float(item.min), float(item.max)
            amplitude = SYNTHETIC_AMPLITUDE_FRACTION * (high - low)
            phase = elapsed / SYNTHETIC_PERIOD_S + index * SYNTHETIC_PHASE_STEP_RAD
            value = float(item.default) + amplitude * math.sin(phase)
            # A default sitting near an edge of its range would otherwise swing outside it.
            # A model that validates its controls with config "error" raises on such a value,
            # which would turn the live loop into a stream of error events.
            values[item.id] = min(max(value, low), high)
        return values


def get_input_provider(info, source: str | None = None,
                       restrict_to: list[str] | None = None) -> InputProvider:
    source = (source or live_source_from_env()).strip().lower()
    if source == SYNTHETIC:
        return SyntheticInputProvider(info, restrict_to)
    if source == EPICS:
        return EpicsInputProvider(info, restrict_to)
    raise ValueError(f"Unknown LUME_LIVE_SOURCE {source!r}. Use {EPICS!r} or {SYNTHETIC!r}.")
