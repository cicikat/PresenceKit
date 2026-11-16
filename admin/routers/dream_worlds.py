"""Dream worlds API responsibilities; legacy names re-exported by dream.py."""

import logging
import json
import re
import re as _re
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from admin.auth import require_scopes
from admin.routers._common import requested_char_id, resolve_requested_char_id
from core.config_loader import get_config
from core.sandbox import get_paths

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

_SAFE_WORLD_RE = _re.compile(r'^[^/\\<>:"|?*\x00-\x1f]{1,64}$')


_RESERVED_WORLD_NAMES = frozenset({"_default", "reality_derived"})


def _validate_world_name(world: str, *, allow_reserved: bool = False) -> None:
    """校验世界名称：合法文件名字符集 + 不是 . / ..（防越出 dream_worlds_dir 一级）。

    allow_reserved=False 时额外拒绝 _default / reality_derived 及任意下划线开头
    的隐藏名（与 world_loader.discover_worlds() 的隐藏目录约定一致）。
    """
    from admin.routers import dream as _runtime
    if not _runtime._SAFE_WORLD_RE.match(world) or world in (".", ".."):
        raise _runtime.HTTPException(status_code=422, detail=f"世界名称不合法: {world!r}")
    if not allow_reserved and (world in _runtime._RESERVED_WORLD_NAMES or world.startswith("_")):
        raise _runtime.HTTPException(status_code=422, detail=f"{world!r} 是保留名，不能使用")


def _world_dir(world: str):
    """Resolve a Dream world package for reading (legacy fallback preserved)."""
    from admin.routers import dream as _runtime
    if not _runtime._SAFE_WORLD_RE.match(world) or world in (".", ".."):
        raise _runtime.HTTPException(status_code=422, detail=f"世界名称不合法: {world!r}")
    from core.dream.world_loader import resolve_dream_world
    item = resolve_dream_world(world)
    return item.path if item is not None else _runtime.get_paths().dream_worlds_dir() / world


def _world_write_dir(world: str) -> Path:
    from admin.routers import dream as _runtime
    _runtime._validate_world_name(world, allow_reserved=True)
    return _runtime.get_paths().dream_world_write_dir(world)


def _materialize_world_for_write(world: str) -> Path:
    """Copy one effective world package into userdata before mutating it."""
    from admin.routers import dream as _runtime
    read_dir = _runtime._world_dir(world)
    write_dir = _runtime._world_write_dir(world)
    if write_dir.exists():
        return write_dir
    if not read_dir.is_dir():
        raise _runtime.HTTPException(status_code=404, detail=f"世界 {world} 不存在")
    import shutil as _shutil
    _shutil.copytree(read_dir, write_dir)
    _runtime._log_dream_write(kind="dream_world", read_path=read_dir, canonical=write_dir)
    return write_dir


def _preset_path(world: str):
    """Resolve the legacy-compatible world-named preset for reading."""
    from admin.routers import dream as _runtime
    if not _runtime._SAFE_WORLD_RE.match(world) or world in (".", ".."):
        raise _runtime.HTTPException(status_code=422, detail=f"世界名称不合法: {world!r}")
    from core.authored_asset_resolver import resolve_layered_file
    user_dir, legacy_dir = _runtime.get_paths().dream_preset_read_dirs()
    item = resolve_layered_file(user_dir, legacy_dir, f"{world}.md", logical_asset="dream_preset")
    return item.path if item is not None else _runtime.get_paths().dream_preset_write_path(world)


def _dream_active_referencing_world(world: str) -> bool:
    """当前是否有「进行中」的梦冻结着这个世界（DREAM_ACTIVE / CLOSING / EXIT_REQUESTED）。"""
    from admin.routers import dream as _runtime
    from core.dream.dream_state import read_state, DreamStatus
    uid = _runtime._owner_uid()
    state = read_state(uid)
    active_statuses = {
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_CLOSING.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
    }
    return state.get("status") in active_statuses and state.get("frozen_world") == world


def _reset_world_layer_setting_if(match_world: str, reset_to: str) -> None:
    """若当前 dream_settings.world_layer == match_world，改写为 reset_to。"""
    from admin.routers import dream as _runtime
    from core.dream.dream_settings import load as _load_settings, save as _save_settings
    uid = _runtime._owner_uid()
    char_id = _runtime._active_dream_char_id()
    settings = _load_settings(uid, char_id=char_id)
    if settings.get("world_layer") == match_world:
        settings["world_layer"] = reset_to
        _save_settings(uid, settings, char_id=char_id)


def _ensure_default_world_template_seeded() -> Path:
    """Return the tracked default package as a read-only seed source.

    C1.1 deliberately does not build a ``characters/dream_worlds`` subtree.
    New worlds copy directly into the canonical userdata target.
    """
    from admin.routers import dream as _runtime
    from core.sandbox import get_paths
    template = get_paths().default_dream_world_template_dir()
    if not template.is_dir():
        raise _runtime.HTTPException(status_code=500, detail="默认 Dream 世界模板缺失")
    return template


