"""
tests/test_dream_world_management.py — 梦境世界文件夹管理端点契约测试（Brief 96 §1）

Covers:
  ① POST /dream/worlds 新建：骨架文件从 tracked 模板复制、可被 GET /dream/worlds 列出
  ② POST 保留名 / 已存在 / 非法名 拒绝
  ③ PUT rename：文件夹改名 + 同名预设文件跟随改名 + dream_settings.world_layer 同步
  ④ rename 到保留名 / 已存在名 / 源世界不存在 拒绝
  ⑤ rename 时若正在被进行中的梦引用则拒绝
  ⑥ DELETE：删除文件夹 + 同名预设文件 + world_layer 命中时重置为 _default
  ⑦ DELETE 保留名拒绝；DELETE 时若正在被进行中的梦引用则拒绝
  ⑧ PATCH /dream/settings world_layer 动态校验：新建的自定义世界可选，未知名仍被拒
"""

import asyncio

import pytest
from fastapi import HTTPException
from unittest.mock import patch

_UID = "dream_world_mgmt_test"


def _run(coro):
    return asyncio.run(coro)


def _as(uid):
    return patch('admin.routers.dream_common._owner_uid', return_value=uid)


# ═══════════════════════════════════════════════════════════════════════════
# ① 新建世界：骨架来自 tracked 模板
# ═══════════════════════════════════════════════════════════════════════════

def test_create_world_copies_skeleton_from_tracked_template(sandbox):
    from admin.routers.dream import create_dream_world, list_dream_worlds

    with _as(_UID):
        result = _run(create_dream_world({"world": "custom_test_world", "label": "测试世界"}))
        assert result["ok"] is True
        assert result["world"] == "custom_test_world"

        listed = _run(list_dream_worlds())
        assert "custom_test_world" in listed["worlds"]

    world_dir = sandbox.dream_worlds_dir() / "custom_test_world"
    for name in ("ruleset.md", "mes_example.md", "vocab.json", "lorebook.yaml"):
        assert (world_dir / name).exists(), f"missing skeleton file {name}"
    assert (world_dir / "ruleset.md").read_text(encoding="utf-8").strip()
    assert (world_dir / "meta.json").exists()


# ═══════════════════════════════════════════════════════════════════════════
# ② 保留名 / 重复 / 非法名 拒绝
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad_name", ["_default", "reality_derived", "_hidden"])
def test_create_world_rejects_reserved_names(sandbox, bad_name):
    from admin.routers.dream import create_dream_world

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(create_dream_world({"world": bad_name}))
    assert exc.value.status_code == 422


def test_create_world_rejects_duplicate(sandbox):
    from admin.routers.dream import create_dream_world

    with _as(_UID):
        _run(create_dream_world({"world": "dupe_world"}))
        with pytest.raises(HTTPException) as exc:
            _run(create_dream_world({"world": "dupe_world"}))
    assert exc.value.status_code == 409


@pytest.mark.parametrize("bad_name", [".", "..", "a/b", "a\\b"])
def test_create_world_rejects_unsafe_names(sandbox, bad_name):
    from admin.routers.dream import create_dream_world

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(create_dream_world({"world": bad_name}))
    assert exc.value.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# ③ 重命名：文件夹 + 同名预设 + world_layer 同步
# ═══════════════════════════════════════════════════════════════════════════

def test_rename_world_keeps_independent_preset_and_syncs_settings(sandbox):
    from admin.routers.dream import create_dream_world, rename_dream_world, put_dream_preset, dream_settings_patch
    from core.dream.dream_settings import load as load_settings

    with _as(_UID):
        _run(create_dream_world({"world": "rename_src"}))
        _run(put_dream_preset("rename_src", {"content": "hello preset"}))
        _run(dream_settings_patch({"world_layer": "rename_src"}))

        result = _run(rename_dream_world("rename_src", {"new_name": "rename_dst"}))
        assert result["world"] == "rename_dst"

        settings = load_settings(_UID)
        assert settings["world_layer"] == "rename_dst"

    assert not (sandbox.dream_worlds_dir() / "rename_src").exists()
    assert (sandbox.dream_worlds_dir() / "rename_dst").exists()
    old_preset = sandbox.dream_presets_dir() / "rename_src.md"
    assert old_preset.exists()
    assert old_preset.read_text(encoding="utf-8") == "hello preset"
    assert not (sandbox.dream_presets_dir() / "rename_dst.md").exists()


