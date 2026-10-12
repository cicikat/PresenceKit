"""
tests/test_act2_stream_anti_collapse.py — cc-tasks/105 · ACT-2

流式路径反坍缩（方案B·软降级）回归测试：
(1) core/memory/short_term.py 一次性信号 note/consume 读写与消费语义。
(2) Pipeline.run_llm_stream() 命中同质坍缩检测时不中断/不截断本轮输出，
    只落信号；未命中或缺 user_id 时不落信号。
(3) core/prompt_builder.py::build() 下一轮读到信号 → 注入 stream_collapse_hint 层
    并立即消费清除（下下轮不再注入）。
"""

from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import itertools

import pytest

_uid_counter = itertools.count()


def _fresh_uid() -> str:
    return f"act2_test_{next(_uid_counter)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. core/memory/short_term.py：一次性信号 note/consume
# ═══════════════════════════════════════════════════════════════════════════════

def test_consume_returns_none_when_no_signal(sandbox):
    from core.memory.short_term import consume_stream_collapse_signal
    assert consume_stream_collapse_signal(_fresh_uid()) is None


def test_note_then_consume_roundtrip(sandbox):
    from core.memory.short_term import (
        consume_stream_collapse_signal,
        note_stream_collapse_signal,
    )
    uid = _fresh_uid()
    note_stream_collapse_signal(uid, "嗯。", char_id=TEST_CHAR_ID)
    assert consume_stream_collapse_signal(uid, char_id=TEST_CHAR_ID) == "嗯。"


def test_consume_is_one_shot(sandbox):
    """信号读到即删——同一 uid 连续两次 consume，第二次必须返回 None。"""
    from core.memory.short_term import (
        consume_stream_collapse_signal,
        note_stream_collapse_signal,
    )
    uid = _fresh_uid()
    note_stream_collapse_signal(uid, "现在，", char_id=TEST_CHAR_ID)
    first = consume_stream_collapse_signal(uid, char_id=TEST_CHAR_ID)
    second = consume_stream_collapse_signal(uid, char_id=TEST_CHAR_ID)
    assert first == "现在，"
    assert second is None


def test_signal_isolated_by_char_id(sandbox):
    """不同 char_id 的信号互不串桶。"""
    from core.memory.short_term import (
        consume_stream_collapse_signal,
        note_stream_collapse_signal,
    )
    uid = _fresh_uid()
    note_stream_collapse_signal(uid, "嗯。", char_id="char_a")
    assert consume_stream_collapse_signal(uid, char_id="char_b") is None
    assert consume_stream_collapse_signal(uid, char_id="char_a") == "嗯。"


def test_signal_persisted_via_sandbox_path(sandbox):
    """落盘位置必须经 sandbox.get_paths()，落在 tmp_path 隔离目录下而非硬编码真实路径。"""
    from core.memory.short_term import note_stream_collapse_signal
    uid = _fresh_uid()
    note_stream_collapse_signal(uid, "嗯。", char_id=TEST_CHAR_ID)
    path = sandbox.stream_collapse_signal(uid, char_id=TEST_CHAR_ID)
    assert path.exists()
    assert str(sandbox._base) in str(path)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Pipeline.run_llm_stream()：命中检测 → 不中断输出 + 落信号
# ═══════════════════════════════════════════════════════════════════════════════

def _make_pipeline():
    from core.pipeline import Pipeline
    return Pipeline.__new__(Pipeline)


def _history_messages(contents: list[str]) -> list[dict]:
    return [{"role": "assistant", "content": c, "_layer": "9_history"} for c in contents]


async def _collect(gen):
    out = []
    async for piece in gen:
        out.append(piece)
    return out


@pytest.mark.asyncio
async def test_stream_collapse_hit_does_not_interrupt_output(monkeypatch, sandbox):
    """流式坍缩样本：命中同质前缀检测时，流式输出原样放行、不中断不截断。"""
    pipeline = _make_pipeline()
    uid = _fresh_uid()
    messages = _history_messages(["嗯。第一条", "嗯。第二条", "嗯。第三条"])

    async def _fake_chat_stream(_messages, *args, **kwargs):
        for piece in ["嗯", "。", "这次还是这样开头"]:
            yield piece

    monkeypatch.setattr("core.llm_client.chat_stream", _fake_chat_stream)

    pieces = await _collect(
        pipeline.run_llm_stream(messages, char_id=TEST_CHAR_ID, user_id=uid)
    )

    assert "".join(pieces) == "嗯。这次还是这样开头"

    from core.memory.short_term import consume_stream_collapse_signal
    assert consume_stream_collapse_signal(uid, char_id=TEST_CHAR_ID) == "嗯。"


