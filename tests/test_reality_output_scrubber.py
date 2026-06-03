"""
tests/test_reality_output_scrubber.py — reality context scrubber (memory / prompt side)

Spec coverage:
  Scrubber unit (1-6): unchanged — function behaviour is unmodified.
  Fanout visible output (7a-7b): UPDATED — fanout channel now receives strip-tags-only text;
      action descriptions ARE allowed in visible output (no longer scrubbed away).
  Memory chain (8): unchanged — short_term + event_log still scrubbed via capture_turn.
  Dream (9): unchanged.

New tests per task spec:
  N1. Visible reply preserves scene / action text (not collapsed to dialogue only).
  N2. short_term history assistant entry is scrubbed.
  N3. event_log assistant entry is scrubbed.
  N4. QQ/mobile visible fanout: render tags stripped but bracket actions survive.
  N5. All-action reply: visible output kept; short_term/event_log skip assistant write.
  N6. Dream unaffected: do/feel/env segments still parsed correctly.
"""

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _scrub(text, *, segments=None):
    from core.reality_output_scrubber import scrub_reality_output_text
    return scrub_reality_output_text(text, segments=segments)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Pure dialogue preserved
# ═══════════════════════════════════════════════════════════════════════════════

def test_pure_dialogue_preserved():
    text = "那先放着。\n休息一下，换个脑子再回来。"
    result = _scrub(text)
    assert result is not None
    assert "那先放着" in result
    assert "休息一下" in result
    assert result.count("\n") >= 1  # two lines


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Whole-line Chinese bracket action → None
# ═══════════════════════════════════════════════════════════════════════════════

def test_cjk_bracket_action_deleted():
    text = "（猫抬起脑袋看你一眼，尾巴尖扫过屏幕边缘。）"
    assert _scrub(text) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Mixed text: bracket action dropped, dialogue kept
# ═══════════════════════════════════════════════════════════════════════════════

def test_mixed_text_bracket_removed_dialogue_kept():
    text = "我在。\n（猫抬起脑袋看你一眼。）\n先休息。"
    result = _scrub(text)
    assert result is not None
    assert "我在" in result
    assert "先休息" in result
    assert "（" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Markdown do deleted, dialogue kept
# ═══════════════════════════════════════════════════════════════════════════════

def test_markdown_do_deleted_dialogue_kept():
    text = "*他低头看了你一眼*\n别闹。"
    result = _scrub(text)
    assert result is not None
    assert "别闹" in result
    assert "*" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Env (> …) deleted, dialogue kept
# ═══════════════════════════════════════════════════════════════════════════════

def test_env_deleted_dialogue_kept():
    text = "> 房间安静下来。\n我在。"
    result = _scrub(text)
    assert result is not None
    assert "我在" in result
    assert ">" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Segments path: only say segments kept
# ═══════════════════════════════════════════════════════════════════════════════

def test_segments_only_say_kept():
    segments = [
        {"type": "say",  "text": "我在。"},
        {"type": "do",   "text": "他靠近你。"},
        {"type": "feel", "text": "你感到安心。"},
        {"type": "env",  "text": "窗外下雨。"},
    ]
    result = _scrub("（他靠近你）我在。", segments=segments)
    assert result == "我在。"


def test_segments_no_say_falls_back_to_line_scrub():
    """No say segments → line scrub on original text; pure dialogue kept."""
    segments = [{"type": "narration", "text": "好，我在。"}]
    assert _scrub("好，我在。", segments=segments) == "好，我在。"


def test_segments_all_non_say_action_text_returns_none():
    """No say segments and raw text is all-action → None."""
    segments = [{"type": "do", "text": "*动作顿住了*"}]
    assert _scrub("*动作顿住了*", segments=segments) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 7a. Fanout visible text: action text NOW ALLOWED (only render tags stripped)
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeChannel:
    def __init__(self, name):
        self.name = name
        self.is_active = True
        self.sent: list[str] = []

    async def send(self, content, user_id, behavior=None, **kwargs):
        self.sent.append(content)


