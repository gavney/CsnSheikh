"""Repositories for rounds, rule states, and alerts logging."""

import json
import time
from typing import Any, Dict, List, Optional
import aiosqlite

from src.parser.models import Outcome, RoundResult
from src.utils.logging import get_logger

logger = get_logger("store.repo")


class RoundRepository:
    """Stores and retrieves baccarat rounds with idempotency."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def save_round(self, r: RoundResult, shoe_no: int = 1) -> bool:
        """Insert round into database. Returns True if inserted, False if duplicate."""
        query = """
        INSERT OR IGNORE INTO rounds (
            table_id, round_id, ts, outcome, score, player_pair, banker_pair, shoe_no, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        ts = r.created_at.timestamp()
        async with self.conn.execute(
            query,
            (
                r.table_id,
                r.round_id,
                ts,
                r.result.value,
                r.score,
                1 if r.player_pair else 0,
                1 if r.banker_pair else 0,
                shoe_no,
                r.source,
            ),
        ) as cursor:
            await self.conn.commit()
            is_inserted = cursor.rowcount > 0
            if not is_inserted:
                logger.debug("Round %s on table %s already exists. Ignored duplicate.", r.round_id, r.table_id)
            return is_inserted

    async def get_recent_rounds(self, table_id: str, limit: int = 200) -> List[RoundResult]:
        """Fetch recent rounds for a table ordered chronologically (oldest to newest)."""
        query = """
        SELECT table_id, round_id, ts, outcome, score, player_pair, banker_pair, source
        FROM rounds
        WHERE table_id = ?
        ORDER BY ts DESC, id DESC
        LIMIT ?
        """
        rows = await self.conn.execute_fetchall(query, (table_id, limit))
        results: List[RoundResult] = []
        for row in reversed(rows):
            results.append(
                RoundResult(
                    round_id=row["round_id"],
                    table_id=row["table_id"],
                    result=Outcome.from_str(row["outcome"]),
                    score=row["score"],
                    player_pair=bool(row["player_pair"]),
                    banker_pair=bool(row["banker_pair"]),
                    source=row["source"],
                )
            )
        return results

    async def get_count(self, table_id: str) -> int:
        """Get total number of rounds stored for table."""
        query = "SELECT COUNT(*) as count FROM rounds WHERE table_id = ?"
        async with self.conn.execute(query, (table_id,)) as cursor:
            row = await cursor.fetchone()
            return row["count"] if row else 0


class RuleStateRepository:
    """Persists rule evaluation states across restarts."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def save_state(self, rule_id: str, table_id: str, state: Dict[str, Any]) -> None:
        """Save rule state JSON."""
        query = """
        INSERT INTO rule_state (rule_id, table_id, state_json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(rule_id, table_id) DO UPDATE SET
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
        """
        now = time.time()
        await self.conn.execute(query, (rule_id, table_id, json.dumps(state), now))
        await self.conn.commit()

    async def get_state(self, rule_id: str, table_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve rule state JSON if exists."""
        query = "SELECT state_json FROM rule_state WHERE rule_id = ? AND table_id = ?"
        async with self.conn.execute(query, (rule_id, table_id)) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row["state_json"])
            return None


class AlertRepository:
    """Logs sent alerts for history and deduplication."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn

    async def log_alert(
        self,
        alert_id: str,
        rule_id: str,
        table_id: str,
        level: str,
        message: str,
        round_id: Optional[str] = None
    ) -> None:
        """Record an alert in the database."""
        query = """
        INSERT INTO alerts_log (alert_id, rule_id, table_id, level, message, round_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        await self.conn.execute(
            query,
            (alert_id, rule_id, table_id, level, message, round_id, time.time())
        )
        await self.conn.commit()
