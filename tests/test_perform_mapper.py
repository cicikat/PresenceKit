"""
tests/test_perform_mapper.py — Brief 20 句级表演意图映射

覆盖 core/perform_mapper.py 的三块行为：
  1. 段落归属（_assign_action_text）：do/feel → say 的挂靠规则
  2. rules provider（_map_with_rules）：词典命中 + energy clamp
  3. enrich_say_segments 端到端：fail-open、config 门控、llm provider 三态
"""

import asyncio

import pytest

from core.perform_mapper import (
    _assign_action_text,
    _map_with_rules,
    enrich_say_segments,
)


# ── 1. 段落归属 ──────────────────────────────────────────────────────────────

class TestAssignActionText:
    def test_do_before_say(self):
        all_segs = [
            {"type": "do", "text": "她凑近了"},
            {"type": "say", "text": "你好"},
        ]
        say_segs = [{"type": "say", "text": "你好"}]
        result = _assign_action_text(all_segs, say_segs)
        assert result == [("她凑近了", "你好")]

    def test_do_after_say_residual_on_last(self):
        all_segs = [
            {"type": "say", "text": "你好"},
            {"type": "do", "text": "她笑了"},
        ]
        say_segs = [{"type": "say", "text": "你好"}]
        result = _assign_action_text(all_segs, say_segs)
        assert result == [("她笑了", "你好")]

    def test_trailing_residual_appends_to_last_say_only(self):
        all_segs = [
            {"type": "say", "text": "A"},
            {"type": "say", "text": "B"},
            {"type": "do", "text": "残留动作"},
        ]
        say_segs = [{"type": "say", "text": "A"}, {"type": "say", "text": "B"}]
        result = _assign_action_text(all_segs, say_segs)
        assert result == [("", "A"), ("残留动作", "B")]

    def test_no_say_segments_single_fallback(self):
        all_segs = [
            {"type": "do", "text": "她叹了口气"},
            {"type": "feel", "text": "有点难过"},
        ]
        # Caller-supplied say_segs synthesized as a single whole-content segment,
        # matching build_say_segments' content fallback for a say-less reply.
        say_segs = [{"type": "say", "text": "她叹了口气，有点难过"}]
        result = _assign_action_text(all_segs, say_segs)
        assert result == [("她叹了口气 有点难过", "她叹了口气，有点难过")]

    def test_multi_say_multi_do_interleaved(self):
        all_segs = [
            {"type": "do", "text": "D1"},
            {"type": "say", "text": "S1"},
            {"type": "feel", "text": "F1"},
            {"type": "say", "text": "S2"},
        ]
        say_segs = [{"type": "say", "text": "S1"}, {"type": "say", "text": "S2"}]
        result = _assign_action_text(all_segs, say_segs)
        assert result == [("D1", "S1"), ("F1", "S2")]

    def test_count_mismatch_returns_none(self):
        all_segs = [
            {"type": "say", "text": "S1"},
            {"type": "say", "text": "S2"},
        ]
        say_segs = [{"type": "say", "text": "S1"}]  # caller's list is short
        assert _assign_action_text(all_segs, say_segs) is None


# ── 2. rules provider ────────────────────────────────────────────────────────

class TestMapWithRules:
    def test_posture_lean_in(self):
        perform = _map_with_rules("她凑近了一些", "")
        assert perform["posture"] == "lean_in"

    def test_posture_lean_back_excludes_suo_cheng(self):
        perform = _map_with_rules("她往后缩了一下", "")
        assert perform["posture"] == "lean_back"

    def test_posture_shrink_wins_over_lean_back_when_suo_cheng(self):
        perform = _map_with_rules("她往后缩成一团", "")
        assert perform["posture"] == "shrink"

    def test_posture_shrink(self):
        perform = _map_with_rules("她蜷起来", "")
        assert perform["posture"] == "shrink"
        assert perform["energy"] == pytest.approx(0.3)

    def test_posture_straighten(self):
        perform = _map_with_rules("她挺直了背", "")
        assert perform["posture"] == "straighten"

    def test_head_nod(self):
        assert _map_with_rules("她点了点头", "")["head"] == "nod"

    def test_head_shake(self):
        assert _map_with_rules("她摇了摇头", "")["head"] == "shake"

    def test_head_tilt_r(self):
        assert _map_with_rules("她歪头看着", "")["head"] == "tilt_r"

    def test_head_dip_sets_gaze_down(self):
        perform = _map_with_rules("她垂下头", "")
        assert perform["head"] == "dip"
        assert perform["gaze"] == "down"

    def test_gaze_user(self):
        assert _map_with_rules("她盯着你看", "")["gaze"] == "user"

    def test_gaze_away(self):
        assert _map_with_rules("她移开视线", "")["gaze"] == "away"

    def test_gaze_wander(self):
        assert _map_with_rules("她环顾四周", "")["gaze"] == "wander"

    def test_expression_happy(self):
        assert _map_with_rules("她笑了笑", "")["expression"] == "happy"

    def test_expression_sad_lowers_energy(self):
        perform = _map_with_rules("她叹气", "")
        assert perform["expression"] == "sad"
        assert perform["energy"] == pytest.approx(0.3)

    def test_expression_surprised(self):
        assert _map_with_rules("她瞪大眼睛", "")["expression"] == "surprised"

    def test_expression_angry_sets_intensity(self):
        perform = _map_with_rules("她哼了一声", "")
        assert perform["expression"] == "angry"
        assert perform["intensity"] == pytest.approx(0.5)

    def test_expression_gentle_sets_gaze_away(self):
        perform = _map_with_rules("她脸红了", "")
        assert perform["expression"] == "gentle"
        assert perform["gaze"] == "away"

    def test_expression_word_matches_in_say_text_too(self):
        perform = _map_with_rules("", "笑死我了")
        assert perform["expression"] == "happy"

    def test_energy_bonus_from_exclamation(self):
        perform = _map_with_rules("", "才没有等你很久呢！")
        assert perform["energy"] == pytest.approx(0.7)

    def test_energy_penalty_from_ellipsis(self):
        perform = _map_with_rules("", "是吗……")
        assert perform["energy"] == pytest.approx(0.35)

    def test_energy_clamp_lower_bound(self):
        perform = _map_with_rules("她蜷起来，情绪黯淡", "没关系……")
        assert perform["energy"] == 0.0

    def test_no_hit_returns_none(self):
        assert _map_with_rules("她坐在椅子上安静地看书", "今天天气不错") is None

    def test_defaults_when_only_posture_hits(self):
        perform = _map_with_rules("她凑近了一些", "你好呀")
        assert perform["expression"] is None
        assert perform["head"] is None
        assert perform["gaze"] is None
        assert perform["intensity"] == pytest.approx(0.6)
        assert perform["energy"] == pytest.approx(0.5)


