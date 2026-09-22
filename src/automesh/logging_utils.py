"""Logging helpers: one console stream plus a per-run log file."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from typing import Iterator, Optional

LOGGER_NAME = "automesh"

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class _Formatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[37m",
        logging.INFO: "\033[0m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;31m",
    }
    RESET = "\033[0m"

    def __init__(self, colored: bool) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
        self.colored = colored

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not self.colored:
            return text
        return "{0}{1}{2}".format(self.COLORS.get(record.levelno, ""), text, self.RESET)


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_logging(level: str = "info", log_file: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(_LEVELS.get(str(level).lower(), logging.INFO))
    logger.handlers = []
    logger.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(_Formatter(colored=sys.stderr.isatty()))
    logger.addHandler(console)

    if log_file:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
        )
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)
    return logger


@contextlib.contextmanager
def timed(label: str, logger: Optional[logging.Logger] = None) -> Iterator[dict]:
    """Time a block and log how long it took.

    Yields a dict that receives ``duration_s`` when the block exits, so the
    caller can store it in an :class:`~automesh.models.StepRecord`.
    """
    log = logger or get_logger()
    log.info("-> %s", label)
    box = {"duration_s": 0.0}
    started = time.time()
    try:
        yield box
    finally:
        box["duration_s"] = time.time() - started
        log.info("<- %s (%.1f s)", label, box["duration_s"])
