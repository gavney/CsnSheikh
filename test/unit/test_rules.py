"""Comprehensive unit tests for Rule Engine and Condition plugins on synthetic sequences."""

import pytest
from src.config.models import RuleConfig, RuleLevelConfig
from src.parser.models import Outcome, RoundResult
from src.rules.engine import RuleEngine
from src.rules.registry import list_conditions
from src.state.window import SlidingWindow
from src.store.db import Database
from src.store.repository import RuleStateRepository


def make_round(round_id: str, outcome_char: str) -> RoundResult:
    """Helper to synthesize a round with given outcome (P, B, T)."""
    if outcome_char.upper() == "P":
        outcome = Outcome.PLAYER
    elif outcome_char.upper() == "B":
        outcome = Outcome.BANKER
    else:
        outcome = Outcome.TIE
    return RoundResult(round_id=round_id, table_id="t1", result=outcome, score=5)


def test_registered_conditions_list():
    conditions = list_conditions()
    assert "streak" in conditions
    assert "count_in_window" in conditions
    assert "pattern" in conditions
    assert "absence" in conditions
    assert "ratio" in conditions
    assert "and" in conditions
    assert "or" in conditions
    assert "not" in conditions


@pytest.mark.asyncio
async def test_streak_front_trigger_and_escalation():
    """Test levels escalation (at 4 -> level 4 alert, at 7 -> level 7 alert) and front-triggering."""
    rule = RuleConfig(
        id="loss_streak_test",
        enabled=True,
        tables="all",
        perspective="player",
        tie_policy="ignore",
        condition={"type": "streak", "of": "loss", "window": 30},
        levels=[
            RuleLevelConfig(at=4, actions=["telegram"]),
            RuleLevelConfig(at=7, actions=["telegram_urgent"]),
        ],
        rearm="on_break",
        notify_on_end=True,
    )

    engine = RuleEngine([rule])
    window = SlidingWindow(maxlen=50)

    # Round 1..3: Banker (Loss for player). No alerts.
    for i in range(1, 4):
        r = make_round(f"r_{i}", "B")
        window.append(r)
        alerts = await engine.evaluate_round("t1", window, r)
        assert len(alerts) == 0

    # Round 4: Banker (Loss #4). Level 4 alert MUST fire!
    r4 = make_round("r_4", "B")
    window.append(r4)
    alerts4 = await engine.evaluate_round("t1", window, r4)
    assert len(alerts4) == 1
    assert alerts4[0].level == 4
    assert alerts4[0].actions == ["telegram"]

    # Round 5: Banker (Loss #5). Front-trigger: Level 4 must NOT fire again!
    r5 = make_round("r_5", "B")
    window.append(r5)
    alerts5 = await engine.evaluate_round("t1", window, r5)
    assert len(alerts5) == 0

    # Round 6: Banker (Loss #6). Still no alert.
    r6 = make_round("r_6", "B")
    window.append(r6)
    alerts6 = await engine.evaluate_round("t1", window, r6)
    assert len(alerts6) == 0

    # Round 7: Banker (Loss #7). Level 7 alert MUST fire!
    r7 = make_round("r_7", "B")
    window.append(r7)
    alerts7 = await engine.evaluate_round("t1", window, r7)
    assert len(alerts7) == 1
    assert alerts7[0].level == 7
    assert alerts7[0].actions == ["telegram_urgent"]

    # Round 8: Player (Win!). Breaks the streak.
    # notify_on_end is True -> End notification must fire!
    r8 = make_round("r_8", "P")
    window.append(r8)
    alerts8 = await engine.evaluate_round("t1", window, r8)
    assert len(alerts8) == 1
    assert alerts8[0].is_end_notification is True
    assert "Серия (loss_streak_test) завершена" in alerts8[0].message

    # Rule is rearmed! Next 4 losses should trigger Level 4 again
    for i in range(9, 12):
        r = make_round(f"r_{i}", "B")
        window.append(r)
        await engine.evaluate_round("t1", window, r)

    r12 = make_round("r_12", "B")
    window.append(r12)
    alerts12 = await engine.evaluate_round("t1", window, r12)
    assert len(alerts12) == 1
    assert alerts12[0].level == 4


