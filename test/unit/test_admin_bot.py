"""Unit tests for TelegramAdminBot and dynamic configuration controls."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
import httpx

from src.cli.supervisor import Supervisor
from src.config.loader import load_config, save_config
from src.config.models import AppConfig, RuleConfig, RuleLevelConfig, TableConfig
from src.notifier.admin_bot import TelegramAdminBot
from src.parser.models import Outcome, RoundResult
from src.rules.engine import RuleEngine
from src.state.window import SlidingWindow


@pytest.fixture
def mock_supervisor(tmp_path: Path):
    """Create a minimal mock supervisor with in-memory config."""
    cfg_file = tmp_path / "config.yaml"
    config = AppConfig(
        tables=[
            TableConfig(id="t1", name="Table One", enabled=True, url="http://t1"),
            TableConfig(id="t2", name="Table Two", enabled=True, url="http://t2"),
        ],
        rules=[
            RuleConfig(
                id="player_streak_all",
                name="Игрок 4 победы подряд",
                enabled=True,
                tables="all",
                perspective="player",
                tie_policy="ignore",
                condition={"type": "streak", "of": "player", "window": 30},
                levels=[RuleLevelConfig(at=4, actions=["telegram"])],
                rearm="on_break",
            )
        ],
        telegram_bot_token="test_token_123",
        bot_admin_password="SuperSecretPassword123!",
    )
    save_config(config, cfg_file)

    sup = MagicMock()
    sup.config = config
    sup.config_path = cfg_file
    sup.clients = {"t1": MagicMock(is_connected=True), "t2": MagicMock(is_connected=True)}
    sup.windows = {"t1": SlidingWindow(), "t2": SlidingWindow()}
    sup.rule_engine = RuleEngine(config.rules)

    # Attach real methods from Supervisor class
    sup.get_global_streak = Supervisor.get_global_streak.__get__(sup)
    sup.get_table_streak = Supervisor.get_table_streak.__get__(sup)
    sup.set_global_streak = Supervisor.set_global_streak.__get__(sup)
    sup.set_table_streak = Supervisor.set_table_streak.__get__(sup)
    sup.reset_table_to_global = Supervisor.reset_table_to_global.__get__(sup)
    sup.toggle_table = Supervisor.toggle_table.__get__(sup)
    sup.reload_rules = Supervisor.reload_rules.__get__(sup)

    return sup


@pytest.mark.asyncio
async def test_admin_bot_password_authentication():
    """Verify that only the correct password authenticates the user in private chat."""
    bot = TelegramAdminBot(
        bot_token="test_token",
        admin_password="CorrectPassword123!",
    )
    bot.send_message = AsyncMock()
    bot.delete_message = AsyncMock()
    bot._send_main_menu = AsyncMock()

    # 1. Ignore non-private chats
    group_msg = {
        "message_id": 1,
        "chat": {"id": 100, "type": "group"},
        "from": {"id": 100},
        "text": "CorrectPassword123!",
    }
    await bot._handle_message(group_msg)
    assert 100 not in bot._auth_sessions
    assert bot.send_message.call_count == 0

    # 2. Wrong password in private chat
    wrong_msg = {
        "message_id": 2,
        "chat": {"id": 200, "type": "private"},
        "from": {"id": 200},
        "text": "WrongPassword",
    }
    await bot._handle_message(wrong_msg)
    assert 200 not in bot._auth_sessions
    bot.send_message.assert_called_with(
        200,
        "🔒 *Доступ ограничен!*\n\nДля управления мониторингом и настройки столов введите пароль администратора:"
    )

    # 3. Correct password in private chat
    correct_msg = {
        "message_id": 3,
        "chat": {"id": 200, "type": "private"},
        "from": {"id": 200},
        "text": "CorrectPassword123!",
    }
    await bot._handle_message(correct_msg)
    assert 200 in bot._auth_sessions
    bot.delete_message.assert_called_with(200, 3)
    bot._send_main_menu.assert_called_with(200)

    # 4. /logout command
    logout_msg = {
        "message_id": 4,
        "chat": {"id": 200, "type": "private"},
        "from": {"id": 200},
        "text": "/logout",
    }
    await bot._handle_message(logout_msg)
    assert 200 not in bot._auth_sessions


@pytest.mark.asyncio
async def test_admin_bot_set_global_streak(mock_supervisor):
    """Verify that clicking button to change global streak updates config and reloads engine."""
    bot = TelegramAdminBot(
        bot_token="test_token",
        admin_password="SuperSecretPassword123!",
        supervisor=mock_supervisor,
    )
    bot.answer_callback = AsyncMock()
    bot.edit_message = AsyncMock()
    bot._auth_sessions.add(999)  # Authenticated user

    # User clicks 's_all:5' (Streak 5 for all tables)
    cq = {
        "id": "cq_1",
        "from": {"id": 999},
        "message": {"message_id": 10, "chat": {"id": 999}},
        "data": "s_all:5",
    }
    await bot._handle_callback_query(cq)

    bot.answer_callback.assert_called_with("cq_1", "✅ Для всех столов установлено 5 побед!")
    assert mock_supervisor.get_global_streak() == 5
    assert mock_supervisor.config.rules[0].levels[0].at == 5


@pytest.mark.asyncio
async def test_admin_bot_set_and_reset_table_streak(mock_supervisor):
    """Verify individual table streak setting and resetting back to global."""
    bot = TelegramAdminBot(
        bot_token="test_token",
        supervisor=mock_supervisor,
    )
    bot.answer_callback = AsyncMock()
    bot.edit_message = AsyncMock()
    bot._auth_sessions.add(999)

    # Initial state: t1 has no custom streak
    assert mock_supervisor.get_table_streak("t1") is None

    # 1. Set t1 streak to 6: 'st:t1:6'
    cq_set = {
        "id": "cq_2",
        "from": {"id": 999},
        "message": {"message_id": 11, "chat": {"id": 999}},
        "data": "st:t1:6",
    }
    await bot._handle_callback_query(cq_set)
    assert mock_supervisor.get_table_streak("t1") == 6
    assert mock_supervisor.get_global_streak() == 4  # Global remains 4

    # 2. Reset t1 to global: 'rt:t1'
    cq_reset = {
        "id": "cq_3",
        "from": {"id": 999},
        "message": {"message_id": 12, "chat": {"id": 999}},
        "data": "rt:t1",
    }
    await bot._handle_callback_query(cq_reset)
    assert mock_supervisor.get_table_streak("t1") is None


@pytest.mark.asyncio
async def test_rule_engine_override_behavior():
    """Verify that a specific table rule overrides the global rule for that table only."""
    rules = [
        RuleConfig(
            id="global_rule",
            name="Игрок 4 победы подряд",
            enabled=True,
            tables="all",
            condition={"type": "streak", "of": "player", "window": 30},
            levels=[RuleLevelConfig(at=4, actions=["telegram"])],
            rearm="on_break",
        ),
        RuleConfig(
            id="t1_rule",
            name="Игрок 6 побед подряд [t1]",
            enabled=True,
            tables=["t1"],
            condition={"type": "streak", "of": "player", "window": 30},
            levels=[RuleLevelConfig(at=6, actions=["telegram"])],
            rearm="on_break",
        ),
    ]

    engine = RuleEngine(rules)

    # Create 4 consecutive player rounds for t1
    window_t1 = SlidingWindow()
    for i in range(1, 5):
        window_t1.append(RoundResult(round_id=str(i), table_id="t1", result=Outcome.PLAYER, score=9))

    latest_round = window_t1.get_latest()
    alerts_t1 = await engine.evaluate_round("t1", window_t1, latest_round)
    # Global rule should NOT trigger for t1 because t1 has its own rule (at=6), so 0 alerts at streak=4!
    assert len(alerts_t1) == 0

    # For table t2 (which has no override), 4 player wins SHOULD trigger the global rule!
    window_t2 = SlidingWindow()
    for i in range(1, 5):
        window_t2.append(RoundResult(round_id=str(i), table_id="t2", result=Outcome.PLAYER, score=9))
    alerts_t2 = await engine.evaluate_round("t2", window_t2, window_t2.get_latest())
    assert len(alerts_t2) == 1
    assert alerts_t2[0].rule_id == "global_rule"
    assert alerts_t2[0].level == 4


@pytest.mark.asyncio
async def test_admin_bot_toggle_table(mock_supervisor):
    """Verify toggling table enabled/disabled state."""
    bot = TelegramAdminBot(
        bot_token="test_token",
        supervisor=mock_supervisor,
    )
    bot.answer_callback = AsyncMock()
    bot.edit_message = AsyncMock()
    bot._auth_sessions.add(999)

    assert mock_supervisor.config.tables[0].enabled is True

    cq = {
        "id": "cq_4",
        "from": {"id": 999},
        "message": {"message_id": 13, "chat": {"id": 999}},
        "data": "tg:t1",
    }
    await bot._handle_callback_query(cq)
    assert mock_supervisor.config.tables[0].enabled is False

    await bot._handle_callback_query(cq)
    assert mock_supervisor.config.tables[0].enabled is True


@pytest.mark.asyncio
async def test_admin_bot_manual_number_input_all_tables(mock_supervisor):
    """Verify entering an arbitrary number from 1 to N for all tables."""
    bot = TelegramAdminBot(
        bot_token="test_token",
        supervisor=mock_supervisor,
    )
    bot.send_message = AsyncMock()
    bot.edit_message = AsyncMock()
    bot.answer_callback = AsyncMock()
    bot._auth_sessions.add(999)

    # 1. User clicks button to enter custom number for all tables
    cq = {
        "id": "cq_inp_all",
        "from": {"id": 999},
        "message": {"message_id": 20, "chat": {"id": 999}},
        "data": "inp:all",
    }
    await bot._handle_callback_query(cq)
    assert 999 in bot._waiting_input
    assert bot._waiting_input[999]["target"] == "all"
    bot.edit_message.assert_called()

    # 2. User sends number '14' in chat
    msg = {
        "message_id": 21,
        "chat": {"id": 999, "type": "private"},
        "from": {"id": 999},
        "text": "14",
    }
    await bot._handle_message(msg)
    assert 999 not in bot._waiting_input
    assert mock_supervisor.get_global_streak() == 14
    assert mock_supervisor.config.rules[0].levels[0].at == 14
    # Condition window should be scaled
    assert mock_supervisor.config.rules[0].condition["window"] >= 30


@pytest.mark.asyncio
async def test_admin_bot_manual_number_input_specific_table(mock_supervisor):
    """Verify entering an arbitrary number from 1 to N for a specific table."""
    bot = TelegramAdminBot(
        bot_token="test_token",
        supervisor=mock_supervisor,
    )
    bot.send_message = AsyncMock()
    bot.edit_message = AsyncMock()
    bot.answer_callback = AsyncMock()
    bot._auth_sessions.add(999)

    # 1. User clicks button to enter custom number for table 't1'
    cq = {
        "id": "cq_inp_t1",
        "from": {"id": 999},
        "message": {"message_id": 22, "chat": {"id": 999}},
        "data": "inp:t:t1",
    }
    await bot._handle_callback_query(cq)
    assert 999 in bot._waiting_input
    assert bot._waiting_input[999]["target"] == "table"
    assert bot._waiting_input[999]["table_id"] == "t1"

    # 2. User sends number '1' in chat (1 win threshold)
    msg = {
        "message_id": 23,
        "chat": {"id": 999, "type": "private"},
        "from": {"id": 999},
        "text": "1",
    }
    await bot._handle_message(msg)
    assert 999 not in bot._waiting_input
    assert mock_supervisor.get_table_streak("t1") == 1
    assert mock_supervisor.get_global_streak() == 4  # Global remains unchanged


@pytest.mark.asyncio
async def test_admin_bot_direct_number_interactive_prompt(mock_supervisor):
    """Verify sending a number directly without entering input mode first."""
    bot = TelegramAdminBot(
        bot_token="test_token",
        supervisor=mock_supervisor,
    )
    bot.send_message = AsyncMock()
    bot._auth_sessions.add(999)
    bot._last_viewed_table[999] = "t2"

    # User simply sends '8'
    msg = {
        "message_id": 24,
        "chat": {"id": 999, "type": "private"},
        "from": {"id": 999},
        "text": "8",
    }
    await bot._handle_message(msg)
    bot.send_message.assert_called()
    call_args = bot.send_message.call_args[0]
    assert "Вы отправили число *8*" in call_args[1]
    reply_markup = bot.send_message.call_args[1]["reply_markup"]
    assert any("s_all:8" in btn["callback_data"] for row in reply_markup["inline_keyboard"] for btn in row)
    assert any("st:t2:8" in btn["callback_data"] for row in reply_markup["inline_keyboard"] for btn in row)


@pytest.mark.asyncio
async def test_admin_bot_input_cancellation(mock_supervisor):
    """Verify input cancellation via /cancel command and callback query."""
    bot = TelegramAdminBot(
        bot_token="test_token",
        supervisor=mock_supervisor,
    )
    bot.send_message = AsyncMock()
    bot.edit_message = AsyncMock()
    bot.answer_callback = AsyncMock()
    bot._auth_sessions.add(999)

    # 1. Set waiting input
    bot._waiting_input[999] = {"target": "all"}
    msg_cancel = {
        "message_id": 25,
        "chat": {"id": 999, "type": "private"},
        "from": {"id": 999},
        "text": "/cancel",
    }
    await bot._handle_message(msg_cancel)
    assert 999 not in bot._waiting_input
    bot.send_message.assert_called()

    # 2. Cancel via callback query
    bot._waiting_input[999] = {"target": "table", "table_id": "t1"}
    cq_cancel = {
        "id": "cq_canc",
        "from": {"id": 999},
        "message": {"message_id": 26, "chat": {"id": 999}},
        "data": "cancel_inp:t:t1",
    }
    await bot._handle_callback_query(cq_cancel)
    assert 999 not in bot._waiting_input


@pytest.mark.asyncio
async def test_admin_bot_invalid_number_validation(mock_supervisor):
    """Verify rejection of non-positive numbers or values exceeding maximum."""
    bot = TelegramAdminBot(
        bot_token="test_token",
        supervisor=mock_supervisor,
    )
    bot.send_message = AsyncMock()
    bot._auth_sessions.add(999)
    bot._waiting_input[999] = {"target": "all"}

    # 1. 0 is rejected
    await bot._handle_message({"message_id": 27, "chat": {"id": 999, "type": "private"}, "from": {"id": 999}, "text": "0"})
    assert 999 in bot._waiting_input  # Still waiting
    assert "не меньше 1" in bot.send_message.call_args[0][1]

    # 2. Non-numeric text
    await bot._handle_message({"message_id": 28, "chat": {"id": 999, "type": "private"}, "from": {"id": 999}, "text": "abc"})
    assert 999 in bot._waiting_input
    assert "целое число" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_admin_bot_logs_and_error_display(mock_supervisor):
    """Verify logs menu, /logs command, /errors, and error diagnostics in status."""
    from src.utils.logging import get_logger
    logger = get_logger("test.bot.logs")
    logger.error("Database connection refused for table t1")

    bot = TelegramAdminBot(
        bot_token="test_token",
        supervisor=mock_supervisor,
    )
    bot.send_message = AsyncMock()
    bot.edit_message = AsyncMock()
    bot.answer_callback = AsyncMock()
    bot.send_document = AsyncMock()
    bot._auth_sessions.add(999)

    # 1. /logs command
    await bot._handle_message({"message_id": 29, "chat": {"id": 999, "type": "private"}, "from": {"id": 999}, "text": "/logs"})
    bot.send_message.assert_called()
    assert "Последние" in bot.send_message.call_args[0][1]

    # 2. /errors command
    await bot._handle_message({"message_id": 30, "chat": {"id": 999, "type": "private"}, "from": {"id": 999}, "text": "/errors"})
    assert "ошибки" in bot.send_message.call_args[0][1].lower()

    # 3. Callback act:logs
    await bot._handle_callback_query({
        "id": "cq_logs",
        "from": {"id": 999},
        "message": {"message_id": 31, "chat": {"id": 999}},
        "data": "act:logs",
    })
    bot.edit_message.assert_called()

    # 4. Status menu includes error detail if client has last_error
    mock_client = mock_supervisor.clients["t1"]
    mock_client.is_connected = False
    mock_client.reconnect_count = 5
    mock_client.last_error = "TimeoutError: proxy handshake failed"

    await bot._handle_callback_query({
        "id": "cq_status",
        "from": {"id": 999},
        "message": {"message_id": 32, "chat": {"id": 999}},
        "data": "act:status",
    })
    status_text = bot.edit_message.call_args[0][2]
    assert "connecting (#5)" in status_text or "connecting" in status_text
    assert "TimeoutError" in status_text

    # 5. Download log document
    await bot._handle_callback_query({
        "id": "cq_dl",
        "from": {"id": 999},
        "message": {"message_id": 33, "chat": {"id": 999}},
        "data": "act:logs_download",
    })
    bot.send_document.assert_called()

