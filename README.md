# Ammeter QA Framework

A testing framework for current-measurement systems. It samples three ammeter emulators
(Greenlee, ENTES and CIRCUTOR) over TCP, analyses the measurements, archives every run, and
produces HTML reports and a cross-device comparison.

- Fixes made to the provided code: [docs/FIXES.md](docs/FIXES.md)
- Design decisions: [docs/DESIGN.md](docs/DESIGN.md)
- Sample results: [docs/sample_results/](docs/sample_results/)
- CI: every push runs lint, the tests, `main.py` and the `smoke` plan on Ubuntu, macOS and
  Windows ([.github/workflows/tests.yml](.github/workflows/tests.yml))

## Quick start

Requires Python 3.10 or newer. On an older Python, the scripts stop with a message that
says so.

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt

python main.py          # start the 3 emulators and read one measurement from each
python run_tests.py run # sample every ammeter, analyse, compare, archive, write a report
pytest                  # run the test suite
```

On Windows, if `python` is an older version, create the venv with `py -3.11 -m venv .venv`.

## Running tests

```sh
python run_tests.py run                               # all ammeters, sampling from config
python run_tests.py run -a greenlee                   # one ammeter
python run_tests.py run -a entes -a circutor          # several
python run_tests.py run -n 50 -f 20                   # 50 measurements at 20 Hz
python run_tests.py run -d 10 -f 5                    # 10 seconds at 5 Hz
python run_tests.py run --simulate-errors             # inject faults (see config)
python run_tests.py run --no-emulators                # devices are already running
python run_tests.py run --plan stress                 # a named test plan (see below)
python run_tests.py plans                             # list the test plans
```

Any two of count (`-n`), duration (`-d`) and frequency (`-f`) define the third. If you give
only one, the other comes from `config/config.yaml`.

Exit code: `0` if every run meets its acceptance criteria, `1` if any run fails them or a
device could not start, `2` for usage errors (unknown ammeter or plan, invalid sampling or
config, unknown run id). A CI job can use the exit code directly.

Each run prints a summary table with a PASS/FAIL result, the reason for every failed
acceptance check, and for a suite the reliability ranking:

```
ammeter   n    mean (A)   median (A)  std (A)  min (A)    max (A)   fail %  result
greenlee  181  0.393775   0.118042    0.8609   0.013593   5.95978   9.5     PASS
entes     181  94.3334    57.9035     168.9    5.84576    1285.7    9.5     PASS
circutor  181  0.0423285  0.0301462   0.0679   0.0053788  0.648203  9.5     PASS

rank  ammeter   CV %   rel. err %  fail %  score
1     circutor  160.4  39.93       9.5     295.3
...
VERDICT: PASS
```

## Test plans

A test plan is a named scenario in `config/config.yaml` under `test_plans`. It can set the
ammeters, the sampling, error simulation and acceptance criteria. Whatever it does not set
comes from the rest of the config.

| Plan | What it does |
|------|--------------|
| `smoke` | 5 samples per device: does every device answer? |
| `standard` | 100 samples at 20 Hz: statistics with a stable mean |
| `precision` | ENTES and CIRCUTOR, 200 samples, with CV and accuracy limits |
| `soak` | 60 s at 5 Hz: drift and intermittent failures |
| `stress` | 200 samples at 40 Hz with injected faults: up to 15 % failures allowed |

A new scenario is a few lines of YAML:

```yaml
test_plans:
  nightly:
    description: "Long run on the Hall-effect meter"
    ammeters: [entes]
    sampling: {total_duration_seconds: 600, sampling_frequency_hz: 2}
    acceptance_criteria: {max_cv_percent: 70, max_drift_percent: 10}
```

## Acceptance criteria (pass / fail)

Every run is checked against limits. A limit set to `null` is not checked.

| Criterion | Fails when |
|-----------|-----------|
| `max_failure_rate_percent` | more requests failed than allowed (default 0) |
| `min_frequency_ratio` | achieved / target sampling rate is too low (default 0.9) |
| `max_cv_percent` | precision is worse than the limit |
| `max_abs_relative_error_percent` | the mean is too far from `reference_current_a` |
| `max_outliers` | too many Tukey outliers |
| `max_drift_percent` | the trend over the run is too large |

A run with no valid measurement always fails. Limits are merged in three layers, later wins:
the global `acceptance_criteria`, then `ammeters.<name>.acceptance_criteria` (what that
device type should achieve), then the plan's `acceptance_criteria` (what the scenario
tolerates). The checks and their actual values are stored in the run's JSON, printed by
`show`, and shown in the HTML report.

## Retrieving and comparing results

```sh
python run_tests.py list                      # archived runs, newest first
python run_tests.py list -a entes -l 5        # filter by ammeter
python run_tests.py show <run_id>             # one run: metadata, statistics, acceptance checks
python run_tests.py compare <run_id> ...      # rank any stored runs (e.g. one device, two days)
```

Results are written under `results/` (git-ignored):

```
results/runs/<run_id>.json        samples, statistics and metadata for one ammeter
results/suites/<suite_id>.json    the runs taken together and their comparison
results/reports/<id>.html         report with charts; open it in any browser
results/logs/<run_id>.log         every request of one run: time, latency, value or error
```

Run ids look like `20261124-123051-entes-2a58be` (timestamp, device, random suffix): they are
unique and sort by time.

## Using it from Python

```python
from src.testing.sampling import SamplingConfig
from src.testing.test_framework import AmmeterTestFramework

