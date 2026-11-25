"""Fault injection (bonus: error simulation).

Wraps a measurement function and, at configured rates, makes it fail the way a real
device link fails (timeout, garbled reply) or return a spike. Used to show that the
sampler records failures instead of crashing and that the analysis flags outliers.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from Ammeters.client import AmmeterConnectionError, AmmeterProtocolError


@dataclass(frozen=True)
class ErrorSimulationConfig:
    enabled: bool = False
    connection_error_rate: float = 0.0  # probability of a simulated timeout
    protocol_error_rate: float = 0.0  # probability of a simulated garbled reply
    spike_rate: float = 0.0  # probability of a spike (value x spike_factor)
    spike_factor: float = 10.0
    seed: int | None = None  # fix it for reproducible runs

    def __post_init__(self) -> None:
        rates = (self.connection_error_rate, self.protocol_error_rate, self.spike_rate)
        if any(not 0.0 <= r <= 1.0 for r in rates) or sum(rates) > 1.0:
            raise ValueError(f"Error-simulation rates must be in [0, 1] and sum to <= 1: {rates}")

    @classmethod
    def from_config(cls, section: dict[str, Any] | None) -> ErrorSimulationConfig:
        section = section or {}
        known = {k: v for k, v in section.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def with_fault_injection(
    measure: Callable[[], float], config: ErrorSimulationConfig, stream: str = ""
) -> Callable[[], float]:
    """``stream`` (e.g. the device name) gives each device its own reproducible fault
    pattern from one seed, instead of every device failing on the same samples."""
    if not config.enabled:
        return measure
    rng = random.Random(None if config.seed is None else f"{config.seed}:{stream}")

    def faulty_measure() -> float:
        roll = rng.random()
        threshold = config.connection_error_rate
        if roll < threshold:
            raise AmmeterConnectionError("simulated timeout")
        threshold += config.protocol_error_rate
        if roll < threshold:
            raise AmmeterProtocolError("simulated non-numeric response")
        value = measure()
        threshold += config.spike_rate
        return value * config.spike_factor if roll < threshold else value

    return faulty_measure
