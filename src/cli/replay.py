"""HAR replay engine and local mock WebSocket server for testing."""

import asyncio
import json
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlparse
import websockets
from websockets.asyncio.server import serve

from src.client.table_client import is_ws_closed
from src.utils.logging import get_logger

logger = get_logger("replay")


class HarReplayServer:
    """Mock WebSocket server replaying captured Pragmatic Play Live streams."""

    def __init__(
        self,
        har_path: Path,
        table_id: str,
        host: str = "127.0.0.1",
        port: int = 8765,
        speed: float = 0.0,
        loop_rounds_count: int = 1,
    ) -> None:
        self.har_path = Path(har_path)
        self.table_id = table_id
        self.host = host
        self.port = port
        self.speed = speed
        self.loop_rounds_count = loop_rounds_count
        self._server = None
        self._messages_to_send: List[dict] = []
        self._load_har()

    def _load_har(self) -> None:
        """Extract server receive messages from HAR for game websocket."""
        logger.info("Loading WebSocket stream from %s...", self.har_path)
        with open(self.har_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)

        for entry in data.get("log", {}).get("entries", []):
            url = entry.get("request", {}).get("url", "")
            if "/game?" in url and self.table_id in url:
                for m in entry.get("_webSocketMessages", []):
                    if m.get("type") == "receive":
                        self._messages_to_send.append(m)

        logger.info("Loaded %d server messages from HAR", len(self._messages_to_send))

    async def start(self) -> str:
        """Start local websocket server and return ws url."""
        self._server = await serve(self._handle_client, self.host, self.port)
        if self.port == 0 and self._server.sockets:
            self.port = self._server.sockets[0].getsockname()[1]
        ws_url = f"ws://{self.host}:{self.port}"
        logger.info("HAR Replay Server running at %s", ws_url)
        return ws_url

    async def stop(self) -> None:
        """Stop local server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(self, websocket) -> None:
        """Handle incoming connection and replay frames."""
        client_table_id = self.table_id
        if hasattr(websocket, "request") and websocket.request and websocket.request.path:
            parsed_q = parse_qs(urlparse(websocket.request.path).query)
            if "tableId" in parsed_q:
                client_table_id = parsed_q["tableId"][0]

        logger.info("Client connected to HAR replay server for table '%s'", client_table_id)

        async def ping_responder():
            try:
                async for raw in websocket:
                    if isinstance(raw, str) and "<ping" in raw:
                        pong = f'{{"pong":{{"channel":"table-{client_table_id}","time":"1795000000000","seq":999}}}}'
                        try:
                            await websocket.send(pong)
                        except Exception:
                            break
            except Exception:
                pass

        responder_task = asyncio.create_task(ping_responder())

        try:
            for loop_idx in range(self.loop_rounds_count):
                prev_time: Optional[float] = None
                for idx, m in enumerate(self._messages_to_send):
                    if is_ws_closed(websocket):
                        break

                    # Timing delay simulation if speed > 0
                    if self.speed > 0:
                        msg_time = m.get("time")
                        if prev_time is not None and msg_time is not None:
                            delay = max(0.0, (msg_time - prev_time) * self.speed)
                            await asyncio.sleep(min(delay, 2.0))
                        prev_time = msg_time

                    payload = m.get("data", "")
                    if client_table_id != self.table_id:
                        payload = payload.replace(self.table_id, client_table_id)
                    
                    # If this is subsequent loop, skip initial snapshot events
                    if loop_idx > 0 and any(
                        payload.startswith(f'{{"{k}":')
                        for k in ["table", "dealer", "statistic", "statisticLA", "SideBetsLimits"]
                    ):
                        continue

                    # If looping, rewrite game ID so each round has a distinct round_id
                    if loop_idx > 0 and '"gameresult"' in payload:
                        try:
                            obj = json.loads(payload)
                            gr = obj["gameresult"]
                            orig_id = int(gr.get("id", "17680000000"))
                            new_id = str(orig_id + loop_idx)
                            gr["id"] = new_id
                            gr["gameId"] = new_id
                            payload = json.dumps(obj)
                        except Exception:
                            pass
                    elif loop_idx > 0 and '"ShoeSummary"' in payload:
                        try:
                            obj = json.loads(payload)
                            ss = obj["ShoeSummary"]
                            orig_tg = int(ss.get("totalGames", "0"))
                            orig_pwc = int(ss.get("playerWinCounter", "0"))
                            ss["totalGames"] = str(orig_tg + loop_idx)
                            ss["playerWinCounter"] = str(orig_pwc + loop_idx)
                            payload = json.dumps(obj)
                        except Exception:
                            pass

                    try:
                        await websocket.send(payload)
                    except Exception:
                        break

                # Small sleep between simulated rounds
                await asyncio.sleep(0.01)

            # Wait for client to finish reading or close connection
            try:
                await websocket.wait_closed()
            except Exception:
                pass
        finally:
            responder_task.cancel()
            logger.info("HAR replay finished for client")
