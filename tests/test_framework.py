"""End-to-end: the framework and CLI against real emulators on free ports."""

from __future__ import annotations

import copy

import pytest
import yaml

import run_tests
from src.testing.results import ResultStore
from src.testing.sampling import SamplingConfig
from src.testing.test_framework import AmmeterTestFramework, UnknownAmmeterError
from src.utils.config import ConfigError, load_config
from tests.conftest import EMULATORS, get_free_port, start_emulator

FAST = SamplingConfig.from_values(measurements_count=8, sampling_frequency_hz=40)


@pytest.fixture
def lab(tmp_path):
    """All three emulators on free ports, and a config + result store pointing at them."""
    config = copy.deepcopy(load_config())
    config["testing"]["sampling"] = {
        "measurements_count": 8,
        "total_duration_seconds": None,
        "sampling_frequency_hz": 40,
    }
    config["result_management"]["results_dir"] = str(tmp_path / "results")
    emulators = {}
    for name, cls in EMULATORS.items():
        port = get_free_port()
        config["ammeters"][name]["port"] = port
        emulators[name] = start_emulator(cls, port)
    yield config, tmp_path
    for emulator in emulators.values():
        emulator.stop()


def make_framework(config) -> AmmeterTestFramework:
    return AmmeterTestFramework(
        config=config, store=ResultStore(config["result_management"]["results_dir"])
    )


@pytest.mark.parametrize("ammeter", sorted(EMULATORS))
def test_run_test_measures_analyses_and_archives(lab, ammeter):
    config, _ = lab
    fw = make_framework(config)
    result = fw.run_test(ammeter, FAST)

    assert result.passed
    stats = result.analysis["statistics"]
    assert stats["count"] == 8
    assert stats["min"] <= stats["median"] <= stats["max"]
    assert result.analysis["accuracy"]["reference_current"] == pytest.approx(
        config["ammeters"][ammeter]["reference_current_a"]
    )
    assert result.metadata["port"] == config["ammeters"][ammeter]["port"]
    assert fw.store.load_run(result.run_id)["ammeter"] == ammeter
    report = fw.store.report_path(result.run_id).read_text(encoding="utf-8")
    assert "<svg" in report and result.run_id in report


def test_run_suite_compares_all_devices(lab):
    config, _ = lab
    fw = make_framework(config)
    suite, results = fw.run_suite(sampling=FAST)

    assert {r.ammeter for r in results} == set(EMULATORS)
    assert {r.metadata["suite_id"] for r in results} == {suite.suite_id}
    assert suite.comparison["most_reliable"] in EMULATORS
    assert [r["rank"] for r in suite.comparison["ranking"]] == [1, 2, 3]
    assert fw.store.load_suite(suite.suite_id)["run_ids"] == suite.run_ids
    assert fw.compare_runs(suite.run_ids).most_reliable == suite.comparison["most_reliable"]


def test_unknown_ammeter_is_rejected(lab):
    config, _ = lab
    with pytest.raises(UnknownAmmeterError, match="greenlee"):
        make_framework(config).run_test("fluke")


def test_device_down_gives_failed_result_not_crash(lab):
    config, _ = lab
    config["ammeters"]["greenlee"]["port"] = get_free_port()  # nothing listening
    config["testing"]["connection"]["timeout_seconds"] = 0.2
    result = make_framework(config).run_test(
        "greenlee", SamplingConfig.from_values(3, sampling_frequency_hz=20)
    )
    assert not result.passed
    assert result.analysis["statistics"] is None
    assert result.analysis["consistency"]["failure_rate_percent"] == 100
    assert all("AmmeterConnectionError" in s["error"] for s in result.samples)


def test_error_simulation_failures_are_recorded(lab):
    config, _ = lab
    config["error_simulation"] = {"enabled": True, "connection_error_rate": 0.5, "seed": 1}
    result = make_framework(config).run_test(
        "entes", SamplingConfig.from_values(20, sampling_frequency_hz=50)
    )
    failures = result.analysis["consistency"]["failure_rate_percent"]
    assert 0 < failures < 100
    assert not result.passed