class _FakePipeline:
    async def post_process(self, uid, content, reply, **kwargs):
        return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}


async def test_fanout_channel_preserves_action_text(monkeypatch):
    """Visible fanout now keeps Markdown action markers — not scrubbed."""
    from channels import registry
    from core.turn_sink import TurnSource, record_assistant_turn

    registry._channels = {}
    ch = _FakeChannel("qq")
    registry.register(ch)

    reply_with_action = "我在。\n*他低头看了你一眼*\n别担心。"
    await record_assistant_turn(
        assistant_text=reply_with_action,
        uid="uid_test7",
        source=TurnSource.USER_CHAT,
        user_text="在吗",
        fanout="qq",
        pipeline=_FakePipeline(),
    )

    assert len(ch.sent) == 1
    text = ch.sent[0]
    # Dialogue must survive
    assert "我在" in text or "别担心" in text
    # Action text must also survive in visible output (not scrubbed)
    assert "低头看了你一眼" in text


async def test_fanout_channel_strips_render_tags_not_bracket_actions(monkeypatch):
    """Chinese bracket actions reach the channel; only NMP/XML render tags are stripped."""
    from channels import registry
    from core.turn_sink import TurnSource, record_assistant_turn

    registry._channels = {}
    ch = _FakeChannel("mobile")
    registry.register(ch)

    reply = "好啊。\n（猫抬起脑袋看你一眼。）\n等你回来。"
    await record_assistant_turn(
        assistant_text=reply,
        uid="uid_test7b",
        source=TurnSource.TRIGGER,
        trigger_name="scheduler_test",
        fanout="mobile",
        pipeline=_FakePipeline(),
    )

    assert len(ch.sent) == 1
    sent = ch.sent[0]
    # Dialogue preserved
    assert "好啊" in sent
    assert "等你回来" in sent
    # Bracket action also preserved in visible output
    assert "（" in sent


async def test_fanout_render_tags_stripped():
    """<say>/<do> NMP markup tags must be stripped even though action text is kept."""
    from channels import registry
    from core.turn_sink import TurnSource, record_assistant_turn

    registry._channels = {}
    ch = _FakeChannel("mobile")
    registry.register(ch)

    reply_with_nmp = "<say>我在。</say><do>他靠近你。</do>"
    await record_assistant_turn(
        assistant_text=reply_with_nmp,
        uid="uid_test7c",
        source=TurnSource.USER_CHAT,
        user_text="在吗",
        fanout="mobile",
        pipeline=_FakePipeline(),
    )

    assert len(ch.sent) == 1
    text = ch.sent[0]
    assert "<say>" not in text
    assert "<do>" not in text
    # Content inside the tags survives
    assert "我在" in text or "他靠近你" in text


# ═══════════════════════════════════════════════════════════════════════════════
# 8. short_term history AND event_log both get scrubbed text via capture_turn
# ═══════════════════════════════════════════════════════════════════════════════

def test_capture_turn_event_log_is_scrubbed(sandbox):
    """event_log assistant entry must not contain action descriptions."""
    from unittest.mock import patch
    from core.write_envelope import WriteEnvelope

    env = WriteEnvelope()
    env.can_write_memory = True
    env.can_affect_mood = False

    reply = "我在。\n（猫抬起脑袋看你一眼。）\n别担心。"
    uid = "test_scrub_el"

    appended_st: list[dict] = []
    appended_el: list[dict] = []

    def fake_st_append(user_id, role, content, turn_id=None):
        appended_st.append({"role": role, "content": content})
        return True

    def fake_el_append(user_id, role, content, **kwargs):
        appended_el.append({"role": role, "content": content})
        return True

    with (
        patch("core.memory.short_term.append", side_effect=fake_st_append),
        patch("core.memory.event_log.append", side_effect=fake_el_append),
    ):
        from core.memory.fixation_pipeline import capture_turn
        capture_turn(uid, "在吗", reply, envelope=env)

    el_assistant = [e for e in appended_el if e["role"] == "assistant"]
    assert len(el_assistant) == 1
    assert "（" not in el_assistant[0]["content"]
    assert "）" not in el_assistant[0]["content"]
    assert "我在" in el_assistant[0]["content"] or "别担心" in el_assistant[0]["content"]

    st_assistant = [e for e in appended_st if e["role"] == "assistant"]
    assert len(st_assistant) == 1
    assert "（" not in st_assistant[0]["content"]