@router.get("/dream/worlds", summary="列出梦境世界目录")
async def list_dream_worlds(_auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    from core.dream.world_loader import discover_worlds
    return {"worlds": discover_worlds()}


@router.post("/dream/worlds", summary="新建梦境世界（建文件夹 + 最小骨架）")
async def create_dream_world(body: dict, _auth=Depends(require_scopes("activity"))):
    """新建 characters/dream_worlds/{world}/，骨架文件从 _default 复制而来。

    _default 本身若因 fresh 安装缺失，先从 defaults/dream_worlds/_default/
    （tracked）播种一份，保证骨架"有东西可复制"（Brief 96 §1）。
    """
    from admin.routers import dream as _runtime
    world = (body.get("world") or "").strip()
    if not world:
        raise _runtime.HTTPException(status_code=422, detail="world 不能为空")
    _runtime._validate_world_name(world)

    from core.sandbox import get_paths
    import shutil as _shutil

    dest = get_paths().dream_world_write_dir(world)
    if _runtime._world_dir(world).exists():
        raise _runtime.HTTPException(status_code=409, detail=f"世界 {world} 已存在")

    template = _runtime._ensure_default_world_template_seeded()
    from core.dream.world_authoring import create_world_files
    create_world_files(template, dest, body)

    _runtime._log_dream_write(kind="dream_world", read_path=template, canonical=dest, source="default")

    return {"ok": True, "world": world}


@router.put("/dream/worlds/{world}/rename", summary="重命名梦境世界文件夹")
async def rename_dream_world(world: str, body: dict, _auth=Depends(require_scopes("activity"))):
    """重命名世界文件夹，并同步世界选择引用：

    - dream_settings.world_layer 若正指向旧名，改写为新名。
    - 独立破限预设绝不随世界移动；早期同名文件也保留为独立资产。
    - anchor_weights.json 是全局字符→权重表，不含世界名字符串，核实后确认无需同步。
    """
    from admin.routers import dream as _runtime
    _runtime._validate_world_name(world)
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise _runtime.HTTPException(status_code=422, detail="new_name 不能为空")
    _runtime._validate_world_name(new_name)

    src = _runtime._world_dir(world)
    if not src.is_dir():
        raise _runtime.HTTPException(status_code=404, detail=f"世界 {world} 不存在")
    dst = _runtime._world_write_dir(new_name)
    if _runtime._world_dir(new_name).exists():
        raise _runtime.HTTPException(status_code=409, detail=f"世界 {new_name} 已存在")

    if _runtime._dream_active_referencing_world(world):
        raise _runtime.HTTPException(status_code=409, detail="该世界正被进行中的梦境使用，梦醒后再重命名")

    if src != _runtime._world_write_dir(world):
        raise _runtime.HTTPException(status_code=409, detail="legacy fallback Dream 世界为只读，不能重命名")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)

    _runtime._reset_world_layer_setting_if(world, new_name)

    return {"ok": True, "world": new_name}


@router.delete("/dream/worlds/{world}", summary="删除梦境世界文件夹")
async def delete_dream_world(world: str, _auth=Depends(require_scopes("activity"))):
    """删除世界文件夹（二次确认由前端做）。

    - _default / reality_derived 拒删。
    - 正在被进行中的梦引用时拒绝。
    - 独立破限预设不会随世界删除（即使它恰好同名）。
    - 若为当前 dream_settings.world_layer，重置为 _default。
    """
    from admin.routers import dream as _runtime
    _runtime._validate_world_name(world)  # 已在此拒绝 _default / reality_derived

    target = _runtime._world_dir(world)
    if not target.is_dir():
        legacy = _runtime.get_paths().legacy_dream_worlds_dir() / world
        if legacy.is_dir():
            raise _runtime.HTTPException(status_code=409, detail="legacy fallback Dream 世界为只读，不能删除")
        raise _runtime.HTTPException(status_code=404, detail=f"世界 {world} 不存在")

    canonical = _runtime._world_write_dir(world)
    if target != canonical:
        raise _runtime.HTTPException(status_code=409, detail="legacy fallback Dream 世界为只读，不能删除")
    if _runtime._dream_active_referencing_world(world):
        raise _runtime.HTTPException(status_code=409, detail="该世界正被进行中的梦境使用，梦醒后再删除")
    import shutil as _shutil
    _shutil.rmtree(target)

    _runtime._reset_world_layer_setting_if(world, "_default")

    legacy_reappeared = (_runtime.get_paths().legacy_dream_worlds_dir() / world).is_dir()
    return {"ok": True, "deleted": world, "legacy_fallback_reappeared": legacy_reappeared}