# ═══════════════════════════════════════════════════════════════════════════
# ④ rename 目标非法情况
# ═══════════════════════════════════════════════════════════════════════════

def test_rename_world_missing_source_404(sandbox):
    from admin.routers.dream import rename_dream_world

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(rename_dream_world("does_not_exist", {"new_name": "whatever"}))
    assert exc.value.status_code == 404


def test_rename_world_target_exists_409(sandbox):
    from admin.routers.dream import create_dream_world, rename_dream_world

    with _as(_UID):
        _run(create_dream_world({"world": "src_world"}))
        _run(create_dream_world({"world": "dst_world"}))
        with pytest.raises(HTTPException) as exc:
            _run(rename_dream_world("src_world", {"new_name": "dst_world"}))
    assert exc.value.status_code == 409


def test_rename_world_rejects_reserved_target(sandbox):
    from admin.routers.dream import create_dream_world, rename_dream_world

    with _as(_UID):
        _run(create_dream_world({"world": "src_world2"}))
        with pytest.raises(HTTPException) as exc:
            _run(rename_dream_world("src_world2", {"new_name": "_default"}))
    assert exc.value.status_code == 422


def test_rename_world_rejects_reserved_source(sandbox):
    from admin.routers.dream import rename_dream_world

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(rename_dream_world("reality_derived", {"new_name": "whatever"}))
    assert exc.value.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ rename 时正在被进行中的梦引用 → 拒绝
# ═══════════════════════════════════════════════════════════════════════════

def test_rename_world_blocked_while_dream_active(sandbox):
    from admin.routers.dream import create_dream_world, rename_dream_world
    from core.dream.dream_state import write_state, DreamStatus

    with _as(_UID):
        _run(create_dream_world({"world": "active_world"}))
        write_state(_UID, {
            "status": DreamStatus.DREAM_ACTIVE.value,
            "frozen_world": "active_world",
            "dream_id": "d1",
        })
        with pytest.raises(HTTPException) as exc:
            _run(rename_dream_world("active_world", {"new_name": "renamed_world"}))
        assert exc.value.status_code == 409

        # positive control: after dream ends, rename succeeds
        write_state(_UID, {"status": DreamStatus.REALITY_CHAT.value})
        result = _run(rename_dream_world("active_world", {"new_name": "renamed_world"}))
        assert result["world"] == "renamed_world"


# ═══════════════════════════════════════════════════════════════════════════
# ⑥ 删除：文件夹 + 同名预设 + world_layer 重置为 _default
# ═══════════════════════════════════════════════════════════════════════════

def test_delete_world_keeps_independent_preset_and_resets_settings(sandbox):
    from admin.routers.dream import create_dream_world, delete_dream_world, put_dream_preset, dream_settings_patch
    from core.dream.dream_settings import load as load_settings

    with _as(_UID):
        _run(create_dream_world({"world": "delete_me"}))
        _run(put_dream_preset("delete_me", {"content": "will be orphaned"}))
        _run(dream_settings_patch({"world_layer": "delete_me"}))

        result = _run(delete_dream_world("delete_me"))
        assert result["deleted"] == "delete_me"

        settings = load_settings(_UID)
        assert settings["world_layer"] == "_default"

    assert not (sandbox.dream_worlds_dir() / "delete_me").exists()
    preset = sandbox.dream_presets_dir() / "delete_me.md"
    assert preset.exists()
    assert preset.read_text(encoding="utf-8") == "will be orphaned"


