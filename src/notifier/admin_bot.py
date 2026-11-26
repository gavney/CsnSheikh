"""Interactive Telegram Admin Bot for dynamic configuration, streak thresholds and table management."""

import asyncio
import time
from typing import Any, Dict, List, Optional, Set
import httpx

from src.utils.logging import get_logger, mask_token

logger = get_logger("notifier.admin_bot")


class TelegramAdminBot:
    """Asynchronous polling Telegram Bot providing interactive inline menus for admin configuration."""

    def __init__(
        self,
        bot_token: Optional[str],
        admin_password: str = "CasinoBoss2026!Strict#Admin",
        supervisor: Any = None,
        base_url: str = "https://api.telegram.org",
        poll_interval_sec: float = 1.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.bot_token = bot_token
        self.admin_password = admin_password
        self.supervisor = supervisor
        self.base_url = base_url.rstrip("/")
        self.poll_interval_sec = poll_interval_sec

        self._custom_client = http_client
        self._client: Optional[httpx.AsyncClient] = None
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._auth_sessions: Set[int] = set()
        self._last_update_id: int = 0
        self._waiting_input: Dict[int, Dict[str, Any]] = {}
        self._last_viewed_table: Dict[int, str] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the Telegram long-polling worker."""
        if not self.bot_token:
            logger.info("TelegramAdminBot disabled: no TELEGRAM_BOT_TOKEN provided.")
            return
        if self._running:
            return

        self._running = True
        self._client = self._custom_client or httpx.AsyncClient(timeout=30.0, trust_env=False, proxy=None)
        self._poll_task = asyncio.create_task(self._polling_loop(), name="tg_admin_bot_poll")
        logger.info("TelegramAdminBot started polling (admin password protected).")

    async def stop(self) -> None:
        """Stop polling and close HTTP client."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._client and not self._custom_client:
            await self._client.aclose()
            self._client = None
        logger.info("TelegramAdminBot stopped.")

    # -------------------------------------------------------------------------
    # Telegram API Low-Level Wrappers
    # -------------------------------------------------------------------------

    async def _api_call(self, method: str, json_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Perform an HTTP POST to Telegram Bot API."""
        if not self._client or not self.bot_token:
            return None
        url = f"{self.base_url}/bot{self.bot_token}/{method}"
        try:
            resp = await self._client.post(url, json=json_data)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 400 and "message is not modified" in resp.text:
                return None
            elif resp.status_code == 400 and ("can't parse entities" in resp.text or "entity" in resp.text.lower()):
                # Fallback without parse_mode if markdown formatting caused an error
                if "parse_mode" in json_data:
                    retry_data = dict(json_data)
                    del retry_data["parse_mode"]
                    logger.warning("Telegram API %s entity parse error, retrying without parse_mode", method)
                    retry_resp = await self._client.post(url, json=retry_data)
                    if retry_resp.status_code == 200:
                        return retry_resp.json()
                logger.debug("Telegram API %s returned status %d: %s", method, resp.status_code, resp.text[:150])
                return None
            else:
                logger.debug("Telegram API %s returned status %d: %s", method, resp.status_code, resp.text[:150])
                return None
        except Exception as e:
            logger.debug("Telegram API %s call failed: %s", method, e)
            return None

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "Markdown",
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self._api_call("sendMessage", payload)

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "Markdown",
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self._api_call("editMessageText", payload)

    async def answer_callback(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        alert: bool = False,
    ) -> None:
        payload: Dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": alert,
        }
        if text:
            payload["text"] = text
        await self._api_call("answerCallbackQuery", payload)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        await self._api_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    # -------------------------------------------------------------------------
    # Long Polling Loop
    # -------------------------------------------------------------------------

    async def _polling_loop(self) -> None:
        """Fetch updates in loop and dispatch to handlers."""
        while self._running:
            try:
                updates = await self._fetch_updates()
                for upd in updates:
                    upd_id = upd.get("update_id", 0)
                    if upd_id >= self._last_update_id:
                        self._last_update_id = upd_id + 1

                    if "message" in upd:
                        await self._handle_message(upd["message"])
                    elif "callback_query" in upd:
                        await self._handle_callback_query(upd["callback_query"])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in TelegramAdminBot polling loop: %s", e)
                await asyncio.sleep(self.poll_interval_sec)

    async def _fetch_updates(self) -> List[Dict[str, Any]]:
        """Fetch unconfirmed updates via getUpdates."""
        payload: Dict[str, Any] = {
            "offset": self._last_update_id,
            "timeout": 20,
            "allowed_updates": ["message", "callback_query"],
        }
        res = await self._api_call("getUpdates", payload)
        if res and res.get("ok"):
            return res.get("result", [])
        return []

    # -------------------------------------------------------------------------
    # Message Dispatcher & Password Authentication
    # -------------------------------------------------------------------------

    async def _handle_message(self, msg: Dict[str, Any]) -> None:
        """Handle incoming text messages from users in private chat."""
        chat = msg.get("chat", {})
        chat_type = chat.get("type", "")
        # Only accept private messages for admin controls
        if chat_type != "private":
            return

        chat_id = chat.get("id")
        user_id = msg.get("from", {}).get("id", chat_id)
        text = (msg.get("text") or "").strip()
        msg_id = msg.get("message_id")

        if not chat_id:
            return

        # Check /logout
        if text == "/logout":
            self._auth_sessions.discard(user_id)
            await self.send_message(chat_id, "🔒 Вы вышли из панели управления. Для входа отправьте пароль.")
            return

        # If user is NOT authenticated
        if user_id not in self._auth_sessions:
            if text == self.admin_password:
                self._auth_sessions.add(user_id)
                # Attempt to delete the message containing plaintext password
                if msg_id:
                    try:
                        await self.delete_message(chat_id, msg_id)
                    except Exception:
                        pass
                await self.send_message(
                    chat_id,
                    "✅ *Авторизация успешна!*\nДобро пожаловать в панель управления Casino Monitor."
                )
                await self._send_main_menu(chat_id)
            else:
                await self.send_message(
                    chat_id,
                    "🔒 *Доступ ограничен!*\n\n"
                    "Для управления мониторингом и настройки столов введите пароль администратора:"
                )
            return

        # If user IS authenticated
        # 1. Handle cancellation of input
        if text.lower() in ("/cancel", "отмена", "cancel"):
            inp_state = self._waiting_input.pop(user_id, None)
            if inp_state:
                await self.send_message(chat_id, "❌ Ввод числа отменен.")
                if inp_state.get("target") == "table" and "table_id" in inp_state:
                    await self._send_table_menu(chat_id, inp_state["table_id"])
                else:
                    await self._send_all_tables_menu(chat_id)
            else:
                await self._send_main_menu(chat_id)
            return

        # 2. Handle number input (1 to N)
        try:
            val = int(text)
            is_int = True
        except ValueError:
            is_int = False

        if is_int:
            if val < 1:
                await self.send_message(chat_id, "⚠️ Число должно быть не меньше 1 (от 1 до N). Попробуйте еще раз:")
                return
            if val > 100:
                await self.send_message(chat_id, "⚠️ Число слишком большое (максимум 100). Попробуйте еще раз:")
                return

            if user_id in self._waiting_input:
                inp_state = self._waiting_input.pop(user_id)
                if inp_state.get("target") == "table":
                    tbl_id = inp_state.get("table_id", "")
                    tbl_name = tbl_id
                    if self.supervisor:
                        for t in self.supervisor.config.tables:
                            if t.id == tbl_id:
                                tbl_name = t.name
                                break
                        await self.supervisor.set_table_streak(tbl_id, val)
                    await self.send_message(
                        chat_id,
                        f"✅ *Порог успешно установлен!*\nДля стола *{tbl_name}*: 🔥 *Игрок {val} побед подряд*."
                    )
                    await self._send_table_menu(chat_id, tbl_id)
                else:
                    if self.supervisor:
                        await self.supervisor.set_global_streak(val)
                    await self.send_message(
                        chat_id,
                        f"✅ *Порог успешно установлен!*\nДля *ВСЕХ столов*: 🔥 *Игрок {val} побед подряд*."
                    )
                    await self._send_all_tables_menu(chat_id)
                return
            else:
                # Direct number sent without clicking input button
                last_tbl = self._last_viewed_table.get(user_id)
                kb_rows = [
                    [{"text": f"🌐 Для ВСЕХ столов ({val} подряд)", "callback_data": f"s_all:{val}"}],
                ]
                if last_tbl:
                    t_name = last_tbl
                    if self.supervisor:
                        for t in self.supervisor.config.tables:
                            if t.id == last_tbl:
                                t_name = t.name
                                break
                    kb_rows.append([{"text": f"🎯 Для стола {t_name} ({val} подряд)", "callback_data": f"st:{last_tbl}:{val}"}])
                kb_rows.append([{"text": "📋 Выбрать другой стол", "callback_data": f"pick_tbl:{val}"}])
                kb_rows.append([{"text": "❌ Отмена", "callback_data": "m:main"}])
                await self.send_message(
                    chat_id,
                    f"🔢 Вы отправили число *{val}*.\nКуда вы хотите применить порог *Игрок {val} побед подряд*?",
                    reply_markup={"inline_keyboard": kb_rows}
                )
                return

        # 3. If user was in waiting mode and typed non-integer
        if user_id in self._waiting_input:
            await self.send_message(
                chat_id,
                "⚠️ Пожалуйста, отправьте целое число от *1 до N* (или отправьте /cancel для отмены):"
            )
            return

        # 4. Standard commands
        await self._send_main_menu(chat_id)

    # -------------------------------------------------------------------------
    # Callback Query Dispatcher (Button Clicks)
    # -------------------------------------------------------------------------

    async def _handle_callback_query(self, cq: Dict[str, Any]) -> None:
        """Handle inline button clicks."""
        cq_id = cq.get("id", "")
        user_id = cq.get("from", {}).get("id")
        msg = cq.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        msg_id = msg.get("message_id")
        data = cq.get("data", "")

        if not chat_id or not msg_id or not user_id:
            return

        # Verify authentication
        if user_id not in self._auth_sessions:
            await self.answer_callback(cq_id, "🔒 Доступ закрыт. Введите пароль в чат.", alert=True)
            return

        # 1. Navigation / Menus
        if data == "m:main":
            self._waiting_input.pop(user_id, None)
            await self.answer_callback(cq_id)
            await self._render_main_menu(chat_id, msg_id)
            return

        if data == "m:all":
            self._waiting_input.pop(user_id, None)
            self._last_viewed_table.pop(user_id, None)
            await self.answer_callback(cq_id)
            await self._render_all_tables_menu(chat_id, msg_id)
            return

        if data.startswith("m:tables"):
            page = 0
            if ":" in data[8:]:
                try:
                    page = int(data.split(":")[-1])
                except ValueError:
                    page = 0
            await self.answer_callback(cq_id)
            await self._render_tables_list_menu(chat_id, msg_id, page=page)
            return

        if data.startswith("t:"):
            table_id = data[2:]
            self._waiting_input.pop(user_id, None)
            self._last_viewed_table[user_id] = table_id
            await self.answer_callback(cq_id)
            await self._render_table_menu(chat_id, msg_id, table_id)
            return

        # 1.1 Input Mode Prompts
        if data == "inp:all":
            self._waiting_input[user_id] = {"target": "all"}
            await self.answer_callback(cq_id)
            text = (
                "✏️ *Ввод порога для ВСЕХ столов*\n\n"
                "Отправьте желаемое число побед подряд (*от 1 до N*, например: `1`, `5`, `12`, `20`) "
                "сообщением в этот чат:"
            )
            kb = {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "cancel_inp:all"}]]}
            await self.edit_message(chat_id, msg_id, text, reply_markup=kb)
            return

        if data.startswith("inp:t:"):
            table_id = data[6:]
            self._waiting_input[user_id] = {"target": "table", "table_id": table_id}
            self._last_viewed_table[user_id] = table_id
            await self.answer_callback(cq_id)
            t_name = table_id
            if self.supervisor:
                for t in self.supervisor.config.tables:
                    if t.id == table_id:
                        t_name = t.name
                        break
            text = (
                f"✏️ *Ввод персонального порога для стола*\n\n"
                f"🎯 *Стол*: *{t_name}*\n\n"
                "Отправьте желаемое число побед подряд (*от 1 до N*, например: `1`, `6`, `14`) "
                "сообщением в этот чат:"
            )
            kb = {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": f"cancel_inp:t:{table_id}"}]]}
            await self.edit_message(chat_id, msg_id, text, reply_markup=kb)
            return

        if data == "cancel_inp:all":
            self._waiting_input.pop(user_id, None)
            await self.answer_callback(cq_id, "Ввод отменен")
            await self._render_all_tables_menu(chat_id, msg_id)
            return

        if data.startswith("cancel_inp:t:"):
            table_id = data[13:]
            self._waiting_input.pop(user_id, None)
            await self.answer_callback(cq_id, "Ввод отменен")
            await self._render_table_menu(chat_id, msg_id, table_id)
            return

        if data.startswith("pick_tbl:"):
            streak_val = int(data.split(":")[1])
            await self.answer_callback(cq_id)
            tables = self.supervisor.config.tables if self.supervisor else []
            rows = []
            for i in range(0, len(tables), 2):
                row = []
                for t in tables[i : i + 2]:
                    row.append({
                        "text": t.name.replace("Speed Baccarat", "SB").replace("Baccarat", "Bacc"),
                        "callback_data": f"st:{t.id}:{streak_val}",
                    })
                rows.append(row)
            rows.append([{"text": "❌ Отмена", "callback_data": "m:main"}])
            await self.edit_message(
                chat_id,
                msg_id,
                f"🎯 Выберите стол для установки порога *Игрок {streak_val} побед подряд*:",
                reply_markup={"inline_keyboard": rows}
            )
            return

        # 2. Actions: Set Global Streak
        if data.startswith("s_all:"):
            try:
                streak_val = int(data.split(":")[1])
                if self.supervisor:
                    await self.supervisor.set_global_streak(streak_val)
                await self.answer_callback(cq_id, f"✅ Для всех столов установлено {streak_val} побед!")
                await self._render_all_tables_menu(chat_id, msg_id)
            except Exception as e:
                logger.error("Error setting global streak: %s", e)
                await self.answer_callback(cq_id, f"Ошибка: {e}", alert=True)
            return

        # 3. Actions: Set Specific Table Streak
        if data.startswith("st:"):
            parts = data.split(":")
            if len(parts) >= 3:
                table_id = parts[1]
                streak_val = int(parts[2])
                try:
                    if self.supervisor:
                        await self.supervisor.set_table_streak(table_id, streak_val)
                    await self.answer_callback(cq_id, f"✅ Установлено: Игрок {streak_val} побед подряд!")
                    await self._render_table_menu(chat_id, msg_id, table_id)
                except Exception as e:
                    logger.error("Error setting table streak: %s", e)
                    await self.answer_callback(cq_id, f"Ошибка: {e}", alert=True)
            return

        # 4. Actions: Reset Table Streak to Global
        if data.startswith("rt:"):
            table_id = data[3:]
            try:
                if self.supervisor:
                    await self.supervisor.reset_table_to_global(table_id)
                await self.answer_callback(cq_id, "🔄 Сброшено к общему правилу!")
                await self._render_table_menu(chat_id, msg_id, table_id)
            except Exception as e:
                logger.error("Error resetting table: %s", e)
                await self.answer_callback(cq_id, f"Ошибка: {e}", alert=True)
            return

        # 5. Actions: Toggle Table Enabled/Disabled
        if data.startswith("tg:"):
            table_id = data[3:]
            try:
                new_state = False
                if self.supervisor:
                    new_state = await self.supervisor.toggle_table(table_id)
                status_txt = "включен" if new_state else "отключен"
                await self.answer_callback(cq_id, f"Стол {status_txt}!")
                await self._render_table_menu(chat_id, msg_id, table_id)
            except Exception as e:
                logger.error("Error toggling table: %s", e)
                await self.answer_callback(cq_id, f"Ошибка: {e}", alert=True)
            return

        # 6. Status and System Actions
        if data == "act:status":
            await self.answer_callback(cq_id)
            await self._render_status_menu(chat_id, msg_id)
            return

        if data == "act:refresh":
            if self.supervisor:
                asyncio.create_task(self.supervisor.trigger_proactive_refresh())
            await self.answer_callback(cq_id, "🔄 Запущен фоновый процесс обновления токена сессии!", alert=True)
            return

        if data == "act:logout":
            self._auth_sessions.discard(user_id)
            await self.answer_callback(cq_id, "Сессия завершена.")
            await self.edit_message(chat_id, msg_id, "🔒 Вы вышли из панели управления. Для входа отправьте пароль.")
            return

    # -------------------------------------------------------------------------
    # UI Renderers (Keyboards & Layouts)
    # -------------------------------------------------------------------------

    def _get_global_streak_info(self) -> int:
        if self.supervisor:
            return self.supervisor.get_global_streak()
        return 4

    async def _send_main_menu(self, chat_id: int) -> None:
        text, markup = self._build_main_menu_payload()
        await self.send_message(chat_id, text, reply_markup=markup)

    async def _render_main_menu(self, chat_id: int, message_id: int) -> None:
        text, markup = self._build_main_menu_payload()
        await self.edit_message(chat_id, message_id, text, reply_markup=markup)

    def _build_main_menu_payload(self) -> tuple[str, Dict[str, Any]]:
        global_streak = self._get_global_streak_info()
        total_tables = len(self.supervisor.config.tables) if self.supervisor else 14
        active_tables = sum(1 for t in self.supervisor.config.tables if t.enabled) if self.supervisor else 14
        custom_rules = 0
        if self.supervisor:
            custom_rules = sum(
                1 for r in self.supervisor.config.rules
                if r.enabled and r.tables != "all"
            )

        text = (
            "🎛 *Панель управления Casino Monitor*\n\n"
            f"📊 *Столов активно*: `{active_tables}/{total_tables}`\n"
            f"⚡️ *Общее условие*: `Игрок {global_streak} подряд`\n"
            f"🎯 *Индивидуальных столов*: `{custom_rules}`\n\n"
            "Выберите раздел для настройки:"
        )

        keyboard = {
            "inline_keyboard": [
                [
                    {"text": f"⚙️ Все столы ({global_streak} подряд)", "callback_data": "m:all"},
                ],
                [
                    {"text": "🎯 Отдельный стол", "callback_data": "m:tables:0"},
                ],
                [
                    {"text": "📊 Статус мониторинга", "callback_data": "act:status"},
                    {"text": "🔄 Обновить токен", "callback_data": "act:refresh"},
                ],
                [
                    {"text": "🔒 Завершить сеанс", "callback_data": "act:logout"},
                ],
            ]
        }
        return text, keyboard

    def _build_all_tables_payload(self) -> tuple[str, Dict[str, Any]]:
        global_streak = self._get_global_streak_info()
        text = (
            "⚙️ *Настройка порога для ВСЕХ столов*\n\n"
            f"Текущий глобальный порог: 🔥 *Игрок {global_streak} побед подряд*\n"
            "_Правило: ничья (Tie) игнорируется, сброс при победе Банкира._\n\n"
            "Выберите кнопку или *отправьте число от 1 до N сообщением в чат*:"
        )

        def btn(n: int):
            label = f"🔥 {n}" if n == global_streak else f"{n}"
            return {"text": f"{label} подряд", "callback_data": f"s_all:{n}"}

        keyboard = {
            "inline_keyboard": [
                [btn(1), btn(2), btn(3), btn(4)],
                [btn(5), btn(6), btn(7), btn(8)],
                [btn(9), btn(10), btn(12), btn(15)],
                [{"text": "✏️ Ввести любое число (1 - N)", "callback_data": "inp:all"}],
                [{"text": "⬅️ Назад в меню", "callback_data": "m:main"}],
            ]
        }
        return text, keyboard

    async def _render_all_tables_menu(self, chat_id: int, message_id: int) -> None:
        text, keyboard = self._build_all_tables_payload()
        await self.edit_message(chat_id, message_id, text, reply_markup=keyboard)

    async def _send_all_tables_menu(self, chat_id: int) -> None:
        text, keyboard = self._build_all_tables_payload()
        await self.send_message(chat_id, text, reply_markup=keyboard)

    async def _render_tables_list_menu(self, chat_id: int, message_id: int, page: int = 0) -> None:
        tables = self.supervisor.config.tables if self.supervisor else []
        page_size = 8
        total_pages = max(1, (len(tables) + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))

        start_idx = page * page_size
        page_tables = tables[start_idx : start_idx + page_size]

        text = (
            f"🎯 *Выбор отдельного стола (Стр. {page + 1}/{total_pages})*\n\n"
            "Выберите стол для изменения персонального порога побед или включения/отключения:"
        )

        rows = []
        for i in range(0, len(page_tables), 2):
            row = []
            for t in page_tables[i : i + 2]:
                custom_streak = self.supervisor.get_table_streak(t.id) if self.supervisor else None
                streak_label = f"({custom_streak}🔥)" if custom_streak else ""
                status_icon = "🟢" if t.enabled else "🔴"
                short_name = t.name.replace("Speed Baccarat", "SB").replace("Baccarat", "Bacc")
                row.append({
                    "text": f"{status_icon} {short_name} {streak_label}".strip(),
                    "callback_data": f"t:{t.id}",
                })
            rows.append(row)

        nav_row = []
        if page > 0:
            nav_row.append({"text": "◀️ Назад", "callback_data": f"m:tables:{page - 1}"})
        if page < total_pages - 1:
            nav_row.append({"text": "Вперед ▶️", "callback_data": f"m:tables:{page + 1}"})
        if nav_row:
            rows.append(nav_row)

        rows.append([{"text": "⬅️ Назад в главное меню", "callback_data": "m:main"}])

        keyboard = {"inline_keyboard": rows}
        await self.edit_message(chat_id, message_id, text, reply_markup=keyboard)

    def _build_table_menu_payload(self, table_id: str) -> tuple[str, Dict[str, Any]]:
        table = None
        if self.supervisor:
            for t in self.supervisor.config.tables:
                if t.id == table_id:
                    table = t
                    break

        if not table:
            return f"Стол '{table_id}' не найден.", {"inline_keyboard": [[{"text": "⬅️ К списку столов", "callback_data": "m:tables:0"}]]}

        global_streak = self._get_global_streak_info()
        custom_streak = self.supervisor.get_table_streak(table_id) if self.supervisor else None
        effective_streak = custom_streak if custom_streak is not None else global_streak

        type_str = "Индивидуальный" if custom_streak is not None else f"Глобальный ({global_streak})"
        status_str = "🟢 Включен (активен)" if table.enabled else "🔴 Отключен"
        t_url = getattr(table, "url", "")

        table_link = f"[{table.name}]({t_url})" if t_url else f"*{table.name}*"

        text = (
            f"🎯 *Настройка стола*\n\n"
            f"📊 *Стол*: {table_link}\n"
            f"🆔 *ID*: `{table.id}`\n"
            f"🔥 *Текущий порог*: *Игрок {effective_streak} подряд* [{type_str}]\n"
            f"🔘 *Статус*: {status_str}\n\n"
            "Выберите кнопку или *отправьте число от 1 до N сообщением в чат*:"
        )

        def btn(n: int):
            mark = "✅ " if n == effective_streak else ""
            return {"text": f"{mark}{n}", "callback_data": f"st:{table.id}:{n}"}

        keyboard_rows = [
            [btn(1), btn(2), btn(3), btn(4)],
            [btn(5), btn(6), btn(7), btn(8)],
            [btn(9), btn(10), btn(12), btn(15)],
            [{"text": "✏️ Ввести любое число (1 - N)", "callback_data": f"inp:t:{table.id}"}],
        ]

        # Reset button if custom rule exists
        if custom_streak is not None:
            keyboard_rows.append([
                {"text": f"🔄 Сбросить к общему ({global_streak} подряд)", "callback_data": f"rt:{table.id}"}
            ])

        # Toggle table status
        toggle_txt = "🔴 Отключить стол" if table.enabled else "🟢 Включить стол"
        keyboard_rows.append([
            {"text": toggle_txt, "callback_data": f"tg:{table.id}"}
        ])

        keyboard_rows.append([
            {"text": "⬅️ К списку столов", "callback_data": "m:tables:0"},
            {"text": "🏠 Главное меню", "callback_data": "m:main"},
        ])

        return text, {"inline_keyboard": keyboard_rows}

    async def _render_table_menu(self, chat_id: int, message_id: int, table_id: str) -> None:
        text, markup = self._build_table_menu_payload(table_id)
        await self.edit_message(chat_id, message_id, text, reply_markup=markup)

    async def _send_table_menu(self, chat_id: int, table_id: str) -> None:
        text, markup = self._build_table_menu_payload(table_id)
        await self.send_message(chat_id, text, reply_markup=markup)

    async def _render_status_menu(self, chat_id: int, message_id: int) -> None:
        if not self.supervisor:
            await self.edit_message(chat_id, message_id, "Статус недоступен.")
            return

        global_streak = self._get_global_streak_info()
        lines = [
            "📊 *Статус мониторинга столов*\n",
            f"⚡️ *Глобальный порог*: `{global_streak} подряд`\n"
        ]

        now = time.time()
        for t in self.supervisor.config.tables:
            client = self.supervisor.clients.get(t.id)
            if client and client.is_connected:
                status_dot = "🟢"
                conn_txt = "online"
            elif t.enabled:
                status_dot = "🟡"
                conn_txt = "connecting"
            else:
                status_dot = "🔴"
                conn_txt = "off"
            custom = self.supervisor.get_table_streak(t.id)
            streak_txt = f"{custom}🔥" if custom else f"{global_streak}"
            window = self.supervisor.windows.get(t.id)
            rounds_count = len(window) if window else 0
            lines.append(f"{status_dot} *{t.name}*: `{conn_txt}` | {rounds_count} раундов | порог: *{streak_txt}*")

        lines.append("\n_Нажмите 'Обновить' для актуализации данных._")

        keyboard = {
            "inline_keyboard": [
                [{"text": "🔄 Обновить данные", "callback_data": "act:status"}],
                [{"text": "⬅️ Назад в меню", "callback_data": "m:main"}],
            ]
        }
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3900] + "\n..."
        await self.edit_message(chat_id, message_id, text, reply_markup=keyboard)
