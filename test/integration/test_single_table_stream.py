"""Integration test: Single Table Stream verification with HAR replay and watchdog."""

import asyncio
from pathlib import Path
import pytest

from src.cli.replay import HarReplayServer
from src.client.table_client import TableClient
from src.config.models import ConnectionConfig
from src.parser.models import BoardSnapshot, RoundResult, ShoeSummary
from src.session.base import SessionInfo
from src.session.env_provider import EnvSessionProvider


@pytest.mark.asyncio
async def test_single_table_stream_20_rounds_acceptance():
    """Phase 1 Acceptance Criteria:
    1. Continuous stream receives >= 20 rounds.
    2. Received rounds match ShoeSummary totalGames.
    3. Initial snapshot and heartbeat are correctly processed.
    """
    har_path = Path("test/bxgnanhldtnm.top2.har")
    table_id = "spto11bctorobc11"
    rounds_target = 22

    server = HarReplayServer(
        har_path=har_path,
        table_id=table_id,
        port=8777,
        speed=0.0,
        loop_rounds_count=rounds_target,
    )
    ws_url = await server.start()

    session_provider = EnvSessionProvider()
    session_provider.register_session(
        table_id=table_id,
        session=SessionInfo(
            jsessionid="mock_jsessionid_12345",
            socket_server=ws_url,
            table_id=table_id,
            client_origin="https://client.pplcru001.net",
        ),
    )

    received_rounds = []
    received_summaries = []
    snapshot_received = []

    def on_round(r: RoundResult):
        received_rounds.append(r)

    def on_summary(s: ShoeSummary):
        received_summaries.append(s)

    def on_snapshot(sn: BoardSnapshot):
        snapshot_received.append(sn)

    client = TableClient(
        table_id=table_id,
        table_name="Vietnamese Speed Baccarat 1",
        session_provider=session_provider,
        connection_config=ConnectionConfig(heartbeat_interval_sec=0.2, watchdog_timeout_sec=5.0),
        on_round=on_round,
        on_shoe_summary=on_summary,
        on_snapshot=on_snapshot,
    )

    await client.start()

    # Wait for target rounds or timeout
    max_wait = 10.0
    elapsed = 0.0
    while len(received_rounds) < rounds_target and elapsed < max_wait:
        await asyncio.sleep(0.05)
        elapsed += 0.05

    await client.stop()
    await server.stop()
    await session_provider.close()

    # Criteria Verification
    assert len(snapshot_received) >= 1, "Snapshot beadPlate must be received at connection"
    assert snapshot_received[0].total_games == 33, "Initial totalGames should match HAR initial state"

    assert len(received_rounds) >= 20, f"Must receive >= 20 rounds, got {len(received_rounds)}"
    assert len(received_summaries) >= 20, "ShoeSummary events should arrive for each round"

    # Verify matching with ShoeSummary totalGames
    last_summary = received_summaries[-1]
    assert last_summary.player_win_counter + last_summary.banker_win_counter + last_summary.tie_counter == last_summary.total_games


@pytest.mark.asyncio
async def test_watchdog_reconnect_on_silence():
    """Verify that watchdog detects silent connection and triggers reconnect."""
    import websockets
    from websockets.asyncio.server import serve

    connection_count = 0

    async def handler(websocket):
        nonlocal connection_count
        connection_count += 1
        try:
            await websocket.wait_closed()
        except Exception:
            pass

    mock_server = await serve(handler, "127.0.0.1", 8788)

    session_provider = EnvSessionProvider()
    session_provider.register_session(
        table_id="test_silence",
        session=SessionInfo(
            jsessionid="token",
            socket_server="ws://127.0.0.1:8788",
            table_id="test_silence",
            client_origin="https://client.test",
        ),
    )

    # Configure short watchdog timeout (0.15s) and quick reconnect (0.05s)
    config = ConnectionConfig(
        watchdog_timeout_sec=0.15,
        heartbeat_interval_sec=10.0,
        reconnect_min_delay_sec=0.05,
    )

    client = TableClient(
        table_id="test_silence",
        table_name="Silence Test",
        session_provider=session_provider,
        connection_config=config,
    )

    await client.start()
    await asyncio.sleep(0.8)
    await client.stop()
    mock_server.close()
    await mock_server.wait_closed()

    assert connection_count >= 2, f"Expected watchdog to trigger reconnects (got {connection_count} connections)"
