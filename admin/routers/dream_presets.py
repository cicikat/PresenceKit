"""Dream presets API responsibilities; legacy names re-exported by dream.py."""

from admin.routers import dream_common
from core import sandbox

from core.dream import world_authoring

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from admin.auth import require_scopes
from admin.routers._common import resolve_requested_char_id

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

_VALID_MEMORY_ACCESS = frozenset({"card_only", "relationship_summary", "full_snapshot"})


_VALID_BOUNDARY_LEVEL = frozenset({"vague", "body_perceptible", "numbers_visible", "threshold_break"})


_VALID_WORLD_LAYER_BUILTIN = frozenset({"reality_derived", "abo", "vampire", "cat", "flower_bud", "custom"})


_VALID_LUCID_MODE = frozenset({"lucid_shared", "non_lucid"})


_VALID_DREAM_MODE = frozenset({"sandbox", "scenario", "mirror", "rpg"})


_VALID_SCENARIO_INJECTION_MODE = frozenset({"strict_stage", "full_script"})


_ENUM_VALIDATORS: dict[str, frozenset] = {
    "memory_access": _VALID_MEMORY_ACCESS,
    "boundary_level": _VALID_BOUNDARY_LEVEL,
    "lucid_mode": _VALID_LUCID_MODE,
    "scenario_injection_mode": _VALID_SCENARIO_INJECTION_MODE,
}


_PATCH_ALLOWED = frozenset({
    "memory_access", "boundary_level", "world_layer", "lucid_mode",
    "enable_dream_lorebook", "jailbreak_presets", "display",
    "scenario_injection_mode",
})


def _valid_world_layer_values() -> frozenset[str]:
    """内建六个世界 ∪ 磁盘上实际发现的世界（含面板新建的自定义世界）∪ _default。

    _default 是保留兜底世界（Brief 96 §1）：删除当前世界时设置回退到它。

    直接扫描 sandbox.get_paths().dream_worlds_dir()（与本文件世界管理端点用的是同一个
    accessor），不经 core.dream.world_loader.discover_worlds() —— 后者用的是
    模块级裸 Path("characters/dream_worlds")，不随 sandbox 测试夹具重定向，
    生产模式下两者指向同一目录、行为等价，但测试夹具下会读到不一致的目录。
    """
    from core.dream.world_loader import discover_worlds
    discovered = frozenset(discover_worlds())
    return _VALID_WORLD_LAYER_BUILTIN | discovered | {"_default"}


@router.get("/dream/presets", summary="列出可用破限预设名（只读，供群聊梦境 per-char 选择器，Brief 100 §3）")
async def list_dream_presets(_auth=Depends(require_scopes("activity"))):
    """列出 characters/dream_presets/ 下经 asset registry 登记的预设（经此层解析
    真实文件名，兼容中文命名的预设）。只读，不返回正文内容。"""
    from core.asset_registry import get_registry

    entries = get_registry().list_all("dream_preset")
    presets = sorted(
        ({"id": e.id, "label": e.label} for e in entries if not e.hidden),
        key=lambda item: item["id"],
    )
    return {"presets": presets}


def _preset_asset_path(preset: str) -> Path:
    """Resolve a standalone dream-preset id to its authored asset path.

    Asset registry ids are the public contract (including mapped legacy file
    names).  New assets use the same safe ASCII id convention as
    ``jailbreak_presets`` in dream settings.
    """
    if not isinstance(preset, str) or not dream_common._SAFE_PRESET_RE.match(preset):
        raise HTTPException(status_code=422, detail=f"预设 id 不合法: {preset!r}")

    from core.asset_registry import get_registry
    from core import sandbox

    try:
        return get_registry().resolve(preset, "dream_preset").path()
    except ValueError:
        from core.authored_asset_resolver import resolve_layered_file
        user_dir, legacy_dir = sandbox.get_paths().dream_preset_read_dirs()
        item = resolve_layered_file(
            user_dir, legacy_dir, f"{preset}.md", logical_asset="dream_preset"
        )
        return item.path if item is not None else sandbox.get_paths().dream_preset_write_path(preset)


def _preset_write_path(preset: str) -> Path:
    if not isinstance(preset, str) or not dream_common._SAFE_PRESET_RE.match(preset):
        raise HTTPException(status_code=422, detail=f"预设 id 不合法: {preset!r}")
    return sandbox.get_paths().dream_preset_write_path(preset)


def _reload_dream_preset_registry() -> None:
    """Make authoring changes visible to the next dream without a restart."""
    from core.asset_registry import reload_registry
    reload_registry()


@router.get("/dream/presets/{preset}", summary="读取独立梦境破限预设")
async def get_standalone_dream_preset(
    preset: str,
    _auth=Depends(require_scopes("activity")),
):
    path = _preset_asset_path(preset)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"预设 {preset} 不存在")
    return {"id": preset, "content": path.read_text(encoding="utf-8")}


@router.post("/dream/presets", summary="新建独立梦境破限预设")
async def create_standalone_dream_preset(
    body: dict,
    _auth=Depends(require_scopes("activity")),
):
    preset = body.get("id")
    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content 必须为字符串")
    read_path = _preset_asset_path(preset)
    path = _preset_write_path(preset)
    if read_path.exists():
        raise HTTPException(status_code=409, detail=f"预设 {preset} 已存在")
    world_authoring.write_preset(path, content)
    dream_common._log_dream_write(kind="dream_preset", read_path=path, canonical=path, source="user")
    _reload_dream_preset_registry()
    return {"ok": True, "id": preset, "bytes": len(content.encode("utf-8"))}