# ── 3. enrich_say_segments 端到端 ────────────────────────────────────────────

def _cfg(**overrides):
    base = {"enabled": True, "provider": "rules", "llm_timeout_sec": 3.0}
    base.update(overrides)
    return {"performance_mapping": base}


class TestEnrichSaySegments:
    async def test_rules_provider_attaches_perform(self, monkeypatch):
        monkeypatch.setattr("core.perform_mapper.get_config", lambda: _cfg())
        reply = "*她凑近了一些*\n你好呀"
        from core.narrative_parser import build_say_segments
        _content, say_segs = build_say_segments(reply)
        result = await enrich_say_segments(reply, say_segs, char_id="yexuan")
        assert len(result) == len(say_segs)
        assert result[0]["perform"]["posture"] == "lean_in"

    async def test_disabled_passthrough(self, monkeypatch):
        monkeypatch.setattr("core.perform_mapper.get_config", lambda: _cfg(enabled=False))
        reply = "*她凑近了一些*\n你好呀"
        from core.narrative_parser import build_say_segments
        _content, say_segs = build_say_segments(reply)
        result = await enrich_say_segments(reply, say_segs, char_id="yexuan")
        assert result == say_segs
        assert "perform" not in result[0]

    async def test_fail_open_on_parser_exception(self, monkeypatch):
        monkeypatch.setattr("core.perform_mapper.get_config", lambda: _cfg())

        def _boom(_reply):
            raise RuntimeError("boom")

        monkeypatch.setattr("core.narrative_parser.parse_narrative_segments", _boom)
        say_segs = [{"type": "say", "text": "你好"}]
        result = await enrich_say_segments("*她凑近了*\n你好", say_segs, char_id="yexuan")
        assert result == say_segs

    async def test_fail_open_on_say_count_mismatch(self, monkeypatch):
        monkeypatch.setattr("core.perform_mapper.get_config", lambda: _cfg())
        reply = "第一句\n第二句"
        # Deliberately mismatched say_segs (caller bug) — must fail-open untouched.
        say_segs = [{"type": "say", "text": "第一句"}]
        result = await enrich_say_segments(reply, say_segs, char_id="yexuan")
        assert result == say_segs

    async def test_llm_provider_legal_response(self, monkeypatch):
        monkeypatch.setattr("core.perform_mapper.get_config", lambda: _cfg(provider="llm"))

        async def fake_chat(messages, call_category=None, **kwargs):
            assert call_category == "perform"
            return (
                '[{"expression":"happy","intensity":0.7,"head":"tilt_r",'
                '"posture":null,"gaze":null,"energy":0.6}]'
            )

        monkeypatch.setattr("core.llm_client.chat", fake_chat)
        reply = "*她歪着头*\n才、才没有等你很久呢"
        from core.narrative_parser import build_say_segments
        _content, say_segs = build_say_segments(reply)
        result = await enrich_say_segments(reply, say_segs, char_id="yexuan")
        assert result[0]["perform"]["expression"] == "happy"
        assert result[0]["perform"]["head"] == "tilt_r"

    async def test_llm_provider_illegal_response_fails_open(self, monkeypatch):
        monkeypatch.setattr("core.perform_mapper.get_config", lambda: _cfg(provider="llm"))

        async def fake_chat(messages, call_category=None, **kwargs):
            return "not json at all"

        monkeypatch.setattr("core.llm_client.chat", fake_chat)
        reply = "*她歪着头*\n才、才没有等你很久呢"
        from core.narrative_parser import build_say_segments
        _content, say_segs = build_say_segments(reply)
        result = await enrich_say_segments(reply, say_segs, char_id="yexuan")
        assert "perform" not in result[0]

    async def test_llm_provider_timeout_fails_open(self, monkeypatch):
        monkeypatch.setattr(
            "core.perform_mapper.get_config",
            lambda: _cfg(provider="llm", llm_timeout_sec=0.05),
        )

        async def fake_chat(messages, call_category=None, **kwargs):
            await asyncio.sleep(1.0)
            return "[]"

        monkeypatch.setattr("core.llm_client.chat", fake_chat)
        reply = "*她歪着头*\n才、才没有等你很久呢"
        from core.narrative_parser import build_say_segments
        _content, say_segs = build_say_segments(reply)
        result = await enrich_say_segments(reply, say_segs, char_id="yexuan")
        assert "perform" not in result[0]
