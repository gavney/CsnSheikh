"""Playwright-based session provider that extracts session tokens from browser."""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse
from src.utils.logging import get_logger, mask_token

from .base import SessionInfo, SessionProvider

logger = get_logger("session.playwright")

DEFAULT_CACHE_PATH = Path(".session_cache.json")

# Known game URLs on the casino portal
TABLE_URL_MAP = {
    "bc281koreanch281": "https://1xbet.fi/ru/casino/brand?game=68018",
    "kkqnazmd8ttq7fgd": "https://1xbet.fi/ru/casino/brand?game=51523",
    "oq808ojps709qqaf": "https://1xbet.fi/ru/casino/brand?game=59580",
    "tobc51koreanto51": "https://1xbet.fi/ru/casino/brand?game=81262",
    "bc392chromabc392": "https://1xbet.fi/ru/casino/brand?game=122203",
    "speedbca18generi": "https://1xbet.fi/ru/casino/brand?game=81947",
    "speedbca14gesbc1": "https://1xbet.fi/ru/casino/brand?game=85635",
    "spto41bctorobc41": "https://1xbet.fi/ru/casino/brand?game=93989",
    "spto42bctorobc42": "https://1xbet.fi/ru/casino/brand?game=93990",
    "spijbnsu408vmv71": "https://1xbet.fi/ru/casino/brand?game=94056",
    "spijbnsu408vmv72": "https://1xbet.fi/ru/casino/brand?game=94059",
    "spto11bctorobc11": "https://1xbet.fi/ru/casino/brand?game=94375",
    "m88hicogrzeod202": "https://1xbet.fi/ru/casino/brand?game=124383",
    "spijbnsu408vmv73": "https://1xbet.fi/ru/casino/brand?game=122197",
}


