"""Logging setup shared across AutoBoost.

Mirrors the versioned-folder approach that worked in BoostPY: a per-run log file
plus console output, with debug screenshots saved alongside so a failure log
points at exactly what was on screen.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from . import __version__


def setup_logging(logs_root: str = "logs") -> tuple[logging.Logger, str]:
    """Configure the 'AutoBoost' logger. Returns (logger, run_log_dir).

    Log files and debug screenshots for a run go under logs/<version>/.
    """
    run_dir = os.path.join(logs_root, __version__)
    os.makedirs(run_dir, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(run_dir, f"autoboost_{stamp}.log")

    logger = logging.getLogger("AutoBoost")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger, run_dir
