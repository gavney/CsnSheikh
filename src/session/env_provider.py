"""Direct environment-based session provider."""

import os
from typing import Dict, Optional
from src.utils.logging import get_logger, mask_token

from .base import SessionInfo, SessionProvider

logger = get_logger("session.env")


class EnvSessionProvider(SessionProvider):
    """Session provider reading tokens directly from environment or memory.
    
    Ideal for unit/integration tests, manual token injection, and replay testing.
    """

    def __init__(
        self,
        jsessionid: Optional[str] = None,
        socket_server: Optional[str] = None,
        client_origin: Optional[str] = None,
        table_sessions: Optional[Dict[str, SessionInfo]] = None,
    ) -> None:
        self._default_jsessionid = jsessionid or os.getenv("PRAGMATIC_JSESSIONID")
        self._default_socket_server = (
            socket_server
            or os.getenv("PRAGMATIC_SOCKET_SERVER")
            or "wss://gs24.pragmaticplaylive.net"
        )
        self._default_client_origin = (
            client_origin
            or os.getenv("PRAGMATIC_CLIENT_ORIGIN")
            or "https://client.pragmaticplaylive.net"
        )
        self._table_sessions: Dict[str, SessionInfo] = table_sessions or {}

        # If no explicit token provided, attempt to load from local session cache
        if not self._default_jsessionid and not self._table_sessions:
            from .browser import load_sessions_from_cache
            cached = load_sessions_from_cache()
            if cached:
                self._table_sessions.update(cached)
                first_sess = next(iter(cached.values()))
                self._default_jsessionid = first_sess.jsessionid
                if first_sess.socket_server:
                    self._default_socket_server = first_sess.socket_server
                if first_sess.client_origin:
                    self._default_client_origin = first_sess.client_origin
                logger.info(
                    "Loaded cached session token (%s tables found in .session_cache.json)",
                    len(cached)
                )

    def register_session(self, table_id: str, session: SessionInfo) -> None:
        """Register session info for a specific table."""
        self._table_sessions[table_id] = session

    async def get_session(self, table_id: str, force_refresh: bool = False) -> SessionInfo:
        """Get session for table."""
        if table_id in self._table_sessions and not force_refresh:
            return self._table_sessions[table_id]

        if not self._default_jsessionid:
            raise ValueError(
                f"No JSESSIONID provided for table '{table_id}'. Set PRAGMATIC_JSESSIONID "
                f"in .env or configure PlaywrightSessionProvider."
            )

        logger.info(
            "Using static session for table '%s': server=%s, token=%s",
            table_id,
            self._default_socket_server,
            mask_token(self._default_jsessionid),
        )

        session = SessionInfo(
            jsessionid=self._default_jsessionid,
            socket_server=self._default_socket_server,
            table_id=table_id,
            client_origin=self._default_client_origin,
        )
        self._table_sessions[table_id] = session
        return session

    async def close(self) -> None:
        """No cleanup needed for env provider."""
        pass
