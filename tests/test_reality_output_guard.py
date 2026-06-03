"""
tests/test_reality_output_guard.py — P2.2 shared reality output guard

Spec coverage:
  Unit (G1-G6): clean_reality_reply_text() function contract.
  Integration (I1-I4): desktop/mobile path — guard applied before record_assistant_turn.
  QQ isolation (Q1): response_processor.process() segmentation unaffected.
  Dream isolation (D1): dream_pipeline does not import the guard.
  Boundary (B1-B2): scrub and strip_render_tags responsibilities unchanged.
"""

import pytest


# ── helper ────────────────────────────────────────────────────────────────────

def _guard(text, char_name=None):
    from core.reality_output_guard import clean_reality_reply_text
    return clean_reality_reply_text(text, char_name)


# ═══════════════════════════════════════════════════════════════════════════════
# G1. Character-name prefix removed
# ═══════════════════════════════════════════════════════════════════════════════

def test_character_prefix_colon_full_removed():
    assert _guard("叶瑄：你好啊", "叶瑄") == "你好啊"


def test_character_prefix_colon_half_removed():
    assert _guard("叶瑄:你好啊", "叶瑄") == "你好啊"


def test_character_prefix_bracket_removed():
    assert _guard("[叶瑄] 你好啊", "叶瑄") == "你好啊"


def test_character_prefix_says_removed():
    assert _guard("叶瑄 说：你好啊", "叶瑄") == "你好啊"


def test_character_prefix_absent_unchanged():
    text = "你好啊，今天怎么样？"
    assert _guard(text, "叶瑄") == text


def test_no_char_name_prefix_not_removed():
    text = "叶瑄：你好啊"
    result = _guard(text, None)
    assert "叶瑄：" in result


# ═══════════════════════════════════════════════════════════════════════════════
# G2. Tool-call residue tags removed
# ═══════════════════════════════════════════════════════════════════════════════

def test_tool_call_tag_removed():
    text = "<tool_call>get_weather({})</tool_call> 今天天气晴。"
    result = _guard(text)
    assert "<tool_call>" not in result
    assert "今天天气晴" in result


def test_tool_call_multiline_removed():
    text = "<tool_call>\n{\"name\": \"get_time\"}\n</tool_call>现在是三点。"
    result = _guard(text)
    assert "<tool_call>" not in result
    assert "现在是三点" in result


def test_no_tool_tags_unchanged():
    text = "嗯，我在呢。"
    assert _guard(text) == text


# ═══════════════════════════════════════════════════════════════════════════════
# G3. AI self-censor phrases filtered sentence-by-sentence
# ═══════════════════════════════════════════════════════════════════════════════

def test_self_censor_ai_sentence_removed():
    text = "我在。作为一个AI，我没有真实感情。嗯，好的。"
    result = _guard(text)
    assert "作为一个AI" not in result
    assert "我在" in result or "嗯，好的" in result


def test_self_censor_language_model_removed():
    text = "我是一个语言模型，无法真正陪伴你。但我在这里。"
    result = _guard(text)
    assert "语言模型" not in result
    assert "但我在这里" in result


def test_pure_dialogue_preserved():
    text = "嗯，你说的对，我在想这件事。"
    assert _guard(text, "叶瑄") == text


# ═══════════════════════════════════════════════════════════════════════════════
# G4. Empty / falsy inputs handled safely
# ═══════════════════════════════════════════════════════════════════════════════

def test_empty_string_returned_as_is():
    assert _guard("") == ""


def test_whitespace_stripped():
    assert _guard("  你好  ") == "你好"


# ═══════════════════════════════════════════════════════════════════════════════
# G5. Action descriptions NOT touched by guard (scrubber's job)
# ═══════════════════════════════════════════════════════════════════════════════

def test_action_descriptions_preserved():
    text = "我在。（抬起脑袋看你一眼）睡吧。"
    result = _guard(text, "叶瑄")
    assert "（抬起脑袋看你一眼）" in result


def test_markdown_action_preserved():
    text = "*他低头看了你一眼*\n别闹。"
    result = _guard(text, "叶瑄")
    assert "*他低头看了你一眼*" in result


