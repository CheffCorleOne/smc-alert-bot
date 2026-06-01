"""
Structured logging for the SMC trading bot.

Provides colored console output and rotating file logs.
Every decision point in the bot logs through this module.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone


# ── Color codes for console ──────────────────────────────────
class _Colors:
    RESET = "\033[0m"
    GRAY = "\033[90m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"


class ColoredFormatter(logging.Formatter):
    """Console formatter with colors per log level."""

    LEVEL_COLORS = {
        logging.DEBUG: _Colors.GRAY,
        logging.INFO: _Colors.GREEN,
        logging.WARNING: _Colors.YELLOW,
        logging.ERROR: _Colors.RED,
        logging.CRITICAL: _Colors.RED + _Colors.BOLD,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, _Colors.RESET)
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        level = record.levelname[:4]
        name = record.name.split(".")[-1][:12].ljust(12)
        msg = record.getMessage()
        return f"{_Colors.GRAY}{timestamp}{_Colors.RESET} {color}{level:>4}{_Colors.RESET} {_Colors.CYAN}{name}{_Colors.RESET} {msg}"


class FileFormatter(logging.Formatter):
    """Plain text formatter for log files."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return f"{timestamp} | {record.levelname:>8} | {record.name:<25} | {record.getMessage()}"


# ── Singleton-style log directory ────────────────────────────
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_INITIALIZED = False


def _ensure_log_dir() -> None:
    """Create log directory if it doesn't exist."""
    os.makedirs(_LOG_DIR, exist_ok=True)


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    Get or create a named logger. Handlers are attached to the root 'smc' logger.
    """
    global _INITIALIZED

    # The root logger for the whole bot
    root_logger = logging.getLogger("smc")
    
    # Ensure root has handlers
    if not root_logger.handlers:
        root_logger.setLevel(logging.DEBUG)
        
        # Console handler
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(ColoredFormatter())
        root_logger.addHandler(console)

        # File handler
        _ensure_log_dir()
        log_file = os.path.join(_LOG_DIR, "smc_bot.log")
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(FileFormatter())
        root_logger.addHandler(file_handler)

    # Specific named logger (child of 'smc')
    logger = logging.getLogger(f"smc.{name}")
    logger.setLevel(level)
    logger.propagate = True # Propagate to 'smc' root logger

    if not _INITIALIZED:
        root_logger.info("═" * 50)
        root_logger.info("SMC Bot logger initialized")
        root_logger.info(f"Log file: {os.path.join(_LOG_DIR, 'smc_bot.log')}")
        root_logger.info("═" * 50)
        _INITIALIZED = True

    return logger
