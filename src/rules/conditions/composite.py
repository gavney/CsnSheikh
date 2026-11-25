"""Composite logical conditions: AND, OR, NOT."""

from typing import Any, Dict, List
from src.config.models import RuleConfig
from src.parser.models import RoundResult
from src.rules.base import Condition, ConditionResult
from src.rules.registry import get_condition, register_condition


@register_condition("and")
class AndCondition(Condition):
    """Combines multiple child conditions with logical AND."""

    def evaluate(
        self,
        rounds: List[RoundResult],
        params: Dict[str, Any],
        rule_config: RuleConfig
    ) -> ConditionResult:
        children: List[Dict[str, Any]] = params.get("conditions", [])
        if not children:
            return ConditionResult(matched=False, metric_value=0.0)

        all_matched = True
        child_details = {}
        min_metric = float("inf")

        for idx, child_spec in enumerate(children):
            cond_type = child_spec.get("type")
            cond_cls = get_condition(cond_type)
            result = cond_cls().evaluate(rounds, child_spec, rule_config)
            child_details[f"cond_{idx}_{cond_type}"] = result.details
            min_metric = min(min_metric, result.metric_value)
            if not result.matched:
                all_matched = False

        return ConditionResult(
            matched=all_matched,
            metric_value=min_metric if all_matched and min_metric != float("inf") else 0.0,
            details={"all_matched": all_matched, "children": child_details},
        )


@register_condition("or")
class OrCondition(Condition):
    """Combines multiple child conditions with logical OR."""

    def evaluate(
        self,
        rounds: List[RoundResult],
        params: Dict[str, Any],
        rule_config: RuleConfig
    ) -> ConditionResult:
        children: List[Dict[str, Any]] = params.get("conditions", [])
        if not children:
            return ConditionResult(matched=False, metric_value=0.0)

        any_matched = False
        child_details = {}
        max_metric = 0.0

        for idx, child_spec in enumerate(children):
            cond_type = child_spec.get("type")
            cond_cls = get_condition(cond_type)
            result = cond_cls().evaluate(rounds, child_spec, rule_config)
            child_details[f"cond_{idx}_{cond_type}"] = result.details
            if result.matched:
                any_matched = True
                max_metric = max(max_metric, result.metric_value)

        return ConditionResult(
            matched=any_matched,
            metric_value=max_metric,
            details={"any_matched": any_matched, "children": child_details},
        )


@register_condition("not")
class NotCondition(Condition):
    """Inverts the evaluation of a child condition with logical NOT."""

    def evaluate(
        self,
        rounds: List[RoundResult],
        params: Dict[str, Any],
        rule_config: RuleConfig
    ) -> ConditionResult:
        child_spec: Dict[str, Any] = params.get("condition", {})
        cond_type = child_spec.get("type")
        if not cond_type:
            return ConditionResult(matched=False, metric_value=0.0)

        cond_cls = get_condition(cond_type)
        result = cond_cls().evaluate(rounds, child_spec, rule_config)

        inverted = not result.matched
        return ConditionResult(
            matched=inverted,
            metric_value=1.0 if inverted else 0.0,
            details={"inverted_from": result.matched, "child": result.details},
        )
