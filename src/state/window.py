"""In-memory sliding window state for baccarat table rounds."""

from collections import deque
from typing import List, Literal, Optional
from src.parser.models import Outcome, RoundResult


class SlidingWindow:
    """Bounded queue holding the most recent rounds for a specific table."""

    def __init__(self, maxlen: int = 200) -> None:
        self.maxlen = maxlen
        self._rounds: deque[RoundResult] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self._rounds)

    def append(self, round_result: RoundResult) -> None:
        """Add a newly completed round."""
        self._rounds.append(round_result)

    def extend(self, rounds: List[RoundResult]) -> None:
        """Add multiple rounds (e.g. on restore or reconciliation)."""
        self._rounds.extend(rounds)

    def clear(self) -> None:
        """Empty the window (e.g. on shoe reset)."""
        self._rounds.clear()

    def get_all(self) -> List[RoundResult]:
        """Return all rounds currently in window, oldest to newest."""
        return list(self._rounds)

    def get_latest(self) -> Optional[RoundResult]:
        """Get the most recent round."""
        return self._rounds[-1] if self._rounds else None

    def get_filtered(
        self,
        perspective: Literal["player", "banker"] = "player",
        tie_policy: Literal["ignore", "break", "count_as_loss"] = "ignore",
        window_size: Optional[int] = None,
    ) -> List[RoundResult]:
        """Return rounds filtered according to rule perspective and tie policy."""
        rounds = list(self._rounds)
        if window_size is not None and window_size > 0:
            rounds = rounds[-window_size:]

        if tie_policy == "ignore":
            # Ties are completely omitted from evaluation
            return [r for r in rounds if r.result != Outcome.TIE]
        return rounds
