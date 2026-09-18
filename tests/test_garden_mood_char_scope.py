from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_garden_mood_char_scope.py

P1-0F: garden manager mood_state char_id 隔离验收测试

Covers:
1. force_water(char_id="character_b") 读取 character_b mood，不读 yexuan mood
2. force_water(char_id=TEST_CHAR_ID) 读取 yexuan mood，不读 character_b mood
3. auto_water_tick(char_id="character_b") 读取 character_b mood
4. get_state(char_id="character_b") 使用 character_b 花园路径，不接触 yexuan 路径
5. water() 写入 char_id 对应花园路径，不写另一角色路径
6. 生产调用点（garden_tools.water_garden）透传 active char_id
7. 生产调用点（admin/routers/garden）透传 active char_id
8. 回归：char_id="yexuan_j5412" 的 mood 读取到 j5412 路径
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.garden import manager as garden_manager


# ── helpers ───────────────────────────────────────────────────────────────────

def _seed_mood(sandbox, char_id: str, mood: str) -> None:
    p = sandbox.mood_state(char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"current": mood, "intensity": 0.5, "previous": "neutral", "updated_at": 0.0}),
        encoding="utf-8",
    )


def _write_active(sandbox, char_id: str) -> None:
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


# ── 1. force_water reads character_b mood, not yexuan ────────────────────────────

def test_force_water_character_b_reads_character_b_mood(sandbox):
    """force_water(char_id='character_b') must use character_b mood, not yexuan's."""
    # character_b mood → calm slot (matches neutral-ish moods)
    # yexuan mood → different slot
    _seed_mood(sandbox, "character_b", "neutral")
    _seed_mood(sandbox, TEST_CHAR_ID, "yandere")

    captured = []

    real_get_current = __import__(
        "core.memory.mood_state", fromlist=["get_current"]
    ).get_current

    def spy_get_current(*, char_id=TEST_CHAR_ID):
        captured.append(char_id)
        return real_get_current(char_id=char_id)

    with patch("core.memory.mood_state.get_current", side_effect=spy_get_current):
        garden_manager.force_water(char_id="character_b")

    assert captured, "get_current must be called"
    assert all(cid == "character_b" for cid in captured), (
        f"force_water(char_id='character_b') must call get_current(char_id='character_b'), got {captured}"
    )


# ── 2. force_water reads yexuan mood, not character_b ────────────────────────────

def test_force_water_yexuan_reads_yexuan_mood(sandbox):
    """force_water(char_id=TEST_CHAR_ID) must use yexuan mood, not character_b's."""
    _seed_mood(sandbox, TEST_CHAR_ID, "neutral")
    _seed_mood(sandbox, "character_b", "yandere")

    captured = []
    real_get_current = __import__(
        "core.memory.mood_state", fromlist=["get_current"]
    ).get_current

    def spy_get_current(*, char_id=TEST_CHAR_ID):
        captured.append(char_id)
        return real_get_current(char_id=char_id)

    with patch("core.memory.mood_state.get_current", side_effect=spy_get_current):
        garden_manager.force_water(char_id=TEST_CHAR_ID)

    assert all(cid == TEST_CHAR_ID for cid in captured), (
        f"force_water(char_id=TEST_CHAR_ID) must call get_current(char_id=TEST_CHAR_ID), got {captured}"
    )


# ── 3. auto_water_tick passes char_id to get_current ─────────────────────────

def test_auto_water_tick_passes_char_id_to_mood(sandbox, monkeypatch):
    """auto_water_tick(char_id='character_b') must call get_current(char_id='character_b')."""
    _seed_mood(sandbox, "character_b", "neutral")
    # Force probability to always trigger
    monkeypatch.setattr(garden_manager.random, "random", lambda: 0.0)

    captured = []
    real_get_current = __import__(
        "core.memory.mood_state", fromlist=["get_current"]
    ).get_current

    def spy_get_current(*, char_id=TEST_CHAR_ID):
        captured.append(char_id)
        return real_get_current(char_id=char_id)

    with patch("core.memory.mood_state.get_current", side_effect=spy_get_current):
        garden_manager.auto_water_tick(char_id="character_b")

    assert captured, "get_current must be called when probability triggers"
    assert all(cid == "character_b" for cid in captured), (
        f"auto_water_tick(char_id='character_b') must pass char_id='character_b', got {captured}"
    )


# ── 4. get_state uses char_id garden path, not other char ────────────────────

def test_get_state_uses_char_id_path(sandbox):
    """get_state(char_id='character_b') must bootstrap character_b garden, not yexuan's."""
    state = garden_manager.get_state(char_id="character_b")

    character_b_plants = sandbox.garden(char_id="character_b") / "plants.json"
    yexuan_plants  = sandbox.garden(char_id=TEST_CHAR_ID) / "plants.json"

    assert character_b_plants.exists(), "get_state must bootstrap character_b plants.json"
    assert not yexuan_plants.exists(), f"get_state(char_id='character_b') must NOT create {TEST_CHAR_ID} plants.json"

    assert "slots" in state


# ── 5. water writes to char_id path only ─────────────────────────────────────

