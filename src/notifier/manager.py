"""Notification Manager orchestrating delivery channels, deduplication, and system alerts."""

from typing import Dict, List, Optional
from src.config.models import AppConfig
from src.notifier.base import AlertMessage, Notifier, Priority
from src.notifier.console import ConsoleNotifier
from src.notifier.formatter import AlertFormatter
from src.notifier.telegram import TelegramNotifier
from src.rules.base import TriggeredAlert
from src.store.repository import AlertRepository
from src.utils.logging import get_logger

logger = get_logger("notifier.manager")


class NotificationManager:
    """Dispatches alerts to configured channels with deduplication and priority routing."""

    def __init__(
        self,
        config: AppConfig,
        alert_repo: Optional[AlertRepository] = None,
        custom_telegram: Optional[TelegramNotifier] = None,
        custom_console: Optional[ConsoleNotifier] = None,
    ) -> None:
        self.config = config
        self.alert_repo = alert_repo
        self.telegram = custom_telegram or TelegramNotifier(
            bot_token=config.telegram_bot_token,
            default_chat_id=config.telegram_chat_id,
            admin_chat_id=config.telegram_admin_chat_id,
        )
        self.console = custom_console or ConsoleNotifier()
        self._sent_alert_keys = set()
        self._running = False

    async def start(self) -> None:
        """Start all underlying notifiers."""
        self._running = True
        await self.console.start()
        await self.telegram.start()
        logger.info("NotificationManager active.")

    async def stop(self) -> None:
        """Stop all underlying notifiers."""
        self._running = False
        await self.telegram.stop()
        await self.console.stop()
        logger.info("NotificationManager stopped.")

    async def dispatch_rule_alert(
        self,
        alert: TriggeredAlert,
        table_name: str,
        table_url: Optional[str] = None,
    ) -> bool:
        """Format and dispatch a rule alert based on its action targets."""
        # 1. Deduplication key
        dedup_key = f"{alert.rule_id}_{alert.table_id}_{alert.round_id}_{alert.level}"
        if dedup_key in self._sent_alert_keys:
            logger.debug("Duplicate alert '%s' suppressed by NotificationManager", dedup_key)
            return False

        # 2. Priority calculation
        is_urgent = ("telegram_urgent" in alert.actions) or (alert.level is not None and alert.level >= 10)
        priority = Priority.URGENT if is_urgent else Priority.NORMAL

        # 3. Format message
        text = AlertFormatter.format_rule_alert(
            table_name=table_name,
            table_id=alert.table_id,
            rule_id=alert.rule_id,
            level=alert.level,
            metric=alert.metric_value,
            round_id=alert.round_id,
            priority=priority,
            details=alert.details,
            is_end=alert.is_end_notification,
            table_url=table_url,
        )

        message = AlertMessage(
            priority=priority,
            alert_id=dedup_key,
            rule_id=alert.rule_id,
            table_id=alert.table_id,
            level=alert.level,
            text=text,
        )

        # 4. Dispatch to channels
        actions = set(alert.actions)
        if "telegram" in actions or "telegram_urgent" in actions:
            await self.telegram.send(message)

        # Always log to console notifier
        await self.console.send(message)

        # 5. Persist to SQLite
        self._sent_alert_keys.add(dedup_key)
        if self.alert_repo:
            await self.alert_repo.log_alert(
                alert_id=dedup_key,
                rule_id=alert.rule_id,
                table_id=alert.table_id,
                level=str(alert.level or "info"),
                message=text,
                round_id=alert.round_id,
            )

        return True

    async def dispatch_system_alert(
        self,
        level: str,
        text: str,
        priority: Priority = Priority.NORMAL
    ) -> bool:
        """Send system alert (connection drops, watchdog triggers, auth issues, heartbeat)."""
        formatted = AlertFormatter.format_system_alert(level, text)
        msg = AlertMessage(
            priority=priority,
            alert_id=f"system_{level}_{int(priority)}",
            rule_id="system",
            table_id="system",
            text=formatted,
            is_system=True,
        )
        await self.console.send(msg)
        return await self.telegram.send(msg)
