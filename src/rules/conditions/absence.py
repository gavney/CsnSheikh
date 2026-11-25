"""Absence condition: checks if an outcome has been absent for N rounds."""

from typing import Any, Dict, List
from src.config.models import RuleConfig
from src.parser.models import Outcome, RoundResult
from src.rules.base import Condition, ConditionResult
from src.rules.registry import register_condition


@register_condition("absence")
class AbsenceCondition(Condition):
    """Counts how many rounds have passed since an event was last observed."""

    def evaluate(
        self,
        rounds: List[RoundResult],
        params: Dict[str, Any],
        rule_config: RuleConfig
    ) -> ConditionResult:
        if not rounds:
            return ConditionResult(matched=False, metric_value=0.0)

        target = str(params.get("of", "tie")).lower()
        window_limit = int(params.get("window", 50))
        perspective = rule_config.perspective

        rounds_since = 0
        inspected = 0

        for r in reversed(rounds):
            if inspected >= window_limit:
                break
            inspected += 1

            is_target = False
            if target in ("tie", "t") and r.result == Outcome.TIE:
                is_target = True
            elif target in ("player", "p") and r.result == Outcome.PLAYER:
                is_target = True
            elif target in ("banker", "b") and r.result == Outcome.BANKER:
                is_target = True
            elif target in ("win", "victory") and r.result.value == perspective:
                is_target = True
            elif target in ("loss", "lose") and r.result.value != perspective and r.result != Outcome.TIE:
                is_target = True

            if is_target:
                break
            rounds_since += 1

        target_threshold = params.get("rounds") or params.get("at")
        matched = (rounds_since >= int(target_threshold)) if target_threshold else (rounds_since > 0)

        return ConditionResult(
            matched=matched,
            metric_value=float(rounds_since),
            details={
                "rounds_since": rounds_since,
                "of": target,
                "window": window_limit,
            },
        )
