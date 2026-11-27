"""Unit tests for secret masking in logs."""

import logging
from src.utils.logging import MaskingFormatter, mask_token


def test_mask_token():
    assert mask_token("uJVEj-i_kZHkarWLtMDhzyprm41LIQ2baS-jmUrx0piT346Pvk4E!746047912-bf1814d9") == "uJVE***"
    assert mask_token("short") == "shor***"
    assert mask_token("") == "<empty>"
    assert mask_token(None) == "<empty>"


def test_masking_formatter():
    formatter = MaskingFormatter("%(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Connecting wss://gs24.pplcru001.net/game?JSESSIONID=uJVEj-i_kZHkarWLtMDhzyprm41LIQ2baS&tableId=bc281 with bot 123456789:ABCDefgh-12345678 and ppToken=super_secret_token",
        args=(),
        exc_info=None
    )
    formatted = formatter.format(record)
    assert "uJVEj-i_kZHkarWLtMDhzyprm41LIQ2baS" not in formatted
    assert "JSESSIONID=uJVE***" in formatted or "JSESSIONID=uJVE" in formatted
    assert "123456789:ABCDefgh-12345678" not in formatted
    assert "123456789:ABCD***" in formatted
    assert "super_secret_token" not in formatted


def test_log_buffer_handler():
    from src.utils.logging import setup_logging, get_logger, get_recent_log_lines, get_full_log_text

    setup_logging(level="DEBUG")
    test_log = get_logger("unit.test.buffer")

    test_log.info("Message Alpha for testing")
    test_log.warning("Warning Beta for testing")
    test_log.error("Error Gamma for testing")

    lines = get_recent_log_lines(limit=10)
    assert any("Message Alpha for testing" in line for line in lines)
    assert any("Warning Beta for testing" in line for line in lines)
    assert any("Error Gamma for testing" in line for line in lines)

    warn_lines = get_recent_log_lines(limit=10, level="WARNING")
    assert any("Warning Beta for testing" in line for line in warn_lines)
    assert any("Error Gamma for testing" in line for line in warn_lines)
    assert not any("Message Alpha for testing" in line for line in warn_lines)

    full = get_full_log_text()
    assert "Message Alpha for testing" in full

