"""Pattern matching condition: checks for an exact sequence of outcomes."""

from typing import Any, Dict, List, Union
from src.config.models import RuleConfig
from src.parser.models import Outcome, RoundResult
from src.rules.base import Condition, ConditionResult
from src.rules.registry import register_condition


@register_condition("pattern")
class PatternCondition(Condition):
    """Checks if the most recent rounds match a target sequence (e.g. 'PBPB' or ['P', 'B'])."""

    def evaluate(
        self,
        rounds: List[RoundResult],
        params: Dict[str, Any],
        rule_config: RuleConfig
    ) -> ConditionResult:
        raw_pattern: Union[str, List[str]] = params.get("sequence") or params.get("pattern") or ""
        if isinstance(raw_pattern, str):
            target_seq = [c.upper() for c in raw_pattern if c.strip()]
        else:
            target_seq = [str(x).upper() for x in raw_pattern]

        if not target_seq or len(rounds) < len(target_seq):
            return ConditionResult(matched=False, metric_value=0.0)

        # Slice the most recent N rounds matching target sequence length
        recent = rounds[-len(target_seq):]
        recent_seq = [r.outcome_char for r in recent]

        matched = (recent_seq == target_seq)
        return ConditionResult(
            matched=matched,
            metric_value=1.0 if matched else 0.0,
            details={
                "target_pattern": "".join(target_seq),
                "actual_pattern": "".join(recent_seq),
            },
        )
