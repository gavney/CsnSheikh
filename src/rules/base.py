"""Base abstractions for Rule Conditions, Results, Alerts, and Rule State."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from src.config.models import RuleConfig
from src.parser.models import RoundResult


class ConditionResult(BaseModel):
    """Result of evaluating a condition on the sliding window."""
    matched: bool
    metric_value: float = 0.0
    details: Dict[str, Any] = Field(default_factory=dict)


class Condition(ABC):
    """Abstract condition plugin for rule evaluation."""

    @abstractmethod
    def evaluate(
        self,
        rounds: List[RoundResult],
        params: Dict[str, Any],
        rule_config: RuleConfig
    ) -> ConditionResult:
        """Evaluate condition on chronological rounds list (oldest to newest)."""
        pass


@dataclass
class TriggeredAlert:
    """Alert event generated when a rule condition reaches an escalation threshold."""
    rule_id: str
    table_id: str
    level: Optional[int]
    actions: List[str]
    metric_value: float
    round_id: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    is_end_notification: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RuleState:
    """State of a rule for a given table, tracking escalation and rearm."""
    rule_id: str
    table_id: str
    current_metric: float = 0.0
    triggered_levels: Set[int] = field(default_factory=set)
    last_triggered_round_id: Optional[str] = None
    rounds_since_trigger: int = 0
    last_metric_at_alert: float = 0.0
    was_matched: bool = False

    def reset(self) -> None:
        """Reset state upon sequence break or shoe reset."""
        self.current_metric = 0.0
        self.triggered_levels.clear()
        self.last_triggered_round_id = None
        self.rounds_since_trigger = 0
        self.last_metric_at_alert = 0.0
        self.was_matched = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize state for SQLite storage."""
        return {
            "rule_id": self.rule_id,
            "table_id": self.table_id,
            "current_metric": self.current_metric,
            "triggered_levels": sorted(list(self.triggered_levels)),
            "last_triggered_round_id": self.last_triggered_round_id,
            "rounds_since_trigger": self.rounds_since_trigger,
            "last_metric_at_alert": self.last_metric_at_alert,
            "was_matched": self.was_matched,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuleState":
        """Deserialize state from SQLite storage."""
        return cls(
            rule_id=data.get("rule_id", ""),
            table_id=data.get("table_id", ""),
            current_metric=float(data.get("current_metric", 0.0)),
            triggered_levels=set(data.get("triggered_levels", [])),
            last_triggered_round_id=data.get("last_triggered_round_id"),
            rounds_since_trigger=int(data.get("rounds_since_trigger", 0)),
            last_metric_at_alert=float(data.get("last_metric_at_alert", 0.0)),
            was_matched=bool(data.get("was_matched", False)),
        )
