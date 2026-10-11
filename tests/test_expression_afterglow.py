import time
from core.memory import mood_state
from core.expression_signal import explicit_expression
from core.perform_mapper import _map_with_rules


def test_source_order_ttl_no_read_renewal(sandbox, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1000)
    mood_state.update("happy", source_turn_id="new", source_at=999, char_id="companion")
    before = sandbox.mood_state(char_id="companion").read_bytes()
    mood_state.update("sad", source_turn_id="late", source_at=998, char_id="companion")
    assert mood_state.get_current(char_id="companion") == "happy"
    assert sandbox.mood_state(char_id="companion").read_bytes() == before
    monkeypatch.setattr(time, "time", lambda: 2800)
    assert mood_state.load(char_id="companion")["has_signal"] is False
    assert sandbox.mood_state(char_id="companion").read_bytes() == before


def test_no_signal_neutral_retract_and_legacy(sandbox, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1000)
    assert mood_state.get_current(char_id="companion") == ""
    mood_state.update("happy", source_turn_id="a", source_at=990, char_id="companion")
    mood_state.update("", source_turn_id="b", source_at=995, char_id="companion")
    assert mood_state.load(char_id="companion")["source_turn_id"] == "a"
    mood_state.update("sad", source_turn_id="late", source_at=991, char_id="companion")
    assert mood_state.get_current(char_id="companion") == "happy"
    mood_state.update("neutral", source_turn_id="c", source_at=996, char_id="companion")
    assert mood_state.load(char_id="companion")["has_signal"] is True
    mood_state.update("", source_turn_id="d", source_at=997, retract=True, char_id="companion")
    assert mood_state.get_current(char_id="companion") == ""
    mood_state.save({"current": "sleepy", "updated_at": 1000}, char_id="other")
    assert mood_state.get_current(char_id="other") == ""


def test_explicit_only_no_quoted_user_or_negated_feelings():
    assert explicit_expression("<feel>有点开心</feel><say>嗯</say>") == "happy"
    for text in ("<say>你不开心吗！</say>", "<feel>并不开心</feel>", '<feel>引用“很开心”</feel>', "<feel>你很开心</feel>"):
        assert explicit_expression(text) == ""
    assert _map_with_rules("", "你笑了吗！") is None
    assert _map_with_rules("没有笑", "嗯") is None
