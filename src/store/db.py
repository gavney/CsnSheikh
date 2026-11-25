"""SQLite database initialization with WAL mode and schema definitions."""

from pathlib import Path
from typing import Optional
import aiosqlite

from src.utils.logging import get_logger

logger = get_logger("store.db")

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id TEXT NOT NULL,
    round_id TEXT NOT NULL,
    ts REAL NOT NULL,
    outcome TEXT NOT NULL,
    score INTEGER NOT NULL,
    player_pair INTEGER NOT NULL,
    banker_pair INTEGER NOT NULL,
    shoe_no INTEGER DEFAULT 1,
    source TEXT NOT NULL,
    UNIQUE(table_id, round_id)
);

CREATE INDEX IF NOT EXISTS idx_rounds_table_ts ON rounds(table_id, ts);

CREATE TABLE IF NOT EXISTS rule_state (
    rule_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(rule_id, table_id)
);

CREATE TABLE IF NOT EXISTS alerts_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    round_id TEXT,
    sent_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_table_time ON alerts_log(table_id, sent_at);
"""


class Database:
    """Manages an async SQLite database connection."""

    def __init__(self, db_path: Path = Path("casino_monitor.db")) -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> aiosqlite.Connection:
        """Connect to database and ensure schema is migrated."""
        if self._conn is not None:
            return self._conn

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row

        # Execute migration schema
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()
        logger.info("Connected to SQLite database at %s (WAL mode active)", self.db_path)
        return self._conn

    async def close(self) -> None:
        """Close database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Closed SQLite connection")
