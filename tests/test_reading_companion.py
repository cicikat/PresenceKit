"""
tests/test_reading_companion.py

Reading Companion Chat + Grounding 验收测试

T1.  generate_reply 返回 3-tuple (reply, control, grounding)
T2.  chat 后 transcript.jsonl 写入磁盘
T3.  transcript 包含 user_chat 和 assistant_chat
T4.  chat 不创建 short_term history 目录
T5.  chat 不创建 user_hidden_state 文件
T6.  LLM 异常时有 fallback reply 且 transcript 仍写入
T7.  grounding 包含 current_page / total_pages / progress_pct 字段
T8.  build_reading_grounding_facts 截断超长 page_text
T9.  build_reading_grounding_facts None page_text → has_text=False
T10. format_reading_grounding_for_prompt 含 <page_context> 标签
T11. prompt 包含 current_page 信息
T12. 合法 control 值保存到 transcript
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.activity import reading_companion as RC
from core.activity import transcript as TR
from core.activity.reading_grounding import (
    build_reading_grounding_facts,
    format_reading_grounding_for_prompt,
)


def _transcript_path(sandbox, char_id, uid, session_id) -> Path:
    return sandbox.activity_session_dir(
        char_id=char_id, uid=uid, activity_type="reading", session_id=session_id
    ) / "transcript.jsonl"


def _fake_llm(reply_text: str):
    async def _chat(messages, **kwargs):
        return reply_text
    return _chat


def _raising_llm(exc=RuntimeError("llm error")):
    async def _chat(messages, **kwargs):
        raise exc
    return _chat


# T1
@pytest.mark.asyncio
async def test_generate_reply_returns_3_tuple(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("这段开头很有意思。"))
    result = await RC.generate_reply("yexuan", "user1", "sess1", 3, 50, "sample.pdf", "第三页内容", "你觉得这段怎么样")
    assert isinstance(result, tuple) and len(result) == 3
    reply, control, grounding = result
    assert isinstance(reply, str) and reply
    assert isinstance(control, dict)
    assert isinstance(grounding, dict)


# T2
@pytest.mark.asyncio
async def test_transcript_written_to_disk(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("嗯，看到这里了。"))
    await RC.generate_reply("yexuan", "user1", "sessA", 1, 100, "book.pdf", "内容", "你好")
    p = _transcript_path(sandbox, "yexuan", "user1", "sessA")
    assert p.exists()


# T3
@pytest.mark.asyncio
async def test_transcript_has_both_types(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("这本书不错。"))
    await RC.generate_reply("yexuan", "user1", "sessB", 5, 200, "novel.pdf", "页面内容", "感觉如何")
    p = _transcript_path(sandbox, "yexuan", "user1", "sessB")
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    types = {e["type"] for e in lines}
    assert "user_chat" in types
    assert "assistant_chat" in types


# T4
@pytest.mark.asyncio
async def test_chat_does_not_create_short_term(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("好的。"))
    await RC.generate_reply("yexuan", "user1", "sessC", 1, 10, "f.pdf", None, "test")
    short_term_dir = sandbox._base / "history"
    assert not short_term_dir.exists()


# T5
@pytest.mark.asyncio
async def test_chat_does_not_create_user_hidden_state(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("好的。"))
    await RC.generate_reply("yexuan", "user1", "sessD", 1, 10, "f.pdf", None, "test")
    hidden_state_dir = sandbox._base / "user_hidden_state"
    assert not hidden_state_dir.exists()


# T6
@pytest.mark.asyncio
async def test_fallback_on_llm_error(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _raising_llm())
    reply, control, grounding = await RC.generate_reply("yexuan", "user1", "sessE", 2, 30, "f.pdf", "text", "test")
    assert reply == RC._FALLBACK_REPLY
    p = _transcript_path(sandbox, "yexuan", "user1", "sessE")
    assert p.exists()


# T7
@pytest.mark.asyncio
async def test_grounding_has_expected_fields(sandbox, monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", _fake_llm("好的。"))
    _, _, grounding = await RC.generate_reply("yexuan", "user1", "sessF", 7, 50, "book.pdf", "content", "test")
    assert grounding["current_page"] == 7
    assert grounding["total_pages"] == 50
    assert "progress_pct" in grounding


# T8
def test_build_grounding_truncates_long_text():
    long_text = "X" * 1000
    facts = build_reading_grounding_facts(1, 100, "book.pdf", long_text)
    assert len(facts["page_excerpt"]) <= 210  # 200 chars + suffix
    assert facts["has_text"] is True


# T9
def test_build_grounding_none_text():
    facts = build_reading_grounding_facts(1, 100, "book.pdf", None)
    assert facts["has_text"] is False
    assert facts["page_excerpt"] == ""


# T10
def test_format_has_page_context_tags():
    facts = build_reading_grounding_facts(3, 50, "sample.pdf", "一些内容")
    out = format_reading_grounding_for_prompt(facts)
    assert "<page_context>" in out
    assert "</page_context>" in out


# T11
def test_format_contains_page_number():
    facts = build_reading_grounding_facts(12, 200, "novel.pdf", "段落内容")
    out = format_reading_grounding_for_prompt(facts)
    assert "12" in out
    assert "200" in out


# T12
@pytest.mark.asyncio
async def test_valid_control_saved_to_transcript(sandbox, monkeypatch):
    reply_with_control = '不错的一段。\n\n<activity_control>\n{"commentary_tone":"focused"}\n</activity_control>'
    monkeypatch.setattr("core.llm_client.chat", _fake_llm(reply_with_control))
    await RC.generate_reply("yexuan", "user1", "sessG", 1, 10, "f.pdf", "text", "test")
    p = _transcript_path(sandbox, "yexuan", "user1", "sessG")
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assistant = next(e for e in lines if e["type"] == "assistant_chat")
    assert "control" in assistant
    assert assistant["control"]["commentary_tone"] == "focused"