def test_capture_turn_all_action_skips_both_writes(sandbox):
    """All-action reply → both short_term and event_log skip assistant entry."""
    from unittest.mock import patch
    from core.write_envelope import WriteEnvelope

    env = WriteEnvelope()
    env.can_write_memory = True
    env.can_affect_mood = False

    reply = "*动作顿住了*\n*沉默蔓延了几秒*"
    uid = "test_scrub_all_action"

    appended_st: list[dict] = []
    appended_el: list[dict] = []

    def fake_st_append(user_id, role, content, turn_id=None):
        appended_st.append({"role": role, "content": content})
        return True

    def fake_el_append(user_id, role, content, **kwargs):
        appended_el.append({"role": role, "content": content})
        return True

    with (
        patch("core.memory.short_term.append", side_effect=fake_st_append),
        patch("core.memory.event_log.append", side_effect=fake_el_append),
    ):
        from core.memory.fixation_pipeline import capture_turn
        capture_turn(uid, "用户消息", reply, envelope=env)

    st_roles = [e["role"] for e in appended_st]
    assert "assistant" not in st_roles

    el_roles = [e["role"] for e in appended_el]
    assert "assistant" not in el_roles


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Dream unaffected: NMP parser still yields do/feel/env segments correctly
# ═══════════════════════════════════════════════════════════════════════════════

def test_dream_nmp_parser_produces_do_feel_env_segments():
    """
    The NMP narrative parser must still return do/feel/env segment types.
    This confirms that Dream's rendering protocol is intact — the scrubber
    module is separate and does NOT interfere with parsing.
    """
    from core.narrative_parser import parse_narrative_segments

    dream_text = "<say>你来了。</say><do>她向你走来。</do><feel>空气凝滞。</feel><env>星光透过窗洒落。</env>"
    result = parse_narrative_segments(dream_text)

    types = {s["type"] for s in result["segments"]}
    assert "say" in types
    assert "do" in types
    assert "feel" in types
    assert "env" in types


def test_dream_pipeline_does_not_import_scrubber():
    """dream_pipeline must not import the reality output scrubber."""
    import importlib
    import sys

    dp_module = sys.modules.get("core.dream.dream_pipeline")
    if dp_module is None:
        dp_module = importlib.import_module("core.dream.dream_pipeline")

    scrubber_imported = hasattr(dp_module, "scrub_reality_output_text")
    assert not scrubber_imported, "dream_pipeline must not import the reality scrubber"


# ═══════════════════════════════════════════════════════════════════════════════
# N1. Visible reply preserves scene / action text — not collapsed to dialogue only
# ═══════════════════════════════════════════════════════════════════════════════

def test_visible_reply_preserves_action_descriptions():
    """
    The HTTP visible_reply in admin/routers/chat.py must preserve the model's
    original action descriptions — only XML render tags are stripped.
    Input:  好好好，不欺负了。\n（在你发顶落下一个很轻的吻）\n睡吧。
    Must NOT be collapsed to:  好好好，不欺负了。\n睡吧。  or  我在。
    """
    from core.response_processor import strip_render_tags

    raw = "好好好，不欺负了。\n（在你发顶落下一个很轻的吻）\n睡吧。"
    visible = strip_render_tags(raw) or raw

    # All three lines must survive strip_render_tags — it only removes XML tags
    assert "好好好，不欺负了" in visible
    assert "发顶" in visible   # action text preserved
    assert "吻" in visible     # action text preserved
    assert "睡吧" in visible

    # Confirm scrubber would have removed it (contrast test)
    scrubbed = _scrub(raw)
    assert "发顶" not in (scrubbed or "")  # scrubber strips it for memory


