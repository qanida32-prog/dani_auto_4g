"""Start and stop the ammeter emulators (the stand-in for the physical devices)."""

from __future__ import annotations

import threading
from typing import Any

from Ammeters.base_ammeter import AmmeterEmulatorBase
from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter

EMULATOR_CLASSES: dict[str, type[AmmeterEmulatorBase]] = {
    "greenlee": GreenleeAmmeter,
    "entes": EntesAmmeter,
    "circutor": CircutorAmmeter,
}
STARTUP_TIMEOUT_SECONDS = 5.0


class EmulatorStartupError(RuntimeError):
    """An emulator could not bind its port or did not start in time."""


def start_emulators(
    config: dict[str, Any], names: list[str] | None = None
) -> dict[str, AmmeterEmulatorBase]:
    """Start the configured emulators in daemon threads and wait until each one listens."""
    host = config["testing"]["connection"]["host"]
    emulators: dict[str, AmmeterEmulatorBase] = {}
    for name, settings in config["ammeters"].items():
        if names is not None and name not in names:
            continue
        if name not in EMULATOR_CLASSES:
            raise EmulatorStartupError(f"No emulator for configured ammeter {name!r}")
        emulator = EMULATOR_CLASSES[name](settings["port"], host=host)
        threading.Thread(target=emulator.start_server, name=f"{name}-emulator", daemon=True).start()
        emulators[name] = emulator

    for name, emulator in emulators.items():
        if not emulator.wait_until_ready(STARTUP_TIMEOUT_SECONDS):
            stop_emulators(emulators)
            raise EmulatorStartupError(f"{name} emulator failed to start: {emulator.startup_error}")
    return emulators


def stop_emulators(emulators: dict[str, AmmeterEmulatorBase]) -> None:
    for emulator in emulators.values():
        emulator.stop()
