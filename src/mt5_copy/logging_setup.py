from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(app_log_file: Path, log_level: str) -> logging.Logger:
    app_log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("mt5_copy")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    preserved_handlers = [
        handler
        for handler in logger.handlers
        if getattr(handler, "_mt5_copy_preserve", False)
    ]
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        app_log_file,
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    for handler in preserved_handlers:
        logger.addHandler(handler)
    return logger
