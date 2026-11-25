"""Count in window condition: counts matches of target outcome in last N rounds."""

from typing import Any, Dict, List
from src.config.models import RuleConfig
from src.parser.models import Outcome, RoundResult
from src.rules.base import Condition, ConditionResult
from src.rules.registry import register_condition


@register_condition("count_in_window")
class CountInWindowCondition(Condition):
    """Counts occurrences of an event in the sliding window of size N."""

    def evaluate(
        self,
        rounds: List[RoundResult],
        params: Dict[str, Any],
        rule_config: RuleConfig
    ) -> ConditionResult:
        if not rounds:
            return ConditionResult(matched=False, metric_value=0.0)

        target = str(params.get("of", "loss")).lower()
        window_size = int(params.get("window", 30))
        perspective = rule_config.perspective
        tie_policy = rule_config.tie_policy

        inspected_rounds = rounds[-window_size:] if len(rounds) > window_size else rounds
        count = 0

        for r in inspected_rounds:
            if r.result == Outcome.TIE:
                if target in ("tie", "t"):
                    count += 1
                elif tie_policy == "count_as_loss" and target in ("loss", "lose"):
                    count += 1
                continue

            if target in ("win", "victory") and r.result.value == perspective:
                count += 1
            elif target in ("loss", "lose") and r.result.value != perspective:
                count += 1
            elif target in ("player", "p") and r.result == Outcome.PLAYER:
                count += 1
            elif target in ("banker", "b") and r.result == Outcome.BANKER:
                count += 1

        target_threshold = params.get("count") or params.get("at")
        matched = (count >= int(target_threshold)) if target_threshold else (count > 0)

        return ConditionResult(
            matched=matched,
            metric_value=float(count),
            details={
                "count": count,
                "window": len(inspected_rounds),
                "of": target,
                "perspective": perspective,
            },
        )