# ═══════════════════════════════════════════════════════════════════════════════
# G6. Render tags NOT touched by guard (strip_render_tags' job)
# ═══════════════════════════════════════════════════════════════════════════════

def test_render_tags_preserved():
    text = "<say>你好。</say><do>他靠近你。</do>"
    result = _guard(text, "叶瑄")
    assert "<say>" in result
    assert "<do>" in result


# ═══════════════════════════════════════════════════════════════════════════════
# I1. desktop/mobile path: character prefix does not reach record_assistant_turn
# ═══════════════════════════════════════════════════════════════════════════════

async def test_desktop_guard_removes_character_prefix_before_record():
    """
    Simulate run_owner_chat_turn():
    raw LLM reply = "叶瑄：你好" → after guard = "你好" → what record_assistant_turn receives.
    """
    received: list[str] = []

    class _FakePipeline:
        class character:
            name = "叶瑄"

        async def fetch_context(self, *a, **kw):
            return {}

        def build_prompt(self, *a, **kw):
            return [], {}

        async def run_llm(self, messages):
            return "叶瑄：你好"

        async def post_process(self, uid, content, reply, **kwargs):
            received.append(reply)
            return {"turn_id": "t1", "critical_written": True, "emotion": "neutral"}

    from core.reality_output_guard import clean_reality_reply_text

    raw = "叶瑄：你好"
    cleaned = clean_reality_reply_text(raw, "叶瑄")
    assert cleaned == "你好"
    # Verify the guard output is what post_process would see as memory_text
    assert "叶瑄：" not in cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# I2. desktop/mobile path: self-censor text does not reach record_assistant_turn
# ═══════════════════════════════════════════════════════════════════════════════

async def test_desktop_guard_removes_self_censor_before_record():
    """
    raw LLM reply with AI self-censor sentence → it must not appear in the
    text that flows into record_assistant_turn (and thus visible output and memory).
    """
    from core.reality_output_guard import clean_reality_reply_text

    raw = "作为一个AI，我没有真实的感情。但我在这里陪你。"
    cleaned = clean_reality_reply_text(raw, "叶瑄")
    assert "作为一个AI" not in cleaned
    assert "我在这里陪你" in cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# I3. desktop/mobile path: tool_call residue does not reach record_assistant_turn
# ═══════════════════════════════════════════════════════════════════════════════

def test_desktop_guard_removes_tool_call_residue():
    from core.reality_output_guard import clean_reality_reply_text

    raw = "<tool_call>get_time({})</tool_call>现在是下午三点。"
    cleaned = clean_reality_reply_text(raw, "叶瑄")
    assert "<tool_call>" not in cleaned
    assert "现在是下午三点" in cleaned


# ═══════════════════════════════════════════════════════════════════════════════
# I4. Integration: guard runs as part of run_owner_chat_turn pipeline shape
#     (monkeypatched pipeline — no actual HTTP call)
# ═══════════════════════════════════════════════════════════════════════════════

