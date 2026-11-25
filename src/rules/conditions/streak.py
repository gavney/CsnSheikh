"""Streak condition: evaluates consecutive wins, losses or specific outcomes."""

from typing import Any, Dict, List
from src.config.models import RuleConfig
from src.parser.models import Outcome, RoundResult
from src.rules.base import Condition, ConditionResult
from src.rules.registry import register_condition


@register_condition("streak")
class StreakCondition(Condition):
    """Evaluates the length of consecutive events ending at the latest round."""

    def evaluate(
        self,
        rounds: List[RoundResult],
        params: Dict[str, Any],
        rule_config: RuleConfig
    ) -> ConditionResult:
        if not rounds:
            return ConditionResult(matched=False, metric_value=0.0)

        target = str(params.get("of", "loss")).lower()
        window_limit = int(params.get("window", rule_config.condition.get("window", 30)))
        perspective = rule_config.perspective
        tie_policy = rule_config.tie_policy

        streak = 0
        inspected = 0

        # Traverse backwards from the most recent round
        for r in reversed(rounds):
            if inspected >= window_limit:
                break
            inspected += 1

            # 1. Handle Tie Policy
            if r.result == Outcome.TIE:
                if target in ("tie", "t"):
                    streak += 1
                    continue
                elif tie_policy == "ignore":
                    # Ignore tie: does not increment streak, does not break streak
                    continue
                elif tie_policy == "break":
                    # Break tie: breaks streak immediately
                    break
                elif tie_policy == "count_as_loss":
                    if target in ("loss", "lose"):
                        streak += 1
                        continue
                    else:
                        break

            # 2. Evaluate win / loss / explicit outcome
            is_match = False
            if target in ("win", "victory"):
                is_match = (r.result.value == perspective)
            elif target in ("loss", "lose"):
                is_match = (r.result.value != perspective and r.result != Outcome.TIE)
            elif target in ("player", "p"):
                is_match = (r.result == Outcome.PLAYER)
            elif target in ("banker", "b"):
                is_match = (r.result == Outcome.BANKER)
            elif target in ("tie", "t"):
                is_match = (r.result == Outcome.TIE)

            if is_match:
                streak += 1
            else:
                break

        return ConditionResult(
            matched=(streak > 0),
            metric_value=float(streak),
            details={
                "streak": streak,
                "of": target,
                "perspective": perspective,
                "tie_policy": tie_policy,
                "window": window_limit,
                "last_outcomes": [r.outcome_char for r in rounds[-min(len(rounds), 10):]],
            },
        )
