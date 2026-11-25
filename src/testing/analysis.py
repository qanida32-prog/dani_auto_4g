"""Statistical analysis of sampling runs and cross-ammeter comparison (stdlib only)."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any

from src.testing.sampling import SamplingRun

# Two-sided 95 % Student-t critical values by degrees of freedom. Above 30 the normal
# value 1.96 is close enough. Kept as a table so scipy is not needed.
# fmt: off
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
    9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
    16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
    23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}
# fmt: on
_Z95 = 1.96
OUTLIER_IQR_FACTOR = 1.5


def t_critical_95(dof: int) -> float:
    return _T95.get(dof, _Z95) if dof >= 1 else math.nan


@dataclass(frozen=True)
class Statistics:
    """Descriptive statistics of the successful measurements, in amperes."""

    count: int
    mean: float
    median: float
    std_dev: float  # sample standard deviation (n - 1)
    min: float
    max: float
    range: float
    cv_percent: float  # coefficient of variation = std_dev / mean
    sem: float  # standard error of the mean
    ci95_low: float
    ci95_high: float

    @classmethod
    def from_values(cls, values: list[float]) -> Statistics:
        if not values:
            raise ValueError("Cannot compute statistics of an empty sample")
        n = len(values)
        mean = statistics.fmean(values)
        std = statistics.stdev(values) if n > 1 else 0.0
        sem = std / math.sqrt(n)
        half_width = t_critical_95(n - 1) * sem if n > 1 else math.nan
        return cls(
            count=n,
            mean=mean,
            median=statistics.median(values),
            std_dev=std,
            min=min(values),
            max=max(values),
            range=max(values) - min(values),
            cv_percent=100.0 * std / abs(mean) if mean else math.inf,
            sem=sem,
            ci95_low=mean - half_width,
            ci95_high=mean + half_width,
        )


@dataclass(frozen=True)
class TimingStatistics:
    """How well the sampler kept to its schedule."""

    target_frequency_hz: float
    achieved_frequency_hz: float
    mean_jitter_ms: float
    max_jitter_ms: float
    mean_latency_ms: float
    max_latency_ms: float
    elapsed_s: float

    @classmethod
    def from_run(cls, run: SamplingRun) -> TimingStatistics:
        samples = run.samples
        jitters = [s.jitter_s * 1000 for s in samples] or [0.0]
        latencies = [s.latency_s * 1000 for s in samples] or [0.0]
        if len(samples) > 1:
            span = samples[-1].actual_offset_s - samples[0].actual_offset_s
            achieved = (len(samples) - 1) / span if span > 0 else math.inf
        else:
            achieved = math.nan
        return cls(
            target_frequency_hz=run.config.sampling_frequency_hz,
            achieved_frequency_hz=achieved,
            mean_jitter_ms=statistics.fmean(jitters),
            max_jitter_ms=max(jitters),
            mean_latency_ms=statistics.fmean(latencies),
            max_latency_ms=max(latencies),
            elapsed_s=run.elapsed_s,
        )


THEIL_SEN_MAX_POINTS = 600  # ~180k pairs: well under a second


def theil_sen_slope(xs: list[float], ys: list[float]) -> float:
    """Median of the slopes between all pairs of points.

    Unlike least squares, one extreme reading cannot drag the result: up to ~29 % of the
    points can be arbitrary before the estimate breaks down. Heavy-tailed devices (Greenlee:
    I = V / R with R down to 0.1 ohm) would otherwise show large "drift" from a single spike.
    Long runs are thinned to evenly spaced points to keep the O(n^2) cost bounded.
    """
    n = len(xs)
    if n > THEIL_SEN_MAX_POINTS:
        keep = [
            round(i * (n - 1) / (THEIL_SEN_MAX_POINTS - 1)) for i in range(THEIL_SEN_MAX_POINTS)
        ]
        xs, ys = [xs[i] for i in keep], [ys[i] for i in keep]
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(xs))
        for j in range(i + 1, len(xs))
        if xs[j] != xs[i]
    ]
    return statistics.median(slopes) if slopes else 0.0


@dataclass(frozen=True)
class Consistency:
    """Is the device stable over the run? (bonus: performance consistency evaluation)"""

    outlier_count: int  # outside Tukey fences (Q1 - 1.5 IQR, Q3 + 1.5 IQR)
    outlier_indices: list[int]
    drift_per_sample: float  # Theil-Sen slope of value vs sample index, A/sample
    drift_percent: float  # slope * (n - 1) as a % of the median: total trend over the run
    first_half_mean: float
    second_half_mean: float
    failure_rate_percent: float

    @classmethod
    def from_run(cls, run: SamplingRun) -> Consistency:
        ok = [s for s in run.samples if s.ok]
        values = [s.value for s in ok]
        total = len(run.samples)
        failure_rate = 100.0 * (total - len(ok)) / total if total else 0.0

        outliers: list[int] = []
        if len(values) >= 4:
            q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
            iqr = q3 - q1
            low, high = q1 - OUTLIER_IQR_FACTOR * iqr, q3 + OUTLIER_IQR_FACTOR * iqr
            outliers = [s.index for s in ok if not low <= s.value <= high]

        slope = theil_sen_slope([s.index for s in ok], values) if len(values) >= 2 else 0.0
        median = statistics.median(values) if values else math.nan
        drift_pct = 100.0 * slope * (len(values) - 1) / abs(median) if values and median else 0.0

        half = len(values) // 2
        return cls(
            outlier_count=len(outliers),
            outlier_indices=outliers,
            drift_per_sample=slope,
            drift_percent=drift_pct,
            first_half_mean=statistics.fmean(values[:half]) if half else math.nan,
            second_half_mean=statistics.fmean(values[half:]) if values[half:] else math.nan,
            failure_rate_percent=failure_rate,
        )


@dataclass(frozen=True)
class Accuracy:
    """Bias against a known reference current, when one is configured."""

    reference_current: float
    bias: float  # mean - reference
    relative_error_percent: float
    reference_within_ci95: bool

    @classmethod
    def from_statistics(cls, stats: Statistics, reference: float) -> Accuracy:
        bias = stats.mean - reference
        within = stats.ci95_low <= reference <= stats.ci95_high if stats.count > 1 else False
        return cls(reference, bias, 100.0 * bias / reference if reference else math.inf, within)


@dataclass(frozen=True)
class Analysis:
    statistics: Statistics | None  # None when every measurement failed
    timing: TimingStatistics
    consistency: Consistency
    accuracy: Accuracy | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze(run: SamplingRun, reference_current: float | None = None) -> Analysis:
    values = run.values
    stats = Statistics.from_values(values) if values else None
    accuracy = (
        Accuracy.from_statistics(stats, reference_current)
        if stats is not None and reference_current is not None
        else None
    )
    return Analysis(stats, TimingStatistics.from_run(run), Consistency.from_run(run), accuracy)


# ---------------------------------------------------------------------- comparison
@dataclass(frozen=True)
class RankedAmmeter:
    rank: int
    ammeter: str
    run_id: str
    mean: float | None
    cv_percent: float | None
    failure_rate_percent: float
    relative_error_percent: float | None
    score: float  # lower is better


@dataclass(frozen=True)
class Comparison:
    """Cross-ammeter comparison (bonus: accuracy assessment).

    The ammeters measure on very different scales (milliamps to tens of amps), so the
    absolute standard deviation cannot be compared. Precision is compared with the
    coefficient of variation, which has no unit. Accuracy needs a known true value; when
    ``reference_current_a`` is configured for a device, the relative error is used too.

    Score (lower is better) = CV % + |relative error %| (when known) + 10 x failure rate %.
    Failures are weighted heavily: a device that does not answer is the least reliable.
    """

    ranking: list[RankedAmmeter]
    most_reliable: str | None
    method: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


FAILURE_WEIGHT = 10.0
SCORE_METHOD = (
    "score = CV % + |relative error %| (if a reference is configured) "
    f"+ {FAILURE_WEIGHT:g} x failure rate %; lower is better"
)


def compare(results: list[dict[str, Any]]) -> Comparison:
    """Rank stored test results (``TestResult.to_dict()`` form) by reliability."""
    rows = []
    for r in results:
        a = r["analysis"]
        stats, acc, cons = a["statistics"], a["accuracy"], a["consistency"]
        cv = stats["cv_percent"] if stats else None
        rel = acc["relative_error_percent"] if acc else None
        failure = cons["failure_rate_percent"]
        score = (
            math.inf
            if cv is None
            else cv + (abs(rel) if rel is not None else 0.0) + FAILURE_WEIGHT * failure
        )
        rows.append((score, r, cv, rel, failure, stats))

    rows.sort(key=lambda row: row[0])
    ranking = [
        RankedAmmeter(
            rank=i,
            ammeter=r["ammeter"],
            run_id=r["run_id"],
            mean=stats["mean"] if stats else None,
            cv_percent=cv,
            failure_rate_percent=failure,
            relative_error_percent=rel,
            score=score,
        )
        for i, (score, r, cv, rel, failure, stats) in enumerate(rows, start=1)
    ]
    best = ranking[0].ammeter if ranking and math.isfinite(ranking[0].score) else None
    return Comparison(ranking, best, SCORE_METHOD)