@pytest.mark.asyncio
async def test_tie_policy_ignore():
    """tie_policy: ignore -> Ties do not break or count towards loss streak."""
    rule = RuleConfig(
        id="tie_ignore_test",
        enabled=True,
        perspective="player",
        tie_policy="ignore",
        condition={"type": "streak", "of": "loss", "window": 20},
        levels=[RuleLevelConfig(at=3, actions=["telegram"])],
    )
    engine = RuleEngine([rule])
    window = SlidingWindow(maxlen=50)

    # Sequence: B, B, T, B (for player: Loss, Loss, Tie, Loss) -> streak should be 3!
    seq = ["B", "B", "T", "B"]
    last_alerts = []
    for idx, char in enumerate(seq):
        r = make_round(f"r_{idx}", char)
        window.append(r)
        last_alerts = await engine.evaluate_round("t1", window, r)

    assert len(last_alerts) == 1
    assert last_alerts[0].level == 3
    assert last_alerts[0].metric_value == 3.0


@pytest.mark.asyncio
async def test_tie_policy_break():
    """tie_policy: break -> Ties break the streak immediately."""
    rule = RuleConfig(
        id="tie_break_test",
        enabled=True,
        perspective="player",
        tie_policy="break",
        condition={"type": "streak", "of": "loss", "window": 20},
        levels=[RuleLevelConfig(at=3, actions=["telegram"])],
    )
    engine = RuleEngine([rule])
    window = SlidingWindow(maxlen=50)

    # Sequence: B, B, T, B -> Tie broke it at round 3, so at round 4 streak is only 1
    for idx, char in enumerate(["B", "B", "T", "B"]):
        r = make_round(f"r_{idx}", char)
        window.append(r)
        alerts = await engine.evaluate_round("t1", window, r)
        assert len(alerts) == 0


@pytest.mark.asyncio
async def test_tie_policy_count_as_loss():
    """tie_policy: count_as_loss -> Ties count as a loss."""
    rule = RuleConfig(
        id="tie_as_loss_test",
        enabled=True,
        perspective="player",
        tie_policy="count_as_loss",
        condition={"type": "streak", "of": "loss", "window": 20},
        levels=[RuleLevelConfig(at=3, actions=["telegram"])],
    )
    engine = RuleEngine([rule])
    window = SlidingWindow(maxlen=50)

    # Sequence: B, T, B -> 3 losses for player -> Level 3 alert!
    last_alerts = []
    for idx, char in enumerate(["B", "T", "B"]):
        r = make_round(f"r_{idx}", char)
        window.append(r)
        last_alerts = await engine.evaluate_round("t1", window, r)

    assert len(last_alerts) == 1
    assert last_alerts[0].level == 3


@pytest.mark.asyncio
async def test_reset_on_shoe():
    """reset_on_shoe: true -> Shoe reset resets streak state."""
    rule = RuleConfig(
        id="shoe_reset_test",
        enabled=True,
        reset_on_shoe=True,
        condition={"type": "streak", "of": "loss", "window": 20},
        levels=[RuleLevelConfig(at=3, actions=["telegram"])],
    )
    engine = RuleEngine([rule])
    window = SlidingWindow(maxlen=50)

    # 2 losses (streak 2)
    for idx, char in enumerate(["B", "B"]):
        r = make_round(f"r_{idx}", char)
        window.append(r)
        await engine.evaluate_round("t1", window, r)

    # Shoe resets!
    await engine.on_shoe_reset("t1")
    window.clear()

    # Next 1 loss -> streak is 1, not 3.
    r_new = make_round("r_new", "B")
    window.append(r_new)
    alerts = await engine.evaluate_round("t1", window, r_new)
    assert len(alerts) == 0


@pytest.mark.asyncio
async def test_rule_state_persistence_across_restarts(tmp_path):
    """Test that rule state is saved to SQLite and survives restart."""
    db_path = tmp_path / "rules_test.db"
    db = Database(db_path)
    conn = await db.connect()
    repo = RuleStateRepository(conn)

    rule = RuleConfig(
        id="persistent_streak",
        enabled=True,
        condition={"type": "streak", "of": "loss", "window": 20},
        levels=[
            RuleLevelConfig(at=3, actions=["telegram"]),
            RuleLevelConfig(at=5, actions=["telegram_urgent"]),
        ],
    )

    # 1. Run engine 1: trigger level 3
    engine1 = RuleEngine([rule], state_repo=repo)
    window = SlidingWindow(maxlen=50)

    for idx in range(3):
        r = make_round(f"r_{idx}", "B")
        window.append(r)
        alerts = await engine1.evaluate_round("t1", window, r)
        if idx == 2:
            assert len(alerts) == 1
            assert alerts[0].level == 3

    # 2. Simulate restart: instantiate fresh RuleEngine and load states from SQLite
    engine2 = RuleEngine([rule], state_repo=repo)
    await engine2.load_states(["t1"])

    state = engine2.states.get(("persistent_streak", "t1"))
    assert state is not None
    assert 3 in state.triggered_levels

    # Round 4: loss (streak 4) -> Level 3 must NOT re-trigger!
    r4 = make_round("r_3", "B")
    window.append(r4)
    alerts4 = await engine2.evaluate_round("t1", window, r4)
    assert len(alerts4) == 0

    # Round 5: loss (streak 5) -> Level 5 MUST trigger!
    r5 = make_round("r_4", "B")
    window.append(r5)
    alerts5 = await engine2.evaluate_round("t1", window, r5)
    assert len(alerts5) == 1
    assert alerts5[0].level == 5

    await db.close()


