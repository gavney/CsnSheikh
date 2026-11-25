"""Data models for Pragmatic Play Live Baccarat stream events."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class Outcome(str, Enum):
    """Baccarat round outcome."""
    PLAYER = "player"
    BANKER = "banker"
    TIE = "tie"

    @classmethod
    def from_str(cls, val: str) -> "Outcome":
        val = val.lower().strip()
        if val in ("p", "player"):
            return cls.PLAYER
        if val in ("b", "banker"):
            return cls.BANKER
        if val in ("t", "tie"):
            return cls.TIE
        raise ValueError(f"Unknown outcome value: {val}")


class BeadPlateCell(BaseModel):
    """Decoded cell in the beadPlate road."""
    outcome: Outcome
    pair: Literal["none", "banker", "player", "both"]
    score: int
    raw_code: str

    @classmethod
    def from_code(cls, code: str) -> Optional["BeadPlateCell"]:
        """Parse a 3-character beadPlate code, e.g. 'RN9', 'BB5', 'GN6'."""
        if not code or code == "---" or len(code) < 3:
            return None
        
        # 1st char: Color / Outcome
        c = code[0].upper()
        if c == "R":
            outcome = Outcome.BANKER
        elif c == "B":
            outcome = Outcome.PLAYER
        elif c == "G":
            outcome = Outcome.TIE
        else:
            return None

        # 2nd char: Pair
        p = code[1].upper()
        if p == "N":
            pair = "none"
        elif p == "B":
            pair = "banker"
        elif p == "P":
            pair = "player"
        else:
            pair = "none"

        # 3rd char: Score (0-9)
        try:
            score = int(code[2])
        except ValueError:
            score = 0

        return cls(outcome=outcome, pair=pair, score=score, raw_code=code)


class ShoeSummary(BaseModel):
    """Deck counters and current shoe state."""
    total_games: int
    player_win_counter: int
    banker_win_counter: int
    tie_counter: int
    player_pair_counter: int = 0
    banker_pair_counter: int = 0
    table: str
    seq: Optional[int] = None


class RoundResult(BaseModel):
    """Completed round outcome."""
    round_id: str
    table_id: str
    result: Outcome
    score: int
    player_pair: bool = False
    banker_pair: bool = False
    time: str = ""
    seq: Optional[int] = None
    source: Literal["live", "snapshot"] = "live"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def outcome_char(self) -> str:
        """Single character representation: P, B, or T."""
        if self.result == Outcome.PLAYER:
            return "P"
        if self.result == Outcome.BANKER:
            return "B"
        return "T"


class BoardSnapshot(BaseModel):
    """Initial snapshot of the table board (beadPlate and counters)."""
    table_id: str
    total_games: int
    bead_plate: List[List[Optional[BeadPlateCell]]] = Field(default_factory=list)
    raw_rounds_count: int = 0
    seq: Optional[int] = None


class PongEvent(BaseModel):
    """Heartbeat acknowledgement from server."""
    channel: str
    time: str
    seq: Optional[int] = None


class SessionOfflineEvent(BaseModel):
    """Sent by Pragmatic Play server when JSESSIONID is expired or invalid."""
    table_id: str
    raw_message: str = "<session>offline</session>"


class TableInfoEvent(BaseModel):
    """Table metadata event."""
    table_id: str
    table_name: Optional[str] = None
    dealer_name: Optional[str] = None
    game_id: Optional[str] = None
    status: Optional[str] = None
