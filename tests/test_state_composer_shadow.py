"""工单 S2：StatePacket 影子模式。"""
import json

from core import prompt_builder, state_composer
from core.memory.authority import LAYER_AUTHORITY, authority_of


def test_all_known_layers_registered():
    missing = [n for n, _ in prompt_builder.KNOWN_LAYERS if n not in LAYER_AUTHORITY]
    assert missing == []
    assert authority_of("nope") == {"authority": "unregistered"}


def _msgs():
    return [
        {"_layer": "1_system_prompt", "content": "sys"},
        {"_layer": "6c_episodic", "content": "ep"},
        {"_layer": "6c_episodic", "_report_layer": "6c_episodic_fallback", "content": "fb"},
        {"_layer": "6b_event_search", "content": "ev"},
        {"_layer": "5_profile", "content": "pf"},
        {"_layer": "6a_user_identity", "content": "id"},
    ]


def test_reason_duplicates_and_untraced():
    ctx = {"_state_trace": {
        "episodic_ids": ["ep1"], "episodic_event_ids": ["e1", "e2"],
        "episodic_matches": ["keyword"],
        "fallback_ids": ["ep9"], "fallback_event_ids": [],
        "event_log_ids": ["t1"], "event_log_event_ids": ["e1"],
    }}
    packet = state_composer.compose_shadow_packet(
        ctx, {"messages": _msgs(), "debug_info": {"removed_layers": ["6g_dream_impression"]}},
        query="q",
    )
    by = {e["layer"]: e for e in packet["layers"]}
    assert by["6c_episodic"]["reason"] == "query_match"
    assert by["6c_episodic_fallback"]["reason"] == "query_free"
    assert packet["query_free_layers"] == ["6c_episodic_fallback"]
    assert packet["duplicate_event_ids"] == 1
    assert "5_profile" in packet["untraced_sources"]
    assert by["6g_dream_impression"]["dropped"] is True
    assert set(packet["conflict_candidates"]) == {"5_profile", "6a_user_identity"}
    assert "sys" not in json.dumps(packet["layers"][0].get("item_ids"))  # ids only


def test_time_and_long_term_reason():
    msgs = [{"_layer": "6c_episodic", "content": "x"}]
    p = state_composer.compose_shadow_packet(
        {"_state_trace": {"episodic_matches": ["time"], "time_intent": True}},
        {"messages": msgs, "debug_info": {}}, query="q")
    assert p["layers"][0]["reason"] == "time_intent"
    p = state_composer.compose_shadow_packet(
        {"_state_trace": {"episodic_matches": ["long_term_fill"]}},
        {"messages": msgs, "debug_info": {}}, query="q")
    assert p["layers"][0]["reason"] == "long_term_intent"


def test_gating(monkeypatch):
    cfg = {}
    monkeypatch.setattr(state_composer, "shadow_config", lambda: cfg)
    assert state_composer.enabled_for("u", "c") is False
    cfg.update({"uids": ["u"]})
    assert state_composer.enabled_for("u", "c") is True
    assert state_composer.enabled_for("x", "c") is False
    cfg.update({"enabled": True})
    assert state_composer.enabled_for("x", "c") is True


def test_record_writes_and_fail_open(monkeypatch, tmp_path):
    monkeypatch.setattr(state_composer, "enabled_for", lambda u, c: True)
    monkeypatch.setattr(state_composer, "packet_dir", lambda u, c: tmp_path / "state_packet")
    state_composer.record_shadow_packet("u", "c", {}, _msgs(), {}, query="q")
    files = list((tmp_path / "state_packet").glob("*.jsonl"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])["uid"] == "u"

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(state_composer, "compose_shadow_packet", boom)
    state_composer.record_shadow_packet("u", "c", {}, _msgs(), {}, query="q")  # no raise


def test_disabled_does_not_compute(monkeypatch):
    monkeypatch.setattr(state_composer, "enabled_for", lambda u, c: False)

    def boom(*a, **k):
        raise AssertionError("computed")
    monkeypatch.setattr(state_composer, "compose_shadow_packet", boom)
    state_composer.record_shadow_packet("u", "c", {}, _msgs(), {}, query="q")


def test_shadow_does_not_mutate_messages(monkeypatch, tmp_path):
    import copy
    monkeypatch.setattr(state_composer, "enabled_for", lambda u, c: True)
    monkeypatch.setattr(state_composer, "packet_dir", lambda u, c: tmp_path / "sp")
    msgs = _msgs()
    before = copy.deepcopy(msgs)
    state_composer.record_shadow_packet("u", "c", {}, msgs, {}, query="q")
    assert msgs == before
