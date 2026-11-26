"""Command Line Interface for Casino Monitoring Service."""

import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from src.cli.supervisor import Supervisor
from src.client.table_client import TableClient
from src.config.loader import load_config
from src.parser.events import EventParser
from src.parser.models import BoardSnapshot, RoundResult, ShoeSummary
from src.session.base import SessionInfo
from src.session.browser import PlaywrightSessionProvider, save_session_to_cache
from src.session.env_provider import EnvSessionProvider
from src.store.db import Database
from src.utils.logging import get_logger, setup_logging

logger = get_logger("cli")


async def show_status(config_path: Path, env_path: Path) -> None:
    """Display real-time table status, window lengths, and data age."""
    config = load_config(config_path, env_path)
    db = Database(config.storage.database_path)
    session_provider = EnvSessionProvider()
    supervisor = Supervisor(config, session_provider, db)
    await supervisor.initialize()

    print("\n" + "=" * 80)
    print("CASINO MONITOR: SYSTEM STATUS")
    print(f"Database: {config.storage.database_path.resolve()}")
    print("=" * 80)
    print(supervisor.format_status_table())
    print("=" * 80 + "\n")
    await db.close()


async def send_test_notification(
    config_path: Path,
    env_path: Path,
    level: str,
    message: str
) -> None:
    """Send test notification via NotificationManager to verify Telegram delivery."""
    from src.notifier.base import Priority
    from src.notifier.manager import NotificationManager

    config = load_config(config_path, env_path)
    setup_logging(level=config.log_level)

    mgr = NotificationManager(config)
    await mgr.start()
    priority = Priority.URGENT if level == "urgent" else Priority.NORMAL
    sent = await mgr.dispatch_system_alert(level=level.upper(), text=message, priority=priority)
    await asyncio.sleep(0.5)
    await mgr.stop()

    if sent:
        print(f"\n[OK] Тестовое уведомление ({level}) успешно отправлено в Telegram!")
    else:
        print(f"\n[ИНФО] Отправка в Telegram пропущена (не задан TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID в .env). Оповещение выведено в консоль.")


async def run_supervisor(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    table_filter: Optional[str] = None,
) -> None:
    """Run parallel monitoring with Supervisor."""
    config = load_config(config_path, env_path)
    setup_logging(level=config.log_level)

    if table_filter and table_filter != "all":
        # Enable only specified table
        for t in config.tables:
            t.enabled = (t.id == table_filter)

    if config.session.provider == "playwright":
        session_provider = PlaywrightSessionProvider(
            user_data_dir=config.session.browser_profile_dir,
            headless=config.session.headless,
        )
    else:
        session_provider = EnvSessionProvider(
            jsessionid=config.pragmatic_jsessionid,
            socket_server=config.pragmatic_socket_server,
            client_origin=config.pragmatic_client_origin,
        )

    db = Database(config.storage.database_path)
    supervisor = Supervisor(config, session_provider, db)
    await supervisor.initialize()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_stop():
        logger.info("Shutdown signal received. Stopping supervisor...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_stop)
        except NotImplementedError:
            pass

    await supervisor.start()
    try:
        await stop_event.wait()
    finally:
        await supervisor.stop()
        logger.info("Supervisor shutdown completed.")


async def manual_login(
    url: str = "https://1xlite-32085.pro/ru/casino/brand?game=122197",
    profile_dir: Path = Path("./browser_profile")
) -> None:
    """Open a headed browser window for manual login and automatic token interception."""
    from playwright.async_api import async_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    print("\n" + "=" * 70)
    print("ВХОД В АККАУНТ И АВТОМАТИЧЕСКИЙ ПЕРЕХВАТ ТОКЕНА")
    print(f"Открытие браузера: {url}")
    print(f"Каталог профиля  : {profile_dir.resolve()}")
    print("=" * 70)
    print("1. В открывшемся окне выполните вход в аккаунт.")
    print("2. Перейдите на любой стол Live Baccarat (например, Speed Baccarat).")
    print("   Скрипт автоматически перехватит токен JSESSIONID и сохранит его!")
    print("3. После появления сообщения об успешном перехвате окно можно закрыть.")
    print("=" * 70 + "\n")

    captured_count = 0

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )

        def handle_request(req):
            nonlocal captured_count
            url_str = req.url
            if "JSESSIONID=" in url_str and ("/game?" in url_str or "/desktop/launcher/?" in url_str):
                parsed = urlparse(url_str)
                qs = parse_qs(parsed.query)
                token = qs.get("JSESSIONID", [""])[0]
                tbl = qs.get("tableId", [""])[0] or qs.get("table_id", [""])[0] or "bc281koreanch281"
                server = qs.get("socket_server", [""])[0]
                if not server and "/game?" in url_str:
                    server = f"wss://{parsed.netloc}"
                origin = req.headers.get("origin") or f"https://client.{parsed.netloc.split('.', 1)[-1]}"
                if token and server:
                    captured_count += 1
                    sess = SessionInfo(
                        jsessionid=token,
                        socket_server=server,
                        table_id=tbl,
                        client_origin=origin,
                    )
                    save_session_to_cache(sess)
                    print("\n" + "*" * 65)
                    print("🔥 [УСПЕХ] Токен автоматически перехвачен и сохранен!")
                    print(f"   Стол          : {tbl}")
                    print(f"   Сервер        : {server}")
                    print(f"   JSESSIONID    : {token[:8]}...{token[-6:]}")
                    print("   Кэш сессий    : .session_cache.json")
                    print("*" * 65 + "\n")

        context.on("request", handle_request)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(url)
            while context.pages:
                await asyncio.sleep(1.0)
        except Exception:
            pass
        finally:
            await context.close()

    print(f"\n[OK] Браузер закрыт. Перехвачено сессий: {captured_count}. Данные сохранены в {profile_dir}/ и .session_cache.json.")


