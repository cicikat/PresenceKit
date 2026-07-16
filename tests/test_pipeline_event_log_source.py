"""
tests/test_pipeline_event_log_source.py — Brief 79 §1

post_process_critical() 把 web_echo / coplay_echo（调用方入参）与本地重新判定的
dream_echo（core.pipeline._detect_dream_echo，只读，不消费
forced_impression_rounds_left）换算成 event_log 的 source 标记。

覆盖：
  1. web_echo=True → event_log 当日文件 assistant 行 source:web
  2. coplay_echo=True → source:coplay
  3. dream_echo（_detect_dream_echo 返回 True）→ source:dream_echo，优先级高于 web/coplay
  4. 三者皆无 → 不写 source 字段（回归）
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import core.pipeline as _pipeline_mod
from core.pipeline import Pipeline
from core.write_envelope import stamp_user_chat


class _MockCharacter:
    name = "Companion"


def _day_log_text(uid: str) -> str:
    from core.sandbox import get_paths
    day_dir = get_paths().user_memory_root(uid) / "event_log"
    day_files = [f for f in day_dir.glob("*.md") if f.name != "full_log.md"]
    assert day_files, "event_log 今日文件未创建"
    return day_files[0].read_text(encoding="utf-8")


async def test_web_echo_tags_source_web(sandbox, monkeypatch):
    monkeypatch.setattr(_pipeline_mod, "_detect_dream_echo", lambda *a, **kw: False)
    pipeline = Pipeline(_MockCharacter(), lore_engine=None)

    await pipeline.post_process_critical(
        "uid_source_web", "帮我查下天气", "查到了，明天晴",
        envelope=stamp_user_chat(), web_echo=True,
    )

    assert "source:web" in _day_log_text("uid_source_web")


async def test_coplay_echo_tags_source_coplay(sandbox, monkeypatch):
    monkeypatch.setattr(_pipeline_mod, "_detect_dream_echo", lambda *a, **kw: False)
    pipeline = Pipeline(_MockCharacter(), lore_engine=None)

    await pipeline.post_process_critical(
        "uid_source_coplay", "继续演", "（演出内容）",
        envelope=stamp_user_chat(), coplay_echo=True,
    )

    assert "source:coplay" in _day_log_text("uid_source_coplay")


async def test_dream_echo_tags_source_and_wins_over_web_and_coplay(sandbox, monkeypatch):
    monkeypatch.setattr(_pipeline_mod, "_detect_dream_echo", lambda *a, **kw: True)
    pipeline = Pipeline(_MockCharacter(), lore_engine=None)

    await pipeline.post_process_critical(
        "uid_source_dream", "你好", "（带着梦里的印象回应）",
        envelope=stamp_user_chat(), web_echo=True, coplay_echo=True,
    )

    text = _day_log_text("uid_source_dream")
    assert "source:dream_echo" in text
    assert "source:web" not in text
    assert "source:coplay" not in text


async def test_no_echo_flags_omits_source_field(sandbox, monkeypatch):
    """三者皆无 → event_log 不写 source 字段（回归：普通轮为空标记）。"""
    monkeypatch.setattr(_pipeline_mod, "_detect_dream_echo", lambda *a, **kw: False)
    pipeline = Pipeline(_MockCharacter(), lore_engine=None)

    await pipeline.post_process_critical(
        "uid_source_none", "普通对话", "普通回复",
        envelope=stamp_user_chat(),
    )

    assert "source:" not in _day_log_text("uid_source_none")
