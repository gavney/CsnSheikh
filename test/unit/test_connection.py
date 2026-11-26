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


def test_table_client_proxy_configuration():
    from src.client.table_client import TableClient
    from src.config.models import ConnectionConfig
    from src.session.env_provider import EnvSessionProvider

    provider = EnvSessionProvider(jsessionid="tok", socket_server="wss://dummy", client_origin="http://origin")
    
    # 1. Inherit from connection_config
    cfg = ConnectionConfig(proxy="socks5://127.0.0.1:10808")
    client1 = TableClient(
        table_id="tbl1",
        table_name="Table 1",
        session_provider=provider,
        connection_config=cfg,
    )
    assert client1.proxy == "socks5://127.0.0.1:10808"

    # 2. Explicit proxy overrides
    client2 = TableClient(
        table_id="tbl2",
        table_name="Table 2",
        session_provider=provider,
        connection_config=cfg,
        proxy="socks5://explicit:1080",
    )
    assert client2.proxy == "socks5://explicit:1080"
