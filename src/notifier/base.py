"""Base models and interfaces for notification channels."""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class Priority(IntEnum):
    """Message priority for queuing. Lower integer value = higher priority."""
    URGENT = 1
    NORMAL = 2
    LOW = 3


@dataclass(order=True)
class AlertMessage:
    """Prioritized alert message ready for delivery."""
    # Priority and created_at come first for heapq / PriorityQueue ordering
    priority: Priority
    created_at: float = field(default_factory=time.time)
    
    # Non-compared fields
    alert_id: str = field(compare=False, default="")
    rule_id: str = field(compare=False, default="")
    table_id: str = field(compare=False, default="")
    level: Optional[int] = field(compare=False, default=None)
    text: str = field(compare=False, default="")
    chat_id: Optional[str] = field(compare=False, default=None)
    is_system: bool = field(compare=False, default=False)
    attempts: int = field(compare=False, default=0)


class Notifier(ABC):
    """Abstract notification delivery provider."""

    @abstractmethod
    async def send(self, message: AlertMessage) -> bool:
        """Enqueue or send alert message."""
        pass

    @abstractmethod
    async def start(self) -> None:
        """Start background queue workers."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully drain queue and stop workers."""
        pass
