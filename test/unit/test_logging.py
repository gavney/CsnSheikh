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