@router.put("/dream/presets/{preset}", summary="编辑独立梦境破限预设")
async def put_standalone_dream_preset(
    preset: str,
    body: dict,
    _auth=Depends(require_scopes("activity")),
):
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content 必须为字符串")
    read_path = _preset_asset_path(preset)
    if not read_path.is_file():
        raise HTTPException(status_code=404, detail=f"预设 {preset} 不存在")
    path = _preset_write_path(preset)
    dream_common._log_dream_write(kind="dream_preset", read_path=read_path, canonical=path)
    world_authoring.write_preset(path, content)
    _reload_dream_preset_registry()
    return {"ok": True, "id": preset, "bytes": len(content.encode("utf-8"))}


@router.delete("/dream/presets/{preset}", summary="删除独立梦境破限预设")
async def delete_standalone_dream_preset(
    preset: str,
    _auth=Depends(require_scopes("activity")),
):
    path = _preset_asset_path(preset)
    if not path.is_file():
        legacy = sandbox.get_paths().legacy_dream_presets_dir() / f"{preset}.md"
        if legacy.is_file():
            raise HTTPException(status_code=409, detail="legacy fallback Dream 预设为只读，不能删除")
        raise HTTPException(status_code=404, detail=f"预设 {preset} 不存在")

    from core.dream.dream_settings import load as _load_settings
    if preset in (_load_settings(dream_common._owner_uid(), char_id=dream_common._active_dream_char_id()).get("jailbreak_presets") or []):
        raise HTTPException(status_code=409, detail="该预设正在下一场梦的选用列表中，请先取消选用")

    canonical = _preset_write_path(preset)
    if path != canonical:
        raise HTTPException(status_code=409, detail="legacy fallback Dream 预设为只读，不能删除")
    world_authoring.delete_preset(path)
    _reload_dream_preset_registry()
    legacy_reappeared = (sandbox.get_paths().legacy_dream_presets_dir() / path.name).is_file()
    return {"ok": True, "deleted": preset, "legacy_fallback_reappeared": legacy_reappeared}


@router.get("/dream/settings", summary="读取梦境设置（全字段）")
async def dream_settings_get(
    char_id: str | None = Query(default=None),
    _auth=Depends(require_scopes("activity")),
):
    """Read-only: returns all dream settings fields with defaults applied."""
    uid = dream_common._owner_uid()
    from core.dream.dream_settings import load as _load
    return _load(uid, char_id=dream_common._validated_live_char_id(char_id))


@router.patch("/dream/settings", summary="部分更新梦境设置（校验枚举值；仅影响下一场梦）")
async def dream_settings_patch(body: dict, _auth=Depends(require_scopes("activity"))):
    """
    Partial update for dream settings. Validates enum values before writing.

    Allowed fields: memory_access / boundary_level / world_layer / lucid_mode /
                    enable_dream_lorebook / display / scenario_injection_mode

    ★ NEVER backfills into a running dream's frozen_world / lucid_mode.
      Those fields are frozen at dream entry (enter_dream reads from settings
      and copies into dream_state). PATCH only affects the next dream session.
      Changing world_layer while DREAM_ACTIVE does NOT change the current dream.
    """
    uid = dream_common._owner_uid()
    char_id = resolve_requested_char_id(
        body.get("char_id"), fallback=dream_common._active_dream_char_id,
    )
    from core.dream.dream_settings import load as _load, save as _save

    updates = {k: v for k, v in body.items() if k in _PATCH_ALLOWED}
    if not updates:
        raise HTTPException(status_code=422, detail=f"可更新字段：{sorted(_PATCH_ALLOWED)}")

    errors: list[str] = []
    for key, valid_set in _ENUM_VALIDATORS.items():
        if key in updates:
            val = updates[key]
            if val not in valid_set:
                errors.append(f"{key}={val!r} 非法，有效值：{sorted(valid_set)}")
    if "world_layer" in updates:
        valid_worlds = _valid_world_layer_values()
        if updates["world_layer"] not in valid_worlds:
            errors.append(f"world_layer={updates['world_layer']!r} 非法，有效值：{sorted(valid_worlds)}")
    if "enable_dream_lorebook" in updates and not isinstance(updates["enable_dream_lorebook"], bool):
        errors.append(
            f"enable_dream_lorebook 必须为 bool，收到：{updates['enable_dream_lorebook']!r}"
        )
    if "jailbreak_presets" in updates:
        val = updates["jailbreak_presets"]
        if not isinstance(val, list) or len(val) == 0 or len(val) > 10:
            errors.append("jailbreak_presets 必须为非空列表（最多 10 项）")
        else:
            for item in val:
                if not isinstance(item, str) or not dream_common._SAFE_PRESET_RE.match(item):
                    errors.append(
                        f"jailbreak_presets 条目 {item!r} 非法，只允许字母/数字/下划线/短横线（1-64字符）"
                    )
    if "display" in updates:
        val = updates["display"]
        if not isinstance(val, dict):
            errors.append(f"display 必须为对象，收到：{val!r}")
        elif set(val) != {"physiological_arousal"}:
            errors.append("display 只允许 physiological_arousal 字段")
        elif not isinstance(val["physiological_arousal"], bool):
            errors.append(
                "display.physiological_arousal 必须为 bool，"
                f"收到：{val['physiological_arousal']!r}"
            )
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    current = _load(uid, char_id=char_id)
    current.update(updates)
    _save(uid, current, char_id=char_id)
    return {"ok": True, "settings": current}
