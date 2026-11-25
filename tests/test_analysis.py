"""Statistics, consistency, accuracy and comparison."""

from __future__ import annotations

import math
from dataclasses import asdict

import pytest

from src.testing.analysis import Statistics, analyze, compare, t_critical_95
from src.testing.sampling import Sample, SamplingConfig, SamplingRun


def make_run(values: list[float | None], freq: float = 10.0) -> SamplingRun:
    cfg = SamplingConfig.from_values(len(values), sampling_frequency_hz=freq)
    samples = [
        Sample(i, i / freq, i / freq, 0.001, v, None if v is not None else "simulated")
        for i, v in enumerate(values)
    ]
    return SamplingRun(cfg, samples, elapsed_s=len(values) / freq)


def test_statistics_known_values():
    s = Statistics.from_values([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert s.count == 8
    assert s.mean == pytest.approx(5.0)
    assert s.median == pytest.approx(4.5)
    assert s.std_dev == pytest.approx(2.138089935)  # sample std (n - 1)
    assert (s.min, s.max, s.range) == (2.0, 9.0, 7.0)
    assert s.cv_percent == pytest.approx(100 * 2.138089935 / 5)
    half = t_critical_95(7) * 2.138089935 / math.sqrt(8)
    assert (s.ci95_low, s.ci95_high) == pytest.approx((5 - half, 5 + half))


def test_single_value_has_zero_spread_and_no_ci():
    s = Statistics.from_values([1.5])
    assert s.std_dev == 0
    assert math.isnan(s.ci95_low)


def test_empty_values_raise():
    with pytest.raises(ValueError):
        Statistics.from_values([])


def test_analysis_counts_failures_and_ignores_them_in_statistics():
    a = analyze(make_run([1.0, None, 3.0, None]))
    assert a.statistics.count == 2
    assert a.statistics.mean == pytest.approx(2.0)
    assert a.consistency.failure_rate_percent == pytest.approx(50.0)


def test_all_failed_gives_no_statistics():
    a = analyze(make_run([None, None]), reference_current=1.0)
    assert a.statistics is None
    assert a.accuracy is None
    assert a.consistency.failure_rate_percent == 100


def test_spike_is_flagged_as_outlier():
    values = [1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 10.0, 1.0]
    assert analyze(make_run(values)).consistency.outlier_indices == [6]


def test_linear_trend_is_reported_as_drift():
    values = [10.0 + 0.5 * i for i in range(11)]  # 10 -> 15
    c = analyze(make_run(values)).consistency
    assert c.drift_per_sample == pytest.approx(0.5)
    assert c.drift_percent == pytest.approx(100 * 5 / 12.5)  # trend / median
    assert c.second_half_mean > c.first_half_mean


def test_single_spike_is_not_reported_as_drift():
    """Regression: a least-squares slope turned one Greenlee spike into 210 % 'drift'."""
    values = [0.1 + 0.01 * ((i * 7) % 5) for i in range(300)]
    values[290] = 40.0  # one heavy-tail reading near the end
    c = analyze(make_run(values)).consistency
    assert abs(c.drift_percent) < 5
    assert 290 in c.outlier_indices


def test_theil_sen_thins_long_runs_and_keeps_the_slope():
    from src.testing.analysis import THEIL_SEN_MAX_POINTS, theil_sen_slope

    n = THEIL_SEN_MAX_POINTS * 3
    assert theil_sen_slope(list(range(n)), [2.0 * i + 1 for i in range(n)]) == pytest.approx(2.0)


def test_accuracy_against_reference():
    a = analyze(make_run([9.0, 10.0, 11.0, 10.0]), reference_current=10.0)
    assert a.accuracy.bias == pytest.approx(0.0)
    assert a.accuracy.relative_error_percent == pytest.approx(0.0)
    assert a.accuracy.reference_within_ci95

    biased = analyze(make_run([12.0, 12.1, 11.9, 12.0]), reference_current=10.0)
    assert biased.accuracy.relative_error_percent == pytest.approx(20.0)
    assert not biased.accuracy.reference_within_ci95


def test_timing_statistics():
    t = analyze(make_run([1.0] * 11, freq=10)).timing
    assert t.achieved_frequency_hz == pytest.approx(10.0)
    assert t.mean_jitter_ms == pytest.approx(0.0)
    assert t.mean_latency_ms == pytest.approx(1.0)


def _stored(name: str, values: list[float | None], ref: float | None = None) -> dict:
    return {
        "ammeter": name,
        "run_id": f"run-{name}",
        "analysis": asdict(analyze(make_run(values), ref)),
    }


def test_comparison_ranks_precise_device_first_regardless_of_scale():
    precise_big = _stored("big", [100.0, 101.0, 99.0, 100.0])  # CV ~0.8 %
    noisy_small = _stored("small", [0.01, 0.02, 0.005, 0.015])  # CV ~50 %
    result = compare([noisy_small, precise_big])
    assert [r.ammeter for r in result.ranking] == ["big", "small"]
    assert result.most_reliable == "big"


def test_comparison_penalises_failures_and_bias():
    clean = _stored("clean", [10.0, 10.2, 9.8, 10.0], ref=10.0)
    flaky = _stored("flaky", [10.0, None, 9.9, 10.1], ref=10.0)
    biased = _stored("biased", [13.0, 13.2, 12.8, 13.0], ref=10.0)
    ranking = [r.ammeter for r in compare([biased, flaky, clean]).ranking]
    assert ranking == ["clean", "biased", "flaky"]


def test_comparison_with_no_valid_device():
    result = compare([_stored("dead", [None, None])])
    assert result.most_reliable is None
    assert math.isinf(result.ranking[0].score)


@pytest.mark.parametrize(("lo", "hi"), [(0.0187, 1.4957), (12.0, 1540.8), (0.0, 7.0), (-3.2, 0.4)])
def test_chart_axis_ticks_cover_the_data(lo, hi):
    from src.testing.report import _nice_ticks

    ticks = _nice_ticks(lo, hi)
    assert ticks[0] <= lo and ticks[-1] >= hi
    assert 3 <= len(ticks) <= 12


def test_one_late_last_sample_does_not_skew_achieved_frequency():
    """Regression: (n-1)/span made a healthy 8-sample run at 40 Hz look like 34 Hz."""
    run = make_run([1.0] * 8, freq=40)
    last = run.samples[-1]
    run.samples[-1] = Sample(
        last.index, last.scheduled_offset_s, last.actual_offset_s + 0.0156, 0.001, 1.0
    )
    t = analyze(run).timing
    assert t.achieved_frequency_hz == pytest.approx(40.0)
    assert t.max_jitter_ms == pytest.approx(15.6)


def test_device_slower_than_the_rate_shows_in_achieved_frequency():
    run = make_run([1.0] * 6, freq=40)
    run.samples[:] = [
        Sample(s.index, s.scheduled_offset_s, s.index * 0.05, 0.05, 1.0) for s in run.samples
    ]
    assert analyze(run).timing.achieved_frequency_hz == pytest.approx(20.0)


def test_achieved_frequency_on_real_macos_ci_timings():
    """Regression with the send times a macOS CI runner actually logged at 10 Hz: random
    wake-ups up to 77 ms late, absolute schedule catching up. Median-gap said 0.84."""
    sent = [0, 0.1769, 0.2204, 0.3679, 0.4102, 0.5296, 0.6751, 0.7056, 0.8771, 0.9517]
    run = make_run([1.0] * len(sent), freq=10)
    run.samples[:] = [Sample(i, i / 10, t, 0.001, 1.0) for i, t in enumerate(sent)]
    t = analyze(run).timing
    assert t.achieved_frequency_hz == pytest.approx(10, rel=0.05)
    assert t.max_jitter_ms == pytest.approx(77.1, abs=0.1)  # lateness is still reported
