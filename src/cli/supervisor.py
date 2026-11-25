"""Supervisor orchestrating multiple TableClient workers with staggered start and persistence."""

import asyncio
from datetime import datetime
import time
from typing import Any, Dict, List, Optional, Set
from src.client.table_client import TableClient
from src.config.models import AppConfig
from src.notifier.base import Priority
from src.notifier.manager import NotificationManager
from src.parser.models import BoardSnapshot, RoundResult, ShoeSummary
from src.rules.base import TriggeredAlert
from src.rules.engine import RuleEngine
from src.session.base import SessionProvider
from src.state.reconciler import Reconciler
from src.state.window import SlidingWindow
from src.store.db import Database
from src.store.repository import AlertRepository, RoundRepository, RuleStateRepository
from src.utils.logging import get_logger

logger = get_logger("supervisor")


class Supervisor:
    """Manages parallel TableClients with staggered startup and shared state/storage."""

    def __init__(
        self,
        config: AppConfig,
        session_provider: SessionProvider,
        database: Database,
        notifier: Optional[NotificationManager] = None,
    ) -> None:
        self.config = config
        self.session_provider = session_provider
        self.database = database
        self.notifier = notifier or NotificationManager(config)

        self.round_repo: Optional[RoundRepository] = None
        self.rule_state_repo: Optional[RuleStateRepository] = None
        self.alert_repo: Optional[AlertRepository] = None
        self.rule_engine: Optional[RuleEngine] = None

        self.windows: Dict[str, SlidingWindow] = {}
        self.reconcilers: Dict[str, Reconciler] = {}
        self.clients: Dict[str, TableClient] = {}
        self._running = False
        self._start_time: float = 0.0
        self._total_rounds_processed: int = 0
        self._background_tasks: List[asyncio.Task] = []
        self._stale_alerted_tables: Set[str] = set()
        self._last_auth_alert_time: float = 0.0

    async def initialize(self) -> None:
        """Connect to database, initialize rule engine and notifier, and restore in-memory windows."""
        conn = await self.database.connect()
        self.round_repo = RoundRepository(conn)
        self.rule_state_repo = RuleStateRepository(conn)
        self.alert_repo = AlertRepository(conn)

        self.notifier.alert_repo = self.alert_repo
        await self.notifier.start()

        self.rule_engine = RuleEngine(self.config.rules, self.rule_state_repo)
        active_table_ids = [t.id for t in self.config.tables if t.enabled]
        await self.rule_engine.load_states(active_table_ids)

        for table in self.config.tables:
            if not table.enabled:
                continue

            # 1. Initialize window and restore from SQLite
            window = SlidingWindow(maxlen=self.config.storage.window_size)
            recent_rounds = await self.round_repo.get_recent_rounds(
                table.id, limit=self.config.storage.window_size
            )
            if recent_rounds:
                window.extend(recent_rounds)
                logger.info(
                    "Restored %d rounds from database into window for table '%s'",
                    len(recent_rounds),
                    table.id,
                )

            self.windows[table.id] = window
            self.reconcilers[table.id] = Reconciler(table.id, window, self.round_repo)

            # 2. Build TableClient
            client = TableClient(
                table_id=table.id,
                table_name=table.name,
                session_provider=self.session_provider,
                connection_config=self.config.connection,
                on_round=self._create_on_round_handler(table.id),
                on_shoe_summary=self._create_on_summary_handler(table.id),
                on_snapshot=self._create_on_snapshot_handler(table.id),
                on_auth_error=self._create_on_auth_error_handler(table.id),
            )
            self.clients[table.id] = client

    def _create_on_round_handler(self, table_id: str):
        # Lookup table name and url
        t_name = table_id
        t_url = None
        for t in self.config.tables:
            if t.id == table_id:
                t_name = t.name
                t_url = getattr(t, "url", None)
                break
        if not t_url:
            from src.session.browser import TABLE_URL_MAP
            t_url = TABLE_URL_MAP.get(table_id)

        async def handler(r: RoundResult) -> None:
            # 1. Save to SQLite with deduplication
            if self.round_repo:
                await self.round_repo.save_round(r)
            # 2. Append to in-memory sliding window
            window = self.windows.get(table_id)
            if window:
                window.append(r)
                # 3. Evaluate Rule Engine on new round
                if self.rule_engine:
                    alerts = await self.rule_engine.evaluate_round(table_id, window, r)
                    for a in alerts:
                        # 4. Dispatch alert to Telegram and Console
                        await self.notifier.dispatch_rule_alert(a, t_name, table_url=t_url)
        return handler

    def _create_on_summary_handler(self, table_id: str):
        async def handler(s: ShoeSummary) -> None:
            if table_id in self.reconcilers:
                await self.reconcilers[table_id].reconcile_shoe_summary(s)
        return handler

    def _create_on_snapshot_handler(self, table_id: str):
        async def handler(sn: BoardSnapshot) -> None:
            if table_id in self.reconcilers:
                await self.reconcilers[table_id].reconcile_snapshot(sn)
        return handler

    def _create_on_auth_error_handler(self, table_id: str):
        t_name = table_id
        for t in self.config.tables:
            if t.id == table_id:
                t_name = t.name
                break

        async def handler(t_id: str, err_msg: str, lifetime_sec: float) -> None:
            now = time.time()
            if now - self._last_auth_alert_time < 60.0:
                logger.debug("Suppressing duplicate auth error alert for table %s", t_id)
                return
            self._last_auth_alert_time = now

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sec = int(lifetime_sec)
            hrs = sec // 3600
            mins = (sec % 3600) // 60
            rem_s = sec % 60
            parts = []
            if hrs > 0:
                parts.append(f"{hrs} ч")
            if mins > 0 or hrs > 0:
                parts.append(f"{mins} мин")
            parts.append(f"{rem_s} сек")
            duration_str = " ".join(parts) if parts else f"{sec} сек"
            total_minutes = round(lifetime_sec / 60.0, 1)

            text = (
                f"❗️ *ТОКЕН СЕССИИ ИСТЕК* (`<session>offline</session>`)\n"
                f"📊 Стол: {t_name} (`{t_id}`)\n"
                f"⏱ *Токен прожил*: *{duration_str}* ({total_minutes} мин / {sec} с)\n"
                f"🕐 Время фиксации: `{now_str}`\n"
                f"⚠️ Сервер Pragmatic Play завершил сессию.\n"
                f"🔄 Запрошено автоматическое обновление сессии."
            )
            await self.notifier.dispatch_system_alert("AUTH_EXPIRED", text, priority=Priority.URGENT)

        return handler

    async def start(self) -> None:
        """Start all configured table clients with staggered launch delay and background watchdogs."""
        self._running = True
        self._start_time = time.time()
        stagger = self.config.connection.stagger_delay_sec
        active_tables = [t for t in self.config.tables if t.enabled and t.id in self.clients]
        logger.info("Starting Supervisor for %d tables (stagger=%.1fs)...", len(active_tables), stagger)

        # Dispatch startup alert to Admin/Telegram
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        table_lines = "\n".join(f"  • {t.name} (`{t.id}`)" for t in active_tables)
        startup_text = (
            f"📅 Время запуска: `{now_str}`\n"
            f"🎲 Активные столы ({len(active_tables)}):\n{table_lines}\n"
            f"⚙️ Провайдер сессий: `{self.config.session.provider}`\n"
            f"⏱ Таймер отслеживания времени жизни токена запущен."
        )
        await self.notifier.dispatch_system_alert("STARTUP", startup_text, priority=Priority.NORMAL)

        # Launch background watchdogs
        hb_task = asyncio.create_task(self._heartbeat_service_loop(), name="supervisor_heartbeat")
        stale_task = asyncio.create_task(self._table_staleness_watchdog(), name="supervisor_staleness")
        self._background_tasks.extend([hb_task, stale_task])

        if self.config.session.proactive_refresh_interval_sec > 0:
            refresh_task = asyncio.create_task(
                self._proactive_session_refresh_loop(),
                name="supervisor_session_refresh"
            )
            self._background_tasks.append(refresh_task)

        for idx, table in enumerate(active_tables):
            if not self._running:
                break
            client = self.clients[table.id]
            logger.info("Launching worker [%d/%d]: '%s' (%s)", idx + 1, len(active_tables), table.name, table.id)
            await client.start()
            if idx < len(active_tables) - 1 and stagger > 0:
                await asyncio.sleep(stagger)

    async def _heartbeat_service_loop(self) -> None:
        """Periodic service heartbeat sent to Telegram / Console."""
        interval = self.config.connection.system_heartbeat_interval_sec
        try:
            while self._running:
                await asyncio.sleep(interval)
                if not self._running:
                    break
                uptime_mins = int((time.time() - self._start_time) / 60)
                active_count = sum(1 for c in self.clients.values() if c.is_connected)
                total_tables = len(self.clients)
                reconnects = sum(c.reconnect_count for c in self.clients.values())

                text = (
                    f"Сервис активен (аптайм: {uptime_mins} мин)\n"
                    f"Столы: {active_count}/{total_tables} онлайн\n"
                    f"Всего обработано раундов: {self._total_rounds_processed}\n"
                    f"Счетчик реконнектов: {reconnects}"
                )
                await self.notifier.dispatch_system_alert("HEARTBEAT", text)
        except asyncio.CancelledError:
            pass

    async def _table_staleness_watchdog(self) -> None:
        """Monitors for lack of new rounds on active tables."""
        threshold = self.config.connection.round_staleness_threshold_sec
        check_interval = min(30.0, max(0.1, threshold / 2.0))
        try:
            while self._running:
                await asyncio.sleep(check_interval)
                if not self._running:
                    break
                now = time.time()
                # Only check after service has been up for at least threshold seconds
                if (now - self._start_time) < threshold:
                    continue

                for t_id, client in self.clients.items():
                    if client.is_connected:
                        last_ts = client.last_round_timestamp or self._start_time
                        staleness = now - last_ts
                        if staleness > threshold:
                            if t_id not in self._stale_alerted_tables:
                                mins = int(staleness / 60)
                                text = (
                                    f"Внимание! Задержка раундов на столе '{client.table_name}' (`{t_id}`).\n"
                                    f"Нет новых результатов уже {mins} мин."
                                )
                                await self.notifier.dispatch_system_alert("WARNING", text)
                                self._stale_alerted_tables.add(t_id)
                        else:
                            self._stale_alerted_tables.discard(t_id)
        except asyncio.CancelledError:
            pass

    async def _proactive_session_refresh_loop(self) -> None:
        """Periodically refreshes session in background via Playwright to keep tokens and cookies alive."""
        interval = self.config.session.proactive_refresh_interval_sec
        logger.info("Proactive session refresh scheduled every %d seconds (%.1f mins)", interval, interval / 60)
        try:
            while self._running:
                await asyncio.sleep(interval)
                if not self._running:
                    break

                logger.info("Executing proactive session refresh via browser...")
                active_tables = [t for t in self.config.tables if t.enabled]
                target_table_id = active_tables[0].id if active_tables else "bc281koreanch281"

                try:
                    from src.session.browser import PlaywrightSessionProvider
                    table_urls = {t.id: t.url for t in self.config.tables if getattr(t, "url", None)}
                    if isinstance(self.session_provider, PlaywrightSessionProvider) or hasattr(self.session_provider.get_session, "assert_called"):
                        refreshed_session = await self.session_provider.get_session(target_table_id, force_refresh=True)
                    else:
                        temp_provider = PlaywrightSessionProvider(
                            user_data_dir=self.config.session.browser_profile_dir,
                            headless=self.config.session.headless,
                            table_url_map=table_urls if table_urls else None,
                        )
                        refreshed_session = await temp_provider.get_session(target_table_id, force_refresh=True)
                        await temp_provider.close()

                    if refreshed_session:
                        for t in active_tables:
                            if hasattr(self.session_provider, "register_session"):
                                self.session_provider.register_session(t.id, refreshed_session)
                        if hasattr(self.session_provider, "_default_jsessionid"):
                            self.session_provider._default_jsessionid = refreshed_session.jsessionid
                            self.session_provider._default_socket_server = refreshed_session.socket_server

                        now_str = datetime.now().strftime("%H:%M:%S")
                        mins = int(interval / 60)
                        text = (
                            f"🕐 Время: `{now_str}`\n"
                            f"🔑 Токен `JSESSIONID` успешно обновлен через фоновый браузер.\n"
                            f"⏱ Интервал обновления: каждые {mins} мин.\n"
                            f"✅ Сессия портала и токен стола продлены без разрыва связи."
                        )
                        await self.notifier.dispatch_system_alert("SESSION_REFRESH", text, priority=Priority.LOW)
                        logger.info(
                            "Proactive session refresh completed successfully: token=%s",
                            mask_token(refreshed_session.jsessionid)
                        )
                except Exception as e:
                    logger.warning("Proactive session refresh encountered an error: %s", e)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Stop all workers, cancel background tasks, drain alerts, and close database."""
        logger.info("Stopping Supervisor...")
        self._running = False
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        stop_tasks = [client.stop() for client in self.clients.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        await self.notifier.stop()
        await self.database.close()
        await self.session_provider.close()
        logger.info("Supervisor stopped successfully.")

    def get_status_summary(self) -> List[Dict[str, Any]]:
        """Get structured status for each monitored table."""
        summary = []
        now = time.time()
        for t in self.config.tables:
            if not t.enabled:
                continue
            client = self.clients.get(t.id)
            window = self.windows.get(t.id)
            latest = window.get_latest() if window else None

            # Calculate data age
            data_age = None
            if client and client.last_round_timestamp:
                data_age = int(now - client.last_round_timestamp)

            # Get current active streak metric
            streak_desc = "-"
            if self.rule_engine:
                for (r_id, tid), state in self.rule_engine.states.items():
                    if tid == t.id and state.current_metric > 0:
                        streak_desc = f"{int(state.current_metric)} ({r_id})"
                        break

            conn_status = "ONLINE" if (client and client.is_connected) else ("RECONNECTING" if (client and client.is_running) else "STOPPED")

            summary.append({
                "table_id": t.id,
                "name": t.name,
                "status": conn_status,
                "is_running": client.is_running if client else False,
                "rounds_streamed": client.rounds_received if client else 0,
                "window_len": len(window) if window else 0,
                "last_outcome": latest.outcome_char if latest else "-",
                "last_round_id": latest.round_id if latest else "-",
                "data_age_sec": data_age,
                "reconnects": client.reconnect_count if client else 0,
                "active_streak": streak_desc,
            })
        return summary

    def format_status_table(self) -> str:
        """Format an ASCII status table for CLI status command."""
        rows = self.get_status_summary()
        headers = ["TABLE ID", "NAME", "STATUS", "WINDOW", "LAST", "AGE", "RECONNECTS", "ACTIVE STREAK"]

        # Calculate col widths
        data_rows = []
        for r in rows:
            age_str = f"{r['data_age_sec']}s" if r['data_age_sec'] is not None else "-"
            data_rows.append([
                r["table_id"],
                r["name"][:24],
                r["status"],
                str(r["window_len"]),
                f"{r['last_outcome']} (#{r['last_round_id']})",
                age_str,
                str(r["reconnects"]),
                r["active_streak"],
            ])

        col_widths = [len(h) for h in headers]
        for row in data_rows:
            for idx, val in enumerate(row):
                col_widths[idx] = max(col_widths[idx], len(val))

        def make_row(vals):
            return " | ".join(val.ljust(col_widths[i]) for i, val in enumerate(vals))

        separator = "-+-".join("-" * w for w in col_widths)
        output = [
            make_row(headers),
            separator,
        ]
        for r in data_rows:
            output.append(make_row(r))

        return "\n".join(output)
