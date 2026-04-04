from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Mapping, Sequence

import epics


class InputProvider(ABC):
    @abstractmethod
    def read_inputs(self, input_names: Sequence[str]) -> dict[str, float]:
        """Return a mapping of model input name to scalar value."""


class EpicsInputProvider(InputProvider):
    def __init__(
        self,
        timeout: float = 2.0,
        connection_timeout: float = 2.0,
    ) -> None:
        self.timeout = timeout
        self.connection_timeout = connection_timeout

    def read_inputs(self, input_names: Sequence[str]) -> dict[str, float]:
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
