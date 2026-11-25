"""Unit tests for SessionInfo and SessionProvider."""

import pytest
from src.session.base import SessionInfo
from src.session.env_provider import EnvSessionProvider


def test_session_info_ws_url_and_headers():
    session = SessionInfo(
        jsessionid="test_token_12345",
        socket_server="wss://gs24.pplcru001.net",
        table_id="bc281koreanch281",
        client_origin="https://client.pplcru001.net"
    )

    url = session.build_ws_url(page_refresh=True)
    assert url == "wss://gs24.pplcru001.net/game?JSESSIONID=test_token_12345&tableId=bc281koreanch281&type=json&pageRefresh=true"

    headers = session.get_headers()
    assert headers["Origin"] == "https://client.pplcru001.net"


@pytest.mark.asyncio
async def test_env_session_provider():
    provider = EnvSessionProvider(
        jsessionid="env_token",
        socket_server="wss://gs24.pragmaticplaylive.net",
        client_origin="https://client.pragmaticplaylive.net"
    )

    s1 = await provider.get_session("bc281koreanch281")
    assert s1.jsessionid == "env_token"
    assert s1.table_id == "bc281koreanch281"

    await provider.close()


def test_build_playwright_proxy():
    from src.session.browser import build_playwright_proxy

    # None or empty
    assert build_playwright_proxy(None) is None
    assert build_playwright_proxy("") is None

    # SOCKS5 without auth
    p1 = build_playwright_proxy("socks5://127.0.0.1:10808")
    assert p1 == {"server": "socks5://127.0.0.1:10808"}

    # SOCKS5 with auth
    p2 = build_playwright_proxy("socks5://admin:secret123@1.2.3.4:9050")
    assert p2 == {
        "server": "socks5://1.2.3.4:9050",
        "username": "admin",
        "password": "secret123",
    }
