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

    def __init__(
        self, test_name: str, log_dir: str | Path = DEFAULT_LOG_DIR, add_timestamp: bool = True
    ) -> None:
        self._test_name = test_name
        self.log_file = self._build_log_path(Path(log_dir), add_timestamp)
        self.logger = self._setup_logger()

    def _build_log_path(self, log_dir: Path, add_timestamp: bool) -> Path:
        log_dir.mkdir(parents=True, exist_ok=True)
        if not add_timestamp:  # the name is already unique (e.g. a run id)
            return log_dir / f"{self._test_name}.log"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return log_dir / f"{timestamp}_{self._test_name}.log"

    def close(self) -> None:
        """Release the file handle (Windows cannot delete or move an open log file)."""
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)

    def __enter__(self) -> TestLogger:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

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
        # File only: the per-sample detail would flood the console. The framework logs a
        # one-line summary per run to the console separately.
        logger.propagate = False
        return logger

    def info(self, message: str) -> None:
        self.logger.info(message)

    def error(self, message: str) -> None:
        self.logger.error(message)

    def debug(self, message: str) -> None:
        self.logger.debug(message)

    def warning(self, message: str) -> None:
        self.logger.warning(message)
