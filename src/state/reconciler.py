"""State reconciler that detects gaps, fills missing rounds from snapshot, and detects new shoes."""

from typing import List, Optional
from src.parser.models import BeadPlateCell, BoardSnapshot, Outcome, RoundResult, ShoeSummary
from src.state.window import SlidingWindow
from src.store.repository import RoundRepository
from src.utils.logging import get_logger

logger = get_logger("state.reconciler")


class Reconciler:
    """Reconciles in-memory window and SQLite with server snapshots upon connect/reconnect."""

    def __init__(self, table_id: str, window: SlidingWindow, repo: Optional[RoundRepository] = None) -> None:
        self.table_id = table_id
        self.window = window
        self.repo = repo
        self._last_total_games: Optional[int] = None
        self._current_shoe_no: int = 1

    async def reconcile_snapshot(self, snapshot: BoardSnapshot) -> List[RoundResult]:
        """Reconcile window with initial beadPlate snapshot."""
        server_total = snapshot.total_games
        current_len = len(self.window)
        recovered_rounds: List[RoundResult] = []

        logger.info(
            "[%s] Reconciling snapshot: server_totalGames=%d, window_len=%d",
            self.table_id,
            server_total,
            current_len,
        )

        # 1. New Shoe Detection: server totalGames dropped below our current state
        if self._last_total_games is not None and server_total < self._last_total_games:
            logger.warning(
                "[%s] Deck reset detected (New Shoe): totalGames dropped from %d to %d. Resetting window.",
                self.table_id,
                self._last_total_games,
                server_total,
            )
            self.window.clear()
            self._current_shoe_no += 1
            current_len = 0

        self._last_total_games = server_total

        # 2. Flatten all valid rounds from beadPlate (columns left-to-right, rows top-to-bottom)
        all_snapshot_rounds: List[RoundResult] = []
        round_idx = 1
        for col_idx, col in enumerate(snapshot.bead_plate):
            for row_idx, cell in enumerate(col):
                if cell is not None:
                    # Construct synthesized RoundResult with source='snapshot'
                    r = RoundResult(
                        round_id=f"snap_{self.table_id}_{self._current_shoe_no}_{round_idx}",
                        table_id=self.table_id,
                        result=cell.outcome,
                        score=cell.score,
                        player_pair=(cell.pair == "player"),
                        banker_pair=(cell.pair == "banker"),
                        source="snapshot",
                    )
                    all_snapshot_rounds.append(r)
                    round_idx += 1

        # 3. Detect and resolve gap
        if len(self.window) == 0 and all_snapshot_rounds:
            # Cold startup: populate window from snapshot
            logger.info(
                "[%s] Window was empty on startup. Initializing window with %d rounds from snapshot.",
                self.table_id,
                len(all_snapshot_rounds),
            )
            for r in all_snapshot_rounds:
                self.window.append(r)
                if self.repo:
                    await self.repo.save_round(r, shoe_no=self._current_shoe_no)
            recovered_rounds.extend(all_snapshot_rounds)

        elif len(all_snapshot_rounds) > len(self.window):
            # Gap detected during reconnect: missing rounds
            gap = len(all_snapshot_rounds) - len(self.window)
            logger.warning(
                "[%s] Gap detected during reconnect! Missing %d rounds. Reconstructing from beadPlate.",
                self.table_id,
                gap,
            )
            missing = all_snapshot_rounds[-gap:]
            for r in missing:
                self.window.append(r)
                if self.repo:
                    await self.repo.save_round(r, shoe_no=self._current_shoe_no)
            recovered_rounds.extend(missing)
        else:
            logger.info("[%s] State is in sync. No gap detected.", self.table_id)

        return recovered_rounds

    async def reconcile_shoe_summary(self, summary: ShoeSummary) -> None:
        """Track total games and detect deck changes from ShoeSummary."""
        server_total = summary.total_games

        if self._last_total_games is not None and server_total < self._last_total_games:
            logger.warning(
                "[%s] Deck reset detected via ShoeSummary: totalGames %d -> %d.",
                self.table_id,
                self._last_total_games,
                server_total,
            )
            self.window.clear()
            self._current_shoe_no += 1

        self._last_total_games = server_total
