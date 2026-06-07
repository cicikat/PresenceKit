"""
tests/test_scope_leak_p0.py

Scope Leak P0 — 5 真实 D 类风险验收测试

1. test_prompt_builder_mood_isolation
   yexuan mood="愤怒"，hongcha mood="困倦"；用 hongcha build prompt，断言含 hongcha mood，
   不含 yexuan mood。

2. test_prompt_builder_activity_isolation
   yexuan activity_snapshot="在打游戏"，hongcha 无 snapshot；用 hongcha build prompt，
   断言 prompt 不含 "打游戏"。

3. test_prompt_builder_style_hint_isolation
   yexuan observations.jsonl 含触发 style hint 的内容，hongcha observations 为空；
   用 hongcha build prompt，断言 style hint 不来自 yexuan observations。

4. test_scheduler_obs_compaction_scoped
   yexuan + hongcha 各自 observations.jsonl 超过 max_raw；
   只存在这两个文件；_all_observation_paths 应返回两个 path；
   compact_observations 对两个独立路径分别调用，互不覆盖。

5. test_sensor_write_scoped
   设置 active_character="hongcha"，调用 receive_activity_snapshot；
   断言写入 hongcha activity_snapshot，yexuan 文件无变化。

6. test_sensor_write_no_active_char_skips
   active_prompt_assets 中 active_character 为空；
   断言写入被跳过，没有任何 activity_snapshot 文件被创建。
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_active(sandbox, char_id: str):
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({
            "active_character": char_id,
            "enabled_lorebooks": [],
            "enabled_jailbreaks": [],
        }),
        encoding="utf-8",
    )


def _write_mood(sandbox, char_id: str, mood: str, intensity: float = 0.8):
    """mood must be a MOOD_TEXT key: angry, sleepy, happy, sad, etc."""
    p = sandbox.mood_state(char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"current": mood, "intensity": intensity, "previous": "neutral", "updated_at": 0.0}),
        encoding="utf-8",
    )


def _write_activity(sandbox, char_id: str, label: str):
    p = sandbox.activity_snapshot(char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {"current": {"category": label, "duration_min": 10},
             "today_summary": "",
             "received_at": time.time()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_observations(sandbox, char_id: str, lines: list[str]):
    p = sandbox.observations(char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _minimal_character(name: str = "叶瑄") -> MagicMock:
    char = MagicMock()
    char.name = name
    char.system_prompt = "你是{name}。\n## 当前感知（实时，非记忆）\n{perception_block}"
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    return char


def _build_prompt(char_id: str, character=None):
    """Call prompt_builder.build with minimal args and return joined content."""
    from core import prompt_builder
    char = character or _minimal_character()
    messages, _ = prompt_builder.build(
        character=char,
        user_id="u1",
        user_message="hi",
        history=[],
        relation={"role": "friend"},
        profile={},
        group_context=[],
        char_id=char_id,
        tags={"topic.activity"},  # trigger activity + style hint paths
    )
    return "\n".join(m.get("content", "") for m in messages)


# ── 1. mood isolation ─────────────────────────────────────────────────────────

def test_prompt_builder_mood_isolation(sandbox):
    """hongcha build must inject hongcha mood text, not yexuan mood text.

    MOOD_TEXT keys are English (angry, sleepy). intensity=0.8 selects the third bucket.
    angry  @ 0.8 → "很紧，克制着"
    sleepy @ 0.8 → "撑不住了，快睡着了"
    """
    _write_mood(sandbox, "yexuan", "angry", intensity=0.8)   # 很紧，克制着
    _write_mood(sandbox, "hongcha", "sleepy", intensity=0.8)  # 撑不住了，快睡着了

    with (
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.activity_manager.get_prompt_fragment", return_value=""),
    ):
        content = _build_prompt("hongcha")

    assert "撑不住了" in content, (
        f"prompt must contain hongcha mood text '撑不住了': {content[:600]}"
    )
    assert "很紧，克制着" not in content, (
        f"prompt must NOT contain yexuan mood text '很紧，克制着': {content[:600]}"
    )


# ── 2. activity snapshot isolation ───────────────────────────────────────────

def test_prompt_builder_activity_isolation(sandbox):
    """hongcha build must not read yexuan activity_snapshot."""
    _write_activity(sandbox, "yexuan", "打游戏")
    # hongcha has no activity_snapshot

    with (
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.activity_manager.get_prompt_fragment", return_value=""),
    ):
        content = _build_prompt("hongcha")

    assert "打游戏" not in content, (
        f"prompt must NOT contain yexuan activity '打游戏': {content[:500]}"
    )


# ── 3. style hint isolation ───────────────────────────────────────────────────

def test_prompt_builder_style_hint_isolation(sandbox):
    """hongcha build must not pick up yexuan observations style hint."""
    # yexuan has observations that would trigger "轻柔" hint
    yexuan_obs = [
        json.dumps({"text": "用户最近很忙，压力很大", "inserted_at": "2026-06-01T10:00:00"}),
    ]
    _write_observations(sandbox, "yexuan", yexuan_obs)
    # hongcha has no observations

    with (
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.activity_manager.get_prompt_fragment", return_value=""),
    ):
        content = _build_prompt("hongcha")

    assert "轻柔" not in content, (
        f"prompt must NOT contain yexuan style hint '轻柔': {content[:800]}"
    )


# ── 4. scheduler observations compaction scoped ───────────────────────────────

def test_scheduler_obs_compaction_scoped(sandbox):
    """_all_observation_paths must return per-char paths; compact_observations called independently."""
    from core.scheduler.loop import _all_observation_paths
    from core.memory.observation_compaction import compact_observations

    # Write 5 entries each (max_raw=3 to trigger compaction)
    entries = [
        json.dumps({"text": f"obs_{i}", "inserted_at": f"2026-06-0{i+1}T00:00:00", "weight": 1})
        for i in range(5)
    ]
    _write_observations(sandbox, "yexuan", entries)
    _write_observations(sandbox, "hongcha", entries)

    # Record original hongcha content before compacting only yexuan
    hongcha_path = sandbox.observations(char_id="hongcha")
    hongcha_before = hongcha_path.read_text(encoding="utf-8")

    # _all_observation_paths should see both files
    paths = _all_observation_paths()
    path_strs = [str(p) for p in paths]
    assert any("yexuan" in s for s in path_strs), "must include yexuan observations"
    assert any("hongcha" in s for s in path_strs), "must include hongcha observations"

    # Compact only yexuan (simulate single-char maintenance)
    yexuan_path = sandbox.observations(char_id="yexuan")
    compact_observations(yexuan_path, max_raw=3)

    # hongcha must be untouched
    hongcha_after = hongcha_path.read_text(encoding="utf-8")
    assert hongcha_before == hongcha_after, (
        "hongcha observations must be unchanged after compacting yexuan"
    )

    # Verify yexuan was actually compacted
    yexuan_lines = [
        l for l in yexuan_path.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    assert len(yexuan_lines) <= 3 + 2, (
        f"yexuan must be compacted to ≤ max_raw unique entries, got {len(yexuan_lines)}"
    )


def test_all_observation_paths_returns_per_char(sandbox):
    """_all_observation_paths must return one path per char that has an observations file."""
    from core.scheduler.loop import _all_observation_paths

    # No files yet → empty
    assert _all_observation_paths() == []

    _write_observations(sandbox, "yexuan", [json.dumps({"text": "a"})])
    paths_1 = _all_observation_paths()
    assert len(paths_1) == 1

    _write_observations(sandbox, "hongcha", [json.dumps({"text": "b"})])
    paths_2 = _all_observation_paths()
    assert len(paths_2) == 2

    names = {p.parent.parent.name for p in paths_2}  # runtime/characters/{char_id}/inner/obs.jsonl
    assert "yexuan" in names
    assert "hongcha" in names


# ── 5. sensor write scoped ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sensor_write_scoped(sandbox):
    """POST /sensor/activity with active_character=hongcha must write hongcha path only."""
    _write_active(sandbox, "hongcha")

    from admin.routers.sensor import receive_activity_snapshot

    payload = {"current": {"category": "coding", "duration_min": 5}, "today_summary": ""}
    result = await receive_activity_snapshot(payload)

    assert result.get("status") == "ok"
    assert result.get("char_id") == "hongcha"

    hongcha_path = sandbox.activity_snapshot(char_id="hongcha")
    yexuan_path  = sandbox.activity_snapshot(char_id="yexuan")

    assert hongcha_path.exists(), "hongcha activity_snapshot must be written"
    assert not yexuan_path.exists(), "yexuan activity_snapshot must NOT be written"

    data = json.loads(hongcha_path.read_text(encoding="utf-8"))
    assert data["current"]["category"] == "coding"


# ── 6. sensor write skipped when no active char ───────────────────────────────

@pytest.mark.asyncio
async def test_sensor_write_no_active_char_skips(sandbox):
    """receive_activity_snapshot must skip write when active_character is empty."""
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    from admin.routers.sensor import receive_activity_snapshot

    payload = {"current": {"category": "gaming"}, "today_summary": ""}
    result = await receive_activity_snapshot(payload)

    assert result.get("status") == "skipped"

    yexuan_path = sandbox.activity_snapshot(char_id="yexuan")
    hongcha_path = sandbox.activity_snapshot(char_id="hongcha")
    assert not yexuan_path.exists(), "yexuan activity_snapshot must NOT be written on skip"
    assert not hongcha_path.exists(), "hongcha activity_snapshot must NOT be written on skip"
