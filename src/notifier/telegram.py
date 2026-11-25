"""Telegram Notifier with prioritized queue, rate-limiting, backoff, and 429 handling."""

import asyncio
from typing import Optional
import httpx

from src.notifier.base import AlertMessage, Notifier, Priority
from src.utils.logging import get_logger, mask_token

logger = get_logger("notifier.telegram")


class TelegramNotifier(Notifier):
    """Reliable Telegram delivery with priority queue and HTTP 429 backoff handling."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        default_chat_id: Optional[str] = None,
        admin_chat_id: Optional[str] = None,
        base_url: str = "https://api.telegram.org",
        rate_limit_delay_sec: float = 0.05,
        max_retries: int = 5,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.bot_token = bot_token
        self.default_chat_id = default_chat_id
        self.admin_chat_id = admin_chat_id or default_chat_id
        self.base_url = base_url.rstrip("/")
        self.rate_limit_delay_sec = rate_limit_delay_sec
        self.max_retries = max_retries

        self._custom_client = http_client
        self._client: Optional[httpx.AsyncClient] = None
        self._queue: asyncio.PriorityQueue[AlertMessage] = asyncio.PriorityQueue()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        """Initialize HTTP client and launch background worker."""
        if self._running:
            return
        self._running = True
        self._client = self._custom_client or httpx.AsyncClient(timeout=10.0, trust_env=False, proxy=None)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="tg_notifier_worker")
        logger.info(
            "TelegramNotifier started (bot_token=%s, default_chat=%s)",
            mask_token(self.bot_token),
            self.default_chat_id or "not_set"
        )

    async def stop(self) -> None:
        """Drain queue and shut down worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._client and not self._custom_client:
            await self._client.aclose()
            self._client = None
        logger.info("TelegramNotifier stopped.")

    async def send(self, message: AlertMessage) -> bool:
        """Enqueue alert message for delivery."""
        if not self.bot_token:
            logger.debug("Telegram delivery skipped: no TELEGRAM_BOT_TOKEN set.")
            return False

        admin_target = self.admin_chat_id or self.default_chat_id
        target_chat = message.chat_id or (admin_target if message.is_system else self.default_chat_id)
        if not target_chat:
            logger.warning("Telegram delivery skipped: no chat ID configured for alert '%s'", message.alert_id)
            return False

        message.chat_id = target_chat
        await self._queue.put(message)
        logger.debug(
            "Enqueued alert '%s' [Priority %s] (Queue depth: %d)",
            message.alert_id,
            message.priority.name,
            self._queue.qsize()
        )
        return True

    async def _worker_loop(self) -> None:
        """Background worker consuming messages from priority queue."""
        while self._running:
            try:
                message = await self._queue.get()
            except asyncio.CancelledError:
                break

            try:
                success = await self._deliver(message)
                if not success and message.attempts < self.max_retries:
                    # Message wasn't dropped; requeue
                    await self._queue.put(message)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in Telegram worker: %s", e)
            finally:
                self._queue.task_done()
                if self.rate_limit_delay_sec > 0:
                    await asyncio.sleep(self.rate_limit_delay_sec)

    async def _deliver(self, message: AlertMessage) -> bool:
        """Deliver message via Telegram Bot API with HTTP 429 and retry handling."""
        if not self._client or not self.bot_token or not message.chat_id:
            return False

        endpoint = f"{self.base_url}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": message.chat_id,
            "text": message.text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            resp = await self._client.post(endpoint, json=payload)

            # 1. Success (200 OK)
            if resp.status_code == 200:
                logger.info(
                    "Alert '%s' delivered to Telegram chat %s [Priority %s]",
                    message.alert_id,
                    message.chat_id,
                    message.priority.name,
                )
                return True

            # 2. Rate Limit (HTTP 429 Too Many Requests)
            if resp.status_code == 429:
                retry_after = 5
                try:
                    data = resp.json()
                    retry_after = int(data.get("parameters", {}).get("retry_after", 5))
                except Exception:
                    pass
                logger.warning(
                    "Telegram API rate limit (429) hit! Pausing for %d seconds before retrying '%s'",
                    retry_after,
                    message.alert_id,
                )
                await asyncio.sleep(retry_after)
                message.attempts += 1
                return False

            # 3. Client or Server error (4xx / 5xx)
            logger.error(
                "Telegram API error %d for alert '%s': %s",
                resp.status_code,
                message.alert_id,
                resp.text[:200]
            )
            message.attempts += 1
            return False

        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as net_err:
            message.attempts += 1
            backoff = min(15.0, (1.5 ** message.attempts))
            logger.warning(
                "Network error connecting to Telegram (%s) for alert '%s' (attempt %d/%d). Retrying in %.1fs...",
                net_err,
                message.alert_id,
                message.attempts,
                self.max_retries,
                backoff,
            )
            await asyncio.sleep(backoff)
            return False
