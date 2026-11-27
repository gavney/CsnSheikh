"""Structured and secure logging module with automatic secret masking, file rotation and memory ring buffer."""

from collections import deque
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys
import threading
from typing import List, Optional


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


class LogEntry:
    """In-memory representation of a formatted log message."""

    def __init__(self, record: logging.LogRecord, formatted: str) -> None:
        self.timestamp = record.created
        self.level = record.levelname
        self.name = record.name
        self.message = formatted


class LogBufferHandler(logging.Handler):
    """Thread-safe circular log buffer retaining the latest N messages in memory."""

    def __init__(self, capacity: int = 1500) -> None:
        super().__init__()
        self.capacity = capacity
        self.buffer: deque[LogEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            entry = LogEntry(record, formatted)
            with self._lock:
                self.buffer.append(entry)
        except Exception:
            self.handleError(record)

    def get_lines(self, limit: int = 50, level: Optional[str] = None) -> List[str]:
        with self._lock:
            items = list(self.buffer)
        if level:
            lvl_upper = level.upper()
            if lvl_upper in ("WARNING", "WARN"):
                items = [it for it in items if it.level in ("WARNING", "ERROR", "CRITICAL")]
            elif lvl_upper == "ERROR":
                items = [it for it in items if it.level in ("ERROR", "CRITICAL")]
            else:
                items = [it for it in items if it.level == lvl_upper]
        if limit > 0:
            items = items[-limit:]
        return [it.message for it in items]

    def get_raw_text(self) -> str:
        with self._lock:
            return "\n".join(it.message for it in self.buffer)


_global_buffer_handler: Optional[LogBufferHandler] = None
_buffer_lock = threading.Lock()


def get_buffer_handler() -> LogBufferHandler:
    """Retrieve or initialize the global in-memory log buffer handler."""
    global _global_buffer_handler
    with _buffer_lock:
        if _global_buffer_handler is None:
            _global_buffer_handler = LogBufferHandler(capacity=1500)
            date_format = "%Y-%m-%d %H:%M:%S"
            log_format = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
            formatter = MaskingFormatter(log_format, datefmt=date_format)
            _global_buffer_handler.setFormatter(formatter)
        return _global_buffer_handler


def get_recent_log_lines(limit: int = 50, level: Optional[str] = None) -> List[str]:
    """Retrieve the most recent log lines from the in-memory circular buffer."""
    buf = get_buffer_handler()
    return buf.get_lines(limit=limit, level=level)


def get_full_log_text() -> str:
    """Retrieve the entire in-memory log buffer as a single string."""
    buf = get_buffer_handler()
    return buf.get_raw_text()


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = Path("logs/casino.log"),
    logger_name: Optional[str] = None,
) -> logging.Logger:
    """Configure secure logging with secret masking, rotating file and in-memory buffer."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric_level)

    date_format = "%Y-%m-%d %H:%M:%S"
    log_format = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
    formatter = MaskingFormatter(log_format, datefmt=date_format)

    # Ensure in-memory buffer handler is registered
    buf_handler = get_buffer_handler()
    buf_handler.setLevel(numeric_level)
    if buf_handler not in logger.handlers:
        logger.addHandler(buf_handler)

    # Avoid adding duplicate console/file handlers if already configured
    has_console = any(isinstance(h, logging.StreamHandler) and not isinstance(h, (RotatingFileHandler, logging.FileHandler, LogBufferHandler)) for h in logger.handlers)
    if not has_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    has_file = any(isinstance(h, (RotatingFileHandler, logging.FileHandler)) for h in logger.handlers)
    if log_file and not has_file:
        try:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=3,
                encoding="utf-8"
            )
            file_handler.setLevel(numeric_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            sys.stderr.write(f"Warning: could not initialize log file at {log_file}: {e}\n")

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger."""
    return logging.getLogger(name)
