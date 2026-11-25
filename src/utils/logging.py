"""Structured and secure logging module with automatic secret masking."""

import logging
import re
import sys
from pathlib import Path
from typing import Optional


# Patterns for sensitive data that should never be revealed in logs
SENSITIVE_PATTERNS = [
    # JSESSIONID in query strings or json
    (re.compile(r'(JSESSIONID[=:]\s*["\']?)([^"\'&\s%]{4})[^"\'&\s%]*', re.IGNORECASE), r'\1\2***'),
    # Telegram bot tokens: 123456789:ABCDefgh-1234
    (re.compile(r'(\d{8,12}:[A-Za-z0-9_-]{4})[A-Za-z0-9_-]+'), r'\1***'),
    # ppToken / ppExtraData
    (re.compile(r'(ppToken[=:]\s*["\']?)([^"\'&\s]{4})[^"\'&\s]*', re.IGNORECASE), r'\1\2***'),
    # Authorization / Bearer tokens
    (re.compile(r'(Bearer\s+)([A-Za-z0-9._-]{4})[A-Za-z0-9._-]+', re.IGNORECASE), r'\1\2***'),
    # Generic token= or key= or password=
    (re.compile(r'((?:token|password|secret|key|ssid)[=:]\s*["\']?)([^"\'&\s]{4})[^"\'&\s]*', re.IGNORECASE), r'\1\2***'),
]


def mask_token(val: Optional[str], show_prefix: int = 4) -> str:
    """Mask a sensitive string, leaving only a short prefix visible."""
    if not val:
        return "<empty>"
    if len(val) <= show_prefix:
        return "***"
    return f"{val[:show_prefix]}***"


class MaskingFormatter(logging.Formatter):
    """Logging formatter that automatically masks secrets in output messages."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        masked = original
        for pattern, replacement in SENSITIVE_PATTERNS:
            masked = pattern.sub(replacement, masked)
        return masked


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    logger_name: Optional[str] = None
) -> logging.Logger:
    """Configure secure logging with secret masking.
    
    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional path to append log records.
        logger_name: Root or specific logger name.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric_level)

    # Avoid duplicate handlers if already configured
    if logger.handlers:
        return logger

    date_format = "%Y-%m-%d %H:%M:%S"
    log_format = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
    formatter = MaskingFormatter(log_format, datefmt=date_format)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Optional file handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger."""
    return logging.getLogger(name)
