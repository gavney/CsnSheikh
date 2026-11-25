"""WebSocket incoming event parser for Pragmatic Play Live Baccarat."""

import json
import re
from typing import Any, Counter, Dict, List, Optional, Union
from src.utils.logging import get_logger

from .models import (
    BeadPlateCell,
    BoardSnapshot,
    Outcome,
    PongEvent,
    RoundResult,
    SessionOfflineEvent,
    ShoeSummary,
    TableInfoEvent,
)

logger = get_logger("parser")

# Precompile regex for XML pong if server sends it as XML
XML_PONG_RE = re.compile(r'<pong\s+channel="([^"]+)"\s+time="([^"]+)"(?:\s+seq="(\d+)")?\s*/>')


class EventParser:
    """Robust parser for all Pragmatic Play WebSocket stream messages."""

    def __init__(self) -> None:
        self._unknown_events: Counter[str] = Counter()
        self._known_lifecycle_events = {
            "table",
            "dealer",
            "game",
            "timer",
            "betsopen",
            "betsclosingsoon",
            "betsclosed",
            "startDealing",
            "cardinc",
            "card",
            "SideBetsLimits",
            "playersCount",
            "betstats",
            "statisticLA",
            "subscribe",
        }

    def parse(
        self,
        raw_message: str,
        current_table_id: str
    ) -> List[Union[RoundResult, ShoeSummary, BoardSnapshot, PongEvent, TableInfoEvent]]:
        """Parse raw incoming message string into typed events.
        
        Args:
            raw_message: String payload from WebSocket.
            current_table_id: ID of the monitored table.
        """
        raw_message = raw_message.strip()
        if not raw_message:
            return []

        # Check for Session Offline indicator from server
        if "<session>offline</session>" in raw_message or "<session>expired</session>" in raw_message:
            return [SessionOfflineEvent(table_id=current_table_id, raw_message=raw_message)]

        # Check for XML pong
        if raw_message.startswith("<pong"):
            match = XML_PONG_RE.search(raw_message)
            if match:
                channel = match.group(1)
                time_val = match.group(2)
                seq = int(match.group(3)) if match.group(3) else None
                return [PongEvent(channel=channel, time=time_val, seq=seq)]

        # Try JSON parse
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.debug("Non-JSON message received: %s", raw_message[:100])
            return []

        if not isinstance(data, dict):
            return []

        events: List[Union[RoundResult, ShoeSummary, BoardSnapshot, PongEvent, TableInfoEvent]] = []

        for event_type, payload in data.items():
            if not isinstance(payload, dict):
                continue

            # 1. PII Protection: DISCARD winners event immediately
            if event_type == "winners":
                # Intentionally discard without reading players' screen names or userIds
                continue

            # 2. Heartbeat Pong
            if event_type == "pong":
                events.append(
                    PongEvent(
                        channel=payload.get("channel", ""),
                        time=str(payload.get("time", "")),
                        seq=int(payload["seq"]) if "seq" in payload else None,
                    )
                )
                continue

            # 3. Game Result (Crucial event)
            if event_type == "gameresult":
                round_result = self._parse_gameresult(payload, current_table_id)
                if round_result:
                    events.append(round_result)
                continue

            # 4. Shoe Summary (Deck state and round counters)
            if event_type == "ShoeSummary":
                summary = self._parse_shoe_summary(payload, current_table_id)
                if summary:
                    events.append(summary)
                continue

            # 5. Statistic (Contains beadPlate and roads)
            if event_type == "statistic":
                snapshot = self._parse_statistic(payload, current_table_id)
                if snapshot:
                    events.append(snapshot)
                continue

            # 6. goodroad (Filter foreign tables)
            if event_type == "goodroad":
                sourcetable = payload.get("sourcetableId") or payload.get("table")
                if sourcetable != current_table_id:
                    # Ignore foreign table alert
                    continue
                # For Phase 1 we don't need goodroad pattern matching, but we acknowledge it
                continue

            # 7. Known lifecycle events
            if event_type in self._known_lifecycle_events:
                info = self._parse_lifecycle(event_type, payload, current_table_id)
                if info:
                    events.append(info)
                continue

            # 8. Unknown event detector
            self._handle_unknown_event(event_type, payload)

        return events

    def _parse_gameresult(self, data: Dict[str, Any], fallback_table: str) -> Optional[RoundResult]:
        """Parse gameresult payload into RoundResult."""
        round_id = str(data.get("id") or data.get("gameId") or "")
        if not round_id:
            logger.warning("Received gameresult without id: %s", data)
            return None

        table_id = str(data.get("table") or fallback_table)
        result_str = str(data.get("result", "")).lower()
        try:
            outcome = Outcome.from_str(result_str)
        except ValueError:
            logger.warning("Unrecognized result in gameresult: %s", result_str)
            return None

        score = int(data.get("score", 0)) if str(data.get("score", "")).isdigit() else 0
        p_pair = str(data.get("player_pair", "")).lower() == "true"
        b_pair = str(data.get("banker_pair", "")).lower() == "true"
        time_str = str(data.get("time", ""))
        seq = int(data["seq"]) if "seq" in data and str(data["seq"]).isdigit() else None

        return RoundResult(
            round_id=round_id,
            table_id=table_id,
            result=outcome,
            score=score,
            player_pair=p_pair,
            banker_pair=b_pair,
            time=time_str,
            seq=seq,
            source="live",
        )

    def _parse_shoe_summary(self, data: Dict[str, Any], fallback_table: str) -> Optional[ShoeSummary]:
        """Parse ShoeSummary payload."""
        total_games = int(data.get("totalGames", 0))
        p_wins = int(data.get("playerWinCounter", 0))
        b_wins = int(data.get("bankerWinCounter", 0))
        ties = int(data.get("tieCounter", 0))
        p_pairs = int(data.get("playerPairCounter", 0))
        b_pairs = int(data.get("bankerPairCounter", 0))
        table_id = str(data.get("table") or fallback_table)
        seq = int(data["seq"]) if "seq" in data and str(data["seq"]).isdigit() else None

        return ShoeSummary(
            total_games=total_games,
            player_win_counter=p_wins,
            banker_win_counter=b_wins,
            tie_counter=ties,
            player_pair_counter=p_pairs,
            banker_pair_counter=b_pairs,
            table=table_id,
            seq=seq,
        )

    def _parse_statistic(self, data: Dict[str, Any], fallback_table: str) -> Optional[BoardSnapshot]:
        """Parse statistic payload containing serialized JSON in value."""
        table_id = str(data.get("table") or fallback_table)
        val_str = data.get("value")
        if not val_str or not isinstance(val_str, str):
            return None

        try:
            val_obj = json.loads(val_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse statistic.value JSON string")
            return None

        raw_bead_plate = val_obj.get("beadPlate", [])
        decoded_bead_plate: List[List[Optional[BeadPlateCell]]] = []
        rounds_count = 0

        for col in raw_bead_plate:
            decoded_col: List[Optional[BeadPlateCell]] = []
            for cell_code in col:
                cell = BeadPlateCell.from_code(cell_code)
                decoded_col.append(cell)
                if cell is not None:
                    rounds_count += 1
            decoded_bead_plate.append(decoded_col)

        b_wins = int(val_obj.get("bankerWinCounter", 0))
        p_wins = int(val_obj.get("playerWinCounter", 0))
        ties = int(val_obj.get("tieCounter", 0))
        total_games = b_wins + p_wins + ties
        if total_games == 0:
            total_games = rounds_count

        seq = int(data["seq"]) if "seq" in data and str(data["seq"]).isdigit() else None

        return BoardSnapshot(
            table_id=table_id,
            total_games=total_games,
            bead_plate=decoded_bead_plate,
            raw_rounds_count=rounds_count,
            seq=seq,
        )

    def _parse_lifecycle(
        self,
        event_type: str,
        data: Dict[str, Any],
        fallback_table: str
    ) -> Optional[TableInfoEvent]:
        """Extract metadata from lifecycle events."""
        table_id = str(data.get("table") or fallback_table)
        dealer = data.get("value") if event_type == "dealer" else None
        game_id = data.get("id") if event_type == "game" else None
        status = event_type if event_type in ("betsopen", "betsclosed", "startDealing") else None

        return TableInfoEvent(
            table_id=table_id,
            dealer_name=dealer,
            game_id=game_id,
            status=status,
        )

    def _handle_unknown_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Log unknown schema events strictly once, then increment counter."""
        count = self._unknown_events[event_type]
        if count == 0:
            logger.warning(
                "Detected new/unknown WebSocket event schema: '%s'. Keys: %s",
                event_type,
                list(payload.keys())
            )
        self._unknown_events[event_type] += 1