def test_standalone_preset_can_be_created_edited_and_selected_without_world(sandbox):
    from admin.routers.dream import (
        create_standalone_dream_preset,
        delete_standalone_dream_preset,
        dream_settings_patch,
        get_standalone_dream_preset,
        put_standalone_dream_preset,
    )

    with _as(_UID):
        created = _run(create_standalone_dream_preset({"id": "unbound_preset", "content": "first"}))
        assert created["ok"] is True
        assert created["id"] == "unbound_preset"

        assert _run(get_standalone_dream_preset("unbound_preset"))["content"] == "first"

        _run(put_standalone_dream_preset("unbound_preset", {"content": "edited"}))
        assert _run(get_standalone_dream_preset("unbound_preset"))["content"] == "edited"

        settings = _run(dream_settings_patch({"jailbreak_presets": ["unbound_preset"]}))
        assert settings["settings"]["jailbreak_presets"] == ["unbound_preset"]

        with pytest.raises(HTTPException) as exc:
            _run(delete_standalone_dream_preset("unbound_preset"))
        assert exc.value.status_code == 409

        _run(dream_settings_patch({"jailbreak_presets": ["default"]}))
        assert _run(delete_standalone_dream_preset("unbound_preset"))["deleted"] == "unbound_preset"

    assert not (sandbox.dream_presets_dir() / "unbound_preset.md").exists()


@pytest.mark.parametrize("preset_id", ["", "..", "with/slash", "中文"])
def test_standalone_preset_rejects_unsafe_id(sandbox, preset_id):
    from admin.routers.dream import create_standalone_dream_preset

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(create_standalone_dream_preset({"id": preset_id, "content": "x"}))
    assert exc.value.status_code == 422


@pytest.mark.parametrize("reserved", ["_default", "reality_derived"])
def test_delete_world_rejects_reserved(sandbox, reserved):
    from admin.routers.dream import delete_dream_world

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(delete_dream_world(reserved))
    assert exc.value.status_code == 422


def test_delete_world_blocked_while_dream_active(sandbox):
    from admin.routers.dream import create_dream_world, delete_dream_world
    from core.dream.dream_state import write_state, DreamStatus

    with _as(_UID):
        _run(create_dream_world({"world": "active_delete_world"}))
        write_state(_UID, {
            "status": DreamStatus.DREAM_CLOSING.value,
            "frozen_world": "active_delete_world",
            "dream_id": "d2",
        })
        with pytest.raises(HTTPException) as exc:
            _run(delete_dream_world("active_delete_world"))
        assert exc.value.status_code == 409

        # positive control: not referenced by the active dream → deletable
        write_state(_UID, {
            "status": DreamStatus.DREAM_ACTIVE.value,
            "frozen_world": "some_other_world",
            "dream_id": "d3",
        })
        result = _run(delete_dream_world("active_delete_world"))
        assert result["ok"] is True


# ═══════════════════════════════════════════════════════════════════════════
# ⑦ PATCH /dream/settings world_layer 动态校验
# ═══════════════════════════════════════════════════════════════════════════

def test_patch_world_layer_accepts_custom_created_world(sandbox):
    from admin.routers.dream import create_dream_world, dream_settings_patch

    with _as(_UID):
        _run(create_dream_world({"world": "patchable_world"}))
        result = _run(dream_settings_patch({"world_layer": "patchable_world"}))
        assert result["settings"]["world_layer"] == "patchable_world"


def test_patch_world_layer_still_rejects_unknown_name(sandbox):
    from admin.routers.dream import dream_settings_patch

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(dream_settings_patch({"world_layer": "totally_made_up_world"}))
    assert exc.value.status_code == 422


def test_patch_world_layer_accepts_builtin_names_even_without_folder(sandbox):
    """内建六个世界名恒合法，即便对应文件夹在 sandbox 里不存在（fail-open 由 world_loader 保证）。"""
    from admin.routers.dream import dream_settings_patch

    with _as(_UID):
        result = _run(dream_settings_patch({"world_layer": "abo"}))
        assert result["settings"]["world_layer"] == "abo"