def test_missing_config_section_raises_config_error():
    with pytest.raises(ConfigError):
        AmmeterTestFramework(config={"ammeters": {}})


def test_cli_run_list_show_compare(lab, capsys):
    config, tmp_path = lab
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    base = ["-c", str(config_file)]

    assert run_tests.main([*base, "run", "--no-emulators", "-n", "5", "-f", "40"]) == 0
    out = capsys.readouterr().out
    assert "Most reliable:" in out and "Report:" in out

    assert run_tests.main([*base, "list"]) == 0
    listed = capsys.readouterr().out
    run_ids = [line.split()[0] for line in listed.splitlines()[2:]]
    assert len(run_ids) == 3

    assert run_tests.main([*base, "show", run_ids[0]]) == 0
    assert run_ids[0] in capsys.readouterr().out

    assert run_tests.main([*base, "compare", *run_ids]) == 0
    assert "Most reliable:" in capsys.readouterr().out


def test_cli_usage_errors_exit_2(lab, tmp_path):
    config, _ = lab
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    base = ["-c", str(config_file)]
    assert run_tests.main([*base, "show", "no-such-run"]) == 2
    assert run_tests.main([*base, "run", "--no-emulators", "-a", "fluke"]) == 2
    assert run_tests.main([*base, "run", "--no-emulators", "-n", "0", "-f", "5"]) == 2


def test_verdict_is_stored_and_a_failed_limit_fails_the_run(lab):
    config, _ = lab
    config["ammeters"]["greenlee"]["acceptance_criteria"] = {"max_cv_percent": 0.001}
    fw = make_framework(config)
    result = fw.run_test("greenlee", FAST)

    assert not result.passed
    assert any(c.startswith("CV %") for c in result.failed_checks)
    stored = fw.store.load_run(result.run_id)
    assert stored["verdict"]["passed"] is False
    assert stored["metadata"]["acceptance_criteria"]["max_cv_percent"] == 0.001
    assert "badge fail" in fw.store.report_path(result.run_id).read_text(encoding="utf-8")


def test_seeded_faults_differ_between_devices_but_repeat(lab):
    config, _ = lab
    config["error_simulation"] = {"enabled": True, "connection_error_rate": 0.4, "seed": 9}
    fw = make_framework(config)
    sampling = SamplingConfig.from_values(30, sampling_frequency_hz=100)

    def pattern(name):
        return [s["value"] is None for s in fw.run_test(name, sampling).samples]

    assert pattern("entes") == pattern("entes")
    assert pattern("entes") != pattern("circutor")


def test_cli_plan_and_plans_command(lab, capsys):
    config, tmp_path = lab
    config["test_plans"] = {
        "tiny": {
            "description": "two samples, one device",
            "ammeters": ["circutor"],
            "sampling": {"measurements_count": 2, "sampling_frequency_hz": 40},
        },
        "impossible": {
            "ammeters": ["entes"],
            "sampling": {"measurements_count": 4, "sampling_frequency_hz": 40},
            "acceptance_criteria": {"max_cv_percent": 0.0001},
        },
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    base = ["-c", str(config_file)]

    assert run_tests.main([*base, "plans"]) == 0
    assert "two samples, one device" in capsys.readouterr().out

    assert run_tests.main([*base, "run", "--no-emulators", "--plan", "tiny"]) == 0
    out = capsys.readouterr().out
    assert "circutor" in out and "entes" not in out and "VERDICT: PASS" in out

    assert run_tests.main([*base, "run", "--no-emulators", "--plan", "impossible"]) == 1
    out = capsys.readouterr().out
    assert "FAILED CV %" in out and "VERDICT: FAIL" in out

    assert run_tests.main([*base, "run", "--no-emulators", "--plan", "nope"]) == 2
