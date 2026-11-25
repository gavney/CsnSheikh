"""Unit tests for backoff and connection manager."""

from src.client.connection import BackoffManager


def test_backoff_progression_and_reset():
    backoff = BackoffManager(min_delay=1.0, max_delay=10.0, factor=2.0, jitter=0.0)
    
    assert backoff.consecutive_failures == 0
    d1 = backoff.next_delay()
    assert d1 == 1.0
    assert backoff.consecutive_failures == 1

    d2 = backoff.next_delay()
    assert d2 == 2.0

    d3 = backoff.next_delay()
    assert d3 == 4.0

    d4 = backoff.next_delay()
    assert d4 == 8.0

    d5 = backoff.next_delay()
    assert d5 == 10.0  # capped at max_delay

    backoff.reset()
    assert backoff.consecutive_failures == 0
    assert backoff.next_delay() == 1.0
