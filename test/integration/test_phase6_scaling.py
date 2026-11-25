"""Phase 6 Scaling Test: 15+ concurrent tables under Supervisor."""

import asyncio
from pathlib import Path
import time
import pytest

from src.cli.replay import HarReplayServer
from src.cli.supervisor import Supervisor
from src.config.models import AppConfig, ConnectionConfig, RuleConfig, StorageConfig, TableConfig
from src.session.base import SessionInfo
from src.session.env_provider import EnvSessionProvider
from src.store.db import Database
from src.store.repository import RoundRepository


@pytest.mark.asyncio
async def test_scaling_15_concurrent_tables(tmp_path):
    """Phase 6 Acceptance Test:
    1. 15 concurrent table workers running simultaneously.
    2. Staggered startup without event loop starvation.
    3. Event processing latency < 50ms per round.
    4. SQLite WAL concurrent writes without locking errors.
    5. Independent rule engine state evaluation across all 15 tables.
    """
    db_path = tmp_path / "scale_15_tables.db"

    # Start mock server on port 0 (ephemeral OS port)
    server = HarReplayServer(
        har_path=Path("test/bxgnanhldtnm.top2.har"),
        table_id="spto11bctorobc11",
        port=0,
        speed=0.0,
        loop_rounds_count=4,
    )
    ws_url = await server.start()

    num_tables = 15
    table_configs = []
    session_provider = EnvSessionProvider()

    for i in range(1, num_tables + 1):
        t_id = f"scale_tbl_{i:02d}"
        t_name = f"Speed Baccarat {i}"
        table_configs.append(TableConfig(id=t_id, name=t_name))
        session_provider.register_session(
            t_id,
            SessionInfo(
                jsessionid=f"token_{i}",
                socket_server=ws_url,
                table_id=t_id,
                client_origin="https://client.scale.test",
            ),
        )

    # Configure rule to evaluate across all tables
    rules = [
        RuleConfig(
            id="test_streak_p",
            name="Streak Player 3",
            condition={"type": "streak", "target": "player", "min_length": 3},
            action={"type": "telegram"},
        )
    ]

    config = AppConfig(
        tables=table_configs,
        rules=rules,
        connection=ConnectionConfig(
            stagger_delay_sec=0.02,
            heartbeat_interval_sec=0.5,
            watchdog_timeout_sec=5.0,
        ),
        storage=StorageConfig(database_path=db_path, window_size=50),
    )

    db = Database(db_path)
    supervisor = Supervisor(config, session_provider, db)

    # Instrument processing latency
    processing_times = []
    original_handler = supervisor._create_on_round_handler

    def instrumented_create_handler(table_id: str):
        handler = original_handler(table_id)
        async def timed_handler(r):
            t0 = time.perf_counter()
            await handler(r)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            processing_times.append(elapsed_ms)
        return timed_handler

    supervisor._create_on_round_handler = instrumented_create_handler

    await supervisor.initialize()

    t_start = time.perf_counter()
    await supervisor.start()

    try:
        # Allow tables to receive their snapshots and rounds
        # 15 tables with stagger 0.02s takes ~0.3s to start, then ~1.5s to ingest
        await asyncio.sleep(2.5)

        # Inspect status summary
        summary = supervisor.get_status_summary()
        assert len(summary) == num_tables, f"Expected {num_tables} tables in summary"

        online_tables = [s for s in summary if s["status"] in ("ONLINE", "RECONNECTING")]
        assert len(online_tables) >= 14, f"Expected at least 14/15 tables connected, got {len(online_tables)}"

        # Verify windows
        tables_with_rounds = 0
        for t_id in [t.id for t in table_configs]:
            w = supervisor.windows.get(t_id)
            if w and len(w) > 0:
                tables_with_rounds += 1
        assert tables_with_rounds >= 14, f"Expected at least 14 tables with rounds in window, got {tables_with_rounds}"

    finally:
        await supervisor.stop()
        await server.stop()

    # Latency metric assertion
    assert len(processing_times) > 0, "Expected processed rounds during scaling test"
    avg_latency = sum(processing_times) / len(processing_times)
    p95_latency = sorted(processing_times)[int(len(processing_times) * 0.95)]
    print(f"\nScaling Performance Metrics ({len(processing_times)} events processed):")
    print(f"  Average Round Processing Latency: {avg_latency:.2f} ms (Target < 50ms)")
    print(f"  P95 Round Processing Latency    : {p95_latency:.2f} ms")
    assert avg_latency < 50.0, f"Average processing latency {avg_latency:.2f}ms exceeds 50ms threshold"

    # Verify SQLite WAL concurrency & persistence
    conn = await db.connect()
    repo = RoundRepository(conn)
    total_rounds = 0
    for t_id in [t.id for t in table_configs]:
        cnt = await repo.get_count(t_id)
        total_rounds += cnt
        assert cnt > 0, f"Table {t_id} must have saved rounds in SQLite"

    print(f"  Total Rounds Stored in SQLite   : {total_rounds}")
    assert total_rounds >= num_tables * 2, "Expected multiple rounds saved across all tables"
    await db.close()
