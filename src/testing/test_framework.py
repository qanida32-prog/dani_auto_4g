"""Unified test API: sample any configured ammeter, analyse, archive and report.

    framework = AmmeterTestFramework()
    result = framework.run_test("greenlee")              # one device
    suite, results = framework.run_suite()                # every device + comparison
    framework.apply_plan("stress")                        # switch to a named test plan

The framework only talks to devices over the documented TCP protocol (host, port,
command from config), so adding a device type is a config change, not a code change.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from Ammeters.client import request_current_from_ammeter
from src.testing.acceptance import AcceptanceCriteria
from src.testing.analysis import Comparison, analyze, compare
from src.testing.error_simulation import ErrorSimulationConfig, with_fault_injection
from src.testing.plans import TestPlan
from src.testing.report import ReportOptions, render_run_report, render_suite_report
from src.testing.results import (
    ResultStore,
    SuiteResult,
    TestResult,
    environment_metadata,
    new_id,
    utc_now_iso,
)
from src.testing.sampling import Sampler, SamplingConfig
from src.utils.config import ConfigError, load_config
from src.utils.logger import TestLogger

logger = logging.getLogger("framework")


class UnknownAmmeterError(ValueError):
    """The requested ammeter is not in the configuration."""


class AmmeterTestFramework:
    __test__ = False  # not a pytest test class

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        config: dict[str, Any] | None = None,
        store: ResultStore | None = None,
    ) -> None:
        self.config = config if config is not None else load_config(config_path)
        try:
            testing = self.config["testing"]
            self.connection = testing["connection"]
            self.ammeters: dict[str, dict[str, Any]] = self.config["ammeters"]
            self.sampling = SamplingConfig.from_config(testing["sampling"])
        except KeyError as exc:
            raise ConfigError(f"Missing config key: {exc}") from exc
        self.error_simulation = ErrorSimulationConfig.from_config(
            self.config.get("error_simulation")
        )
        results_dir = self.config.get("result_management", {}).get("results_dir", "results")
        self.store = store or ResultStore(results_dir)
        self.report_options = ReportOptions.from_config(self.config.get("analysis"))
        self.write_reports = self.report_options.enabled
        self.plan: TestPlan | None = None
        self.default_ammeters: list[str] = list(self.ammeters)
        for name in self.ammeters:
            self.criteria_for(name)  # validate every acceptance layer at start-up

    def apply_plan(self, name: str) -> TestPlan:
        """Switch to a named test plan from ``test_plans`` in the config."""
        plan = TestPlan.load(self.config, name)
        if plan.sampling is not None:
            self.sampling = SamplingConfig.from_config(plan.sampling)
        if plan.error_simulation:
            self.error_simulation = ErrorSimulationConfig.from_config(
                {**(self.config.get("error_simulation") or {}), **plan.error_simulation}
            )
        if plan.ammeters:
            self.default_ammeters = plan.ammeters
        self.plan = plan
        for ammeter in self.ammeters:
            self.criteria_for(ammeter)
        return plan

    def criteria_for(self, ammeter_type: str) -> AcceptanceCriteria:
        """Global limits, then the device's own, then the plan's (later wins)."""
        return AcceptanceCriteria.from_layers(
            self.config.get("acceptance_criteria"),
            self._device(ammeter_type).get("acceptance_criteria"),
            self.plan.acceptance_criteria if self.plan else None,
        )

    # ------------------------------------------------------------------ public API
    def run_test(
        self,
        ammeter_type: str,
        sampling: SamplingConfig | None = None,
        *,
        suite_id: str | None = None,
    ) -> TestResult:
        """Sample one ammeter, analyse the data, store and (optionally) report it."""
        device = self._device(ammeter_type)
        sampling = sampling or self.sampling
        run_id = new_id(ammeter_type)
        logger.info(
            "%s: %d samples at %g Hz (%g s) - run %s",
            ammeter_type,
            sampling.measurements_count,
            sampling.sampling_frequency_hz,
            sampling.total_duration_seconds,
            run_id,
        )

        criteria = self.criteria_for(ammeter_type)
        with TestLogger(run_id, self.store.logs_dir, add_timestamp=False) as run_log:
            run_log.info(
                f"run {run_id}: {ammeter_type} at {self.connection['host']}:{device['port']} "
                f"command={device['command']!r} plan={self.plan.name if self.plan else '-'}"
            )
            run_log.info(f"sampling {sampling.to_dict()}")
            run_log.info(f"acceptance {criteria.to_dict()}")
            if self.error_simulation.enabled:
                run_log.warning(f"error simulation ON {self.error_simulation.to_dict()}")

            run = Sampler(sampling).run(self._measure_fn(ammeter_type, device))
            for s in run.samples:
                if s.ok:
                    run_log.debug(
                        f"#{s.index} t={s.actual_offset_s:.4f}s latency={s.latency_s * 1000:.1f}ms "
                        f"value={s.value!r} A"
                    )
                else:
                    run_log.error(f"#{s.index} t={s.actual_offset_s:.4f}s FAILED: {s.error}")

            analysis = analyze(run, device.get("reference_current_a"))
            verdict = criteria.evaluate(analysis.to_dict())
            stats = analysis.statistics
            if stats:
                run_log.info(
                    f"statistics n={stats.count} mean={stats.mean:.6g} median={stats.median:.6g} "
                    f"std={stats.std_dev:.4g} min={stats.min:.6g} max={stats.max:.6g}"
                )
            for check in verdict.checks:
                level = run_log.info if check.passed else run_log.error
                level(f"check {check.name}: actual={check.actual} limit={check.limit}")
            run_log.info(f"VERDICT {'PASS' if verdict.passed else 'FAIL'}")
        result = TestResult(
            run_id=run_id,
            ammeter=ammeter_type,
            created_at=utc_now_iso(),
            metadata={
                "host": self.connection["host"],
                "port": device["port"],
                "command": device["command"],
                "timeout_seconds": self.connection["timeout_seconds"],
                "sampling": sampling.to_dict(),
                "error_simulation": self.error_simulation.to_dict(),
                "suite_id": suite_id,
                "plan": self.plan.name if self.plan else None,
                "acceptance_criteria": criteria.to_dict(),
                "started_at_epoch": run.started_at_epoch,
                "environment": environment_metadata(),
            },
            samples=[asdict(s) for s in run.samples],
            analysis=analysis.to_dict(),
            verdict=verdict.to_dict(),
        )
        self.store.save_run(result)
        if self.write_reports:
            self.store.report_path(run_id).write_text(
                render_run_report(result.to_dict(), self.report_options), encoding="utf-8"
            )
        self._log_summary(result)
        return result

    def run_suite(
        self, ammeter_types: list[str] | None = None, sampling: SamplingConfig | None = None
    ) -> tuple[SuiteResult, list[TestResult]]:
        """Sample several ammeters at the same time and rank them."""
        names = ammeter_types or self.default_ammeters
        for name in names:
            self._device(name)  # fail fast on a typo before any sampling starts
        suite_id = new_id("suite")

        # Devices are independent, so they are sampled in parallel: the suite takes as long
        # as one run, and all devices see the same time window.
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futures = [pool.submit(self.run_test, n, sampling, suite_id=suite_id) for n in names]
            results = [f.result() for f in futures]

        comparison = compare([r.to_dict() for r in results])
        suite = SuiteResult(
            suite_id=suite_id,
            created_at=utc_now_iso(),
            run_ids=[r.run_id for r in results],
            comparison=comparison.to_dict(),
            metadata={
                "plan": self.plan.name if self.plan else None,
                "passed": all(r.passed for r in results),
                "environment": environment_metadata(),
            },
        )
        self.store.save_suite(suite)
        if self.write_reports:
            self.store.report_path(suite_id).write_text(
                render_suite_report(
                    suite.to_dict(), [r.to_dict() for r in results], self.report_options
                ),
                encoding="utf-8",
            )
        logger.info("suite %s: most reliable = %s", suite_id, comparison.most_reliable)
        return suite, results

    def compare_runs(self, run_ids: list[str]) -> Comparison:
        """Rank stored runs, e.g. the same device on different days."""
        return compare([self.store.load_run(run_id) for run_id in run_ids])

    # ------------------------------------------------------------------ internals
    def _device(self, ammeter_type: str) -> dict[str, Any]:
        try:
            return self.ammeters[ammeter_type]
        except KeyError:
            known = ", ".join(sorted(self.ammeters))
            raise UnknownAmmeterError(
                f"Unknown ammeter {ammeter_type!r}; configured: {known}"
            ) from None

    def _measure_fn(self, name: str, device: dict[str, Any]):
        command = device["command"].encode("utf-8")
        host, timeout = self.connection["host"], self.connection["timeout_seconds"]

        def measure() -> float:
            return request_current_from_ammeter(device["port"], command, host, timeout)

        return with_fault_injection(measure, self.error_simulation, stream=name)

    @staticmethod
    def _log_summary(result: TestResult) -> None:
        stats = result.analysis["statistics"]
        failures = result.analysis["consistency"]["failure_rate_percent"]
        for check in result.failed_checks:
            logger.warning("%s FAILED %s", result.ammeter, check)
        if stats is None:
            logger.error("%s: no valid measurements (%s)", result.ammeter, result.run_id)
            return
        logger.info(
            "%s: %s - mean %.6g A, median %.6g A, std %.4g A, min %.6g A, max %.6g A, "
            "failures %.1f %%",
            result.ammeter,
            "PASS" if result.passed else "FAIL",
            stats["mean"],
            stats["median"],
            stats["std_dev"],
            stats["min"],
            stats["max"],
            failures,
        )
