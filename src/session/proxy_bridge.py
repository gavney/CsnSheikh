"""Local transparent SOCKS5 bridge for Playwright.

Chromium's network stack does not natively support SOCKS5 proxy authentication.
This bridge runs locally on an ephemeral port without authentication, forwarding
traffic through an upstream authenticated SOCKS5 proxy using python-socks.
"""

import asyncio
from typing import Optional, Set
from urllib.parse import urlparse

from python_socks.async_.asyncio import Proxy
from src.utils.logging import get_logger, mask_token

logger = get_logger("session.proxy_bridge")


class LocalSocks5Bridge:
    """Ephemeral local proxy server bridging unauthenticated Chromium to authenticated SOCKS5."""

    def __init__(self, upstream_url: str) -> None:
        self.upstream_url = upstream_url
        self._server: Optional[asyncio.Server] = None
        self._port: Optional[int] = None
        self._active_tasks: Set[asyncio.Task] = set()
        self._upstream_proxy: Optional[Proxy] = None

    @property
    def local_url(self) -> str:
        if not self._port:
            raise RuntimeError("Bridge is not running")
        return f"socks5://127.0.0.1:{self._port}"

    async def start(self) -> str:
        """Start local bridge if upstream has authentication, otherwise return upstream URL directly."""
        parsed = urlparse(self.upstream_url)
        if not (parsed.username or parsed.password):
            # No credentials; Chromium can connect directly
            return self.upstream_url

        self._upstream_proxy = Proxy.from_url(self.upstream_url)
        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=0,
        )
        self._port = self._server.sockets[0].getsockname()[1]
        logger.info(
            "Started local SOCKS5 authentication bridge on 127.0.0.1:%d -> upstream %s",
            self._port,
            mask_token(self.upstream_url),
        )
        return self.local_url

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task:
            self._active_tasks.add(task)
        try:
            # 1. SOCKS5 Greeting / Method negotiation
            header = await reader.readexactly(2)
            ver, nmethods = header[0], header[1]
            if ver != 5:
                writer.close()
                return
            await reader.readexactly(nmethods)
            writer.write(b"\x05\x00")  # NO AUTHENTICATION REQUIRED
            await writer.drain()

            # 2. SOCKS5 Request
            req = await reader.readexactly(4)
            ver, cmd, rsv, atyp = req[0], req[1], req[2], req[3]
            if ver != 5 or cmd != 1:  # 1 = CONNECT
                writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")  # Command not supported
                await writer.drain()
                writer.close()
                return

            if atyp == 1:  # IPv4
                addr_bytes = await reader.readexactly(4)
                dest_host = ".".join(str(b) for b in addr_bytes)
            elif atyp == 3:  # Domain
                domain_len = (await reader.readexactly(1))[0]
                dest_host = (await reader.readexactly(domain_len)).decode("latin1")
            elif atyp == 4:  # IPv6
                addr_bytes = await reader.readexactly(16)
                dest_host = ":".join(f"{addr_bytes[i]:02x}{addr_bytes[i+1]:02x}" for i in range(0, 16, 2))
            else:
                writer.close()
                return

            port_bytes = await reader.readexactly(2)
            dest_port = int.from_bytes(port_bytes, "big")

            # 3. Connect to upstream through python-socks
            assert self._upstream_proxy is not None
            upstream_sock = await self._upstream_proxy.connect(
                dest_host=dest_host,
                dest_port=dest_port,
                timeout=20.0,
            )
            u_reader, u_writer = await asyncio.open_connection(sock=upstream_sock)

            # 4. Success reply to client
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()

            # 5. Bidirectional pipeline
            async def forward(src: asyncio.StreamReader, dst: asyncio.StreamWriter):
                try:
                    while True:
                        buf = await src.read(65536)
                        if not buf:
                            break
                        dst.write(buf)
                        await dst.drain()
                except Exception:
                    pass
                finally:
                    try:
                        dst.close()
                    except Exception:
                        pass

            await asyncio.gather(forward(reader, u_writer), forward(u_reader, writer))

        except Exception as e:
            logger.debug("Proxy bridge client handling exception: %s", e)
        finally:
            if task:
                self._active_tasks.discard(task)
            try:
                writer.close()
            except Exception:
                pass

    async def close(self) -> None:
        """Stop local bridge and terminate pending connections."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for t in list(self._active_tasks):
            t.cancel()
        self._active_tasks.clear()
        logger.debug("Closed local SOCKS5 authentication bridge.")
