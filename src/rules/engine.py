"""Rule engine evaluating conditions, escalation levels, front-triggers and state persistence."""

from typing import Dict, List, Optional
from src.config.models import RuleConfig
from src.parser.models import RoundResult
from src.rules.base import ConditionResult, RuleState, TriggeredAlert
from src.rules.registry import get_condition
from src.state.window import SlidingWindow
from src.store.repository import RuleStateRepository
from src.utils.logging import get_logger

logger = get_logger("rules.engine")


class RuleEngine:
    """Evaluates declarative rules with front-triggering, escalation levels, and state persistence."""

    def __init__(
        self,
        rules: List[RuleConfig],
        state_repo: Optional[RuleStateRepository] = None
    ) -> None:
        self.rules = rules
        self.state_repo = state_repo
        # Key: (rule_id, table_id) -> RuleState
        self.states: Dict[tuple[str, str], RuleState] = {}

    async def load_states(self, table_ids: List[str]) -> None:
        """Restore rule states from database on startup."""
        if not self.state_repo:
            return

        for rule in self.rules:
            for tid in table_ids:
                saved = await self.state_repo.get_state(rule.id, tid)
                if saved:
                    state = RuleState.from_dict(saved)
                    self.states[(rule.id, tid)] = state
                    logger.debug(
                        "Restored rule state for (%s, %s): metric=%.1f, levels=%s",
                        rule.id,
                        tid,
                        state.current_metric,
                        state.triggered_levels,
                    )

    async def evaluate_round(
        self,
        table_id: str,
        window: SlidingWindow,
        current_round: RoundResult
    ) -> List[TriggeredAlert]:
        """Evaluate all active rules on a new round for the given table."""
        alerts: List[TriggeredAlert] = []
        rounds = window.get_all()

        for rule in self.rules:
            if not rule.enabled:
                continue

            # Scope filter: check if rule applies to this table
            if rule.tables != "all" and table_id not in rule.tables:
                continue

            # If this is a global rule ('all'), check if this table has a dedicated active rule override
            if rule.tables == "all":
                has_specific = any(
                    r.enabled
                    and r.id != rule.id
                    and r.tables != "all"
                    and table_id in r.tables
                    for r in self.rules
                )
                if has_specific:
                    continue

            state_key = (rule.id, table_id)
            state = self.states.get(state_key)
            if state is None:
                state = RuleState(rule_id=rule.id, table_id=table_id)
                self.states[state_key] = state

            # Check cooldown
            if rule.cooldown_rounds > 0 and state.last_triggered_round_id:
                if state.rounds_since_trigger < rule.cooldown_rounds:
                    state.rounds_since_trigger += 1
                    continue

            # Evaluate condition
            cond_type = rule.condition.get("type", "streak")
            try:
                cond_cls = get_condition(cond_type)
            except KeyError as e:
                logger.error("Error evaluating rule '%s': %s", rule.id, e)
                continue

            result: ConditionResult = cond_cls().evaluate(rounds, rule.condition, rule)
            metric = result.metric_value

            if result.matched:
                state.current_metric = metric
                state.was_matched = True

                # Check escalation levels (sorted ascending by threshold)
                sorted_levels = sorted(rule.levels, key=lambda l: l.at)
                for lvl in sorted_levels:
                    # Front-trigger: alert ONLY when threshold is reached and NOT already triggered
                    if metric >= lvl.at and lvl.at not in state.triggered_levels:
                        msg = self._format_message(rule, table_id, lvl.at, metric, current_round, result.details)
                        alert = TriggeredAlert(
                            rule_id=rule.id,
                            table_id=table_id,
                            level=lvl.at,
                            actions=list(lvl.actions),
                            metric_value=metric,
                            round_id=current_round.round_id,
                            message=msg,
                            details=result.details,
                        )
                        alerts.append(alert)
                        state.triggered_levels.add(lvl.at)
                        state.last_triggered_round_id = current_round.round_id
                        state.last_metric_at_alert = metric
                        state.rounds_since_trigger = 0
                        logger.info("Alert triggered for rule '%s' [Level %d] on table '%s'", rule.id, lvl.at, table_id)

                # Repeat every reminder logic
                if (
                    rule.repeat_every > 0
                    and state.last_metric_at_alert > 0
                    and metric > state.last_metric_at_alert
                    and (metric - state.last_metric_at_alert) >= rule.repeat_every
                ):
                    msg = f"Reminder: streak continued on table {table_id} (current length: {int(metric)})"
                    alert = TriggeredAlert(
                        rule_id=rule.id,
                        table_id=table_id,
                        level=None,
                        actions=list(rule.levels[-1].actions) if rule.levels else ["telegram"],
                        metric_value=metric,
                        round_id=current_round.round_id,
                        message=msg,
                        details=result.details,
                    )
                    alerts.append(alert)
                    state.last_metric_at_alert = metric

            else:
                # Sequence broke or condition is no longer matched
                if state.was_matched:
                    # Notify on end if configured
                    if rule.notify_on_end and state.triggered_levels:
                        msg = (
                            f"Серия ({rule.id}) завершена на столе {table_id}. "
                            f"Длина серии: {int(state.current_metric)}. "
                            f"Последний раунд: {current_round.outcome_char} ({current_round.result.value})."
                        )
                        alert = TriggeredAlert(
                            rule_id=rule.id,
                            table_id=table_id,
                            level=None,
                            actions=list(rule.levels[0].actions) if rule.levels else ["telegram"],
                            metric_value=state.current_metric,
                            round_id=current_round.round_id,
                            message=msg,
                            is_end_notification=True,
                            details={"final_metric": state.current_metric, "break_outcome": current_round.outcome_char},
                        )
                        alerts.append(alert)
                        logger.info("Notify-on-end alert generated for rule '%s' on table '%s'", rule.id, table_id)

                    # Rearm rule upon sequence break
                    if rule.rearm == "on_break":
                        state.reset()
                        logger.debug("Rule '%s' on table '%s' rearmed after sequence break.", rule.id, table_id)

                state.was_matched = False
                state.current_metric = 0.0

            # Persist state
            if self.state_repo:
                await self.state_repo.save_state(rule.id, table_id, state.to_dict())

        return alerts

    async def on_shoe_reset(self, table_id: str) -> None:
        """Handle shoe change: reset rules with reset_on_shoe=True."""
        logger.info("Resetting shoe state for table '%s'", table_id)
        for rule in self.rules:
            if rule.reset_on_shoe:
                state_key = (rule.id, table_id)
                if state_key in self.states:
                    self.states[state_key].reset()
                    if self.state_repo:
                        await self.state_repo.save_state(rule.id, table_id, self.states[state_key].to_dict())

    def _format_message(
        self,
        rule: RuleConfig,
        table_id: str,
        level: int,
        metric: float,
        round_result: RoundResult,
        details: dict
    ) -> str:
        """Format alert message with placeholders."""
        streak_len = int(details.get("streak", metric))
        target_of = details.get("of", "loss")
        persp = details.get("perspective", rule.perspective)
        outcomes = "".join(details.get("last_outcomes", []))

        return (
            f"Внимание! Правило '{rule.id}' [Порог {level}]\n"
            f"Стол: {table_id}\n"
            f"Серия: {streak_len} {target_of} подряд (перспектива: {persp})\n"
            f"Последние исходы: {outcomes}\n"
            f"Раунд: #{round_result.round_id} ({round_result.outcome_char}, счет: {round_result.score})"
        )