fw = AmmeterTestFramework()  # or AmmeterTestFramework("my_config.yaml")
result = fw.run_test("greenlee", SamplingConfig.from_values(100, sampling_frequency_hz=50))
print(result.analysis["statistics"]["mean"])

suite, results = fw.run_suite()  # every ammeter, sampled in parallel
print(suite.comparison["most_reliable"])

fw.apply_plan("stress")  # switch sampling, faults and limits to a test plan
suite, results = fw.run_suite()
print(all(r.passed for r in results), [r.failed_checks for r in results])
```

The emulators must be running: `python run_tests.py run` starts them, and from Python you can
use `src.testing.emulators.start_emulators(fw.config)`.

## What is measured

| Area | Output |
|------|--------|
| Statistics | count, mean, median, sample standard deviation, min, max, range, coefficient of variation, standard error, 95 % confidence interval of the mean |
| Timing | achieved vs target frequency, jitter (mean and max), request latency (mean and max) |
| Consistency | outliers (Tukey 1.5 IQR), drift over the run (robust Theil–Sen slope), first-half vs second-half mean, failure rate |
| Accuracy | bias and relative error against `reference_current_a`, and whether the reference is inside the 95 % CI |
| Comparison | ranking by a reliability score (precision + accuracy + failures), most reliable device |
| Charts | time series (outliers and failures marked), histogram, box plot; for a suite also a CV bar chart and a normalised box plot |

## Emulators

| Device   | Port | Command                             | Measurement method          |
|----------|------|-------------------------------------|-----------------------------|
| Greenlee | 5000 | `MEASURE_GREENLEE -get_measurement` | Ohm's law, I = V / R        |
| ENTES    | 5001 | `MEASURE_ENTES -get_data`           | Hall effect, I = B · K      |
| CIRCUTOR | 5002 | `MEASURE_CIRCUTOR -get_measurement` | Rogowski coil, I = ∫V dt    |

## Configuration

Everything is in `config/config.yaml`:

| Section | Keys |
|---------|------|
| `testing.sampling` | `measurements_count`, `total_duration_seconds`, `sampling_frequency_hz` (any two; `null` = derive) |
| `testing.connection` | `host` (use `127.0.0.1`, see FIXES.md #21), `timeout_seconds` |
| `ammeters.<name>` | `port`, `command`, optional `reference_current_a` and `acceptance_criteria` |
| `analysis` | `statistical_metrics`: the statistics shown in tables and reports (`count`, `mean`, `median`, `std_dev`, `min`, `max`, `range`, `cv`, `sem`, `ci95`) |
| `analysis.visualization` | `enabled`: write HTML reports; `plot_types`: `time_series`, `histogram`, `box_plot` |
| `result_management` | `results_dir` |
| `error_simulation` | `enabled`, `connection_error_rate`, `protocol_error_rate`, `spike_rate`, `spike_factor`, `seed` |
| `acceptance_criteria` | the pass/fail limits above |
| `test_plans.<name>` | `description`, `ammeters`, `sampling`, `error_simulation`, `acceptance_criteria` |

Typos are rejected: an unknown acceptance criterion or plan key stops the run with a clear
error instead of being silently ignored.

To test another device that uses the same request/response protocol, add an entry under
`ammeters`. No code change is needed.

## Project structure

```
Ammeters/                  emulators (provided, fixed) + TCP client
config/config.yaml         configuration
src/testing/
  test_framework.py        unified API: run_test, run_suite, compare_runs, apply_plan
  sampling.py              count / duration / frequency, drift-free scheduler
  analysis.py              statistics, timing, consistency, accuracy, comparison
  acceptance.py            pass/fail limits and verdicts
  plans.py                 named test plans
  results.py               result archive (JSON), unique run ids, metadata
  report.py                HTML reports with SVG charts
  error_simulation.py      fault injection
  emulators.py             start / stop the emulators
src/utils/                 config loader, logger, helpers
tests/                     pytest suite (110 tests)
.github/workflows/         CI: Ubuntu, macOS, Windows
docs/                      assignment, fixes log, design notes, sample results
main.py                    one measurement from each emulator
run_tests.py               command-line interface
```

## Dependencies

| Library | Why |
|---------|-----|
| `pyyaml` | Reads the config file (the only runtime dependency) |
| `pytest` | Test runner (dev) |
| `ruff` | Linting and formatting (dev) |

Statistics, charts and storage use only the Python standard library.