# ═══════════════════════════════════════════════════════════════════════════════
# N2. short_term history assistant entry does not contain action descriptions
# ═══════════════════════════════════════════════════════════════════════════════

def test_short_term_history_scrubbed(sandbox):
    """short_term assistant write must not contain bracket action or physical action words."""
    from unittest.mock import patch
    from core.write_envelope import WriteEnvelope

    env = WriteEnvelope()
    env.can_write_memory = True
    env.can_affect_mood = False

    reply = "好好好，不欺负了。\n（在你发顶落下一个很轻的吻）\n睡吧。"
    uid = "test_n2_st"

    appended_st: list[dict] = []

    def fake_st_append(user_id, role, content, turn_id=None):
        appended_st.append({"role": role, "content": content})
        return True

    def fake_el_append(*a, **kw):
        return True

    with (
        patch("core.memory.short_term.append", side_effect=fake_st_append),
        patch("core.memory.event_log.append", side_effect=fake_el_append),
    ):
        from core.memory.fixation_pipeline import capture_turn
        capture_turn(uid, "别欺负我", reply, envelope=env)

    st_assistant = [e for e in appended_st if e["role"] == "assistant"]
    assert len(st_assistant) == 1
    content = st_assistant[0]["content"]
    # Action text must be absent from history
    assert "发顶" not in content
    assert "吻" not in content
    assert "（" not in content
    # Dialogue must survive
    assert "好好好，不欺负了" in content or "睡吧" in content


# ═══════════════════════════════════════════════════════════════════════════════
# N3. event_log assistant entry does not contain action descriptions
# ═══════════════════════════════════════════════════════════════════════════════

def test_event_log_scrubbed(sandbox):
    """event_log assistant write must not contain action descriptions."""
    from unittest.mock import patch
    from core.write_envelope import WriteEnvelope

    env = WriteEnvelope()
    env.can_write_memory = True
    env.can_affect_mood = False

    reply = "好好好，不欺负了。\n（在你发顶落下一个很轻的吻）\n睡吧。"
    uid = "test_n3_el"

    appended_el: list[dict] = []

    def fake_st_append(*a, **kw):
        return True

    def fake_el_append(user_id, role, content, **kwargs):
        appended_el.append({"role": role, "content": content})
        return True

    with (
        patch("core.memory.short_term.append", side_effect=fake_st_append),
        patch("core.memory.event_log.append", side_effect=fake_el_append),
    ):
        from core.memory.fixation_pipeline import capture_turn
        capture_turn(uid, "别欺负我", reply, envelope=env)

    el_assistant = [e for e in appended_el if e["role"] == "assistant"]
    assert len(el_assistant) == 1
    content = el_assistant[0]["content"]
    assert "发顶" not in content
    assert "吻" not in content
    assert "（" not in content


# ═══════════════════════════════════════════════════════════════════════════════
# N4. QQ/mobile visible fanout: render tags stripped, bracket actions survive
# ═══════════════════════════════════════════════════════════════════════════════

async def test_qq_fanout_strips_tags_keeps_bracket_actions():
    """
    QQ/mobile fanout visible text must:
    - Strip <say>/<do> render tags (no raw XML).
    - Preserve bracket actions (（…）) for chat texture.
    - Not collapse to "我在。" fallback.
    """
    from channels import registry
    from core.turn_sink import TurnSource, record_assistant_turn

    registry._channels = {}
    ch = _FakeChannel("qq")
    registry.register(ch)

    reply = "<say>好好好，不欺负了。</say>（在你发顶落下一个很轻的吻）<say>睡吧。</say>"
    await record_assistant_turn(
        assistant_text=reply,
        uid="uid_test_n4",
        source=TurnSource.USER_CHAT,
        user_text="别欺负我",
        fanout="qq",
        pipeline=_FakePipeline(),
    )

    assert len(ch.sent) == 1
    text = ch.sent[0]
    # XML render tags must be gone
    assert "<say>" not in text
    assert "</say>" not in text
    # Bracket action must survive
    assert "发顶" in text or "吻" in text
    # Not collapsed to fallback
    assert text != "我在。"


