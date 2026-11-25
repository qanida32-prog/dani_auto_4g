"""Named test plans (bonus: configuration-driven testing).

A plan is a reusable test scenario defined in config under ``test_plans``. Every key is
optional; whatever a plan does not set comes from the rest of the config::

    test_plans:
      stress:
        description: "High rate with injected faults"
        ammeters: [greenlee, entes]          # default: every configured ammeter
        sampling: {measurements_count: 200, sampling_frequency_hz: 40}
        error_simulation: {enabled: true}    # merged over the global error_simulation
        acceptance_criteria: {max_failure_rate_percent: 20}  # merged over the other layers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.utils.config import ConfigError

PLAN_KEYS = {"description", "ammeters", "sampling", "error_simulation", "acceptance_criteria"}


class UnknownPlanError(ValueError):
    """The requested test plan is not in the configuration."""


@dataclass(frozen=True)
class TestPlan:
    __test__ = False  # not a pytest test class

    name: str
    description: str = ""
    ammeters: list[str] | None = None
    sampling: dict[str, Any] | None = None
    error_simulation: dict[str, Any] = field(default_factory=dict)
    acceptance_criteria: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, config: dict[str, Any], name: str) -> TestPlan:
        plans = config.get("test_plans") or {}
        if name not in plans:
            known = ", ".join(sorted(plans)) or "none configured"
            raise UnknownPlanError(f"Unknown test plan {name!r}; available: {known}")
        section = plans[name] or {}
        unknown = set(section) - PLAN_KEYS
        if unknown:
            raise ConfigError(
                f"Test plan {name!r}: unknown keys {sorted(unknown)}; allowed: {sorted(PLAN_KEYS)}"
            )
        ammeters = section.get("ammeters")
        if ammeters is not None:
            missing = [a for a in ammeters if a not in config.get("ammeters", {})]
            if missing:
                raise ConfigError(f"Test plan {name!r} uses unknown ammeters: {missing}")
        return cls(
            name=name,
            description=section.get("description", ""),
            ammeters=list(ammeters) if ammeters else None,
            sampling=section.get("sampling"),
            error_simulation=section.get("error_simulation") or {},
            acceptance_criteria=section.get("acceptance_criteria") or {},
        )


def list_plans(config: dict[str, Any]) -> list[TestPlan]:
    return [TestPlan.load(config, name) for name in (config.get("test_plans") or {})]
