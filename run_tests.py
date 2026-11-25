"""Command-line interface for the ammeter test framework.

Examples:
    python run_tests.py run                                   # every ammeter, config defaults
    python run_tests.py run -a greenlee -n 50 -f 20           # 50 samples at 20 Hz
    python run_tests.py run -d 10 -f 5 --simulate-errors      # 10 s at 5 Hz, with injected faults
    python run_tests.py run --plan stress                     # a named test plan from config
    python run_tests.py plans                                 # list the test plans
    python run_tests.py list [-a entes]                       # archived runs, newest first
    python run_tests.py show <run_id>                         # one archived run
    python run_tests.py compare <run_id> <run_id> ...         # rank archived runs

Exit code: 0 when every run meets its acceptance criteria, 1 when any run fails them (or a
device cannot start), 2 on usage errors.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from typing import Any

from src.testing.emulators import EmulatorStartupError, start_emulators, stop_emulators
from src.testing.plans import UnknownPlanError, list_plans
from src.testing.report import METRICS, ReportOptions, fmt
from src.testing.results import ResultNotFoundError
from src.testing.sampling import SamplingConfig, SamplingConfigError
from src.testing.test_framework import AmmeterTestFramework, UnknownAmmeterError
from src.utils.config import ConfigError
from src.utils.logger import setup_logging

logger = logging.getLogger("cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ammeter QA test framework")
    parser.add_argument("-c", "--config", help="config file (default: config/config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="sample ammeters, analyse, archive and report")
    run.add_argument("-p", "--plan", help="named test plan from config (see: plans)")
    run.add_argument("-a", "--ammeter", action="append", help="repeatable; default: all")
    run.add_argument("-n", "--count", type=int, help="number of measurements")
    run.add_argument("-d", "--duration", type=float, help="total test duration in seconds")
    run.add_argument("-f", "--frequency", type=float, help="sampling frequency in Hz")
    run.add_argument(
        "--no-emulators",
        action="store_true",
        help="do not start the emulators (devices are already running)",
    )
    run.add_argument(
        "--simulate-errors", action="store_true", help="enable error simulation from config"
    )

    sub.add_parser("plans", help="list the test plans defined in config")

    lst = sub.add_parser("list", help="list archived runs")
    lst.add_argument("-a", "--ammeter")
    lst.add_argument("-l", "--limit", type=int, default=20)

    show = sub.add_parser("show", help="show one archived run")
    show.add_argument("run_id")

    cmp_ = sub.add_parser("compare", help="rank archived runs by reliability")
    cmp_.add_argument("run_ids", nargs="+")
    return parser


# ---------------------------------------------------------------------- commands
def cmd_run(fw: AmmeterTestFramework, args: argparse.Namespace) -> int:
    if args.plan:
        plan = fw.apply_plan(args.plan)
        logger.info("test plan %r: %s", plan.name, plan.description or "-")
    sampling = None
    if any(v is not None for v in (args.count, args.duration, args.frequency)):
        base = fw.sampling
        given = {
            "measurements_count": args.count,
            "total_duration_seconds": args.duration,
            "sampling_frequency_hz": args.frequency,
        }
        # Fill in from config only as much as needed to have two values.
        if sum(v is not None for v in given.values()) < 2:
            for key in ("sampling_frequency_hz", "measurements_count"):
                if given[key] is None:
                    given[key] = getattr(base, key)
                    break
        sampling = SamplingConfig.from_values(**given)
    if args.simulate_errors:
        fw.error_simulation = dataclasses.replace(fw.error_simulation, enabled=True)

    names = args.ammeter or fw.default_ammeters
    emulators = {}
    if not args.no_emulators:
        emulators = start_emulators(fw.config, names)
    try:
        if len(names) == 1:
            results = [fw.run_test(names[0], sampling)]
            suite = None
        else:
            suite, results = fw.run_suite(names, sampling)
    finally:
        stop_emulators(emulators)

    print()
    print_runs([r.to_dict() for r in results], fw.report_options)
    print_failed_checks(results)
    if suite:
        print()
        print_comparison(suite.comparison)
        report_id = suite.suite_id
    else:
        report_id = results[0].run_id
    if fw.write_reports:
        print(f"\nReport:  {fw.store.report_path(report_id)}")
    print(f"Results: {fw.store.runs_dir}")
    passed = all(r.passed for r in results)
    print(f"\nVERDICT: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


def cmd_plans(fw: AmmeterTestFramework, args: argparse.Namespace) -> int:
    plans = list_plans(fw.config)
    if not plans:
        print("No test plans in config.")
        return 0
    rows = []
    for plan in plans:
        s = SamplingConfig.from_config(plan.sampling) if plan.sampling else fw.sampling
        rows.append(
            [
                plan.name,
                ", ".join(plan.ammeters or fw.ammeters),
                f"{s.measurements_count} @ {s.sampling_frequency_hz:g} Hz "
                f"({s.total_duration_seconds:g} s)",
                "on" if plan.error_simulation.get("enabled") else "-",
                plan.description,
            ]
        )
    print_table(["plan", "ammeters", "sampling", "faults", "description"], rows)
    return 0


def cmd_list(fw: AmmeterTestFramework, args: argparse.Namespace) -> int:
    runs = fw.store.list_runs(args.ammeter, args.limit)
    if not runs:
        print("No archived runs.")
        return 0
    print_table(
        ["run_id", "ammeter", "plan", "created (UTC)", "n", "mean (A)", "std (A)", "fail %"]
        + ["result"],
        [
            [
                r["run_id"],
                r["ammeter"],
                r["plan"] or "-",
                r["created_at"],
                r["count"],
                fmt(r["mean"]),
                fmt(r["std_dev"], 4),
                fmt(r["failures"], 3),
                verdict_label(r["passed"]),
            ]
            for r in runs
        ],
    )
    return 0


def cmd_show(fw: AmmeterTestFramework, args: argparse.Namespace) -> int:
    run = fw.store.load_run(args.run_id)
    meta = run["metadata"]
    print(f"{run['run_id']}  ({run['ammeter']}, {run['created_at']})")
    print(f"device   {meta['host']}:{meta['port']}  {meta['command']!r}")
    print(f"sampling {meta['sampling']}")
    print(f"plan     {meta.get('plan') or '-'}")
    print()
    print_runs([run], fw.report_options)
    if run.get("verdict"):
        print()
        print_table(
            ["acceptance check", "actual", "limit", "result"],
            [
                [c["name"], fmt(c["actual"], 4), fmt(c["limit"], 4), verdict_label(c["passed"])]
                for c in run["verdict"]["checks"]
            ],
        )
    report = fw.store.report_path(run["run_id"])
    if report.exists():
        print(f"\nReport: {report}")
    return 0


def cmd_compare(fw: AmmeterTestFramework, args: argparse.Namespace) -> int:
    print_comparison(dataclasses.asdict(fw.compare_runs(args.run_ids)))
    return 0


# ---------------------------------------------------------------------- output
def print_table(headers: list[str], rows: list[list[Any]]) -> None:
    cells = [[str(c) for c in row] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
    print("  ".join("-" * w for w in widths))
    for row in cells:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)))


def verdict_label(passed: bool | None) -> str:
    return "-" if passed is None else ("PASS" if passed else "FAIL")


def print_failed_checks(results) -> None:
    for r in results:
        for check in r.failed_checks:
            print(f"  {r.ammeter}: FAILED {check}")


def print_runs(runs: list[dict[str, Any]], options: ReportOptions) -> None:
    """Summary table; the statistics columns follow ``analysis.statistical_metrics``."""
    rows = []
    for r in runs:
        stats = r["analysis"]["statistics"]
        cells = [METRICS[m][1](stats) if stats else "-" for m in options.metrics]
        rows.append(
            [
                r["ammeter"],
                stats["count"] if stats else 0,
                *cells,
                fmt(r["analysis"]["consistency"]["failure_rate_percent"], 3),
                verdict_label((r.get("verdict") or {}).get("passed")),
            ]
        )
    headers = ["ammeter", "n", *(METRICS[m][0] for m in options.metrics), "fail %", "result"]
    print_table(headers, rows)


def print_comparison(comparison: dict[str, Any]) -> None:
    print_table(
        ["rank", "ammeter", "run_id", "CV %", "rel. err %", "fail %", "score"],
        [
            [
                r["rank"],
                r["ammeter"],
                r["run_id"],
                fmt(r["cv_percent"], 4),
                fmt(r["relative_error_percent"], 4),
                fmt(r["failure_rate_percent"], 3),
                fmt(r["score"], 4),
            ]
            for r in comparison["ranking"]
        ],
    )
    print(f"\nMost reliable: {comparison['most_reliable'] or '-'}   ({comparison['method']})")


COMMANDS = {
    "run": cmd_run,
    "plans": cmd_plans,
    "list": cmd_list,
    "show": cmd_show,
    "compare": cmd_compare,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        fw = AmmeterTestFramework(args.config)
        return COMMANDS[args.command](fw, args)
    except (
        ConfigError,
        SamplingConfigError,
        UnknownAmmeterError,
        UnknownPlanError,
        ResultNotFoundError,
    ) as exc:
        logger.error("%s", exc)
        return 2
    except EmulatorStartupError as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
