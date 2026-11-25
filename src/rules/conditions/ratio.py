"""Ratio condition: calculates proportion of an outcome in the window."""

from typing import Any, Dict, List
from src.config.models import RuleConfig
from src.parser.models import Outcome, RoundResult
from src.rules.base import Condition, ConditionResult
from src.rules.registry import register_condition


@register_condition("ratio")
class RatioCondition(Condition):
    """Calculates proportion (0.0 to 1.0) of target outcome in last N rounds."""

    def evaluate(
        self,
        rounds: List[RoundResult],
        params: Dict[str, Any],
        rule_config: RuleConfig
    ) -> ConditionResult:
        if not rounds:
            return ConditionResult(matched=False, metric_value=0.0)

        window_size = int(params.get("window", 30))
        target = str(params.get("of", "banker")).lower()
        threshold = float(params.get("threshold", 0.5))

        inspected = rounds[-window_size:] if len(rounds) > window_size else rounds
        if not inspected:
            return ConditionResult(matched=False, metric_value=0.0)

        count = 0
        for r in inspected:
            if target in ("banker", "b") and r.result == Outcome.BANKER:
                count += 1
            elif target in ("player", "p") and r.result == Outcome.PLAYER:
                count += 1
            elif target in ("tie", "t") and r.result == Outcome.TIE:
                count += 1

        ratio_val = count / len(inspected)
        matched = (ratio_val >= threshold)

        return ConditionResult(
            matched=matched,
            metric_value=ratio_val,
            details={
                "ratio": round(ratio_val, 3),
                "count": count,
                "window": len(inspected),
                "threshold": threshold,
            },
        )