@pytest.mark.asyncio
async def test_pattern_condition():
    rule = RuleConfig(
        id="pattern_test",
        enabled=True,
        condition={"type": "pattern", "sequence": "PBPB"},
        levels=[RuleLevelConfig(at=1, actions=["telegram"])],
    )
    engine = RuleEngine([rule])
    window = SlidingWindow(maxlen=50)

    for idx, c in enumerate(["P", "B", "P"]):
        r = make_round(f"r_{idx}", c)
        window.append(r)
        alerts = await engine.evaluate_round("t1", window, r)
        assert len(alerts) == 0

    # Completes 'PBPB'
    r4 = make_round("r_3", "B")
    window.append(r4)
    alerts4 = await engine.evaluate_round("t1", window, r4)
    assert len(alerts4) == 1
    assert alerts4[0].details["target_pattern"] == "PBPB"


@pytest.mark.asyncio
async def test_composite_conditions_and_or_not():
    # Rule with AND: pattern PB AND count of P >= 2 in window
    rule_and = RuleConfig(
        id="and_test",
        enabled=True,
        condition={
            "type": "and",
            "conditions": [
                {"type": "pattern", "sequence": "PB"},
                {"type": "count_in_window", "of": "player", "window": 5, "count": 2},
            ],
        },
        levels=[RuleLevelConfig(at=1, actions=["telegram"])],
    )
    engine = RuleEngine([rule_and])
    window = SlidingWindow(maxlen=50)

    # P, P, B -> ends with 'PB', total P in window = 2 -> MATCH!
    for idx, c in enumerate(["P", "P", "B"]):
        r = make_round(f"r_{idx}", c)
        window.append(r)
        alerts = await engine.evaluate_round("t1", window, r)
        if idx == 2:
            assert len(alerts) == 1
            assert alerts[0].details["all_matched"] is True
        else:
            assert len(alerts) == 0


@pytest.mark.asyncio
async def test_player_streak_4_with_tie_ignore():
    """Verify user scenario: Player wins 4 times with a Tie in between (P, P, T, P, P)."""
    from src.notifier.base import Priority
    from src.notifier.formatter import AlertFormatter

    rule = RuleConfig(
        id="player_streak_4",
        name="Игрок 4 победы подряд",
        enabled=True,
        perspective="player",
        tie_policy="ignore",
        condition={"type": "streak", "of": "player", "window": 30},
        levels=[RuleLevelConfig(at=4, actions=["telegram"])],
        rearm="on_break",
        notify_on_end=False,
    )
    engine = RuleEngine([rule])
    window = SlidingWindow(maxlen=50)

    # Sequence from user's screenshot: P, P, T, P, P
    seq = ["P", "P", "T", "P", "P"]
    last_alerts = []
    for idx, char in enumerate(seq):
        r = make_round(f"rnd_{idx}", char)
        window.append(r)
        alerts = await engine.evaluate_round("bc281koreanch281", window, r)
        if alerts:
            last_alerts = alerts

    assert len(last_alerts) == 1, "Alert must fire when Player wins 4 times ignoring Ties"
    alert = last_alerts[0]
    assert alert.metric_value == 4.0
    assert alert.level == 4

    # Verify formatting with hyperlink and concise text
    msg = AlertFormatter.format_rule_alert(
        table_name="Korean Speed Baccarat 1",
        table_id="bc281koreanch281",
        rule_id=alert.rule_id,
        level=alert.level,
        metric=alert.metric_value,
        round_id=alert.round_id,
        priority=Priority.NORMAL,
        details=alert.details,
        is_end=False,
        table_url="https://bxgnanhldtnm.top/ru/casino/game/68018/speed-baccarat-16-korean",
    )

    assert "[Korean Speed Baccarat 1](https://bxgnanhldtnm.top/ru/casino/game/68018/speed-baccarat-16-korean)" in msg
    assert "Игрок 4 подряд" in msg
    assert "Правило" not in msg
    assert "перспектива" not in msg
    assert "Раунд" not in msg
    assert "🔵" in msg
