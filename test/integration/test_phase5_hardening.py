"""Integration tests for Phase 5: Hardening, Watchdogs, Heartbeats, and Recovery."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from src.cli.replay import HarReplayServer
from src.cli.supervisor import Supervisor
from src.config.models import AppConfig, ConnectionConfig, StorageConfig, TableConfig
from src.notifier.base import Priority
from src.session.base import SessionInfo
from src.session.env_provider import EnvSessionProvider
from src.store.db import Database
from src.store.repository import RoundRepository


@pytest.mark.asyncio
async def test_system_heartbeat_dispatch(tmp_path):
    """Verify that Supervisor periodically dispatches system heartbeat alerts."""
    db_path = tmp_path / "heartbeat_test.db"
    config = AppConfig(
        tables=[TableConfig(id="t1", name="Test Table 1")],
        connection=ConnectionConfig(
            system_heartbeat_interval_sec=0.2,
            stagger_delay_sec=0.01,
        ),
        storage=StorageConfig(database_path=db_path),
    )

    db = Database(db_path)
    session_provider = EnvSessionProvider()
    supervisor = Supervisor(config, session_provider, db)
    await supervisor.initialize()

    heartbeats = []
    original_dispatch = supervisor.notifier.dispatch_system_alert

    async def record_alert(level, text, priority=Priority.NORMAL):
        heartbeats.append((level, text))
        return await original_dispatch(level, text, priority)

    supervisor.notifier.dispatch_system_alert = record_alert

    await supervisor.start()
    try:
        # Wait enough for at least 2 heartbeat ticks
        await asyncio.sleep(0.6)
    finally:
        await supervisor.stop()

    hb_events = [h for h in heartbeats if h[0] == "HEARTBEAT"]
    assert len(hb_events) >= 1, "Expected at least 1 heartbeat event dispatched"
    assert "Сервис активен" in hb_events[0][1]
    assert "Столы: " in hb_events[0][1]


@pytest.mark.asyncio
async def test_table_staleness_watchdog(tmp_path):
    """Verify that Supervisor triggers staleness warning when a connected table stops sending rounds."""
    db_path = tmp_path / "staleness_test.db"
    config = AppConfig(
        tables=[TableConfig(id="t_stale", name="Stale Speed Baccarat")],
        connection=ConnectionConfig(
            round_staleness_threshold_sec=0.3,
            stagger_delay_sec=0.01,
        ),
        storage=StorageConfig(database_path=db_path),
    )

    db = Database(db_path)
    session_provider = EnvSessionProvider()
    supervisor = Supervisor(config, session_provider, db)
    await supervisor.initialize()

    # Fake client as connected but with no recent rounds
    client = supervisor.clients["t_stale"]
    client._running = True
    # Mock ws as not closed
    client._ws = AsyncMock()
    client._ws.state.name = "OPEN"

    alerts = []
    async def record_alert(level, text, priority=Priority.NORMAL):
        alerts.append((level, text))
        return True

    supervisor.notifier.dispatch_system_alert = record_alert

    await supervisor.start()
    try:
        # Wait for watchdog to trigger (threshold is 0.3s)
        await asyncio.sleep(0.7)
    finally:
        await supervisor.stop()

    warning_alerts = [a for a in alerts if a[0] == "WARNING"]
    assert len(warning_alerts) >= 1, "Expected at least 1 staleness warning alert"
    assert "Задержка раундов на столе 'Stale Speed Baccarat'" in warning_alerts[0][1]
    assert "t_stale" in supervisor._stale_alerted_tables


@pytest.mark.asyncio
async def test_long_replay_hardening_and_recovery(tmp_path):
    """Stress test: replay multiple rounds through Supervisor, verify storage idempotency and status table."""
    db_path = tmp_path / "long_replay.db"

    server1 = HarReplayServer(
        har_path=Path("test/bxgnanhldtnm.top.har"),
        table_id="bc281koreanch281",
        port=0,
        speed=0.0,
        loop_rounds_count=12,
    )
    server2 = HarReplayServer(
        har_path=Path("test/bxgnanhldtnm.top2.har"),
        table_id="spto11bctorobc11",
        port=0,
        speed=0.0,
        loop_rounds_count=12,
    )
    ws1_url = await server1.start()
    ws2_url = await server2.start()

    session_provider = EnvSessionProvider()
    session_provider.register_session(
        "bc281koreanch281",
        SessionInfo("tok1", ws1_url, "bc281koreanch281", "https://client.test"),
    )
    session_provider.register_session(
        "spto11bctorobc11",
        SessionInfo("tok2", ws2_url, "spto11bctorobc11", "https://client.test"),
    )

    config = AppConfig(
        tables=[
            TableConfig(id="bc281koreanch281", name="Korean Speed Baccarat 1"),
            TableConfig(id="spto11bctorobc11", name="Vietnamese Speed Baccarat 1"),
        ],
        connection=ConnectionConfig(
            stagger_delay_sec=0.05,
            heartbeat_interval_sec=0.2,
            watchdog_timeout_sec=5.0,
        ),
        storage=StorageConfig(database_path=db_path, window_size=50),
    )

    db = Database(db_path)
    supervisor = Supervisor(config, session_provider, db)
    await supervisor.initialize()

    await supervisor.start()
    try:
        # Wait for replay stream processing
        await asyncio.sleep(2.0)

        # Inspect status summary
        summary = supervisor.get_status_summary()
        assert len(summary) == 2

        # Check table formatting
        table_output = supervisor.format_status_table()
        assert "TABLE ID" in table_output
        assert "bc281koreanch281" in table_output
        assert "spto11bctorobc11" in table_output

        # Verify windows contain rounds
        w1 = supervisor.windows["bc281koreanch281"]
        w2 = supervisor.windows["spto11bctorobc11"]
        assert len(w1) > 0
        assert len(w2) > 0

    finally:
        await supervisor.stop()
        await server1.stop()
        await server2.stop()

    # Validate database persistence
    conn = await db.connect()
    repo = RoundRepository(conn)
    c1 = await repo.get_count("bc281koreanch281")
    c2 = await repo.get_count("spto11bctorobc11")
    assert c1 > 0, "Table 1 must have stored rounds"
    assert c2 > 0, "Table 2 must have stored rounds"

    # Verify idempotency by inserting recent rounds again
    rounds1 = await repo.get_recent_rounds("bc281koreanch281", limit=10)
    for r in rounds1:
        # save_round is INSERT OR IGNORE - should not raise and count stays identical
        inserted = await repo.save_round(r)
        assert not inserted, "Duplicate round should be ignored"

    c1_after = await repo.get_count("bc281koreanch281")
    assert c1 == c1_after, "Count should remain identical after duplicate insertions"

    await db.close()


@pytest.mark.asyncio
async def test_startup_and_token_expired_alert(tmp_path):
    """Verify that Supervisor dispatches startup alert and token expired lifetime alert."""
    db_path = tmp_path / "auth_alert_test.db"
    config = AppConfig(
        tables=[TableConfig(id="tbl_live", name="Live Speed Baccarat")],
        connection=ConnectionConfig(stagger_delay_sec=0.01),
        storage=StorageConfig(database_path=db_path),
    )

    db = Database(db_path)
    session_provider = EnvSessionProvider()
    supervisor = Supervisor(config, session_provider, db)
    await supervisor.initialize()

    captured_alerts = []

    async def record_alert(level, text, priority=Priority.NORMAL):
        captured_alerts.append((level, text, priority))
        return True

    supervisor.notifier.dispatch_system_alert = record_alert

    await supervisor.start()
    try:
        # 1. Verify startup alert
        startups = [a for a in captured_alerts if a[0] == "STARTUP"]
        assert len(startups) == 1, "Expected 1 STARTUP notification on supervisor start"
        assert "Live Speed Baccarat" in startups[0][1]
        assert "Время запуска" in startups[0][1]

        # 2. Simulate token expiration with 1 hour 26 mins 30 secs lifetime
        client = supervisor.clients["tbl_live"]
        assert client.on_auth_error is not None
        await client.on_auth_error("tbl_live", "<session>offline</session>", 5190.0)

        auth_alerts = [a for a in captured_alerts if a[0] == "AUTH_EXPIRED"]
        assert len(auth_alerts) == 1, "Expected 1 AUTH_EXPIRED alert"
        assert auth_alerts[0][2] == Priority.URGENT, "Auth expired alert must be URGENT"
        assert "1 ч 26 мин 30 сек" in auth_alerts[0][1]
        assert "86.5 мин" in auth_alerts[0][1]
        assert "<session>offline</session>" in auth_alerts[0][1]

        # 3. Verify deduplication suppresses duplicate alert within 60s
        await client.on_auth_error("tbl_live", "<session>offline</session>", 5191.0)
        auth_alerts_after = [a for a in captured_alerts if a[0] == "AUTH_EXPIRED"]
        assert len(auth_alerts_after) == 1, "Duplicate auth error alert within 60s must be suppressed"

    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_proactive_session_refresh_loop(tmp_path):
    """Verify that Supervisor periodically triggers proactive session refresh and notifies admin."""
    from unittest.mock import AsyncMock
    from src.config.models import SessionConfig

    db_path = tmp_path / "proactive_test.db"
    config = AppConfig(
        tables=[TableConfig(id="t_proactive", name="Proactive Table")],
        connection=ConnectionConfig(stagger_delay_sec=0.01),
        session=SessionConfig(proactive_refresh_interval_sec=0.2),
        storage=StorageConfig(database_path=db_path),
    )

    db = Database(db_path)
    session_provider = EnvSessionProvider()
    session_provider.get_session = AsyncMock(
        return_value=SessionInfo(
            jsessionid="fresh_proactive_token_123",
            socket_server="wss://gs24.test.net",
            table_id="t_proactive",
            client_origin="https://client.test.net",
        )
    )

    supervisor = Supervisor(config, session_provider, db)
    await supervisor.initialize()

    captured_alerts = []

    async def record_alert(level, text, priority=Priority.NORMAL):
        captured_alerts.append((level, text, priority))
        return True

    supervisor.notifier.dispatch_system_alert = record_alert

    await supervisor.start()
    try:
        # Wait enough for proactive refresh to fire (interval is 0.2s)
        await asyncio.sleep(0.5)

        refreshes = [a for a in captured_alerts if a[0] == "SESSION_REFRESH"]
        assert len(refreshes) >= 1, "Expected at least 1 SESSION_REFRESH alert"
        assert "Токен `JSESSIONID` успешно обновлен" in refreshes[0][1]
        assert session_provider.get_session.called
        assert session_provider._default_jsessionid == "fresh_proactive_token_123"

    finally:
        await supervisor.stop()
