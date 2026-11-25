"""Unit tests for Reconciler: gap recovery and shoe reset detection."""

import pytest
from src.parser.models import BeadPlateCell, BoardSnapshot, Outcome, RoundResult, ShoeSummary
from src.state.reconciler import Reconciler
from src.state.window import SlidingWindow
from src.store.db import Database
from src.store.repository import RoundRepository


@pytest.mark.asyncio
async def test_reconciler_cold_startup_populates_window(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    conn = await db.connect()
    repo = RoundRepository(conn)

    window = SlidingWindow(maxlen=50)
    reconciler = Reconciler("t1", window, repo)

    # Simulated beadPlate snapshot with 3 rounds: RN9 (Banker), BB5 (Player), GN6 (Tie)
    cells = [[BeadPlateCell.from_code("RN9"), BeadPlateCell.from_code("BB5"), BeadPlateCell.from_code("GN6")]]
    snapshot = BoardSnapshot(table_id="t1", total_games=3, bead_plate=cells)

    recovered = await reconciler.reconcile_snapshot(snapshot)
    assert len(recovered) == 3
    assert len(window) == 3
    assert [r.result for r in window.get_all()] == [Outcome.BANKER, Outcome.PLAYER, Outcome.TIE]
    assert [r.source for r in window.get_all()] == ["snapshot", "snapshot", "snapshot"]

    # Check saved to DB
    assert await repo.get_count("t1") == 3
    await db.close()


@pytest.mark.asyncio
async def test_reconciler_gap_recovery_on_reconnect(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    conn = await db.connect()
    repo = RoundRepository(conn)

    window = SlidingWindow(maxlen=50)
    reconciler = Reconciler("t1", window, repo)

    # Window already has 2 rounds from live stream
    r1 = RoundResult(round_id="r1", table_id="t1", result=Outcome.BANKER, score=9)
    r2 = RoundResult(round_id="r2", table_id="t1", result=Outcome.PLAYER, score=5)
    window.append(r1)
    window.append(r2)
    await repo.save_round(r1)
    await repo.save_round(r2)

    # Disconnect happened. Reconnect snapshot arrives with 4 total rounds in beadPlate
    cells = [
        [
            BeadPlateCell.from_code("RN9"),
            BeadPlateCell.from_code("BB5"),
            BeadPlateCell.from_code("RN8"),  # Missed round 3
            BeadPlateCell.from_code("BN7"),  # Missed round 4
        ]
    ]
    snapshot = BoardSnapshot(table_id="t1", total_games=4, bead_plate=cells)

    # Reconciler should detect gap of 2 (4 - 2) and synthesize the last 2 rounds
    recovered = await reconciler.reconcile_snapshot(snapshot)
    assert len(recovered) == 2
    assert len(window) == 4
    assert window.get_all()[2].result == Outcome.BANKER
    assert window.get_all()[3].result == Outcome.PLAYER

    # Total in DB must be exactly 4 (2 original + 2 synthesized, no duplicates)
    assert await repo.get_count("t1") == 4
    await db.close()


@pytest.mark.asyncio
async def test_reconciler_shoe_reset_clears_window():
    window = SlidingWindow(maxlen=50)
    reconciler = Reconciler("t1", window)

    # Total games was 50
    await reconciler.reconcile_shoe_summary(ShoeSummary(total_games=50, player_win_counter=25, banker_win_counter=20, tie_counter=5, table="t1"))
    window.append(RoundResult(round_id="r_50", table_id="t1", result=Outcome.PLAYER, score=7))
    assert len(window) == 1

    # New shoe arrives: totalGames resets to 1
    await reconciler.reconcile_shoe_summary(ShoeSummary(total_games=1, player_win_counter=1, banker_win_counter=0, tie_counter=0, table="t1"))
    # Window must be cleared
    assert len(window) == 0
