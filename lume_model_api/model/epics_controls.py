from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Callable, Mapping, Sequence


def configure_epics_from_env() -> None:
    """Apply EPICS_CA_ADDR_LIST / EPICS_CA_AUTO_ADDR_LIST from the environment.

    When running locally with the fake IOC, export these before starting:
        export EPICS_CA_AUTO_ADDR_LIST=NO
        export EPICS_CA_ADDR_LIST=127.0.0.1

    In the container or k8s this is handled by the docker-entrypoint or
    the lume-epics-config ConfigMap.  This function is a no-op when the
    variables are already present.
    """
    if "EPICS_CA_ADDR_LIST" not in os.environ and "EPICS_CA_AUTO_ADDR_LIST" not in os.environ:
        # No EPICS network config at all — default to localhost so the fake
        # IOC (127.0.0.1) is reachable without broadcast discovery.
        os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
        os.environ["EPICS_CA_ADDR_LIST"] = "127.0.0.1"


class InputProvider(ABC):
    @abstractmethod
    def read_inputs(self, input_names: Sequence[str]) -> dict[str, float]:
        """Return a mapping of model input name to scalar value."""


class EpicsInputProvider(InputProvider):
    def __init__(
        self,
        timeout: float = 2.0,
        connection_timeout: float = 3.0,
    ) -> None:
        configure_epics_from_env()
        import epics  # noqa: F401 – import after env is set so CA picks up the vars
        self.timeout = timeout
        self.connection_timeout = connection_timeout

    def read_inputs(self, input_names: Sequence[str]) -> dict[str, float]:
        import epics

        values = epics.caget_many(
            list(input_names),
            timeout=self.timeout,
            connection_timeout=self.connection_timeout,
        )
        output: dict[str, float] = {}
        missing: list[str] = []
        for name, value in zip(input_names, values):
            if value is None:
                missing.append(name)
                continue
            output[name] = float(value)

        if missing:
            missing_text = ", ".join(missing)
            raise RuntimeError(f"Failed to read EPICS PVs: {missing_text}")

        return output


class MappingInputProvider(InputProvider):
    def __init__(
        self,
        values: Mapping[str, float] | Callable[[], Mapping[str, float]],
    ) -> None:
        self._values = values

    def read_inputs(self, input_names: Sequence[str]) -> dict[str, float]:
        source = self._values() if callable(self._values) else self._values
        return {name: float(source[name]) for name in input_names}
