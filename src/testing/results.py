"""Result archive: every test run gets a unique id and is stored as JSON with its metadata.

Layout under ``results_dir``::

    runs/<run_id>.json        one ammeter, one sampling run (samples + analysis + metadata)
    suites/<suite_id>.json    a set of runs taken together, plus their comparison
    reports/<id>.html         human-readable report for a run or a suite

Plain JSON files: no database, readable by any tool, easy to diff and to archive.
"""

from __future__ import annotations

import json
import math
import platform
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.config import PROJECT_ROOT

SCHEMA_VERSION = 1


class ResultNotFoundError(LookupError):
    """No stored run or suite has the requested id."""


def new_id(label: str) -> str:
    """Sortable, unique id: ``20261124-120952-greenlee-3fa2c1``."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{label}-{uuid.uuid4().hex[:6]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def environment_metadata() -> dict[str, str]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": platform.node(),
    }


@dataclass
class TestResult:
    """One ammeter, one sampling run."""

    __test__ = False  # not a pytest test class

    run_id: str
    ammeter: str
    created_at: str
    metadata: dict[str, Any]
    samples: list[dict[str, Any]]
    analysis: dict[str, Any]
    verdict: dict[str, Any] | None = None  # acceptance-criteria result, see acceptance.py
    schema_version: int = SCHEMA_VERSION

    @property
    def passed(self) -> bool:
        """The acceptance verdict. Without one: a valid measurement and no failed request."""
        if self.verdict is not None:
            return bool(self.verdict["passed"])
        cons = self.analysis["consistency"]
        return self.analysis["statistics"] is not None and cons["failure_rate_percent"] == 0

    @property
    def failed_checks(self) -> list[str]:
        if not self.verdict:
            return []
        return [describe_check(c) for c in self.verdict["checks"] if not c["passed"]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SuiteResult:
    suite_id: str
    created_at: str
    run_ids: list[str]
    comparison: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def describe_check(check: dict[str, Any]) -> str:
    actual = "no data" if check["actual"] is None else f"{check['actual']:.4g}"
    return f"{check['name']}: {actual} (limit {check['limit']:g})"


class ResultStore:
    def __init__(self, results_dir: str | Path = "results") -> None:
        root = Path(results_dir)
        self.root = root if root.is_absolute() else PROJECT_ROOT / root
        self.runs_dir = self.root / "runs"
        self.suites_dir = self.root / "suites"
        self.reports_dir = self.root / "reports"

    # ------------------------------------------------------------------ write
    def save_run(self, result: TestResult) -> Path:
        return _write_json(self.runs_dir / f"{result.run_id}.json", result.to_dict())

    def save_suite(self, suite: SuiteResult) -> Path:
        return _write_json(self.suites_dir / f"{suite.suite_id}.json", suite.to_dict())

    def report_path(self, result_id: str) -> Path:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        return self.reports_dir / f"{result_id}.html"

    # ------------------------------------------------------------------ read
    def load_run(self, run_id: str) -> dict[str, Any]:
        return _read_json(self.runs_dir / f"{run_id}.json", run_id)

    def load_suite(self, suite_id: str) -> dict[str, Any]:
        return _read_json(self.suites_dir / f"{suite_id}.json", suite_id)

    def list_runs(self, ammeter: str | None = None, limit: int | None = None) -> list[dict]:
        """Short summaries, newest first, optionally only for one ammeter."""
        summaries = []
        for path in sorted(self.runs_dir.glob("*.json"), reverse=True):
            data = json.loads(path.read_text(encoding="utf-8"))
            if ammeter and data["ammeter"] != ammeter:
                continue
            stats = data["analysis"]["statistics"] or {}
            summaries.append(
                {
                    "run_id": data["run_id"],
                    "ammeter": data["ammeter"],
                    "created_at": data["created_at"],
                    "count": stats.get("count", 0),
                    "mean": stats.get("mean"),
                    "std_dev": stats.get("std_dev"),
                    "failures": data["analysis"]["consistency"]["failure_rate_percent"],
                    "suite_id": data["metadata"].get("suite_id"),
                    "plan": data["metadata"].get("plan"),
                    "passed": (data.get("verdict") or {}).get("passed"),
                }
            )
            if limit and len(summaries) >= limit:
                break
        return summaries

    def list_suites(self) -> list[str]:
        return [p.stem for p in sorted(self.suites_dir.glob("*.json"), reverse=True)]


def _json_safe(obj: Any) -> Any:
    """NaN / inf are not valid JSON; store them as null."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_json_safe(data), indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)  # atomic: a crash never leaves a half-written result
    return path


def _read_json(path: Path, result_id: str) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ResultNotFoundError(f"No stored result with id {result_id!r}") from exc