@router.get("/dream/worlds/{world}/lorebook", summary="读取梦境世界书条目列表")
async def get_dream_lorebook(world: str, _auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    import yaml as _yaml
    p = _runtime._world_dir(world) / "lorebook.yaml"
    if not p.exists():
        return {"entries": []}
    try:
        data = _yaml.safe_load(p.read_text(encoding="utf-8")) or []
        if not isinstance(data, list):
            raise _runtime.HTTPException(status_code=500, detail="lorebook.yaml 格式错误：应为裸 list")
        return {"entries": data}
    except _runtime.HTTPException:
        raise
    except Exception as e:
        raise _runtime.HTTPException(status_code=500, detail=f"读取失败: {e}")


def _write_dream_lorebook(world: str, entries: list):
    from admin.routers import dream as _runtime
    from core.dream.world_authoring import write_lorebook
    write_lorebook(_runtime._materialize_world_for_write(world), entries)


@router.post("/dream/worlds/{world}/lorebook", summary="新增梦境世界书条目")
async def add_dream_lore_entry(world: str, body: dict, _auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    import yaml as _yaml
    p = _runtime._materialize_world_for_write(world) / "lorebook.yaml"
    entries = []
    if p.exists():
        raw = _yaml.safe_load(p.read_text(encoding="utf-8")) or []
        entries = raw if isinstance(raw, list) else []

    keywords = body.get("keywords")
    content = body.get("content", "")
    if not keywords or not isinstance(keywords, list):
        raise _runtime.HTTPException(status_code=422, detail="keywords 必须为非空列表")
    if not content.strip():
        raise _runtime.HTTPException(status_code=422, detail="content 不能为空")

    entry = {
        "keywords": [str(k) for k in keywords],
        "content": str(content),
        "insertion_order": int(body.get("insertion_order", len(entries))),
        "regex": bool(body.get("regex", False)),
    }
    entries.append(entry)
    _runtime._write_dream_lorebook(world, entries)
    return {"ok": True, "index": len(entries) - 1, "entry": entry}


@router.put("/dream/worlds/{world}/lorebook/{index}", summary="修改梦境世界书条目")
async def update_dream_lore_entry(world: str, index: int, body: dict, _auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    import yaml as _yaml
    p = _runtime._materialize_world_for_write(world) / "lorebook.yaml"
    if not p.exists():
        raise _runtime.HTTPException(status_code=404, detail="lorebook.yaml 不存在")
    entries = _yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not isinstance(entries, list) or index < 0 or index >= len(entries):
        raise _runtime.HTTPException(status_code=404, detail=f"条目 {index} 不存在")

    entry = dict(entries[index])
    if "keywords" in body:
        kw = body["keywords"]
        if not isinstance(kw, list) or not kw:
            raise _runtime.HTTPException(status_code=422, detail="keywords 必须为非空列表")
        entry["keywords"] = [str(k) for k in kw]
    if "content" in body:
        entry["content"] = str(body["content"])
    if "insertion_order" in body:
        entry["insertion_order"] = int(body["insertion_order"])
    if "regex" in body:
        entry["regex"] = bool(body["regex"])

    entries[index] = entry
    _runtime._write_dream_lorebook(world, entries)
    return {"ok": True, "index": index, "entry": entry}


@router.delete("/dream/worlds/{world}/lorebook/{index}", summary="删除梦境世界书条目")
async def delete_dream_lore_entry(world: str, index: int, _auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    import yaml as _yaml
    p = _runtime._materialize_world_for_write(world) / "lorebook.yaml"
    if not p.exists():
        raise _runtime.HTTPException(status_code=404, detail="lorebook.yaml 不存在")
    entries = _yaml.safe_load(p.read_text(encoding="utf-8")) or []
    if not isinstance(entries, list) or index < 0 or index >= len(entries):
        raise _runtime.HTTPException(status_code=404, detail=f"条目 {index} 不存在")

    entries.pop(index)
    _runtime._write_dream_lorebook(world, entries)
    return {"ok": True, "remaining": len(entries)}


@router.get("/dream/worlds/{world}/preset", summary="读取旧版同名梦境预设（兼容）")
async def get_dream_preset(world: str, _auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    p = _runtime._preset_path(world)
    if not p.exists():
        return {"world": world, "content": ""}
    return {"world": world, "content": p.read_text(encoding="utf-8")}


@router.put("/dream/worlds/{world}/preset", summary="保存旧版同名梦境预设（兼容）")
async def put_dream_preset(world: str, body: dict, _auth=Depends(require_scopes("activity"))):
    from admin.routers import dream as _runtime
    content = body.get("content", "")
    read_path = _runtime._preset_path(world)
    p = _runtime.get_paths().dream_preset_write_path(world)
    p.parent.mkdir(parents=True, exist_ok=True)
    _runtime._log_dream_write(kind="dream_preset", read_path=read_path, canonical=p)
    p.write_text(str(content), encoding="utf-8")
    _runtime._reload_dream_preset_registry()
    return {"ok": True, "world": world, "bytes": len(content.encode("utf-8"))}
