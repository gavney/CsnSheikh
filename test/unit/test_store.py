"""Unit tests for SQLite storage and repositories."""

import pytest
import aiosqlite
from src.parser.models import Outcome, RoundResult
from src.store.db import Database
from src.store.repository import AlertRepository, RoundRepository, RuleStateRepository


@pytest.mark.asyncio
async def test_round_repository_idempotency(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    conn = await db.connect()

    repo = RoundRepository(conn)
    r1 = RoundResult(
        round_id="round_100",
        table_id="bc281koreanch281",
        result=Outcome.PLAYER,
        score=7,
        player_pair=True,
        banker_pair=False,
    )

    # First insert: must succeed
    inserted = await repo.save_round(r1)
    assert inserted is True
    assert await repo.get_count("bc281koreanch281") == 1

    # Duplicate insert: must be ignored and return False
    duplicate_inserted = await repo.save_round(r1)
    assert duplicate_inserted is False
    assert await repo.get_count("bc281koreanch281") == 1

    # Insert another round on different table with same round_id: must succeed (table_id, round_id unique)
    r2 = RoundResult(
        round_id="round_100",
        table_id="spto11bctorobc11",
        result=Outcome.BANKER,
        score=8,
    )
    assert await repo.save_round(r2) is True
    assert await repo.get_count("spto11bctorobc11") == 1

    await db.close()


@pytest.mark.asyncio
async def test_get_recent_rounds_chronological(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    conn = await db.connect()
    repo = RoundRepository(conn)

    for i in range(1, 11):
        r = RoundResult(
            round_id=f"r_{i}",
            table_id="t1",
            result=Outcome.PLAYER if i % 2 == 0 else Outcome.BANKER,
            score=i % 10,
        )
        await repo.save_round(r)

    rounds = await repo.get_recent_rounds("t1", limit=5)
    assert len(rounds) == 5
    # Must be in chronological order (oldest to newest among the 5 most recent)
    assert [r.round_id for r in rounds] == ["r_6", "r_7", "r_8", "r_9", "r_10"]

    await db.close()


@pytest.mark.asyncio
async def test_rule_state_and_alert_repos(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    conn = await db.connect()

    state_repo = RuleStateRepository(conn)
    alert_repo = AlertRepository(conn)

    # Rule state save & get
    await state_repo.save_state("loss_streak", "t1", {"current_streak": 4, "armed": True})
    state = await state_repo.get_state("loss_streak", "t1")
    assert state == {"current_streak": 4, "armed": True}

    # Update state
    await state_repo.save_state("loss_streak", "t1", {"current_streak": 5, "armed": False})
    updated = await state_repo.get_state("loss_streak", "t1")
    assert updated["current_streak"] == 5

    # Alert logging
    await alert_repo.log_alert("alert_1", "loss_streak", "t1", "telegram", "Streak 4 reached", round_id="r_10")
    # Verify no exceptions raised
    await db.close()
