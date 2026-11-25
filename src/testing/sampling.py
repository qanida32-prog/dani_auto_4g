"""Measurement sampling: configurable count / duration / frequency with drift-free timing."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from Ammeters.client import AmmeterError


class SamplingConfigError(ValueError):
    """The sampling parameters are missing, inconsistent or out of range."""


@dataclass(frozen=True)
class SamplingConfig:
    """How many measurements to take and how often.

    Any two of ``measurements_count``, ``total_duration_seconds`` and
    ``sampling_frequency_hz`` define the third. If all three are given they must agree.
    The duration is the span from the first sample to the end of the last interval,
    so ``count = duration * frequency``.
    """

    measurements_count: int
    total_duration_seconds: float
    sampling_frequency_hz: float

    # Relative tolerance when all three values are given.
    _CONSISTENCY_TOLERANCE = 0.01

    @property
    def interval_seconds(self) -> float:
        return 1.0 / self.sampling_frequency_hz

    @classmethod
    def from_values(
        cls,
        measurements_count: int | None = None,
        total_duration_seconds: float | None = None,
        sampling_frequency_hz: float | None = None,
    ) -> SamplingConfig:
        count, duration, freq = measurements_count, total_duration_seconds, sampling_frequency_hz
        given = sum(v is not None for v in (count, duration, freq))
        if given < 2:
            raise SamplingConfigError(
                "Give at least two of measurements_count, total_duration_seconds and "
                "sampling_frequency_hz"
            )
        for name, value in (
            ("measurements_count", count),
            ("total_duration_seconds", duration),
            ("sampling_frequency_hz", freq),
        ):
            if value is not None and (not _is_number(value) or value <= 0):
                raise SamplingConfigError(f"{name} must be a positive number, got {value!r}")
        if count is not None and int(count) != count:
            raise SamplingConfigError(f"measurements_count must be an integer, got {count!r}")

        if count is None:
            count = max(1, round(duration * freq))
        elif duration is None:
            duration = count / freq
        elif freq is None:
            freq = count / duration
        elif not math.isclose(count, duration * freq, rel_tol=cls._CONSISTENCY_TOLERANCE):
            raise SamplingConfigError(
                f"Inconsistent sampling: {count} measurements != "
                f"{duration} s x {freq} Hz = {duration * freq:g}"
            )
        return cls(int(count), float(duration), float(freq))

    @classmethod
    def from_config(cls, sampling_section: dict[str, Any]) -> SamplingConfig:
        return cls.from_values(
            sampling_section.get("measurements_count"),
            sampling_section.get("total_duration_seconds"),
            sampling_section.get("sampling_frequency_hz"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class Sample:
    """One measurement attempt. ``value`` is None when the attempt failed."""

    index: int
    scheduled_offset_s: float  # when it should have been taken, relative to start
    actual_offset_s: float  # when the request was actually sent
    latency_s: float  # request round-trip time
    value: float | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None

    @property
    def jitter_s(self) -> float:
        return self.actual_offset_s - self.scheduled_offset_s


@dataclass
class SamplingRun:
    config: SamplingConfig
    samples: list[Sample] = field(default_factory=list)
    started_at_epoch: float = 0.0
    elapsed_s: float = 0.0

    @property
    def values(self) -> list[float]:
        return [s.value for s in self.samples if s.value is not None]

    @property
    def failures(self) -> list[Sample]:
        return [s for s in self.samples if not s.ok]


class Sampler:
    """Takes ``config.measurements_count`` measurements at a fixed rate.

    Each sample is scheduled on an absolute timeline (``start + i * interval``) rather
    than by sleeping a fixed interval after each request. Request latency therefore does
    not accumulate into drift. If a request overruns its slot, the next one is sent
    immediately and the delay shows up as jitter in the results.

    A failing request (:class:`AmmeterError`) is recorded and sampling continues, so a
    flaky device produces a result with a failure count instead of no result at all.
    """

    def __init__(
        self,
        config: SamplingConfig,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._clock = clock
        self._sleep = sleep

    def run(self, measure: Callable[[], float]) -> SamplingRun:
        run = SamplingRun(self.config, started_at_epoch=time.time())
        interval = self.config.interval_seconds
        start = self._clock()

        for i in range(self.config.measurements_count):
            scheduled = i * interval
            self._sleep_until(start + scheduled)
            sent = self._clock()
            try:
                value, error = measure(), None
            except AmmeterError as exc:
                value, error = None, f"{type(exc).__name__}: {exc}"
            done = self._clock()
            run.samples.append(Sample(i, scheduled, sent - start, done - sent, value, error))

        # Wait out the last interval so the run honours total_duration_seconds.
        self._sleep_until(start + self.config.measurements_count * interval)
        run.elapsed_s = self._clock() - start
        return run

    def _sleep_until(self, deadline: float) -> None:
        remaining = deadline - self._clock()
        if remaining > 0:
            self._sleep(remaining)
