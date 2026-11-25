"""Start the three ammeter emulators and request one measurement from each.

Usage:
    python main.py
"""

from __future__ import annotations

import logging
import sys

from Ammeters.client import AmmeterError, request_current_from_ammeter
from src.testing.emulators import EmulatorStartupError, start_emulators
from src.utils.config import load_config
from src.utils.logger import setup_logging

logger = logging.getLogger("main")


def main() -> int:
    setup_logging()
    config = load_config()
    connection = config["testing"]["connection"]

    try:
        start_emulators(config)
    except EmulatorStartupError as exc:
        logger.error("%s", exc)
        return 1

    failures = 0
    for name, settings in config["ammeters"].items():
        try:
            current = request_current_from_ammeter(
                settings["port"],
                settings["command"].encode("utf-8"),
                host=connection["host"],
                timeout=connection["timeout_seconds"],
            )
            logger.info("%-9s -> %.6f A", name, current)
        except AmmeterError as exc:
            failures += 1
            logger.error("%-9s -> FAILED: %s", name, exc)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
