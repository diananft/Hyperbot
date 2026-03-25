"""Structured JSON logging with daily rotation."""

import os
import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Format log records as JSON."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data"):
            log_entry.update(record.extra_data)
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_logging(log_dir: str = "logs", level: str = "INFO"):
    """Set up structured logging with separate files."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    formatter = JSONFormatter()
    log_level = getattr(logging, level.upper(), logging.INFO)

    loggers_config = {
        "hyperbot": "system.log",
        "hyperbot.trades": "trades.log",
        "hyperbot.signals": "signals.log",
        "hyperbot.errors": "errors.log",
    }

    for logger_name, filename in loggers_config.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(log_level)
        logger.propagate = logger_name != "hyperbot"

        handler = logging.handlers.TimedRotatingFileHandler(
            os.path.join(log_dir, filename),
            when="midnight",
            interval=1,
            backupCount=30,
            utc=True,
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Console handler for main logger
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    console.setLevel(log_level)
    logging.getLogger("hyperbot").addHandler(console)

    # Error logger also captures ERROR+ from main
    error_logger = logging.getLogger("hyperbot.errors")
    error_handler = error_logger.handlers[0] if error_logger.handlers else None
    if error_handler:
        error_handler.setLevel(logging.ERROR)
        logging.getLogger("hyperbot").addHandler(error_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(f"hyperbot.{name}" if not name.startswith("hyperbot") else name)


def log_trade(logger: logging.Logger, **kwargs):
    """Log a trade event with structured data."""
    record = logger.makeRecord(
        logger.name, logging.INFO, "", 0,
        f"Trade: {kwargs.get('action', 'unknown')} {kwargs.get('asset', '')}",
        (), None
    )
    record.extra_data = kwargs
    logger.handle(record)


def log_signal(logger: logging.Logger, **kwargs):
    """Log a signal event with structured data."""
    record = logger.makeRecord(
        logger.name, logging.INFO, "", 0,
        f"Signal: {kwargs.get('asset', '')} score={kwargs.get('score', 0):.3f}",
        (), None
    )
    record.extra_data = kwargs
    logger.handle(record)
