"""Session models and base provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse


@dataclass
class SessionInfo:
    """Information required to establish and maintain a table WebSocket connection."""
    jsessionid: str
    socket_server: str  # e.g. wss://gs24.pragmaticplaylive.net or wss://gs24.pplcru001.net
    table_id: str       # e.g. bc281koreanch281
    client_origin: str  # e.g. https://client.pragmaticplaylive.net
    expires_at: Optional[datetime] = None

    def build_ws_url(self, page_refresh: bool = True) -> str:
        """Construct full WebSocket URL for the game table."""
        base = self.socket_server.rstrip("/")
        if base.endswith("/game"):
            base = base[:-5]
        # Ensure ws/wss scheme
        if base.startswith("http://"):
            base = "ws://" + base[7:]
        elif base.startswith("https://"):
            base = "wss://" + base[8:]
        elif not base.startswith("ws://") and not base.startswith("wss://"):
            base = "wss://" + base

        url = f"{base}/game?JSESSIONID={self.jsessionid}&tableId={self.table_id}&type=json"
        if page_refresh:
            url += "&pageRefresh=true"
        return url

    def get_headers(self) -> dict:
        """Construct required WebSocket handshake headers including Origin."""
        origin = self.client_origin
        if not origin:
            # Derive origin from socket_server host
            parsed = urlparse(self.socket_server)
            host = parsed.netloc or parsed.path
            # Replace gs24. with client. if applicable
            if "gs" in host:
                parts = host.split(".")
                if len(parts) >= 2:
                    origin = f"https://client.{'.'.join(parts[1:])}"
                else:
                    origin = f"https://{host}"
            else:
                origin = f"https://{host}"
        return {
            "Origin": origin,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }


class SessionProvider(ABC):
    """Abstract interface for acquiring table sessions."""

    @abstractmethod
    async def get_session(self, table_id: str, force_refresh: bool = False) -> SessionInfo:
        """Retrieve a valid session for the given table ID."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Clean up provider resources."""
        pass
