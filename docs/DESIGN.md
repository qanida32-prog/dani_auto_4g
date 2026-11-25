# Design Decisions

## Overview

```
run_tests.py (CLI)
   └─ AmmeterTestFramework         unified API, reads config (+ optional TestPlan)
        ├─ Sampler                 timing: when to take each measurement
        │    └─ measure()          client.request_current_from_ammeter (+ optional fault injection)
        ├─ analyze()               statistics, timing, consistency, accuracy
        ├─ AcceptanceCriteria      PASS / FAIL against configured limits
        ├─ compare()               ranks several runs
        ├─ ResultStore             JSON archive
        └─ report                  HTML + SVG
```

Each module has one job and is tested on its own. The sampler does not know about TCP, the
analysis does not know about the sampler's clock, and the store does not know about statistics.
So each part can be reused or replaced (for example a serial-port client instead of TCP)
without touching the others.

## 1. Unified measurement API

- **Devices are data, not classes.** The framework only needs `host`, `port` and `command`
  from config. All three ammeters use the same request/response protocol, so a fourth device
  is a config entry. The emulator classes are used only to *start* the emulators, never to
  measure.
- **One result shape for every device.** `TestResult` is the same for every device:
  `run_id`, `ammeter`, `created_at`, `metadata`, `samples`, `analysis`. Reporting, storage
  and comparison work on that shape and never look at the device type.
- **Typed errors.** The client raises `AmmeterConnectionError` (device unreachable or timed
  out) or `AmmeterProtocolError` (device answered with something that is not a measurement).
  This is the difference between "the cable is unplugged" and "the firmware is wrong".

## 2. Sampling

- **Any two of count / duration / frequency** define the third (`count = duration × frequency`).
  Giving all three is allowed if they agree within 1 %. Contradictory or missing values are
  rejected with a clear message before any device is contacted.
- **Drift-free scheduling.** Sample *i* is due at `start + i / frequency`. The sampler sleeps
  until that absolute time instead of sleeping a fixed interval after each request. With a
  fixed sleep, a 17 ms request at 10 Hz would make 100 samples take 11.7 s instead of 10 s.
  With absolute deadlines, latency does not accumulate. A request that overruns its slot
  delays only itself. The next sample goes out immediately and the schedule recovers.
- **Timing is measured, not assumed.** Every sample stores its scheduled time, actual send
  time and round-trip latency. The analysis reports achieved frequency and jitter, so a
  run that could not keep up is visible in the results and not hidden.
- **Failures do not abort a run.** A failed request is stored with its error message and
  counted. A flaky device produces a result that says "8 % failed", not a stack trace.
  Unexpected exceptions (bugs) are *not* caught, so they still fail loudly.
- **Suites sample all devices in parallel** (one thread per device, each on its own port).
  A suite takes as long as one run, and all devices see the same time window. On a real
  bench that matters when they measure the same circuit.
- `perf_counter` (monotonic) is used for timing. `time.time()` is used only for the
  wall-clock timestamp in the metadata. Clock and sleep are injectable, so the timing tests
  use a fake clock and are exact and instant.

## 3. Result analysis

Only the standard library (`statistics`, `math`). No numpy or scipy: the spec says to keep
dependencies minimal, and the data sets are small.

- **Sample standard deviation (n − 1).** The measurements are a sample of the device's
  behaviour, not the whole population.
- **Coefficient of variation (CV = std / mean)** is the main precision metric. The devices
  differ by four orders of magnitude (CIRCUTOR ≈ 0.03 A, ENTES ≈ 69 A), so absolute std
  cannot be compared across them. CV has no unit.
- **95 % confidence interval of the mean** uses Student-t critical values from a small table
  (exact for n ≤ 31, normal approximation above). With 20 samples the normal approximation
  would understate the interval by about 6 %.
- **Outliers**: Tukey fences (Q1 − 1.5·IQR, Q3 + 1.5·IQR). They are robust, unlike a z-score
  that the outlier itself inflates.