async def test_run_owner_chat_turn_guard_applied(monkeypatch):
    """
    Monkeypatch pipeline.run_llm to return a prefixed reply.
    Monkeypatch record_assistant_turn to capture what text it receives.
    Verify the captured text has no character-name prefix.
    """
    captured_assistant_text: list[str] = []

    class _FakeCharacter:
        name = "叶瑄"

    class _FakePipeline:
        character = _FakeCharacter()

        async def fetch_context(self, uid, message, group_id=None):
            return {}

        def build_prompt(self, uid, message, context, **kwargs):
            return [{"role": "user", "content": message}], {}

        async def run_llm(self, messages):
            return "叶瑄：今天很开心见到你。"

        async def post_process(self, uid, content, reply, **kwargs):
            return {"turn_id": "t99", "critical_written": True, "emotion": "happy"}

    async def _fake_record(*, assistant_text, uid, source, **kwargs):
        captured_assistant_text.append(assistant_text)
        from core.turn_sink import TurnResult
        return TurnResult(
            turn_id="t99",
            written_to_memory=True,
            fanout_targets=[],
        )

    import core.pipeline_registry as _reg
    import core.turn_sink as _sink
    import core.memory.user_profile as _up

    monkeypatch.setattr(_reg, "_pipeline", _FakePipeline())
    monkeypatch.setattr(_sink, "record_assistant_turn", _fake_record)
    monkeypatch.setattr(_up, "get_affection_level", lambda uid: {"value": 0, "label": "陌生"})

    # Stub out scheduler notifiers
    import core.scheduler.loop as _sl
    import core.scheduler.state_machine as _sm
    monkeypatch.setattr(_sl, "mark_user_active", lambda: None)
    monkeypatch.setattr(_sm, "notify_owner_turn", lambda uid: None)

    # Stub tool probe to skip (must be async — the real function is a coroutine)
    import admin.routers.chat as _chat

    async def _noop_probe(msg, uid):
        return None

    monkeypatch.setattr(_chat, "_probe_and_execute_tools", _noop_probe)

    # Stub conversation_lock to a no-op async context manager
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_lock(uid):
        yield

    monkeypatch.setattr("core.conversation_gate.conversation_lock", _noop_lock)

    # Stub channels registry to avoid channel lookups
    import channels.registry as _cr
    monkeypatch.setattr(_cr, "get", lambda name: None)

    result = await _chat.run_owner_chat_turn("你好", "desktop")

    assert len(captured_assistant_text) == 1
    # Guard must have removed the "叶瑄：" prefix before record_assistant_turn
    assert "叶瑄：" not in captured_assistant_text[0]
    assert "今天很开心见到你" in captured_assistant_text[0]


# ═══════════════════════════════════════════════════════════════════════════════
# Q1. QQ response_processor.process() still segments correctly
# ═══════════════════════════════════════════════════════════════════════════════

def test_qq_process_still_segments():
    from core.response_processor import process

    long_text = "这是第一句话。" * 80  # well over 500 chars
    segments = process(long_text, "叶瑄")
    assert len(segments) > 1
    for seg in segments:
        assert len(seg) <= 500


def test_qq_process_still_removes_prefix_and_filters():
    from core.response_processor import process

    reply = "叶瑄：作为一个AI，我没有感情。但我在。"
    segments = process(reply, "叶瑄")
    full = " ".join(segments)
    assert "叶瑄：" not in full
    assert "作为一个AI" not in full
    assert "我在" in full


# ═══════════════════════════════════════════════════════════════════════════════
# D1. Dream pipeline does not import the guard
# ═══════════════════════════════════════════════════════════════════════════════

def test_dream_pipeline_does_not_import_guard():
    import importlib, sys

    dp = sys.modules.get("core.dream.dream_pipeline")
    if dp is None:
        dp = importlib.import_module("core.dream.dream_pipeline")

    assert not hasattr(dp, "clean_reality_reply_text"), (
        "dream_pipeline must not import reality_output_guard"
    )
    assert not hasattr(dp, "clean_reality_reply_text"), (
        "dream_pipeline must not import clean_reality_reply_text"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# B1. scrub_reality_output_text still only handles action/narration scrub
# ═══════════════════════════════════════════════════════════════════════════════

def test_scrubber_not_affected_by_guard():
    from core.reality_output_scrubber import scrub_reality_output_text

    # Scrubber is for action descriptions — it must NOT strip prefix or self-censor
    text_with_prefix = "叶瑄：你好。"
    result = scrub_reality_output_text(text_with_prefix)
    # Scrubber keeps dialogue lines; it is not responsible for prefix removal
    assert result is not None
    assert "叶瑄：你好" in result  # scrubber left it intact

    text_with_action = "我在。\n（他低头看了你一眼）\n别担心。"
    scrubbed = scrub_reality_output_text(text_with_action)
    assert "（" not in (scrubbed or "")  # scrubber DID strip the bracket action


# ═══════════════════════════════════════════════════════════════════════════════
# B2. strip_render_tags still only handles render markup
# ═══════════════════════════════════════════════════════════════════════════════

def test_strip_render_tags_not_affected_by_guard():
    from core.response_processor import strip_render_tags

    # strip_render_tags only removes XML-like tags — not prefix, not self-censor
    text = "叶瑄：<say>你好。</say>"
    result = strip_render_tags(text)
    assert "<say>" not in result
    assert "叶瑄：" in result   # prefix intact — not strip_render_tags' job
    assert "你好" in result
