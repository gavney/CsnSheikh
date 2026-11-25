"""Console notifier logging alerts directly to standard log output."""

from src.notifier.base import AlertMessage, Notifier, Priority
from src.utils.logging import get_logger

logger = get_logger("notifier.console")


class ConsoleNotifier(Notifier):
    """Outputs alerts to console with visual badges."""

    def __init__(self) -> None:
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, message: AlertMessage) -> bool:
        badge = "🚨 [URGENT ALERT]" if message.priority == Priority.URGENT else "📢 [ALERT]"
        print(f"\n{badge} [{message.rule_id}] Table: {message.table_id}\n{message.text}\n")
        logger.info("[Console Alert] %s on table %s: %s", message.rule_id, message.table_id, message.alert_id)
        return True