- **Drift**: least-squares slope of value against sample index, reported as the total change
  over the run in % of the mean. First-half vs second-half mean gives a simpler check.
- **Statistics ignore failed samples.** Failures are reported separately as a rate.

## 4. Result management

- **One JSON file per run**, plus one per suite. No database: the files are readable by any
  tool, easy to diff, and a results folder can be zipped and attached to a bug report.
- **Run id** = `YYYYMMDD-HHMMSS-<device>-<6 hex>`. It sorts by time, shows the device, and
  the random part keeps two runs in the same second unique.
- **Metadata** stored with each run: host, port, command, timeout, the effective sampling
  config, the error-simulation config, the suite id, the start time, and the environment
  (Python version, OS, hostname). A result can be interpreted months later without the
  config file that produced it.
- **Atomic writes** (write to a temporary file, then rename), so a crash never leaves a
  truncated JSON file.
- `NaN` and `inf` (for example the CI of a single sample) are stored as `null`. They are not
  valid JSON, and other tools would fail to read the file.
- **Retrieval and comparison**: `list` (filtered by device), `show`, and `compare` of any set
  of stored runs. Examples: the same device on two days, or before and after a firmware
  change.

## 5. Accuracy assessment

Precision (repeatability) can be measured from the data alone. Accuracy (closeness to the
true value) needs a reference. On a real bench that is a calibrated meter, and its reading
goes into `reference_current_a`.

The emulators have no physical true current. As the reference we use the **expected value of
each emulator's model**, derived from its uniform input distributions:

| Device | Model | Expected value |
|--------|-------|----------------|
| Greenlee | V/R, V ~ U(1, 10), R ~ U(0.1, 100) | E[V]·E[1/R] = 5.5 · ln(1000)/99.9 ≈ **0.3803 A** |
| ENTES | B·K, B ~ U(0.01, 0.1), K ~ U(500, 2000) | 0.055 · 1250 = **68.75 A** |
| CIRCUTOR | Σ Vᵢ·dt, 10 × V ~ U(0.1, 1), dt ~ U(0.001, 0.01) | 0.0055 · 10 · 0.55 = **0.03025 A** |

(Independent variables, so E[XY] = E[X]·E[Y]. For 1/R: E[1/R] = ln(b/a)/(b − a).)

**Reliability score** = CV % + |relative error %| + 10 × failure rate %. Lower is better.
Precision and accuracy count equally. Failures are weighted heavily because a device that
does not answer is the least reliable of all. The weights are a judgement call, so the
ranking table shows every component and a reader can disagree with the weighting.

**What the sample results show.** CIRCUTOR ranks first. It averages 10 voltage readings per
measurement, and averaging reduces variance. Greenlee ranks last: I = V/R with R down to
0.1 Ω makes the distribution heavy-tailed. The median is about 0.1 A, but a small R
occasionally gives 10 A or more (the maximum in `docs/sample_results/standard/` is 9.5 A,
with a CV of 309 %). Its mean converges slowly, so its CV is very high and its relative
error swings widely from run to run. This is a real
property of the measurement method, not a framework artefact. It is also why the median is
reported next to the mean.

## 6. Error simulation (bonus)

`error_simulation` wraps the measurement function and, at configured rates, raises a
simulated `AmmeterConnectionError` (timeout) or `AmmeterProtocolError` (garbled reply), or
multiplies the value by `spike_factor`. It is off by default. `--simulate-errors` or the
`stress` plan turns it on. With an integer `seed`, the fault pattern is reproducible.

It exercises the paths that clean emulators never reach: failures are recorded and counted,
the run still completes, spikes are flagged as outliers and the reliability score drops.
With the default limits (0 % failures allowed) the run fails with exit code 1. The `stress`
plan allows up to 15 % failures, so there it checks that the framework copes with faults.
See `docs/sample_results/stress/`.

## 7. Acceptance criteria

