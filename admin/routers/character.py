"""
角色卡管理接口
提供角色卡 JSON 文件的列表、读取、保存、上传和切换。

接口列表：
  GET  /characters          — 列出所有 .json 文件，返回当前活跃角色名
  GET  /characters/{name}   — 读取角色卡内容
  PUT  /characters/active   — 切换当前活跃角色（写入 config.yaml）
  POST /characters/upload   — 上传新角色卡 .json 文件
  PUT  /characters/{name}   — 保存编辑后的角色卡并热重载
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core.asset_registry import get_registry, reload_registry
from core.config_loader import get_config
from core.sandbox import get_paths

router = APIRouter()

# New/edited private cards are written beneath userdata.  Read resolution is
# intentionally separate, because a legacy fallback is never a write target.
CHARACTERS_DIR = get_paths().user_character_cards_dir()
CONFIG_FILE = Path("config.yaml")


# ─── 工具函数 ──────────────────────────────────────────────────────────────────

def _safe_path(name: str) -> Path:
    """Resolve a card for reading only, retaining legacy compatibility."""
    if name != Path(name).name:
        raise HTTPException(status_code=400, detail="非法文件名")
    primary = (CHARACTERS_DIR / name).resolve()
    if primary.exists():
        return primary
    if name == "default.json":
        bundled = get_paths().bundled_default_character_card().resolve()
        if bundled.is_file():
            return bundled
    legacy = (get_paths().legacy_character_cards_dir() / name).resolve()
    legacy_base = get_paths().legacy_character_cards_dir().resolve()
    if legacy.exists() and legacy.is_relative_to(legacy_base):
        return legacy
    return primary


def _write_path(name: str) -> Path:
    """Return the canonical userdata target for a validated card filename."""
    if name != Path(name).name:
        raise HTTPException(status_code=400, detail="非法文件名")
    try:
        return get_paths().character_card_write_file(name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail="角色卡文件名不合法") from e


def _card_source(path: Path, canonical: Path) -> str:
    if path == canonical:
        return "user"
    if path == get_paths().bundled_default_character_card():
        return "bundled"
    return "legacy"


def _log_card_write(*, read_path: Path, canonical: Path, operation: str) -> None:
    _char_logger.info(
        "[authored-writer] kind=character operation=%s effective_read_source=%s "
        "canonical_write_target=user",
        operation,
        _card_source(read_path, canonical),
    )


import logging as _logging
_char_logger = _logging.getLogger(__name__)


def _active_character_id() -> str:
    """Return the current active character id.

    Priority: active_prompt_assets.json > config.yaml character.default.
    """
    import json as _json
    from core.sandbox import get_paths as _get_paths
    try:
        active_id = _json.loads(
            _get_paths().active_prompt_assets().read_text(encoding="utf-8")
        ).get("active_character", "")
        if active_id:
            return active_id
    except Exception:
        pass
    raw = get_config().get("character", {}).get("default", "")
    if not raw:
        return ""
    return Path(raw).stem if "." in raw else raw


def _reload_character(char_id: str) -> None:
    """Hot-reload character on the running pipeline.

    Fail-loud on unknown id / missing file — callers should catch and surface errors.
    Silently skips when pipeline is not yet initialized (standalone admin mode).
    """
    from core import character_loader
    from core.pipeline_registry import get as _get_pipeline

    pipeline = _get_pipeline()
    if pipeline is None:
        return  # admin running standalone, no pipeline yet

    new_char = character_loader.load(char_id)
    pipeline.character = new_char
    pipeline._active_character_id = char_id
    if pipeline.lore_engine is not None:
        pipeline.lore_engine.load()
        if new_char.world_book:
            pipeline.lore_engine.load_entries(new_char.world_book)
    _char_logger.info(f"[character] hot-reloaded {char_id!r} ({new_char.name}) on pipeline")


# ─── 路由（注意：精确路由必须在参数路由之前声明）────────────────────────────────

@router.get("/characters", summary="列出所有角色卡文件")
async def list_characters(auth=Depends(require_scopes("persona"))):
    """返回 characters/ 顶层目录下的角色卡资产列表（不含 hidden/template/author_notes 资产）。

    Response shape:
      {
        "characters": [{"id": "yexuan", "label": "叶瑄", "filename": "yexuan.json"}, ...],
        "active_id": "yexuan"
      }
    """
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    reg = get_registry()
    # list_ui excludes hidden; include both hidden=False and hidden=True here so editors can
    # still open all files — but mark hidden ones so the frontend can omit them from the
    # "set active" picker.
    entries = reg.list_all("character")
    chars = [
        {"id": e.id, "label": e.label, "filename": e.filename, "hidden": e.hidden}
        for e in sorted(entries, key=lambda e: e.id)
    ]
    return {"characters": chars, "active_id": _active_character_id()}


@router.put("/characters/active", summary="切换当前活跃角色卡")
async def set_active_character(body: Dict[str, Any], auth=Depends(require_scopes("persona"))):
    """将 config.yaml 中的 character.default 更新为指定角色 id，并热重载。

    Request body: {"id": "yexuan"}
    Legacy compat: {"name": "yexuan.json"} is also accepted and normalized to id.

    Rejects: label strings ("叶瑄") and unknown ids — fail-loud.
    """
    # Accept both "id" (new) and "name" (legacy filename form)
    raw = (body.get("id") or body.get("name") or "").strip()
    if not raw:
        raise HTTPException(status_code=422, detail="id 不能为空")

    reg = get_registry()
    # Normalize legacy filename to id ("yexuan.json" → "yexuan")
    char_id = reg.normalize_legacy(raw, "character")

    # Validate: id must be known, and must not be a hidden template/example
    try:
        entry = reg.resolve(char_id, "character")
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"未知角色 id {char_id!r}。如果提交了 label 或 filename，请改为提交 id。",
        )

    if entry.hidden:
        raise HTTPException(
            status_code=422,
            detail=f"角色 {char_id!r} 是 template/example 资产，不能设为活跃角色。",
        )

    full_cfg = read_config_file(CONFIG_FILE)

    # Write id (not filename) to config
    full_cfg.setdefault("character", {})["default"] = char_id

    write_config_file(CONFIG_FILE, full_cfg)

    # Also persist to active_prompt_assets.json (runtime source of truth)
    import json as _json
    from core.sandbox import get_paths as _get_paths
    try:
        _paths = _get_paths()
        _active = _json.loads(_paths.active_prompt_assets().read_text(encoding="utf-8"))
        _active["active_character"] = char_id
        _paths.active_prompt_assets().write_text(
            _json.dumps(_active, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as _e:
        _char_logger.warning(f"[character] active_prompt_assets.json 更新失败: {_e}")

    from core import config_loader
    config_loader.reload_config()
    try:
        _reload_character(char_id)
    except Exception as _reload_exc:
        _char_logger.warning(f"[character] pipeline hot-reload failed (non-fatal): {_reload_exc}")
    reload_registry()
    return {"message": f"当前角色已切换为 {char_id}", "active_id": char_id, "label": entry.label}


@router.post("/characters/new", summary="从模板新建角色卡")
async def new_character(body: Dict[str, Any], auth=Depends(require_scopes("persona"))):
    """从 examples/character_template.json 派生一张新角色卡，写入 characters/{id}.json。

    Request body: {"id": "some_id", "name": "可选显示名（缺省用 id）"}

    - id 即文件名 stem，禁止包含路径分隔符/以 . 开头；已存在同名卡返回 409。
    - 新卡不写 config.yaml，不切换活跃角色（由用户在既有激活机制里手动切换）。
    """
    raw_id = (body.get("id") or "").strip()
    if not raw_id:
        raise HTTPException(status_code=422, detail="id 不能为空")
    # Do not delegate this contract to pathlib: on POSIX, ``a\\b`` is a
    # normal filename while Windows treats it as a separator.  Character ids
    # are portable logical ids, so reject both separators on every platform.
    if (
        raw_id in {".", ".."}
        or raw_id.startswith(".")
        or "/" in raw_id
        or "\\" in raw_id
        or raw_id != Path(raw_id).name
    ):
        raise HTTPException(status_code=422, detail=f"非法角色 id: {raw_id!r}")

    read_path = _safe_path(f"{raw_id}.json")
    dest = get_paths().character_card_write_path(raw_id)
    if read_path.exists():
        raise HTTPException(status_code=409, detail=f"角色卡 {raw_id} 已存在")

    paths = get_paths()
    bundled_template = paths.bundled_templates_dir() / "character_template.json"
    template_path = bundled_template if bundled_template.exists() else Path("examples") / "character_template.json"
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取角色卡模板失败: {e}")

    label = (body.get("name") or "").strip() or raw_id
    template["name"] = label

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建角色卡失败: {e}")

    reload_registry()
    return {
        "message": f"角色卡 {raw_id} 已创建",
        "id": raw_id,
        "filename": f"{raw_id}.json",
        "label": label,
    }


@router.post("/characters/upload", summary="上传新角色卡（.json / .txt / .md）")
async def upload_character(file: UploadFile = File(...), auth=Depends(require_scopes("persona"))):
    """接收 .json / .txt / .md 文件并保存到 characters/ 目录"""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in (".json", ".txt", ".md"):
        raise HTTPException(status_code=422, detail="只接受 .json / .txt / .md 文件")
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    # 只取文件名部分，防止路径穿越
    safe_name = Path(filename).name
    dest = _write_path(safe_name)
    content = await file.read()
    # JSON 文件额外验证合法性
    if suffix == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=422, detail=f"JSON 解析失败: {e}")
    try:
        dest.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")
    return {"message": f"角色卡 {safe_name} 已上传", "filename": safe_name}


@router.get("/characters/active-info", summary="当前活跃角色基本信息（前端初始化用）")
async def get_active_char_info(auth=Depends(require_scopes("persona"))):
    """返回当前活跃角色的显示名、性别与资产绑定，供前端初始化用。

    Response: {"char_id": "yexuan", "name": "叶瑄", "gender": "male",
               "live2d_model": null, "model_3d": null}

    2026-07-25：附带 live2d_model / model_3d 透传字段（presence_ext，见
    /character/{char_id}/asset-bindings），前端切换角色时不用再单独一次请求
    才知道该换哪个模型。tts_preset / sticker_pack 不放这里——那两个是后端内部
    合成/选图时用，前端不需要。字段读取失败一律静默给 null，不影响角色名display。
    """
    active_id = _active_character_id()
    if not active_id:
        return {"char_id": "", "name": "(未配置)", "gender": "neutral", "live2d_model": None, "model_3d": None}
    try:
        from core.character_loader import load as _load_char
        char = _load_char(active_id)
        presence_ext = char.presence_ext or {}
        return {
            "char_id": active_id, "name": char.name, "gender": char.gender,
            "live2d_model": presence_ext.get("live2d_model") or None,
            "model_3d": presence_ext.get("model_3d") or None,
        }
    except Exception:
        return {"char_id": active_id, "name": active_id, "gender": "neutral", "live2d_model": None, "model_3d": None}


@router.get("/characters/{name}/export", summary="导出角色卡文件")
async def export_character(name: str, auth=Depends(require_scopes("persona"))):
    from fastapi.responses import Response as _Resp
    from urllib.parse import quote
    path = _safe_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"角色卡 {name} 不存在")
    content = path.read_bytes()
    media = "application/json" if path.suffix.lower() == ".json" else "text/plain"
    encoded_name = quote(name)
    return _Resp(content=content, media_type=media,
                 headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"})


@router.get("/characters/{name}", summary="读取角色卡内容")
async def get_character(name: str, auth=Depends(require_scopes("persona"))):
    """返回指定角色卡内容：
    - .txt/.md → {"filename": name, "type": "text", "content": "..."}
    - .json    → 原始 JSON 对象 + "type": "json"
    """
    path = _safe_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"角色卡 {name} 不存在")
    suffix = path.suffix.lower()
    try:
        if suffix in (".txt", ".md"):
            return {
                "filename": name,
                "type":     "text",
                "content":  path.read_text(encoding="utf-8"),
            }
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["type"] = "json"
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取失败: {e}")


@router.put("/characters/{name}", summary="保存角色卡并热重载")
async def save_character(name: str, request: Request, _auth=Depends(require_scopes("persona"))):
    """接收编辑后的角色卡内容，写回文件并热重载角色。
    - .txt/.md：raw body 作为 UTF-8 文本直接写入
    - .json：解析 JSON body 再写入
    """
    read_path = _safe_path(name)
    if not read_path.exists():
        raise HTTPException(status_code=404, detail=f"角色卡 {name} 不存在")
    path = _write_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _log_card_write(read_path=read_path, canonical=path, operation="save")
    suffix = path.suffix.lower()
    try:
        raw = await request.body()
        if suffix in (".txt", ".md"):
            path.write_text(raw.decode("utf-8"), encoding="utf-8")
        else:
            body: Dict[str, Any] = json.loads(raw)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")
    # Reload the currently-active character (the saved file may or may not be active)
    _active_id = _active_character_id()
    if _active_id:
        try:
            _reload_character(_active_id)
        except Exception as _e:
            _char_logger.warning(f"[character] save hot-reload failed (non-fatal): {_e}")
    reload_registry()
    return {
        "message": f"角色卡 {name} 已保存并热重载",
        "effective_read_source": _card_source(read_path, path),
        "canonical_write_target": "user",
    }



@router.post("/characters/{name}/rename", summary="重命名角色卡")
async def rename_character(name: str, body: Dict[str, Any], auth=Depends(require_scopes("persona"))):
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="new_name 不能为空")
    src = _safe_path(name)
    dst = _write_path(new_name)
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"角色卡 {name} 不存在")
    if _safe_path(new_name).exists():
        raise HTTPException(status_code=409, detail=f"角色卡 {new_name} 已存在")
    canonical_src = _write_path(name)
    if src != canonical_src:
        raise HTTPException(
            status_code=409,
            detail="legacy fallback 角色卡为只读；请先保存为 userdata override 后再重命名",
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    # 如果是当前活跃角色，同步更新 config.yaml（写新 id，不写 filename）
    new_id = Path(new_name).stem
    current_id = _active_character_id()
    old_id = Path(name).stem
    if current_id == old_id:
        try:
            full_cfg = read_config_file(CONFIG_FILE)
            full_cfg.setdefault("character", {})["default"] = new_id
            write_config_file(CONFIG_FILE, full_cfg)
            from core import config_loader
            config_loader.reload_config()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"更新配置失败: {e}")
    reload_registry()
    legacy_reappeared = (get_paths().legacy_character_cards_dir() / name).is_file()
    return {
        "message": f"已重命名为 {new_name}",
        "new_name": new_name,
        "new_id": new_id,
        "legacy_fallback_reappeared": legacy_reappeared,
    }


# ─── per-角色模型路由绑定（Brief 87 §1）──────────────────────────────────────────

class ModelRoutingUpdate(BaseModel):
    model_routing: Optional[str] = None


@router.get("/character/{char_id}/model-routing", summary="读取角色卡的模型路由绑定与解析结果")
async def get_character_model_routing(char_id: str, auth=Depends(require_scopes("persona"))):
    """返回角色卡 presence_ext.model_routing 声明 + 实际解析结果。

    resolved 字段（effective_profile / resolved_chat_preset）把 model_registry 的
    profile 回落逻辑摊开给前端，绑定后立刻可见"实际会用哪个 preset"。
    """
    reg = get_registry()
    try:
        reg.resolve(char_id, "character")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"未知角色 id {char_id!r}")

    from core.model_registry import resolve_routing_info
    return resolve_routing_info(char_id)


@router.patch("/character/{char_id}/model-routing", summary="绑定/清除角色卡的模型路由 profile")
async def set_character_model_routing(
    char_id: str, body: ModelRoutingUpdate, auth=Depends(require_scopes("persona"))
):
    """写角色卡 presence_ext.model_routing。null = 清除绑定，回落全局 active_routing。

    非 null 值必须存在于 routing_profiles，否则 422（character_loader 的既有注释已言明
    "存在于 routing_profiles 才生效"——这里把它变成显式错误而不是静默失效）。
    """
    reg = get_registry()
    try:
        entry = reg.resolve(char_id, "character")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"未知角色 id {char_id!r}")

    read_path = entry.path()
    path = get_paths().character_card_write_path(char_id)
    if path.suffix.lower() != ".json":
        raise HTTPException(status_code=422, detail="纯文本角色卡（.txt/.md）不支持 model_routing 绑定")

    if body.model_routing is not None:
        from core.model_registry import _get_preset_config
        profiles = _get_preset_config().get("routing_profiles", {})
        if body.model_routing not in profiles:
            raise HTTPException(
                status_code=422,
                detail=f"routing profile {body.model_routing!r} 不存在。可用: {sorted(profiles)}",
            )

    try:
        with open(read_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取角色卡失败: {e}")

    # 就地改 presence_ext.model_routing，其余键/顺序不动（角色卡是 authored 资产）。
    presence_ext = data.setdefault("presence_ext", {})
    if body.model_routing is None:
        presence_ext.pop("model_routing", None)
    else:
        presence_ext["model_routing"] = body.model_routing

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _log_card_write(read_path=read_path, canonical=path, operation="model_routing")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存角色卡失败: {e}")

    # 缓存边沿：character_loader.load() 每次都重读磁盘，下次路由解析即生效；
    # 若该卡恰好是当前活跃角色，顺带热重载 pipeline.character 保持一致。
    reload_registry()
    _active_id = _active_character_id()
    if _active_id:
        try:
            _reload_character(_active_id)
        except Exception as _e:
            _char_logger.warning(f"[character] model-routing 保存后热重载失败（非致命）: {_e}")

    from core.model_registry import resolve_routing_info
    return {
        "message": f"角色 {char_id!r} 的 model_routing 已更新",
        **resolve_routing_info(char_id),
    }


# ─── 角色资产路由（茶茶 2026-07-25 反馈）─────────────────────────────────────────
# 与上面的 model_routing 绑定同构：chat 模型走 model_routing，TTS 走 tts_preset
# （core/output/voice_adapter.py resolve_tts_config()），表情包走 sticker_pack
# （core/output/sticker.py 缺图回落通用池），Live2D/3D 模型只做透传——后端不渲染
# 模型，live2d_model/model_3d 只是字符串标识，交给前端自行解析映射到本地模型文件。
# 图像识别（vision）刻意不纳入这里：是通用能力不按角色区分，只分「日常观察」
# （vision）与「桌面自动化专用」（use_computer_vision，core/perception/vlm_client.py）
# 两个全局槽位，见该模块函数注释。

_ASSET_BINDING_FIELDS = ("tts_preset", "sticker_pack", "live2d_model", "model_3d")


class AssetBindingsUpdate(BaseModel):
    tts_preset: Optional[str] = None
    sticker_pack: Optional[str] = None
    live2d_model: Optional[str] = None
    model_3d: Optional[str] = None
    # None = 不修改该字段；空字符串 "" = 显式清除该字段的绑定。
    # （与 model_routing 端点的"None=清除"语义不同，因为这里一次可能只想改一个字段，
    # 全部按 None=清除会把其余三个字段一起冲掉；用空串表达"清除"更安全。）


def _resolve_asset_bindings(char_id: str, presence_ext: dict) -> dict:
    tts_preset = presence_ext.get("tts_preset") or None
    resolved_tts_preset_exists = None
    if tts_preset:
        from core.config_loader import get_config
        presets = get_config().get("tts", {}).get("presets", {})
        resolved_tts_preset_exists = isinstance(presets, dict) and tts_preset in presets
    return {
        "char_id": char_id,
        "tts_preset": tts_preset,
        "tts_preset_resolved": resolved_tts_preset_exists,
        "sticker_pack": presence_ext.get("sticker_pack") or None,
        "live2d_model": presence_ext.get("live2d_model") or None,
        "model_3d": presence_ext.get("model_3d") or None,
    }


@router.get("/character/{char_id}/asset-bindings", summary="读取角色卡的资产绑定（TTS预设/表情包/Live2D/3D模型）")
async def get_character_asset_bindings(char_id: str, auth=Depends(require_scopes("persona"))):
    """返回角色卡 presence_ext 里的资产绑定字段。

    tts_preset_resolved：true/false/null——null 表示未声明 tts_preset（用全局 tts 配置，
    不算错误状态）；false 表示声明了但在 tts.presets 里找不到（会在实际合成时 fail-soft
    回落全局配置并记 warning，这里提前把这个隐患暴露给前端/管理面板，供 §9 资源完整性
    检查复用同一份判断逻辑）。
    """
    reg = get_registry()
    try:
        reg.resolve(char_id, "character")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"未知角色 id {char_id!r}")

    from core import character_loader
    try:
        char = character_loader.load(char_id)
        presence_ext = char.presence_ext or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载角色卡失败: {e}")

    return _resolve_asset_bindings(char_id, presence_ext)


@router.patch("/character/{char_id}/asset-bindings", summary="更新角色卡的资产绑定")
async def set_character_asset_bindings(
    char_id: str, body: AssetBindingsUpdate, auth=Depends(require_scopes("persona"))
):
    """就地修改角色卡 presence_ext 里的资产绑定字段。

    每个字段独立：请求体缺省或为 null 的字段不改动现有值；传空字符串 "" 显式清除该
    字段的绑定（回落对应默认行为：tts_preset 清除→用全局 tts 配置，sticker_pack 清除→
    只用通用表情包池，live2d_model/model_3d 清除→前端用其自身默认模型）。
    """
    reg = get_registry()
    try:
        entry = reg.resolve(char_id, "character")
    except ValueError:
        raise HTTPException(status_code=404, detail=f"未知角色 id {char_id!r}")

    read_path = entry.path()
    path = get_paths().character_card_write_path(char_id)
    if path.suffix.lower() != ".json":
        raise HTTPException(status_code=422, detail="纯文本角色卡（.txt/.md）不支持资产绑定")

    try:
        with open(read_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取角色卡失败: {e}")

    presence_ext = data.setdefault("presence_ext", {})
    updates = body.model_dump(exclude_unset=True)
    for field_name in _ASSET_BINDING_FIELDS:
        if field_name not in updates:
            continue
        value = updates[field_name]
        if not value:  # None 或 "" 均视为清除，语义上等价（都不写入 presence_ext）
            presence_ext.pop(field_name, None)
        else:
            presence_ext[field_name] = value

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _log_card_write(read_path=read_path, canonical=path, operation="asset_bindings")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存角色卡失败: {e}")

    reload_registry()
    _active_id = _active_character_id()
    if _active_id:
        try:
            _reload_character(_active_id)
        except Exception as _e:
            _char_logger.warning(f"[character] asset-bindings 保存后热重载失败（非致命）: {_e}")

    return {
        "message": f"角色 {char_id!r} 的资产绑定已更新",
        **_resolve_asset_bindings(char_id, presence_ext),
    }
