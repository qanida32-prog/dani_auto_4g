"""Logging setup: console output plus a per-test log file."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DEFAULT_LOG_DIR = Path("results") / "logs"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root console logging once for CLI entry points."""
    logging.basicConfig(level=level, format=LOG_FORMAT)


class TestLogger:
    """Thin wrapper that writes a dedicated log file for a single test run."""

    __test__ = False  # not a pytest test class

    def __init__(self, test_name: str, log_dir: str | Path = DEFAULT_LOG_DIR) -> None:
        self._test_name = test_name
        self.log_file = self._build_log_path(Path(log_dir))
        self.logger = self._setup_logger()

    def _build_log_path(self, log_dir: Path) -> Path:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return log_dir / f"{timestamp}_{self._test_name}.log"

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"test.{self._test_name}")
        logger.setLevel(logging.DEBUG)
        # Avoid duplicate handlers when the same test name is instantiated twice.
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

        file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(file_handler)
        # Console output goes through the root logger configured by setup_logging().
        return logger

    def info(self, message: str) -> None:
        self.logger.info(message)

    def error(self, message: str) -> None:
        self.logger.error(message)

    def debug(self, message: str) -> None:
        self.logger.debug(message)

    def warning(self, message: str) -> None:
        self.logger.warning(message)
