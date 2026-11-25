"""Unit tests for WebSocket event parser and models."""

import pytest
from src.parser.events import EventParser
from src.parser.models import BeadPlateCell, BoardSnapshot, Outcome, PongEvent, RoundResult, SessionOfflineEvent, ShoeSummary


def test_bead_plate_cell_parsing():
    cell_banker = BeadPlateCell.from_code("RN9")
    assert cell_banker is not None
    assert cell_banker.outcome == Outcome.BANKER
    assert cell_banker.pair == "none"
    assert cell_banker.score == 9

    cell_player = BeadPlateCell.from_code("BB5")
    assert cell_player is not None
    assert cell_player.outcome == Outcome.PLAYER
    assert cell_player.pair == "banker"
    assert cell_player.score == 5

    cell_tie = BeadPlateCell.from_code("GP3")
    assert cell_tie is not None
    assert cell_tie.outcome == Outcome.TIE
    assert cell_tie.pair == "player"
    assert cell_tie.score == 3

    assert BeadPlateCell.from_code("---") is None
    assert BeadPlateCell.from_code("") is None


def test_parse_gameresult():
    parser = EventParser()
    raw = '{"gameresult":{"id":"17680953624","table":"spto11bctorobc11","result":"player","score":"9","player_pair":"false","banker_pair":"true","time":"08:41:55","seq":42}}'
    events = parser.parse(raw, "spto11bctorobc11")
    assert len(events) == 1
    r = events[0]
    assert isinstance(r, RoundResult)
    assert r.round_id == "17680953624"
    assert r.table_id == "spto11bctorobc11"
    assert r.result == Outcome.PLAYER
    assert r.outcome_char == "P"
    assert r.score == 9
    assert r.player_pair is False
    assert r.banker_pair is True
    assert r.time == "08:41:55"
    assert r.seq == 42


def test_parse_shoe_summary():
    parser = EventParser()
    raw = '{"ShoeSummary":{"totalGames":"34","playerWinCounter":"15","bankerWinCounter":"13","tieCounter":"6","playerPairCounter":"2","bankerPairCounter":"8","table":"spto11bctorobc11","seq":46}}'
    events = parser.parse(raw, "spto11bctorobc11")
    assert len(events) == 1
    s = events[0]
    assert isinstance(s, ShoeSummary)
    assert s.total_games == 34
    assert s.player_win_counter == 15
    assert s.banker_win_counter == 13
    assert s.tie_counter == 6
    assert s.player_win_counter + s.banker_win_counter + s.tie_counter == 34


def test_parse_statistic_bead_plate():
    parser = EventParser()
    val = '{"beadPlate":[["RN9","RN6","BN9","BN6","RN8","BN9"],["BB7","BN1","---","---","---","---"]],"bankerWinCounter":4,"playerWinCounter":4,"tieCounter":0}'
    raw = f'{{"statistic":{{"table":"bc281koreanch281","seq":7,"value":{json_repr(val)}}}}}'
    events = parser.parse(raw, "bc281koreanch281")
    assert len(events) == 1
    snap = events[0]
    assert isinstance(snap, BoardSnapshot)
    assert snap.total_games == 8
    assert snap.raw_rounds_count == 8
    assert len(snap.bead_plate) == 2
    assert snap.bead_plate[0][0].outcome == Outcome.BANKER
    assert snap.bead_plate[1][1].outcome == Outcome.PLAYER
    assert snap.bead_plate[1][2] is None  # '---'


def test_winners_pii_discarded():
    parser = EventParser()
    raw = '{"winners":{"gId":"17680953624","winner":[{"screenName":"amaraa055","userId":"ppc1649690506764","win":"4863.8276"}]}}'
    events = parser.parse(raw, "spto11bctorobc11")
    # Must be completely discarded
    assert len(events) == 0


def test_goodroad_foreign_table_filtered():
    parser = EventParser()
    raw = '{"goodroad":{"sourcetableId":"foreign_table_123","table":"spto11bctorobc11"}}'
    events = parser.parse(raw, "spto11bctorobc11")
    assert len(events) == 0


def test_session_offline_parsing():
    parser = EventParser()
    raw = "<session>offline</session>"
    events = parser.parse(raw, "bc281koreanch281")
    assert len(events) == 1
    assert isinstance(events[0], SessionOfflineEvent)
    assert events[0].table_id == "bc281koreanch281"


def test_pong_parsing():
    parser = EventParser()
    raw_xml = '<pong channel="table-spto11bctorobc11" time="1795164139850" seq="58"/>'
    events = parser.parse(raw_xml, "spto11bctorobc11")
    assert len(events) == 1
    assert isinstance(events[0], PongEvent)
    assert events[0].channel == "table-spto11bctorobc11"
    assert events[0].seq == 58

    raw_json = '{"pong":{"channel":"table-spto11bctorobc11","time":"1795164139850","seq":58}}'
    events = parser.parse(raw_json, "spto11bctorobc11")
    assert len(events) == 1
    assert isinstance(events[0], PongEvent)


def json_repr(s: str) -> str:
    import json
    return json.dumps(s)
