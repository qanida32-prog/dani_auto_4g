"""Acceptance criteria (pass/fail) and named test plans."""

from __future__ import annotations

import copy
from dataclasses import asdict

import pytest

from src.testing.acceptance import AcceptanceCriteria
from src.testing.analysis import analyze
from src.testing.plans import TestPlan, UnknownPlanError, list_plans
from src.testing.results import ResultStore
from src.testing.test_framework import AmmeterTestFramework
from src.utils.config import ConfigError, load_config
from tests.test_analysis import make_run


def analysis_of(values, reference=None) -> dict:
    return asdict(analyze(make_run(values), reference))


def failed_names(verdict) -> list[str]:
    return [c.name for c in verdict.failed_checks]


# ---------------------------------------------------------------------- criteria
def test_default_criteria_fail_on_any_failed_request():
    criteria = AcceptanceCriteria()
    assert criteria.evaluate(analysis_of([1.0, 1.1, 0.9])).passed
    verdict = criteria.evaluate(analysis_of([1.0, None, 0.9]))
    assert not verdict.passed
    assert failed_names(verdict) == ["failure rate %"]


def test_no_valid_measurement_always_fails():
    verdict = AcceptanceCriteria(max_failure_rate_percent=None).evaluate(analysis_of([None]))
    assert failed_names(verdict) == ["valid measurements"]


@pytest.mark.parametrize(
    ("criteria", "values", "reference", "failing"),
    [
        ({"max_cv_percent": 5}, [10.0, 10.1, 9.9, 10.0], None, []),
        ({"max_cv_percent": 5}, [10.0, 15.0, 5.0, 10.0], None, ["CV %"]),
        ({"max_abs_relative_error_percent": 5}, [10.0, 10.2, 9.8], 10.0, []),
        ({"max_abs_relative_error_percent": 5}, [12.0, 12.2, 11.8], 10.0, ["|relative error| %"]),
        # an accuracy limit without a reference cannot pass: "no data" is a failure
        ({"max_abs_relative_error_percent": 5}, [10.0, 10.2], None, ["|relative error| %"]),
        ({"max_outliers": 0}, [1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 10.0, 1.0], None, ["outliers"]),
        ({"max_drift_percent": 10}, [10.0 + 0.5 * i for i in range(11)], None, ["|drift| %"]),
        ({"min_frequency_ratio": 0.9}, [1.0, 1.0, 1.0], None, []),
    ],
)
def test_each_limit(criteria, values, reference, failing):
    verdict = AcceptanceCriteria(**criteria).evaluate(analysis_of(values, reference))
    assert failed_names(verdict) == failing
    assert verdict.passed == (not failing)


def test_slow_sampling_fails_frequency_ratio():
    analysis = analysis_of([1.0] * 5)
    analysis["timing"]["achieved_frequency_hz"] = 5.0  # target is 10 Hz
    verdict = AcceptanceCriteria(min_frequency_ratio=0.9).evaluate(analysis)
    assert failed_names(verdict) == ["frequency ratio"]


def test_layers_merge_later_wins():
    criteria = AcceptanceCriteria.from_layers(
        {"max_failure_rate_percent": 0, "max_cv_percent": 50},
        {"max_cv_percent": 20},
        {"max_failure_rate_percent": 15},
    )
    assert criteria.max_cv_percent == 20
    assert criteria.max_failure_rate_percent == 15


@pytest.mark.parametrize(
    "layer",
    [{"max_cv": 5}, {"max_cv_percent": -1}, {"max_outliers": "3"}, {"max_cv_percent": True}],
)
def test_invalid_criteria_rejected(layer):
    with pytest.raises(ConfigError):
        AcceptanceCriteria.from_layers(layer)


# ---------------------------------------------------------------------- plans
@pytest.fixture
def config() -> dict:
    return copy.deepcopy(load_config())


def test_every_plan_in_the_shipped_config_is_valid(config):
    plans = list_plans(config)
    assert {p.name for p in plans} >= {"smoke", "standard", "precision", "soak", "stress"}
    for plan in plans:
        AmmeterTestFramework(config=copy.deepcopy(config)).apply_plan(plan.name)


def test_unknown_plan(config):
    with pytest.raises(UnknownPlanError, match="smoke"):
        TestPlan.load(config, "nope")


@pytest.mark.parametrize(
    "section",
    [{"samples": 5}, {"ammeters": ["fluke"]}, {"acceptance_criteria": {"max_cv": 1}}],
)
def test_invalid_plan_rejected(config, section):
    config["test_plans"] = {"bad": section}
    with pytest.raises(ConfigError):
        AmmeterTestFramework(config=config).apply_plan("bad")


def test_plan_overrides_sampling_ammeters_faults_and_criteria(config, tmp_path):
    config["test_plans"] = {
        "custom": {
            "ammeters": ["entes"],
            "sampling": {"measurements_count": 7, "sampling_frequency_hz": 70},
            "error_simulation": {"enabled": True, "seed": 3},
            "acceptance_criteria": {"max_failure_rate_percent": 50},
        }
    }
    config["ammeters"]["entes"]["acceptance_criteria"] = {"max_cv_percent": 99}
    fw = AmmeterTestFramework(config=config, store=ResultStore(tmp_path))
    fw.apply_plan("custom")

    assert fw.default_ammeters == ["entes"]
    assert fw.sampling.measurements_count == 7
    assert fw.error_simulation.enabled and fw.error_simulation.seed == 3
    # the global rate stays when the plan does not override it
    assert fw.error_simulation.spike_rate == config["error_simulation"]["spike_rate"]
    criteria = fw.criteria_for("entes")
    assert criteria.max_failure_rate_percent == 50  # plan
    assert criteria.max_cv_percent == 99  # device
    assert criteria.min_frequency_ratio == config["acceptance_criteria"]["min_frequency_ratio"]


# ---------------------------------------------------------------------- report options
def test_report_options_follow_config():
    from src.testing.report import ReportOptions

    opts = ReportOptions.from_config(
        {"statistical_metrics": ["mean", "cv"], "visualization": {"plot_types": ["histogram"]}}
    )
    assert opts.metrics == ("mean", "cv")
    assert opts.plots == ("histogram",)
    assert opts.enabled


@pytest.mark.parametrize(
    "section",
    [
        {"statistical_metrics": ["mean", "average"]},
        {"visualization": {"plot_types": ["pie_chart"]}},
    ],
)
def test_unknown_metric_or_plot_rejected(section):
    from src.testing.report import ReportOptions

    with pytest.raises(ConfigError):
        ReportOptions.from_config(section)


def test_report_shows_only_configured_metrics_and_plots():
    from src.testing.report import ReportOptions, render_run_report
    from src.testing.results import TestResult

    analysis = analysis_of([1.0, 1.2, 0.8, 1.1])
    run = TestResult(
        run_id="r1",
        ammeter="entes",
        created_at="2026-11-25T10:00:00+00:00",
        metadata={
            "host": "127.0.0.1",
            "port": 5001,
            "sampling": {
                "measurements_count": 4,
                "sampling_frequency_hz": 10,
                "total_duration_seconds": 0.4,
            },
        },
        samples=[
            {"index": i, "actual_offset_s": i / 10, "value": v, "error": None}
            for i, v in enumerate([1.0, 1.2, 0.8, 1.1])
        ],
        analysis=analysis,
    ).to_dict()
    page = render_run_report(run, ReportOptions(metrics=("median",), plots=("histogram",)))
    assert "median (A)" in page and "std dev (A)" not in page
    assert "Distribution" in page and "Measurements over time" not in page
