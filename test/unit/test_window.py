"""Unit tests for SlidingWindow buffer."""

from src.parser.models import Outcome, RoundResult
from src.state.window import SlidingWindow


def test_sliding_window_capacity():
    win = SlidingWindow(maxlen=5)
    assert len(win) == 0

    for i in range(10):
        win.append(RoundResult(round_id=f"r_{i}", table_id="t1", result=Outcome.PLAYER, score=5))

    assert len(win) == 5
    assert [r.round_id for r in win.get_all()] == ["r_5", "r_6", "r_7", "r_8", "r_9"]
    assert win.get_latest().round_id == "r_9"


def test_sliding_window_filtering_tie_policy():
    win = SlidingWindow(maxlen=10)
    # P, T, P, B, T
    sequence = [Outcome.PLAYER, Outcome.TIE, Outcome.PLAYER, Outcome.BANKER, Outcome.TIE]
    for idx, out in enumerate(sequence):
        win.append(RoundResult(round_id=f"r_{idx}", table_id="t1", result=out, score=6))

    # tie_policy = 'ignore' should drop ties
    filtered_ignore = win.get_filtered(perspective="player", tie_policy="ignore")
    assert len(filtered_ignore) == 3
    assert [r.result for r in filtered_ignore] == [Outcome.PLAYER, Outcome.PLAYER, Outcome.BANKER]

    # tie_policy = 'break' should keep all
    filtered_break = win.get_filtered(perspective="player", tie_policy="break")
    assert len(filtered_break) == 5

    # window_size limit
    limited = win.get_filtered(perspective="player", tie_policy="break", window_size=3)
    assert len(limited) == 3
    assert [r.round_id for r in limited] == ["r_2", "r_3", "r_4"]
