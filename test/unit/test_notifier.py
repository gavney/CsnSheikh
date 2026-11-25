"""Unit tests for TelegramNotifier, PriorityQueue ordering, 429 retry_after, and deduplication."""

import asyncio
from typing import List
import httpx
import pytest

from src.config.models import AppConfig
from src.notifier.base import AlertMessage, Priority
from src.notifier.formatter import AlertFormatter
from src.notifier.manager import NotificationManager
from src.notifier.telegram import TelegramNotifier
from src.rules.base import TriggeredAlert
from src.store.db import Database
from src.store.repository import AlertRepository


@pytest.mark.asyncio
async def test_priority_queue_urgent_delivered_before_normal():
    """Verify that URGENT priority messages are delivered before NORMAL messages."""
    delivery_order: List[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        import json
        payload = json.loads(request.content)
        text = payload.get("text", "")
        delivery_order.append(text)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(delivery_order)}})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    notifier = TelegramNotifier(
        bot_token="test_bot_token",
        default_chat_id="123456",
        rate_limit_delay_sec=0.01,
        http_client=mock_client,
    )

    await notifier.start()

    # Enqueue NORMAL first, then URGENT, then LOW
    msg_normal = AlertMessage(priority=Priority.NORMAL, alert_id="norm", text="NORMAL_MESSAGE")
    msg_urgent = AlertMessage(priority=Priority.URGENT, alert_id="urg", text="URGENT_MESSAGE")
    msg_low = AlertMessage(priority=Priority.LOW, alert_id="low", text="LOW_MESSAGE")

    await notifier.send(msg_normal)
    await notifier.send(msg_urgent)
    await notifier.send(msg_low)

    # Wait for delivery
    max_wait = 2.0
    elapsed = 0.0
    while len(delivery_order) < 3 and elapsed < max_wait:
        await asyncio.sleep(0.05)
        elapsed += 0.05

    await notifier.stop()
    await mock_client.aclose()

    assert len(delivery_order) == 3
    # URGENT must be delivered before NORMAL and LOW!
    assert delivery_order[0] == "URGENT_MESSAGE"
    assert delivery_order[1] == "NORMAL_MESSAGE"
    assert delivery_order[2] == "LOW_MESSAGE"


@pytest.mark.asyncio
async def test_http_429_retry_after_handled_without_loss():
    """Simulate HTTP 429 Too Many Requests: verify pause and successful retry."""
    call_count = 0

    def rate_limit_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Return HTTP 429 on first attempt with retry_after 1 second
            return httpx.Response(
                429,
                json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 1}}
            )
        # Success on second attempt
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 999}})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(rate_limit_handler))
    notifier = TelegramNotifier(
        bot_token="test_bot_token",
        default_chat_id="123456",
        rate_limit_delay_sec=0.01,
        http_client=mock_client,
    )

    await notifier.start()
    msg = AlertMessage(priority=Priority.NORMAL, alert_id="a1", text="RETRY_TEST")
    await notifier.send(msg)

    # Wait for retry to succeed (takes ~1s)
    await asyncio.sleep(1.5)
    await notifier.stop()
    await mock_client.aclose()

    assert call_count == 2, f"Expected 2 attempts (first 429, then 200 OK), got {call_count}"


@pytest.mark.asyncio
async def test_notification_manager_deduplication(tmp_path):
    """Verify that duplicate alerts are suppressed by NotificationManager and recorded in SQLite."""
    db_path = tmp_path / "alerts_test.db"
    db = Database(db_path)
    conn = await db.connect()
    alert_repo = AlertRepository(conn)

    config = AppConfig()
    manager = NotificationManager(config, alert_repo=alert_repo)
    await manager.start()

    alert1 = TriggeredAlert(
        rule_id="loss_streak",
        table_id="bc281",
        level=4,
        actions=["telegram"],
        metric_value=4.0,
        round_id="round_123",
        message="Loss streak 4",
        details={"streak": 4, "of": "loss", "last_outcomes": ["B", "B", "B", "B"]},
    )

    # First dispatch: must succeed
    dispatched1 = await manager.dispatch_rule_alert(alert1, "Korean Speed Baccarat 1")
    assert dispatched1 is True

    # Duplicate dispatch with identical key: must be suppressed
    dispatched2 = await manager.dispatch_rule_alert(alert1, "Korean Speed Baccarat 1")
    assert dispatched2 is False

    await manager.stop()
    await db.close()


def test_formatter_markdown_output():
    """Verify alert formatting with emojis, hyperlinks, and concise text."""
    formatted = AlertFormatter.format_rule_alert(
        table_name="Korean Speed Baccarat 1",
        table_id="bc281",
        rule_id="loss_streak",
        level=10,
        metric=10.0,
        round_id="round_999",
        priority=Priority.URGENT,
        details={"streak": 10, "of": "loss", "perspective": "player", "last_outcomes": ["B", "B", "T", "B"]},
        is_end=False,
        table_url="https://bxgnanhldtnm.top/ru/casino/game/68018/speed-baccarat-16-korean",
    )

    assert "🚨 *СРОЧНОЕ ОПОВЕЩЕНИЕ*" in formatted
    assert "[Порог 10]" in formatted
    assert "[Korean Speed Baccarat 1](https://bxgnanhldtnm.top/ru/casino/game/68018/speed-baccarat-16-korean)" in formatted
    assert "Банкир 10 подряд" in formatted
    assert "🔴🔴🟢🔴" in formatted  # emoji outcomes
    assert "#round_999" not in formatted  # round number removed
    assert "Правило" not in formatted  # rule line removed
