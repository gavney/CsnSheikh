"""Single table WebSocket client with heartbeat, watchdog and auto-reconnect."""

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional, Union
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from src.config.models import ConnectionConfig
from src.parser.events import EventParser
from src.parser.models import BoardSnapshot, PongEvent, RoundResult, SessionOfflineEvent, ShoeSummary
from src.session.base import SessionInfo, SessionProvider
from src.utils.logging import get_logger, mask_token

from .connection import BackoffManager

logger = get_logger("client.table")


def is_ws_closed(ws) -> bool:
    """Helper to detect if a websockets connection is closed across versions."""
    if ws is None:
        return True
    if hasattr(ws, "state"):
        return ws.state.name != "OPEN"
    if hasattr(ws, "closed"):
        return bool(ws.closed)
    return False


class WatchdogTimeoutError(Exception):
    """Raised when server message silence exceeds watchdog timeout."""
    pass


class AuthExpiredError(Exception):
    """Raised when server reports session expired (<session>offline</session>)."""
    pass


class TableClient:
    """Manages WebSocket stream for a single Pragmatic Play Live Baccarat table."""

    def __init__(
        self,
        table_id: str,
        table_name: str,
        session_provider: SessionProvider,
        connection_config: Optional[ConnectionConfig] = None,
        event_parser: Optional[EventParser] = None,
        on_round: Optional[Callable[[RoundResult], Union[None, Awaitable[None]]]] = None,
        on_shoe_summary: Optional[Callable[[ShoeSummary], Union[None, Awaitable[None]]]] = None,
        on_snapshot: Optional[Callable[[BoardSnapshot], Union[None, Awaitable[None]]]] = None,
        on_auth_error: Optional[Callable[[str, str, float], Union[None, Awaitable[None]]]] = None,
    ) -> None:
        self.table_id = table_id
        self.table_name = table_name
        self.session_provider = session_provider
        self.config = connection_config or ConnectionConfig()
        self.parser = event_parser or EventParser()

        self.on_round = on_round
        self.on_shoe_summary = on_shoe_summary
        self.on_snapshot = on_snapshot
        self.on_auth_error = on_auth_error

        self.backoff = BackoffManager(
            min_delay=self.config.reconnect_min_delay_sec,
            max_delay=self.config.reconnect_max_delay_sec,
            jitter=self.config.reconnect_jitter_sec,
        )

        self._running = False
        self._ws = None
        self._last_msg_timestamp: float = 0.0
        self._last_round_timestamp: float = 0.0
        self._session_start_time: float = 0.0
        self._reconnect_count: int = 0
        self._rounds_received_count: int = 0
        self._current_task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._running and not is_ws_closed(self._ws)

    @property
    def rounds_received(self) -> int:
        return self._rounds_received_count

    @property
    def last_round_timestamp(self) -> float:
        return self._last_round_timestamp

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    async def start(self) -> None:
        """Start client background task."""
        if self._running:
            return
        self._running = True
        self._current_task = asyncio.create_task(self._run_loop(), name=f"tc_{self.table_id}")

    async def stop(self) -> None:
        """Gracefully stop client and close connection."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._current_task:
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass

    async def _dispatch_callback(self, callback: Optional[Callable], *args: Any) -> None:
        """Safely invoke callback handling both sync functions and async coroutines."""
        if callback is None:
            return
        try:
            res = callback(*args)
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                await res
        except Exception as e:
            logger.exception("Error in callback %s: %s", callback, e)

    async def _run_loop(self) -> None:
        """Main lifecycle and reconnect loop."""
        logger.info("Started TableClient worker for '%s' (%s)", self.table_name, self.table_id)
        force_refresh_session = False

        while self._running:
            try:
                # 1. Acquire session
                session = await self.session_provider.get_session(
                    self.table_id,
                    force_refresh=force_refresh_session
                )
                force_refresh_session = False

                # 2. Connect and process stream
                await self._connect_and_stream(session)

                # Reset backoff on clean disconnection
                self.backoff.reset()

            except asyncio.CancelledError:
                logger.info("TableClient for '%s' was cancelled", self.table_id)
                break
            except Exception as e:
                # Check for auth/policy errors
                err_msg = str(e).lower()
                is_auth_error = isinstance(e, AuthExpiredError) or any(
                    kw in err_msg for kw in ["401", "403", "1008", "4401", "unauthorized", "forbidden", "session expired"]
                )
                if is_auth_error:
                    logger.warning("Auth error detected for table '%s': %s. Requesting fresh session.", self.table_id, e)
                    force_refresh_session = True
                    if self._session_start_time > 0.0:
                        lifetime = time.time() - self._session_start_time
                        self._session_start_time = 0.0
                        if self.on_auth_error:
                            await self._dispatch_callback(self.on_auth_error, self.table_id, str(e), lifetime)

                delay = self.backoff.next_delay()
                self._reconnect_count += 1
                logger.warning(
                    "Stream error for '%s': %s (attempt #%d). Reconnecting in %.2fs...",
                    self.table_id,
                    e,
                    self.backoff.consecutive_failures,
                    delay,
                )

                if self.backoff.consecutive_failures >= 3:
                    force_refresh_session = True

                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break

        logger.info("TableClient loop terminated for '%s'", self.table_id)

    async def _connect_and_stream(self, session: SessionInfo) -> None:
        """Open WebSocket connection and run heartbeat, watchdog, and receive loops."""
        ws_url = session.build_ws_url(page_refresh=True)
        headers = session.get_headers()

        logger.info(
            "Connecting to %s (Origin: %s, token: %s)",
            ws_url,
            headers.get("Origin"),
            mask_token(session.jsessionid)
        )

        connect_kwargs = {
            "additional_headers": headers,
            "ping_interval": None,
            "ping_timeout": None,
            "close_timeout": 5.0,
        }

        async with websockets.connect(ws_url, **connect_kwargs) as ws:
            self._ws = ws
            self._last_msg_timestamp = time.time()
            if self._session_start_time == 0.0:
                self._session_start_time = time.time()
            self.backoff.reset()
            logger.info("WebSocket connected successfully for table '%s'", self.table_id)

            reader_task = asyncio.create_task(self._reader_loop(ws))
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
            watchdog_task = asyncio.create_task(self._watchdog_loop(ws))

            try:
                done, pending = await asyncio.wait(
                    [reader_task, watchdog_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                heartbeat_task.cancel()
                await asyncio.gather(reader_task, heartbeat_task, watchdog_task, return_exceptions=True)

                for t in done:
                    exc = t.exception()
                    if exc and not isinstance(exc, asyncio.CancelledError):
                        raise exc
            finally:
                if not is_ws_closed(ws):
                    try:
                        await ws.close()
                    except Exception:
                        pass

    async def _reader_loop(self, ws) -> None:
        """Read and dispatch incoming WebSocket messages."""
        try:
            async for raw_message in ws:
                self._last_msg_timestamp = time.time()
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8", errors="ignore")

                events = self.parser.parse(raw_message, self.table_id)
                for event in events:
                    if isinstance(event, RoundResult):
                        self._rounds_received_count += 1
                        self._last_round_timestamp = time.time()
                        logger.info(
                            "[%s] Round #%s: %s (score %d, P_pair=%s, B_pair=%s)",
                            self.table_id,
                            event.round_id,
                            event.result.value.upper(),
                            event.score,
                            event.player_pair,
                            event.banker_pair,
                        )
                        await self._dispatch_callback(self.on_round, event)

                    elif isinstance(event, ShoeSummary):
                        logger.info(
                            "[%s] ShoeSummary: totalGames=%d, P=%d, B=%d, T=%d",
                            self.table_id,
                            event.total_games,
                            event.player_win_counter,
                            event.banker_win_counter,
                            event.tie_counter,
                        )
                        await self._dispatch_callback(self.on_shoe_summary, event)

                    elif isinstance(event, BoardSnapshot):
                        logger.info(
                            "[%s] Initial Snapshot: totalGames=%d, beadPlate columns=%d",
                            self.table_id,
                            event.total_games,
                            len(event.bead_plate),
                        )
                        await self._dispatch_callback(self.on_snapshot, event)

                    elif isinstance(event, PongEvent):
                        logger.debug("[%s] Heartbeat pong received: seq=%s", self.table_id, event.seq)

                    elif isinstance(event, SessionOfflineEvent):
                        logger.warning(
                            "[%s] Server rejected session (%s). Token is expired or offline. Triggering session refresh.",
                            self.table_id,
                            event.raw_message,
                        )
                        lifetime = time.time() - self._session_start_time if self._session_start_time > 0 else 0.0
                        self._session_start_time = 0.0
                        if self.on_auth_error:
                            await self._dispatch_callback(self.on_auth_error, self.table_id, event.raw_message, lifetime)
                        raise AuthExpiredError(f"Session expired on '{self.table_id}' ({event.raw_message})")
        except ConnectionClosed:
            logger.info("[%s] WebSocket connection closed by remote", self.table_id)

    async def _heartbeat_loop(self, ws) -> None:
        """Send XML ping frames every 10 seconds."""
        interval = self.config.heartbeat_interval_sec
        try:
            while self._running and not is_ws_closed(ws):
                await asyncio.sleep(interval)
                if is_ws_closed(ws):
                    break
                now_ms = int(time.time() * 1000)
                ping_frame = f'<ping channel="table-{self.table_id}" time="{now_ms}"/>'
                try:
                    await ws.send(ping_frame)
                    logger.debug("[%s] Sent ping frame (time=%d)", self.table_id, now_ms)
                except (ConnectionClosed, Exception):
                    break
        except asyncio.CancelledError:
            pass

    async def _watchdog_loop(self, ws) -> None:
        """Detect hung connections if no server messages arrive for > watchdog_timeout_sec."""
        timeout = self.config.watchdog_timeout_sec
        check_interval = max(0.05, min(0.5, timeout / 4.0))
        try:
            while self._running and not is_ws_closed(ws):
                await asyncio.sleep(check_interval)
                if is_ws_closed(ws):
                    break
                silence_duration = time.time() - self._last_msg_timestamp
                if silence_duration > timeout:
                    logger.warning(
                        "[%s] Watchdog triggered: silence duration %.2fs exceeds limit %.2fs.",
                        self.table_id,
                        silence_duration,
                        timeout,
                    )
                    raise WatchdogTimeoutError(
                        f"Watchdog timeout on '{self.table_id}': no messages for {silence_duration:.2f}s"
                    )
        except asyncio.CancelledError:
            pass