def test_water_writes_to_char_id_path_only(sandbox):
    """water(char_id='character_b') must write character_b garden, not yexuan's."""
    result = garden_manager.water("calm", reason="test", char_id="character_b")

    character_b_plants = sandbox.garden(char_id="character_b") / "plants.json"
    yexuan_plants  = sandbox.garden(char_id=TEST_CHAR_ID) / "plants.json"

    assert result["ok"] is True
    assert character_b_plants.exists(), "water must write character_b plants.json"
    assert not yexuan_plants.exists(), f"water(char_id='character_b') must not create {TEST_CHAR_ID} plants.json"

    data = json.loads(character_b_plants.read_text(encoding="utf-8"))
    assert data["slots"]["calm"]["growth"] > 0


# ── 6. garden_tools.water_garden passes active char_id ───────────────────────

@pytest.mark.asyncio
async def test_water_garden_tool_passes_active_char_id(sandbox, character_b_registered):
    """water_garden() must resolve active_character and pass it to force_water."""
    _write_active(sandbox, "character_b")

    captured_char_ids = []
    real_force_water = garden_manager.force_water

    def spy_force_water(mood=None, *, char_id=TEST_CHAR_ID):
        captured_char_ids.append(char_id)
        return {"ok": False, "reason": "no_slot_for_mood", "mood": "neutral"}

    with patch.object(garden_manager, "force_water", side_effect=spy_force_water):
        from core.tools.garden_tools import water_garden
        await water_garden()

    assert captured_char_ids, "force_water must be called"
    assert captured_char_ids[0] == "character_b", (
        f"water_garden must pass active char_id='character_b', got {captured_char_ids[0]!r}"
    )


# ── 7. admin garden route passes active char_id ──────────────────────────────

@pytest.mark.asyncio
async def test_garden_admin_route_passes_active_char_id(sandbox, character_b_registered):
    """GET /garden/state must call get_state(char_id=active_character)."""
    _write_active(sandbox, "character_b")

    captured = []
    real_get_state = garden_manager.get_state

    def spy_get_state(*, char_id=TEST_CHAR_ID):
        captured.append(char_id)
        return real_get_state(char_id=char_id)

    with patch.object(garden_manager, "get_state", side_effect=spy_get_state):
        from admin.routers.garden import get_garden_state
        await get_garden_state()

    assert captured == ["character_b"], (
        f"admin garden router must resolve active char_id='character_b', got {captured}"
    )


@pytest.mark.asyncio
async def test_garden_admin_route_explicit_char_id_ignores_active(
    sandbox, character_b_registered,
):
    """GET /garden/state?char_id=character_b must not follow a different active."""
    _write_active(sandbox, TEST_CHAR_ID)

    captured = []
    real_get_state = garden_manager.get_state

    def spy_get_state(*, char_id=TEST_CHAR_ID):
        captured.append(char_id)
        return real_get_state(char_id=char_id)

    with patch.object(garden_manager, "get_state", side_effect=spy_get_state):
        from admin.routers.garden import get_garden_state
        await get_garden_state(char_id="character_b")

    assert captured == ["character_b"], (
        f"explicit char_id must win over active, got {captured}"
    )


# ── 8. char_id='yexuan_j5412' reads j5412 mood path ─────────────────────────

def test_explicit_char_id_reads_correct_mood_path(sandbox):
    """force_water(char_id='yexuan_j5412') reads j5412 mood bucket, not base yexuan."""
    _seed_mood(sandbox, "yexuan_j5412", "happy")
    _seed_mood(sandbox, TEST_CHAR_ID, "sleepy")

    captured = []
    real_get_current = __import__(
        "core.memory.mood_state", fromlist=["get_current"]
    ).get_current

    def spy_get_current(*, char_id=TEST_CHAR_ID):
        captured.append(char_id)
        return real_get_current(char_id=char_id)

    with patch("core.memory.mood_state.get_current", side_effect=spy_get_current):
        garden_manager.force_water(char_id="yexuan_j5412")

    assert captured, "get_current must be called"
    assert all(cid == "yexuan_j5412" for cid in captured), (
        f"force_water(char_id='yexuan_j5412') must read j5412 mood, got {captured}"
    )


# ── 9. two-char isolation: character_b garden ≠ yexuan garden ────────────────────

def test_two_char_garden_isolation(sandbox):
    """Watering character_b and yexuan gardens must be completely independent."""
    # Water yexuan 3 times
    for _ in range(3):
        garden_manager.water("calm", reason="test", char_id=TEST_CHAR_ID)

    # Water character_b 1 time
    garden_manager.water("calm", reason="test", char_id="character_b")

    yexuan_data  = json.loads((sandbox.garden(char_id=TEST_CHAR_ID) / "plants.json").read_text())
    character_b_data = json.loads((sandbox.garden(char_id="character_b") / "plants.json").read_text())

    assert yexuan_data["slots"]["calm"]["growth"] == 30, f'{TEST_CHAR_ID} calm growth must be 30'
    assert character_b_data["slots"]["calm"]["growth"] == 10, "character_b calm growth must be 10"