@pytest.mark.asyncio
async def test_stream_no_collapse_writes_no_signal(monkeypatch, sandbox):
    """未命中同质检测时不落信号。"""
    pipeline = _make_pipeline()
    uid = _fresh_uid()
    messages = _history_messages(["第一条", "完全不同", "第三条也不同"])

    async def _fake_chat_stream(_messages, *args, **kwargs):
        yield "全新的开口方式"

    monkeypatch.setattr("core.llm_client.chat_stream", _fake_chat_stream)

    pieces = await _collect(
        pipeline.run_llm_stream(messages, char_id=TEST_CHAR_ID, user_id=uid)
    )
    assert "".join(pieces) == "全新的开口方式"

    from core.memory.short_term import consume_stream_collapse_signal
    assert consume_stream_collapse_signal(uid, char_id=TEST_CHAR_ID) is None


@pytest.mark.asyncio
async def test_stream_without_user_id_skips_detection(monkeypatch, sandbox):
    """未传 user_id（无法定位下一轮读取位置）时跳过检测，fail-open 不影响输出。"""
    pipeline = _make_pipeline()
    messages = _history_messages(["嗯。第一条", "嗯。第二条", "嗯。第三条"])

    async def _fake_chat_stream(_messages, *args, **kwargs):
        yield "嗯。还是这样开头"

    monkeypatch.setattr("core.llm_client.chat_stream", _fake_chat_stream)

    pieces = await _collect(pipeline.run_llm_stream(messages, char_id=TEST_CHAR_ID))
    assert "".join(pieces) == "嗯。还是这样开头"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. core/prompt_builder.py::build()：下一轮注入 + 一次性消费
# ═══════════════════════════════════════════════════════════════════════════════

def _apply_build_stubs(monkeypatch):
    """复用 test_prompt_builder_period_scope.py 的做法：屏蔽 build() 里其余落盘/网络相关分支。"""
    import core.prompt_builder as _pb
    import core.presence as _pres
    import core.author_note_rotator as _anr
    import core.config_loader as _cl

    monkeypatch.setattr(_pb, "_load_jailbreak", lambda layer=None, **kwargs: "")
    monkeypatch.setattr(_pb, "_load_style_hint", lambda *, char_id="": "")
    monkeypatch.setattr(_pb, "_format_afterglow_soft_hint", lambda uid, char_id=TEST_CHAR_ID: "")
    monkeypatch.setattr(_pres, "get_last_seen_text", lambda uid: "")
    monkeypatch.setattr(_anr, "get_current_note", lambda paths=None, char_id=None: "")
    monkeypatch.setattr(_cl, "get_config", lambda: {"chat": {}})


def _build_messages(uid: str, char_id: str = TEST_CHAR_ID) -> list[dict]:
    import core.prompt_builder as _pb
    from core.character_loader import Character

    char = Character(name="Companion")
    messages, _debug = _pb.build(
        character=char,
        user_id=uid,
        user_message="在吗",
        history=[],
        relation={"role": "friend"},
        profile={},
        group_context=[],
        char_id=char_id,
    )
    return messages


def _find_layer(messages: list[dict], layer: str) -> dict | None:
    return next((m for m in messages if m.get("_layer") == layer), None)


def test_build_injects_stream_collapse_hint_when_signal_present(monkeypatch, sandbox):
    _apply_build_stubs(monkeypatch)
    from core.memory.short_term import note_stream_collapse_signal

    uid = _fresh_uid()
    note_stream_collapse_signal(uid, "嗯。", char_id=TEST_CHAR_ID)

    messages = _build_messages(uid)
    hint_msg = _find_layer(messages, "stream_collapse_hint")
    assert hint_msg is not None
    assert hint_msg["_drop_priority"] == 95
    assert "嗯" not in hint_msg["content"]  # 填充词前缀：不复读字面，避免二次 priming
    assert "语气词" in hint_msg["content"]


def test_build_injects_literal_hint_for_non_filler_prefix(monkeypatch, sandbox):
    _apply_build_stubs(monkeypatch)
    from core.memory.short_term import note_stream_collapse_signal

    uid = _fresh_uid()
    note_stream_collapse_signal(uid, "现在，", char_id=TEST_CHAR_ID)

    messages = _build_messages(uid)
    hint_msg = _find_layer(messages, "stream_collapse_hint")
    assert hint_msg is not None
    assert "现在" in hint_msg["content"]  # 非填充词前缀：保留引用式文案


def test_build_no_hint_when_no_signal(monkeypatch, sandbox):
    _apply_build_stubs(monkeypatch)
    uid = _fresh_uid()
    messages = _build_messages(uid)
    assert _find_layer(messages, "stream_collapse_hint") is None


def test_build_consumes_signal_once_next_round_clean(monkeypatch, sandbox):
    """第二轮 build() 读到信号并消费；第三轮（再下一轮）信号已清除，不再注入。"""
    _apply_build_stubs(monkeypatch)
    from core.memory.short_term import note_stream_collapse_signal

    uid = _fresh_uid()
    note_stream_collapse_signal(uid, "嗯。", char_id=TEST_CHAR_ID)

    round2 = _build_messages(uid)
    assert _find_layer(round2, "stream_collapse_hint") is not None

    round3 = _build_messages(uid)
    assert _find_layer(round3, "stream_collapse_hint") is None
