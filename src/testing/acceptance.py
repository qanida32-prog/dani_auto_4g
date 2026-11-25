"""Acceptance criteria: the limits that decide whether a test run PASSES or FAILS.

Limits come from config, in three layers (later wins):

    acceptance_criteria:                 global defaults
    ammeters.<name>.acceptance_criteria  what this device type is expected to achieve
    test_plans.<plan>.acceptance_criteria  what this scenario tolerates (e.g. stress)

A limit set to null is not checked.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from typing import Any

from src.utils.config import ConfigError


@dataclass(frozen=True)
class Check:
    name: str
    limit: float
    actual: float | None  # None = could not be measured (no valid samples)
    passed: bool


@dataclass(frozen=True)
class Verdict:
    passed: bool
    checks: list[Check]

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AcceptanceCriteria:
    max_failure_rate_percent: float | None = 0.0
    max_cv_percent: float | None = None
    max_abs_relative_error_percent: float | None = None  # needs reference_current_a
    max_outliers: int | None = None
    min_frequency_ratio: float | None = None  # achieved / target sampling frequency
    max_drift_percent: float | None = None  # |trend over the run| as % of the median

    @classmethod
    def from_layers(cls, *layers: dict[str, Any] | None) -> AcceptanceCriteria:
        merged: dict[str, Any] = {}
        known = {f.name for f in fields(cls)}
        for layer in layers:
            for key, value in (layer or {}).items():
                if key not in known:
                    raise ConfigError(
                        f"Unknown acceptance criterion {key!r}; known: {', '.join(sorted(known))}"
                    )
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ConfigError(f"Acceptance criterion {key} must be >= 0, got {value!r}")
                merged[key] = value
        return cls(**merged)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def evaluate(self, analysis: dict[str, Any]) -> Verdict:
        """Check an analysis (``Analysis.to_dict()`` form) against every configured limit."""
        stats, cons = analysis["statistics"], analysis["consistency"]
        timing, acc = analysis["timing"], analysis["accuracy"]
        checks: list[Check] = []

        def at_most(name: str, limit: float | None, actual: float | None) -> None:
            if limit is not None:
                ok = actual is not None and math.isfinite(actual) and actual <= limit
                checks.append(Check(name, limit, actual, ok))

        def at_least(name: str, limit: float | None, actual: float | None) -> None:
            if limit is not None:
                ok = actual is not None and math.isfinite(actual) and actual >= limit
                checks.append(Check(name, limit, actual, ok))

        # A run with no valid sample fails whatever the limits are.
        checks.append(Check("valid measurements", 1, stats["count"] if stats else 0, bool(stats)))
        at_most("failure rate %", self.max_failure_rate_percent, cons["failure_rate_percent"])
        at_most("CV %", self.max_cv_percent, stats["cv_percent"] if stats else None)
        at_most(
            "|relative error| %",
            self.max_abs_relative_error_percent,
            abs(acc["relative_error_percent"])
            if acc and acc["relative_error_percent"] is not None
            else None,
        )
        at_most("outliers", self.max_outliers, cons["outlier_count"] if stats else None)
        at_most(
            "|drift| %",
            self.max_drift_percent,
            abs(cons["drift_percent"]) if stats else None,
        )
        # Frequency needs at least two samples; with fewer it is undefined, not a failure.
        target, achieved = timing["target_frequency_hz"], timing["achieved_frequency_hz"]
        if achieved is not None and math.isfinite(achieved) and target:
            at_least("frequency ratio", self.min_frequency_ratio, achieved / target)
        return Verdict(all(c.passed for c in checks), checks)
