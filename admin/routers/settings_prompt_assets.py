"""
Prompt Asset 配置 API
GET  /settings/prompt-assets  — 读取可用资产列表 + 当前激活配置
PATCH /settings/prompt-assets — 部分更新激活配置，并热重载 lore_engine

Asset identity contract:
- All values in active config and PATCH bodies are IDs (file stems, ASCII).
- Labels, filenames, and Chinese names must NOT appear in config or PATCH bodies.
- Hidden/template/example assets are excluded from UI lists.
- PATCH with a label or filename will be rejected with a clear error.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from admin.auth import verify_token
from core.asset_registry import get_registry, reload_registry, _AVATARS_DIR, _AVATAR_EXTS
from core.sandbox import get_paths
from core.safe_write import safe_write_bytes

router = APIRouter()
logger = logging.getLogger(__name__)


def _validate_id(value: str, kind: str, field: str):
    """Validate that value is a known, non-hidden asset id.

    Rejects path separators, dots (extensions / traversal), and unknown ids.
    Also rejects if value looks like a label or filename (contains Chinese, dots).
    """
    if "/" in value or "\\" in value or "." in value:
        raise HTTPException(
            status_code=422,
            detail=f"{field}: 不接受路径分隔符或扩展名——请提交 id 而非 filename（拒绝：{value!r}）",
        )
    reg = get_registry()
    try:
        reg.resolve(value, kind)
    except ValueError:
        valid = sorted(e.id for e in reg.list_all(kind))
        raise HTTPException(
            status_code=422,
            detail=f"{field}: {value!r} 不在可用列表中（可用：{valid}）",
        )


def _read_active() -> dict:
    p = get_paths().active_prompt_assets()
    return json.loads(p.read_text(encoding="utf-8"))


def _write_active(data: dict):
    p = get_paths().active_prompt_assets()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _reload_lore_engine():
    try:
        from core.pipeline_registry import get as _get_pipeline
        pipeline = _get_pipeline()
        if pipeline is not None and hasattr(pipeline, "lore_engine"):
            pipeline.lore_engine.load()
    except Exception:
        pass


@router.get("/settings/prompt-assets", summary="获取 Prompt 资产列表与激活配置")
async def get_prompt_assets(auth=Depends(verify_token)):
    """Returns all UI-visible (non-hidden) assets and the current active config.

    Response shape:
      {
        "characters":    [{"id": "yexuan", "label": "叶瑄", "kind": "character"}, ...],
        "lorebooks":     [{"id": "base",   "label": "base",  "kind": "reality_lorebook"}, ...],
        "jailbreaks":    [{"id": "base",   "label": "base",  "kind": "reality_jailbreak"}, ...],
        "dream_presets": [{"id": "default","label": "default","kind": "dream_preset"}, ...],
        "active": {
          "active_character":   "yexuan",
          "enabled_lorebooks":  ["base"],
          "enabled_jailbreaks": ["base", "anti_assistant", "style"],
          "active_dream_preset": null
        }
      }
    """
    reg = get_registry()
    return {
        "characters":    [e.as_ui_dict() for e in reg.list_ui("character")],
        "lorebooks":     [e.as_ui_dict() for e in reg.list_ui("reality_lorebook")],
        "jailbreaks":    [e.as_ui_dict() for e in reg.list_ui("reality_jailbreak")],
        "dream_presets": [e.as_ui_dict() for e in reg.list_ui("dream_preset")],
        "active":        _read_active(),
    }


_AVATAR_CONTENT_TYPES: dict[str, str] = {
    "png":  "image/png",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}
_UPLOAD_ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/png":  "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
_AVATAR_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def _resolve_char_or_404(char_id: str):
    """Resolve char_id from registry; raise 404 on unknown id. Never fallbacks."""
    reg = get_registry()
    try:
        return reg.resolve(char_id, "character")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/settings/character-avatar/{char_id}", summary="获取角色头像")
async def get_character_avatar(char_id: str, v: Optional[str] = None, auth=Depends(verify_token)):
    """Serve avatar for a character. Priority: runtime override > authored default > 404.

    Fail-loud on unknown char_id. Never guesses from label or filename.
    v= query param is ignored (cache-busting only).
    """
    _resolve_char_or_404(char_id)

    # Runtime override first
    runtime_dir = get_paths().runtime_character_dir(char_id=char_id)
    for ext in _AVATAR_EXTS:
        p = runtime_dir / f"avatar.{ext}"
        if p.exists():
            return FileResponse(str(p), media_type=_AVATAR_CONTENT_TYPES[ext])

    # Authored default
    authored = _AVATARS_DIR / f"{char_id}.png"
    if authored.exists():
        return FileResponse(str(authored), media_type="image/png")

    raise HTTPException(status_code=404, detail=f"no avatar for character {char_id!r}")


@router.post("/settings/characters/{char_id}/avatar", summary="上传角色头像（runtime override）")
async def upload_character_avatar(
    char_id: str,
    file: UploadFile,
    auth=Depends(verify_token),
):
    """Upload a runtime override avatar for char_id.

    - char_id must exist in the asset registry (fail-loud, no fallback).
    - Accepted types: image/png, image/jpeg, image/webp. Max 5 MB.
    - Atomically replaces any existing runtime avatar.
    - Calls reload_registry() so avatar_url in /settings/prompt-assets reflects the new file.
    """
    _resolve_char_or_404(char_id)

    ct = (file.content_type or "").lower().split(";")[0].strip()
    if ct not in _UPLOAD_ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"不支持的文件类型：{ct!r}，仅支持 image/png、image/jpeg、image/webp",
        )
    ext = _UPLOAD_ALLOWED_CONTENT_TYPES[ct]

    content = await file.read()
    if len(content) > _AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"文件超过 5 MB 限制（实际 {len(content)} 字节）",
        )

    runtime_dir = get_paths().runtime_character_dir(char_id=char_id)

    # Remove any existing runtime avatars with different extensions
    for old_ext in _AVATAR_EXTS:
        if old_ext != ext:
            old_p = runtime_dir / f"avatar.{old_ext}"
            if old_p.exists():
                try:
                    old_p.unlink()
                except Exception:
                    pass

    dest = runtime_dir / f"avatar.{ext}"
    ok = safe_write_bytes(dest, content)
    if not ok:
        raise HTTPException(status_code=500, detail="头像写入失败")

    reload_registry()
    return {"char_id": char_id, "avatar_url": f"/settings/character-avatar/{char_id}"}


@router.delete("/settings/characters/{char_id}/avatar", summary="删除角色 runtime 头像覆盖")
async def delete_character_avatar(char_id: str, auth=Depends(verify_token)):
    """Delete the runtime override avatar for char_id. Falls back to authored default.

    char_id must exist in the asset registry (fail-loud, no fallback to yexuan).
    Returns deleted=false if no runtime override existed.
    """
    _resolve_char_or_404(char_id)

    runtime_dir = get_paths().runtime_character_dir(char_id=char_id)
    deleted = False
    for ext in _AVATAR_EXTS:
        p = runtime_dir / f"avatar.{ext}"
        if p.exists():
            p.unlink()
            deleted = True

    reload_registry()
    return {"char_id": char_id, "deleted": deleted}


class PromptAssetsUpdate(BaseModel):
    active_character:    Optional[str]       = None
    enabled_lorebooks:   Optional[list[str]] = None
    enabled_jailbreaks:  Optional[list[str]] = None
    active_dream_preset: Optional[str]       = None


@router.patch("/settings/prompt-assets", summary="部分更新 Prompt 资产激活配置")
async def patch_prompt_assets(body: PromptAssetsUpdate, auth=Depends(verify_token)):
    """Partial update for active prompt-asset config. All values must be asset ids.

    Rejects labels ("叶瑄"), filenames ("yexuan.json"), and unknown ids.
    """
    if all(v is None for v in (
        body.active_character,
        body.enabled_lorebooks,
        body.enabled_jailbreaks,
        body.active_dream_preset,
    )):
        raise HTTPException(status_code=422, detail="至少提供一个更新字段")

    if body.active_character is not None:
        _validate_id(body.active_character, "character", "active_character")

    if body.enabled_lorebooks is not None:
        for stem in body.enabled_lorebooks:
            _validate_id(stem, "reality_lorebook", "enabled_lorebooks")

    if body.enabled_jailbreaks is not None:
        for stem in body.enabled_jailbreaks:
            _validate_id(stem, "reality_jailbreak", "enabled_jailbreaks")

    if body.active_dream_preset is not None:
        _validate_id(body.active_dream_preset, "dream_preset", "active_dream_preset")

    active = _read_active()
    if body.active_character is not None:
        active["active_character"] = body.active_character
    if body.enabled_lorebooks is not None:
        active["enabled_lorebooks"] = body.enabled_lorebooks
    if body.enabled_jailbreaks is not None:
        active["enabled_jailbreaks"] = body.enabled_jailbreaks
    if body.active_dream_preset is not None:
        active["active_dream_preset"] = body.active_dream_preset

    _write_active(active)

    if body.enabled_lorebooks is not None:
        _reload_lore_engine()
    if body.active_character is not None:
        reload_registry()
        # Hot-swap character on the running pipeline
        try:
            from core import character_loader as _cl
            from core.pipeline_registry import get as _get_pipeline
            pipeline = _get_pipeline()
            if pipeline is not None:
                new_char = _cl.load(body.active_character)
                pipeline.character = new_char
                pipeline._active_character_id = body.active_character
                if pipeline.lore_engine is not None:
                    pipeline.lore_engine.load()
                    if new_char.world_book:
                        pipeline.lore_engine.load_entries(new_char.world_book)
                logger.info(
                    f"[settings/prompt-assets] character hot-swapped to "
                    f"{body.active_character!r} ({new_char.name})"
                )
        except Exception as _exc:
            logger.warning(f"[settings/prompt-assets] pipeline character update failed: {_exc}")

    return {"message": "已更新", "active": active}
