"""Sampling configuration and timing, using a fake clock so the tests are exact and fast."""

from __future__ import annotations

import math
import time

import pytest

from Ammeters.client import AmmeterConnectionError
from src.testing.sampling import Sampler, SamplingConfig, SamplingConfigError


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.now += seconds


def make_sampler(config: SamplingConfig) -> tuple[Sampler, FakeClock]:
    clock = FakeClock()
    return Sampler(config, clock=clock, sleep=clock.sleep), clock


# ---------------------------------------------------------------------- config
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ({"measurements_count": 20, "sampling_frequency_hz": 10}, (20, 2.0, 10.0)),
        ({"measurements_count": 20, "total_duration_seconds": 4}, (20, 4.0, 5.0)),
        ({"total_duration_seconds": 3, "sampling_frequency_hz": 4}, (12, 3.0, 4.0)),
        (
            {"measurements_count": 10, "total_duration_seconds": 2, "sampling_frequency_hz": 5},
            (10, 2.0, 5.0),
        ),
    ],
)
def test_any_two_values_define_the_third(given, expected):
    cfg = SamplingConfig.from_values(**given)
    assert (cfg.measurements_count, cfg.total_duration_seconds, cfg.sampling_frequency_hz) == (
        pytest.approx(expected[0]),
        pytest.approx(expected[1]),
        pytest.approx(expected[2]),
    )


@pytest.mark.parametrize(
    "given",
    [
        {"measurements_count": 10},
        {},
        {"measurements_count": 0, "sampling_frequency_hz": 10},
        {"measurements_count": 10, "sampling_frequency_hz": -1},
        {"measurements_count": 2.5, "sampling_frequency_hz": 10},
        {"measurements_count": True, "sampling_frequency_hz": 10},
        {"total_duration_seconds": float("nan"), "sampling_frequency_hz": 10},
        {"measurements_count": "10", "sampling_frequency_hz": 10},
        {"measurements_count": 10, "total_duration_seconds": 2, "sampling_frequency_hz": 10},
    ],
)
def test_invalid_sampling_config_is_rejected(given):
    with pytest.raises(SamplingConfigError):
        SamplingConfig.from_values(**given)


def test_from_config_section_accepts_nulls():
    cfg = SamplingConfig.from_config(
        {"measurements_count": 20, "total_duration_seconds": None, "sampling_frequency_hz": 10}
    )
    assert cfg.total_duration_seconds == pytest.approx(2.0)


# ---------------------------------------------------------------------- timing
def test_takes_configured_number_of_samples_on_schedule():
    sampler, clock = make_sampler(SamplingConfig.from_values(5, sampling_frequency_hz=10))
    run = sampler.run(lambda: 1.0)

    assert len(run.samples) == 5
    assert [s.scheduled_offset_s for s in run.samples] == pytest.approx([0, 0.1, 0.2, 0.3, 0.4])
    assert all(s.jitter_s == pytest.approx(0) for s in run.samples)
    assert run.elapsed_s == pytest.approx(0.5)  # honours total_duration_seconds


def test_request_latency_does_not_accumulate_into_drift():
    sampler, clock = make_sampler(SamplingConfig.from_values(10, sampling_frequency_hz=10))

    def slow_measure() -> float:
        clock.now += 0.03  # 30 ms per request, less than the 100 ms interval
        return 1.0

    run = sampler.run(slow_measure)
    assert run.samples[-1].actual_offset_s == pytest.approx(0.9)  # not 0.9 + 9 * 0.03
    assert all(s.latency_s == pytest.approx(0.03) for s in run.samples)


def test_overrunning_request_shows_as_jitter_and_schedule_recovers():
    sampler, clock = make_sampler(SamplingConfig.from_values(4, sampling_frequency_hz=10))
    latencies = iter([0.25, 0.0, 0.0, 0.0])

    def measure() -> float:
        clock.now += next(latencies)
        return 1.0

    run = sampler.run(measure)
    offsets = [s.actual_offset_s for s in run.samples]
    assert offsets == pytest.approx([0.0, 0.25, 0.25, 0.3])
    assert run.samples[1].jitter_s == pytest.approx(0.15)
    assert run.samples[3].jitter_s == pytest.approx(0.0)  # back on schedule


def test_failed_request_is_recorded_and_sampling_continues():
    sampler, _ = make_sampler(SamplingConfig.from_values(4, sampling_frequency_hz=100))
    results = iter([1.0, AmmeterConnectionError("timed out"), 3.0, 4.0])

    def measure() -> float:
        item = next(results)
        if isinstance(item, Exception):
            raise item
        return item

    run = sampler.run(measure)
    assert run.values == [1.0, 3.0, 4.0]
    assert len(run.failures) == 1
    assert run.failures[0].index == 1
    assert "AmmeterConnectionError" in run.failures[0].error


def test_unexpected_exception_is_not_swallowed():
    sampler, _ = make_sampler(SamplingConfig.from_values(2, sampling_frequency_hz=100))
    with pytest.raises(ZeroDivisionError):
        sampler.run(lambda: 1 / 0)


def test_real_clock_timing_is_close_to_target():
    cfg = SamplingConfig.from_values(10, sampling_frequency_hz=50)  # 0.2 s
    run = Sampler(cfg).run(lambda: 1.0)
    # Loose bounds on purpose: shared CI runners (macOS especially) can oversleep by tens
    # of ms. The exact scheduling logic is covered by the fake-clock tests above.
    assert run.elapsed_s == pytest.approx(0.2, abs=0.1)
    assert max(s.jitter_s for s in run.samples) < 0.08


def coarse_sleep(seconds: float) -> None:
    """time.sleep as on Windows before Python 3.11: rounded up to the ~15.6 ms timer tick."""
    tick = 0.0156
    time.sleep(math.ceil(seconds / tick) * tick if seconds > 0 else 0)


def test_hybrid_sleep_keeps_the_rate_with_a_coarse_timer():
    """Regression: CI on Windows/Python 3.10 reached only ~87 % of 40 Hz."""
    cfg = SamplingConfig.from_values(20, sampling_frequency_hz=40)
    precise = Sampler(cfg, sleep=coarse_sleep, spin_margin_s=0.02).run(lambda: 1.0)
    naive = Sampler(cfg, sleep=coarse_sleep, spin_margin_s=0.0).run(lambda: 1.0)

    assert precise.elapsed_s == pytest.approx(0.5, abs=0.05)
    # mean, not max: the OS can pause any process for a slot now and then
    assert sum(s.jitter_s for s in precise.samples) / len(precise.samples) < 0.005
    # without the spin phase every slot oversleeps by part of a tick
    assert sum(s.jitter_s for s in naive.samples) / len(naive.samples) > 0.004


def test_fake_clock_never_spins():
    clock = FakeClock()
    sampler = Sampler(SamplingConfig.from_values(3, sampling_frequency_hz=10), clock, clock.sleep)
    assert sampler._spin_margin_s == 0.0