# ═══════════════════════════════════════════════════════════════════════════════
# N5. All-action reply: visible output kept; memory writes skipped
# ═══════════════════════════════════════════════════════════════════════════════

async def test_all_action_reply_visible_kept_memory_skipped():
    """
    All-action reply (e.g. only Chinese bracket):
    - Fanout visible text receives something (not silenced or collapsed).
    - capture_turn skips both short_term and event_log assistant writes.
    """
    from channels import registry
    from core.turn_sink import TurnSource, record_assistant_turn
    from unittest.mock import patch

    registry._channels = {}
    ch = _FakeChannel("mobile")
    registry.register(ch)

    reply = "（他低头看了你一眼。）"

    class _FakePipelineWithCapture:
        """Lets us intercept what memory_text post_process receives."""
        def __init__(self):
            self.received_reply = None

        async def post_process(self, uid, content, reply, **kwargs):
            self.received_reply = reply
            return {"turn_id": "t2", "critical_written": False, "emotion": "neutral"}

    pl = _FakePipelineWithCapture()

    await record_assistant_turn(
        assistant_text=reply,
        uid="uid_test_n5",
        source=TurnSource.USER_CHAT,
        user_text="在吗",
        fanout="mobile",
        pipeline=pl,
    )

    # Visible output was delivered (channel received something)
    assert len(ch.sent) == 1
    # The sent text should contain the stripped version of the reply (strip_render_tags
    # on a bracket-only text does not remove it — that's fine, it's visible)
    assert ch.sent[0]  # non-empty

    # post_process receives empty memory_text (scrubber returned None → "")
    assert pl.received_reply == ""


# ═══════════════════════════════════════════════════════════════════════════════
# N6. Dream segments: do/feel/env still parsed — Dream route unaffected
# ═══════════════════════════════════════════════════════════════════════════════

def test_dream_do_feel_env_segments_intact():
    """NMP parser yields do/feel/env for Dream — scrubber is irrelevant here."""
    from core.narrative_parser import parse_narrative_segments

    dream_text = (
        "<say>你来了。</say>"
        "<do>她低头向你走来，裙角轻扫过地板。</do>"
        "<feel>空气凝滞，像黎明前最后一刻。</feel>"
        "<env>星光透过窗洒落。</env>"
    )
    result = parse_narrative_segments(dream_text)
    types = {s["type"] for s in result["segments"]}
    assert "do" in types
    assert "feel" in types
    assert "env" in types
    assert "say" in types


# ═══════════════════════════════════════════════════════════════════════════════
# Extra edge-case coverage for the scrubber itself (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

def test_english_bracket_action_deleted():
    assert _scrub("(he moves closer)") is None


def test_markdown_feel_deleted():
    assert _scrub("_心里有点慌_") is None


def test_none_input_returns_none():
    assert _scrub(None) is None


def test_empty_input_returns_none():
    assert _scrub("") is None
    assert _scrub("   ") is None


def test_code_block_preserved():
    """Content inside a code fence must never be scrubbed."""
    text = "看看这段代码：\n```\n（注释）\n*bold*\n```\n好的。"
    result = _scrub(text)
    assert result is not None
    assert "（注释）" in result
    assert "*bold*" in result
    assert "好的" in result


def test_multiple_say_segments_joined():
    """Multiple say segments are joined and returned as a single string."""
    segments = [
        {"type": "say", "text": "你好。"},
        {"type": "do",  "text": "动了一下"},
        {"type": "say", "text": "我很好。"},
    ]
    result = _scrub("（动了一下）你好。我很好。", segments=segments)
    assert result is not None
    assert "你好" in result
    assert "我很好" in result
    assert "动了" not in result
