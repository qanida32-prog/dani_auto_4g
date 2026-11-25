"""Result archive and error simulation."""

from __future__ import annotations

import json

import pytest

from Ammeters.client import AmmeterConnectionError, AmmeterError, AmmeterProtocolError
from src.testing.error_simulation import ErrorSimulationConfig, with_fault_injection
from src.testing.results import ResultNotFoundError, ResultStore, TestResult, new_id


def make_result(ammeter: str = "greenlee", mean: float | None = 1.0) -> TestResult:
    stats = None if mean is None else {"count": 2, "mean": mean, "std_dev": float("nan")}
    return TestResult(
        run_id=new_id(ammeter),
        ammeter=ammeter,
        created_at="2026-11-24T10:00:00+00:00",
        metadata={"suite_id": None},
        samples=[],
        analysis={"statistics": stats, "consistency": {"failure_rate_percent": 0.0}},
    )


def test_ids_are_unique_and_carry_the_label():
    ids = {new_id("entes") for _ in range(200)}
    assert len(ids) == 200
    assert all("-entes-" in i for i in ids)


def test_save_and_load_round_trip(tmp_path):
    store = ResultStore(tmp_path)
    result = make_result()
    path = store.save_run(result)
    assert path.parent == tmp_path / "runs"

    loaded = store.load_run(result.run_id)
    assert loaded["run_id"] == result.run_id
    assert loaded["analysis"]["statistics"]["std_dev"] is None  # NaN stored as JSON null
    json.loads(path.read_text(encoding="utf-8"))  # strict JSON, no NaN tokens


def test_list_runs_filters_by_ammeter(tmp_path):
    store = ResultStore(tmp_path)
    for name in ("greenlee", "entes", "greenlee"):
        store.save_run(make_result(name))
    assert len(store.list_runs()) == 3
    assert {r["ammeter"] for r in store.list_runs("greenlee")} == {"greenlee"}
    assert len(store.list_runs(limit=1)) == 1


def test_missing_run_raises(tmp_path):
    with pytest.raises(ResultNotFoundError):
        ResultStore(tmp_path).load_run("does-not-exist")


def test_list_runs_on_empty_store(tmp_path):
    assert ResultStore(tmp_path / "nothing-here").list_runs() == []


def test_passed_flag():
    assert make_result().passed
    assert not make_result(mean=None).passed


# ---------------------------------------------------------------------- error simulation
def test_disabled_simulation_returns_original_function():
    def measure() -> float:
        return 1.0

    assert with_fault_injection(measure, ErrorSimulationConfig()) is measure


@pytest.mark.parametrize(
    "rates",
    [
        {"connection_error_rate": -0.1},
        {"spike_rate": 1.5},
        {"connection_error_rate": 0.6, "protocol_error_rate": 0.6},
    ],
)
def test_invalid_rates_rejected(rates):
    with pytest.raises(ValueError):
        ErrorSimulationConfig(enabled=True, **rates)


def test_injected_faults_match_configured_rates():
    cfg = ErrorSimulationConfig(
        enabled=True,
        connection_error_rate=0.1,
        protocol_error_rate=0.1,
        spike_rate=0.1,
        spike_factor=100,
        seed=42,
    )
    faulty = with_fault_injection(lambda: 1.0, cfg)
    outcomes = {"conn": 0, "proto": 0, "spike": 0, "ok": 0}
    for _ in range(5000):
        try:
            outcomes["spike" if faulty() == 100 else "ok"] += 1
        except AmmeterConnectionError:
            outcomes["conn"] += 1
        except AmmeterProtocolError:
            outcomes["proto"] += 1
    for key in ("conn", "proto", "spike"):
        assert 400 < outcomes[key] < 600, outcomes
    assert 3300 < outcomes["ok"] < 3700


def test_seed_makes_faults_reproducible():
    cfg = ErrorSimulationConfig(enabled=True, connection_error_rate=0.5, seed=7)

    def pattern() -> list[bool]:
        faulty = with_fault_injection(lambda: 1.0, cfg)
        out = []
        for _ in range(50):
            try:
                faulty()
                out.append(True)
            except AmmeterError:
                out.append(False)
        return out

    assert pattern() == pattern()


def test_from_config_ignores_unknown_keys():
    cfg = ErrorSimulationConfig.from_config({"enabled": True, "spike_rate": 0.2, "extra": 1})
    assert cfg.enabled and cfg.spike_rate == 0.2