async def fetch_and_save_token(
    table_id: str = "bc281koreanch281",
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> None:
    """Acquire live token for a table using headless browser and save to cache."""
    config = load_config(config_path, env_path)
    provider = PlaywrightSessionProvider(
        user_data_dir=config.session.browser_profile_dir,
        headless=True,
    )
    print(f"\nПолучение токена для стола '{table_id}' через headless-браузер...")
    try:
        session = await provider.get_session(table_id, force_refresh=True)
        print("\n" + "=" * 60)
        print("ТОКЕН УСПЕШНО ПОЛУЧЕН:")
        print(f"  Table ID      : {session.table_id}")
        print(f"  Socket Server : {session.socket_server}")
        print(f"  Origin        : {session.client_origin}")
        print(f"  JSESSIONID    : {session.jsessionid[:8]}...{session.jsessionid[-6:]}")
        print("  Кэш сохранен  : .session_cache.json")
        print("=" * 60 + "\n")
    finally:
        await provider.close()


def set_manual_token(
    token: str,
    server: str = "wss://gs24.pplcru001.net",
    table_id: str = "bc281koreanch281",
    origin: str = "https://client.pplcru001.net",
) -> None:
    """Manually register a token into local session cache."""
    sess = SessionInfo(
        jsessionid=token,
        socket_server=server,
        table_id=table_id,
        client_origin=origin,
    )
    save_session_to_cache(sess)
    print("\n" + "=" * 60)
    print("ТОКЕН УСПЕШНО СОХРАНЕН ВРУЧНУЮ:")
    print(f"  Table ID      : {table_id}")
    print(f"  Socket Server : {server}")
    print(f"  Origin        : {origin}")
    print(f"  JSESSIONID    : {token[:8]}...{token[-6:]}")
    print("  Кэш сохранен  : .session_cache.json")
    print("=" * 60 + "\n")


async def run_single_table(
    table_id: str,
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None
) -> None:
    """Run real-time monitoring for a single table."""
    config = load_config(config_path, env_path)
    setup_logging(level=config.log_level)

    # Find table config
    target_table = None
    for t in config.tables:
        if t.id == table_id:
            target_table = t
            break

    if not target_table:
        logger.error("Table '%s' not found in configuration tables: %s", table_id, [t.id for t in config.tables])
        sys.exit(1)

    # Initialize session provider
    if config.session.provider == "playwright":
        session_provider = PlaywrightSessionProvider(
            user_data_dir=config.session.browser_profile_dir,
            headless=config.session.headless,
        )
    else:
        session_provider = EnvSessionProvider(
            jsessionid=config.pragmatic_jsessionid,
            socket_server=config.pragmatic_socket_server,
            client_origin=config.pragmatic_client_origin,
        )

    parser = EventParser()

    async def on_round(r: RoundResult):
        print(f"\n[LIVE ROUND] Table: {r.table_id} | Outcome: {r.outcome_char} ({r.result.value}) | Score: {r.score} | Pairs: P={r.player_pair}, B={r.banker_pair} | Time: {r.time}")

    async def on_shoe_summary(s: ShoeSummary):
        print(f"[SHOE SUMMARY] Total Games: {s.total_games} (Player: {s.player_win_counter}, Banker: {s.banker_win_counter}, Tie: {s.tie_counter})")

    async def on_snapshot(sn: BoardSnapshot):
        print(f"[BOARD SNAPSHOT] Received beadPlate with {sn.raw_rounds_count} previous rounds in shoe")

    client = TableClient(
        table_id=target_table.id,
        table_name=target_table.name,
        session_provider=session_provider,
        connection_config=config.connection,
        event_parser=parser,
        on_round=on_round,
        on_shoe_summary=on_shoe_summary,
        on_snapshot=on_snapshot,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_stop():
        logger.info("Shutdown signal received. Stopping...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_stop)
        except NotImplementedError:
            pass

    await client.start()
    try:
        await stop_event.wait()
    finally:
        await client.stop()
        await session_provider.close()
        logger.info("Service shutdown completed.")


async def replay_har_stream(
    har_path: Path,
    table_id: str,
    rounds_count: int = 20,
    speed: float = 0.0,
) -> int:
    """Run HAR replay and verify client stream reception and ShoeSummary alignment."""
    from src.cli.replay import HarReplayServer
    from src.session.base import SessionInfo

    setup_logging(level="INFO")
    logger.info("Starting HAR Replay Verification (rounds_target=%d)...", rounds_count)

    server = HarReplayServer(
        har_path=har_path,
        table_id=table_id,
        port=8765,
        speed=speed,
        loop_rounds_count=rounds_count,
    )
    ws_url = await server.start()

    session_provider = EnvSessionProvider()
    session_provider.register_session(
        table_id=table_id,
        session=SessionInfo(
            jsessionid="replay_session_token",
            socket_server=ws_url,
            table_id=table_id,
            client_origin="https://client.replay.local",
        ),
    )

    received_rounds = []
    latest_shoe_summary: Optional[ShoeSummary] = None

    async def on_round(r: RoundResult):
        received_rounds.append(r)
        print(f"[REPLAY ROUND #{len(received_rounds)}] {r.round_id}: {r.outcome_char} (Score: {r.score})")

    async def on_shoe_summary(s: ShoeSummary):
        nonlocal latest_shoe_summary
        latest_shoe_summary = s

    client = TableClient(
        table_id=table_id,
        table_name="Replay Test Table",
        session_provider=session_provider,
        on_round=on_round,
        on_shoe_summary=on_shoe_summary,
    )

    await client.start()

    # Wait for replay to complete or timeout
    max_wait_seconds = 20.0
    elapsed = 0.0
    try:
        while len(received_rounds) < rounds_count and elapsed < max_wait_seconds:
            await asyncio.sleep(0.2)
            elapsed += 0.2
    finally:
        await client.stop()
        await server.stop()
        await session_provider.close()

    print("\n" + "=" * 60)
    print("REPLAY VERIFICATION RESULTS")
    print(f"Target rounds requested : {rounds_count}")
    print(f"Rounds captured by client: {len(received_rounds)}")
    if latest_shoe_summary:
        print(f"Latest ShoeSummary total: {latest_shoe_summary.total_games}")
        print(f"Counters: P={latest_shoe_summary.player_win_counter}, B={latest_shoe_summary.banker_win_counter}, T={latest_shoe_summary.tie_counter}")
    print("=" * 60)

    return len(received_rounds)


def main():
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Pragmatic Play Live Baccarat Monitoring Service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 'run' command
    run_parser = subparsers.add_parser("run", help="Run monitoring for live tables")
    run_parser.add_argument("--table", default="all", help="Table ID to monitor or 'all' for all configured tables")
    run_parser.add_argument("--config", default="config.yaml", help="Path to config file")
    run_parser.add_argument("--env", default=".env", help="Path to .env file")

    # 'replay-har' command
    replay_parser = subparsers.add_parser("replay-har", help="Replay captured HAR session and test stream")
    replay_parser.add_argument("--har", default="test/bxgnanhldtnm.top2.har", help="Path to HAR file")
    replay_parser.add_argument("--table", default="spto11bctorobc11", help="Table ID in HAR")
    replay_parser.add_argument("--rounds", type=int, default=20, help="Number of rounds to simulate")
    replay_parser.add_argument("--speed", type=float, default=0.0, help="Replay speed factor (0 = immediate)")

    # 'login' command
    login_parser = subparsers.add_parser("login", help="Open browser to log in manually and persist session in browser_profile")
    login_parser.add_argument("--url", default="https://1xlite-32085.pro/ru/casino/brand?game=122197", help="Portal URL to open for login")

    # 'test-notify' command
    notify_parser = subparsers.add_parser("test-notify", help="Send test alert to verify Telegram/Console delivery")
    notify_parser.add_argument("--level", default="info", choices=["info", "urgent"], help="Alert urgency level")
    notify_parser.add_argument("--message", default="Тестовое оповещение системы мониторинга Pragmatic Play Live", help="Message text")
    notify_parser.add_argument("--config", default="config.yaml", help="Path to config file")
    notify_parser.add_argument("--env", default=".env", help="Path to .env file")

    # 'validate-config' command
    val_parser = subparsers.add_parser("validate-config", help="Validate configuration YAML")
    val_parser.add_argument("--config", default="config.yaml", help="Path to config file")

    # 'status' command
    status_parser = subparsers.add_parser("status", help="Show current monitoring status of tables")
    status_parser.add_argument("--config", default="config.yaml", help="Path to config file")
    status_parser.add_argument("--env", default=".env", help="Path to .env file")

    # 'get-token' command
    get_token_parser = subparsers.add_parser("get-token", help="Acquire fresh token via browser and save to session cache")
    get_token_parser.add_argument("--table", default="bc281koreanch281", help="Table ID to fetch token for")
    get_token_parser.add_argument("--config", default="config.yaml", help="Path to config file")
    get_token_parser.add_argument("--env", default=".env", help="Path to .env file")

    # 'set-token' command
    set_token_parser = subparsers.add_parser("set-token", help="Manually save a known token into session cache")
    set_token_parser.add_argument("--token", required=True, help="JSESSIONID token value")
    set_token_parser.add_argument("--server", default="wss://gs24.pplcru001.net", help="WebSocket socket server URL")
    set_token_parser.add_argument("--table", default="bc281koreanch281", help="Table ID")
    set_token_parser.add_argument("--origin", default="https://client.pplcru001.net", help="Client Origin header")

    args = parser.parse_args()

    if args.command == "login":
        asyncio.run(manual_login(args.url))
    elif args.command == "get-token":
        asyncio.run(fetch_and_save_token(args.table, Path(args.config), Path(args.env)))
    elif args.command == "set-token":
        set_manual_token(args.token, args.server, args.table, args.origin)
    elif args.command == "test-notify":
        asyncio.run(send_test_notification(Path(args.config), Path(args.env), args.level, args.message))
    elif args.command == "run":
        asyncio.run(run_supervisor(Path(args.config), Path(args.env), args.table))
    elif args.command == "replay-har":
        rounds = asyncio.run(replay_har_stream(Path(args.har), args.table, args.rounds, args.speed))
        if rounds < args.rounds:
            sys.exit(1)
    elif args.command == "validate-config":
        try:
            cfg = load_config(Path(args.config))
            print(f"Configuration is valid! Monitored tables: {[t.id for t in cfg.tables]}, Rules: {len(cfg.rules)}")
        except Exception as e:
            print(f"Configuration validation failed: {e}")
            sys.exit(1)
    elif args.command == "status":
        asyncio.run(show_status(Path(args.config), Path(args.env)))


if __name__ == "__main__":
    main()
