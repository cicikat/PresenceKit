"""
garden lazy-create tests — 新角色首次访问花园时的行为。

覆盖：
1. 无 garden 目录 → get_state 返回完整默认初始态（5槽位）
2. 调用后在 data/runtime/characters/{char_id}/garden 创建 plants.json + storage.json
3. 不触碰 data/runtime/characters/yexuan/garden
4. 缺失 char_id 参数 → TypeError（不 fallback yexuan）
"""

import json
import pytest


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ── 1. 无 garden 目录 → 返回完整默认初始态 ───────────────────────────────────────

def test_new_char_no_garden_dir_returns_default_state(sandbox):
    from core.garden import manager
    from core.garden.constants import FLOWERS

    char_id = "fresh_char"
    assert not sandbox.garden(char_id=char_id).exists()

    state = manager.get_state(char_id=char_id)

    assert len(state["slots"]) == len(FLOWERS), "应返回所有5个槽位"
    assert state["harvest_count"] == 0
    assert state["vase_count"] == 0

    slot_keys = {s["slot_key"] for s in state["slots"]}
    expected_keys = {f["slot_key"] for f in FLOWERS}
    assert slot_keys == expected_keys

    for slot in state["slots"]:
        assert slot["stage"] == "seed"
        assert slot["growth"] == 0
        assert slot["stage_progress"] == 0.0


# ── 2. 调用后路径在 data/runtime/characters/{char_id}/garden ────────────────────

def test_new_char_garden_files_created_in_char_scope(sandbox):
    from core.garden import manager

    char_id = "fresh_char"
    garden_dir = sandbox.garden(char_id=char_id)
    assert not garden_dir.exists()

    manager.get_state(char_id=char_id)

    assert garden_dir.exists(), "garden 目录应已创建"
    assert (garden_dir / "plants.json").exists(), "plants.json 应已创建"
    assert (garden_dir / "storage.json").exists(), "storage.json 应已创建"

    plants = _read_json(garden_dir / "plants.json")
    storage = _read_json(garden_dir / "storage.json")

    assert "slots" in plants
    assert len(plants["slots"]) == 5
    assert storage == {"harvest": [], "vase": [], "history": []}


# ── 3. 不触碰 data/runtime/characters/yexuan/garden ─────────────────────────────

def test_new_char_does_not_touch_yexuan_garden(sandbox):
    from core.garden import manager

    char_id = "fresh_char"
    yexuan_dir = sandbox.garden(char_id="yexuan")

    assert not yexuan_dir.exists()

    manager.get_state(char_id=char_id)

    assert not yexuan_dir.exists(), (
        f"yexuan 的 garden 目录不应被新角色的请求创建: {yexuan_dir}"
    )


# ── 4. 缺失 char_id → 不 fallback，不接受无参调用 ──────────────────────────────

def test_get_state_requires_char_id():
    from core.garden import manager

    with pytest.raises(TypeError):
        manager.get_state()


def test_water_requires_char_id():
    from core.garden import manager

    with pytest.raises(TypeError):
        manager.water("calm", reason="force")


def test_daily_check_requires_char_id():
    from core.garden import manager

    with pytest.raises(TypeError):
        manager.daily_check()


def test_force_water_requires_char_id():
    from core.garden import manager

    with pytest.raises(TypeError):
        manager.force_water()
