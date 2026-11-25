"""Integration test: Two parallel tables with Supervisor, SQLite persistence and Reconcile."""

import asyncio
from pathlib import Path
import pytest

from src.cli.replay import HarReplayServer
from src.cli.supervisor import Supervisor
from src.config.models import AppConfig, ConnectionConfig, SessionConfig, StorageConfig, TableConfig
from src.session.base import SessionInfo
from src.session.env_provider import EnvSessionProvider
from src.store.db import Database
from src.store.repository import RoundRepository


@pytest.mark.asyncio
async def test_two_tables_parallel_stream_and_sqlite(tmp_path):
    """Phase 2 Acceptance:
    1. Parallel staggered startup of 2 tables.
    2. Concurrent data ingestion into SQLite.
    3. Idempotent storage and window management.
    """
    db_path = tmp_path / "supervisor_test.db"

    # Set up 2 mock servers for the 2 tables
    server1 = HarReplayServer(
        har_path=Path("test/bxgnanhldtnm.top.har"),
        table_id="bc281koreanch281",
        port=0,
        speed=0.0,
        loop_rounds_count=5,
    )
    server2 = HarReplayServer(
        har_path=Path("test/bxgnanhldtnm.top2.har"),
        table_id="spto11bctorobc11",
        port=0,
        speed=0.0,
        loop_rounds_count=5,
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
            stagger_delay_sec=0.1,
            heartbeat_interval_sec=0.2,
            watchdog_timeout_sec=5.0,
        ),
        storage=StorageConfig(database_path=db_path, window_size=50),
    )

    db = Database(db_path)
    supervisor = Supervisor(config, session_provider, db)
    await supervisor.initialize()

    # Start supervisor (runs workers in background)
    await supervisor.start()

    # Allow time for initial snapshots and rounds to be processed
    await asyncio.sleep(1.0)

    # Inspect status summary
    status = supervisor.get_status_summary()
    assert len(status) == 2
    assert all(s["is_running"] for s in status)

    # Verify windows have rounds
    assert len(supervisor.windows["bc281koreanch281"]) > 0
    assert len(supervisor.windows["spto11bctorobc11"]) > 0

    # Stop supervisor
    await supervisor.stop()
    await server1.stop()
    await server2.stop()

    # Verify SQLite database has stored records for both tables
    conn = await db.connect()
    repo = RoundRepository(conn)
    count1 = await repo.get_count("bc281koreanch281")
    count2 = await repo.get_count("spto11bctorobc11")
    assert count1 > 0, "Table 1 must have stored rounds in SQLite"
    assert count2 > 0, "Table 2 must have stored rounds in SQLite"
    await db.close()