def save_session_to_cache(session: SessionInfo, cache_path: Path = DEFAULT_CACHE_PATH) -> None:
    """Save session info to local cache JSON file."""
    data = {}
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[session.table_id] = {
        "jsessionid": session.jsessionid,
        "socket_server": session.socket_server,
        "table_id": session.table_id,
        "client_origin": session.client_origin,
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info("Saved session for table '%s' to cache file %s", session.table_id, cache_path)


def load_sessions_from_cache(cache_path: Path = DEFAULT_CACHE_PATH) -> Dict[str, SessionInfo]:
    """Load cached sessions from local JSON file."""
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        result = {}
        for k, v in raw.items():
            if isinstance(v, dict) and "jsessionid" in v:
                result[k] = SessionInfo(
                    jsessionid=v["jsessionid"],
                    socket_server=v.get("socket_server", ""),
                    table_id=v.get("table_id", k),
                    client_origin=v.get("client_origin", ""),
                )
        return result
    except Exception as e:
        logger.warning("Failed to load session cache from %s: %s", cache_path, e)
        return {}


def build_playwright_proxy(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    """Convert a proxy URL (e.g. socks5://user:pass@host:port) into Playwright ProxySettings dict."""
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return None
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server = f"{server}:{parsed.port}"
    settings = {"server": server}
    if parsed.username:
        settings["username"] = parsed.username
    if parsed.password:
        settings["password"] = parsed.password
    return settings


class PlaywrightSessionProvider(SessionProvider):
    """Acquires live session tokens by navigating in a persistent Playwright browser context."""

    def __init__(
        self,
        user_data_dir: Path = Path("./browser_profile"),
        headless: bool = True,
        table_url_map: Optional[Dict[str, str]] = None,
        cache_path: Path = DEFAULT_CACHE_PATH,
        proxy: Optional[str] = None,
    ) -> None:
        self.user_data_dir = Path(user_data_dir)
        self.headless = headless
        self.proxy = proxy
        merged_urls = dict(TABLE_URL_MAP)
        if table_url_map:
            merged_urls.update(table_url_map)
        self.table_url_map = merged_urls
        self.cache_path = Path(cache_path)
        self._cached_sessions: Dict[str, SessionInfo] = load_sessions_from_cache(self.cache_path)
        self._last_refresh_time: float = 0.0
        self._playwright = None
        self._context = None
        self._lock = asyncio.Lock()

    async def _ensure_context(self):
        """Lazily initialize Playwright browser context."""
        if self._context is not None:
            return self._context

        from playwright.async_api import async_playwright

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        logger.info(
            "Launching browser context (headless=%s, profile=%s, proxy=%s)",
            self.headless,
            self.user_data_dir,
            mask_token(self.proxy) if self.proxy else "direct",
        )
        proxy_settings = build_playwright_proxy(self.proxy)
        launch_kwargs = {
            "user_data_dir": str(self.user_data_dir),
            "headless": self.headless,
            "args": ["--no-sandbox", "--disable-setuid-sandbox"],
        }
        if proxy_settings:
            launch_kwargs["proxy"] = proxy_settings
        self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        return self._context

    async def get_session(self, table_id: str, force_refresh: bool = False) -> SessionInfo:
        """Extract a valid session by opening table page and intercepting game tokens."""
        async with self._lock:
            now = time.time()
            # If force_refresh was requested but another table refreshed within 30s, reuse the fresh token!
            if force_refresh and (now - self._last_refresh_time < 30.0) and table_id in self._cached_sessions:
                logger.info(
                    "Reusing freshly refreshed token for table '%s' (obtained %.1fs ago)",
                    table_id,
                    now - self._last_refresh_time,
                )
                return self._cached_sessions[table_id]

            if not force_refresh and table_id in self._cached_sessions:
                return self._cached_sessions[table_id]

            target_url = self.table_url_map.get(table_id)
            if not target_url:
                raise ValueError(
                    f"No URL configured for table '{table_id}'. Available tables: {list(self.table_url_map.keys())}"
                )

            context = await self._ensure_context()
            page = await context.new_page()

            # Abort heavy images, media, and fonts to speed up load time by 5-10x
            async def route_filter(route):
                if route.request.resource_type in ["image", "media", "font"]:
                    await route.abort()
                else:
                    await route.continue_()

            try:
                await page.route("**/*", route_filter)
            except Exception:
                pass

            extracted_info: Optional[SessionInfo] = None
            extracted_event = asyncio.Event()

            # Handler for intercepted requests
            def handle_request(req):
                nonlocal extracted_info
                url = req.url
                # Look for websocket handshake or launcher URL
                if "JSESSIONID=" in url and "pragmaticplaylive" in url and not extracted_info:
                    parsed = urlparse(url)
                    qs = parse_qs(parsed.query)
                    token = qs.get("JSESSIONID", [""])[0]
                    if token:
                        server = qs.get("socket_server", [""])[0] or "wss://gs24.pragmaticplaylive.net/game"
                        if server.endswith("/game"):
                            server = server[:-5]
                        origin = req.headers.get("origin") or "https://client.pragmaticplaylive.net"
                        extracted_info = SessionInfo(
                            jsessionid=token,
                            socket_server=server,
                            table_id=table_id,
                            client_origin=origin,
                        )
                        extracted_event.set()
                elif "/game?" in url and "JSESSIONID=" in url:
                    parsed = urlparse(url)
                    qs = parse_qs(parsed.query)
                    jsessionid = qs.get("JSESSIONID", [""])[0]
                    table = qs.get("tableId", [table_id])[0]
                    socket_server = f"wss://{parsed.netloc}"
                    origin = req.headers.get("origin") or f"https://client.{parsed.netloc.split('.', 1)[-1]}"
                    extracted_info = SessionInfo(
                        jsessionid=jsessionid,
                        socket_server=socket_server,
                        table_id=table,
                        client_origin=origin,
                    )
                    extracted_event.set()

            page.on("request", handle_request)

            try:
                logger.info("Opening page for table '%s': %s", table_id, target_url)
                await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)

                # Wait for token capture
                try:
                    await asyncio.wait_for(extracted_event.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    raise TimeoutError(
                        f"Timeout waiting for game session tokens on '{target_url}'. "
                        f"Please ensure browser profile is authenticated."
                    )

                assert extracted_info is not None
                logger.info(
                    "Successfully captured session for table '%s': server=%s, token=%s",
                    table_id,
                    extracted_info.socket_server,
                    mask_token(extracted_info.jsessionid),
                )
                # Universal session: update all tables with the new token so other tables don't need to re-open browser!
                self._last_refresh_time = time.time()
                self._cached_sessions[table_id] = extracted_info
                save_session_to_cache(extracted_info, self.cache_path)
                for other_tid in self.table_url_map:
                    if other_tid != table_id:
                        other_info = SessionInfo(
                            jsessionid=extracted_info.jsessionid,
                            socket_server=extracted_info.socket_server,
                            table_id=other_tid,
                            client_origin=extracted_info.client_origin,
                        )
                        self._cached_sessions[other_tid] = other_info
                        save_session_to_cache(other_info, self.cache_path)
                return extracted_info
            finally:
                await page.close()

    async def close(self) -> None:
        """Close browser context and Playwright process."""
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