Statistics say *what* a device did. A QA framework must also say whether that is *good
enough*. Each run is checked against limits (failure rate, achieved sampling rate, CV,
relative error, outliers, drift) and gets a PASS/FAIL verdict. The verdict sets the CLI
exit code, so a CI job can gate on it.

- **Every check is stored with its actual value and limit**, not only the overall verdict.
  A FAIL says why, for example `CV %: 456 (limit 75)`.
- **Three layers, later wins**: global defaults, then the device type (a Rogowski coil and
  a Hall sensor have different realistic precision), then the test plan (a stress test
  tolerates the failures it injects on purpose).
- **"No data" fails.** A limit that cannot be evaluated counts as a failure. Examples: an
  accuracy limit for a device without `reference_current_a`, or a CV limit when every
  request failed. A misconfigured check must never pass silently.
- **The defaults are lenient on purpose**: no failed request, and at least 90 % of the
  target rate. The emulators produce random values with very different spreads, so CV and
  accuracy limits are opt-in per plan or device. The `precision` plan shows them in use.
- Unknown keys are rejected at start-up, so a typo such as `max_cv: 5` cannot disable a
  check without anyone noticing.

## 8. Test plans (bonus: configuration-driven testing)

A test plan is a named scenario in config: which devices, how to sample, whether to inject
faults, and which limits apply. `run_tests.py run --plan soak` replaces a list of flags.
The scenario lives in version control, not in someone's shell history, so runs are
repeatable and reviewable. The plan name is stored in every run's metadata, and `list`
shows which scenario produced each result.

Five plans are shipped: `smoke`, `standard`, `precision`, `soak` and `stress`. A test
checks that every plan in the shipped config loads. A broken plan therefore fails the test
suite instead of being found at run time.

With a fixed `seed`, error simulation derives a separate random stream per device
(`"<seed>:<device>"`). Runs are reproducible, but the devices do not fail on the same
samples. Identical failure patterns would be unrealistic and would make every device's
failure rate the same.

## 9. Visualization (bonus)

Charts are inline SVG generated by `report.py`, in one self-contained HTML file per run or
suite. It needs no matplotlib, no JavaScript and no network, so a report opens in any
browser and can be attached to a ticket.

- Time series: the mean line, outliers highlighted, failed samples shown as dashed vertical
  lines. Hover shows exact values.
- Histogram (Sturges' rule for the bin count) and box plot.
- Suite: CV bar chart, and box plots **normalised to each device's mean**, so devices on
  different scales can be compared on one axis.

## 10. Testing strategy

| File | Covers |
|------|--------|
| `test_emulators_smoke.py` | the phase-1 fixes: protocol, error replies, restart, port conflict |
| `test_sampling.py` | config derivation and validation, exact schedule, no drift under latency, overrun recovery, failure recording (fake clock), plus one real-clock check |
| `test_analysis.py` | statistics against hand-computed values, outliers, drift, accuracy, ranking, chart axis ticks |
| `test_results_and_errors.py` | archive round trip, unique ids, filtering, strict JSON, fault-injection rates and reproducibility |
| `test_acceptance_and_plans.py` | every acceptance limit, layer merging, invalid criteria, every shipped plan loads, plan overrides |
| `test_framework.py` | end to end against real emulators on free ports: every device, suites, a device that is down, error simulation, verdicts, every CLI command and its exit codes |

Tests bind emulators to OS-assigned free ports and write results to `tmp_path`, so they can
run while `main.py` is running and never touch `results/`. The 99 tests run in about 8 s.

## Limitations and possible extensions

- The reliability score weights are fixed in code (`FAILURE_WEIGHT`). They could move into
  config, like the acceptance limits.
- Tested on Windows only. The code avoids platform-specific APIs and handles the one known
  difference (`SO_REUSEADDR`), but it has not been run on Linux or macOS.
- `run_tests.py` starts the emulators in-process. Against real devices use `--no-emulators`.
- Requests are sequential per device, so the maximum frequency is about 1 / latency (around
  50–60 Hz here). That is a property of the request/response protocol, and the timing
  statistics report it when it limits a run.
