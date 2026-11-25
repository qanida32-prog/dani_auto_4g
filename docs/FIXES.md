# Fixes to the Provided Code

The assignment says to use the existing emulator code and to fix and explain any errors in it.
This file lists every change made to the code we received. Each entry gives the problem, its
effect, and the fix.

## Making `main.py` work

| # | File | Problem | Effect | Fix |
|---|------|---------|--------|-----|
| 1 | `main.py` | Emulators started on ports 5001/5002/5003. README documents 5000/5001/5002. | Every request went to the wrong device (port 5001 was Greenlee instead of ENTES). | Ports, hosts and commands are read from `config/config.yaml`. Nothing is hard-coded. |
| 2 | `main.py` | Requests sent `MEASURE_GREENLEE` with no `-get_measurement` (the same for the others). | The server ignored the command and closed the socket, so the client printed `No data received.` | The full documented command is sent, taken from config. |
| 3 | `Circutor_Ammeter.py` | The emulator expected `MEASURE_CIRCUTOR -get_measurement -current`. README documents `MEASURE_CIRCUTOR -get_measurement`. | A client that follows the documentation never got a reading. | The emulator was changed to match README, because README is the documented protocol. The alternative was to change the client to match the emulator. We chose to keep one source of truth. |
| 4 | `main.py` | `time.sleep(5)` was used to wait for startup. | The script was slow and still had a race condition. | Each emulator sets a `threading.Event` once it is listening. `main.py` waits on that event, up to 5 seconds. |
| 5 | `base_ammeter.py` | No `SO_REUSEADDR`. The comment in `main.py` hinted at problems restarting the servers. | On Linux/macOS, running the script again quickly failed with `Address already in use` (TIME_WAIT). | `SO_REUSEADDR` is set on POSIX only. On Windows that option lets another process take over the port, and TIME_WAIT does not block rebinding there anyway. |

## Robustness / error handling

| # | File | Problem | Fix |
|---|------|---------|-----|
| 6 | `base_ammeter.py` | An unknown command closed the connection with no reply. | The server replies `ERROR: unknown command`, so the client can tell "wrong command" apart from "device down". |
| 7 | `base_ammeter.py` | Any socket error on one connection crashed the server thread. | Errors are handled per connection and logged. The server keeps serving. |
| 8 | `base_ammeter.py` | The server could not be stopped, and a bind failure was silent. | Added `stop()`, which polls `accept()` with a timeout, and `wait_until_ready()`. A bind error is stored in `startup_error`. Tests need both. |
| 9 | `base_ammeter.py` | The command was compared byte-for-byte. | Leading and trailing whitespace is stripped before comparing, so `\r\n` and `\n` are accepted. |
| 10 | `base_ammeter.py` | Each instance ran `random.seed(time.time())`, re-seeding the global RNG. | Removed. Python seeds itself from OS entropy, and re-seeding a global from several threads is an anti-pattern. |
| 11 | `client.py` | Results were only printed (the function returned `None`), with no timeout, a single `recv(1024)` and no error handling. | The function returns a `float`, has a configurable timeout and reads until EOF. Failures raise typed exceptions: `AmmeterConnectionError` and `AmmeterProtocolError` (empty, `ERROR`, non-numeric or NaN/inf replies). |
| 12 | emulators | Raw `print` calls. | Replaced with the `logging` module (`emulator.<Class>` loggers). |

## Framework skeleton

| # | File | Problem | Fix |
|---|------|---------|-----|
| 13 | `src/testing/test_framework.py` | `Dict` used without an import, which raised `NameError` on import. It also used a fragile relative import. | Uses built-in generics and an absolute import. |
| 14 | `src/utils/logger.py` | No handler and no level, and the computed `log_file` was never used. | A file handler plus a level are set, duplicate handlers are guarded against, and `setup_logging()` was added for console output. |
| 15 | `src/utils/config.py` | Path was relative to the CWD, with no error handling. | Path is resolved from the project root. Raises `ConfigError` for a missing file or invalid YAML. |
| 16 | `config/config.yaml` | Sampling values were `NULL` and the ammeters section was commented out. | Filled in with the documented protocol and sensible defaults. |
| 17 | `examples/run_tests.py` | Called `run_test()` without the required argument. README says "don't use it". | Removed, and replaced by the real CLI and tests. |
| 18 | `README.md` | Wrong paths: `main.py` is not under `Ammeters/`, `AmmeterTester.py` does not exist, and the file is `run_tests.py`, not `run_test.py`. | README rewritten. |
| 19 | project | No `__init__.py`. `__pycache__/` and `.idea/` were in the tree. | Added package markers and a `.gitignore`. |
| 20 | `requirements.txt` | Listed numpy, scipy, seaborn and pandas, but none of them was used. The spec says to minimize dependencies. | Runtime needs only `pyyaml`. Dev tools are in `requirements-dev.txt`. |

## Found while building phase 2

| # | File | Problem | Effect | Fix |
|---|------|---------|--------|-----|
| 21 | `config/config.yaml`, `client.py`, `base_ammeter.py` | The host was `localhost`. The emulators listen on IPv4 only. On Windows, `socket.create_connection("localhost")` tries IPv6 `::1` first, and Windows retries a refused connection for ~2 s before falling back to `127.0.0.1`. | Every request took ~2.0 s (measured: 2.03 s vs 0.017 s), so `main.py` took 6 s and sampling above ~0.5 Hz was impossible. | The host is `127.0.0.1` in config and in the client/emulator defaults. |
| 22 | `config/config.yaml` | `analysis.statistical_metrics` and `analysis.visualization.plot_types` were in the provided config, but no code read them. | Changing them had no effect, with no warning. | Both now control the console table and the HTML report, and unknown names are rejected. |
| 23 | `src/utils/logger.py` | `TestLogger` (a per-test log file) was provided but never used. Its logger also propagated to the console. | No per-run log existed. | Every run writes `results/logs/<run_id>.log`. Added `close()`, so the file is released (Windows locks open files), and the logger no longer floods the console. |
| 24 | project | `requires-python >= 3.10` was declared, but running on 3.8 failed deep inside the code with `TypeError: zip() takes no keyword arguments`. | A confusing first experience on machines whose default `python` is old. | `src/__init__.py` checks the version first and explains how to create the venv with a newer Python. |

## Kept as-is on purpose

- **Module names** `Circutor_Ammeter.py` and the others are not PEP 8, and "Circutor" is spelled as the
  vendor spells it. They were kept so the provided import paths still work.
- **The emulators' physics and value ranges** were not changed.

## Libraries installed

| Library | Why |
|---------|-----|
| `pyyaml` | Reads the config file (runtime) |
| `pytest` | Test runner (dev) |
| `ruff` | Linting and formatting (dev) |
