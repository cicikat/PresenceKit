"""Admin-only image-generation discovery, routing and production-path probes."""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core import config_loader, image_gen

router = APIRouter(dependencies=[Depends(require_scopes("admin"))])


class Discovery(BaseModel):
    base_url: str
    api_key: str = ""
    preset_name: str | None = None


@router.post("/image-gen-presets/discover")
async def discover_image_models(body: Discovery):
    from core.model_discovery import catalogue_url, discover
    saved = image_gen.catalog()["presets"].get(body.preset_name, {})
    if body.preset_name and not saved:
        raise HTTPException(404, "生图连接不存在")
    try:
        url = catalogue_url(body.base_url)
        key = body.api_key.strip()
        if not key and saved.get("api_key"):
            if url != catalogue_url(saved["base_url"]):
                return {"status": "key_required", "models": []}
            key = saved["api_key"]
        return await discover(body.base_url, key, "chat_completions", "bearer")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class DefaultPreset(BaseModel):
    default_preset: str


class PromptConstraints(BaseModel):
    selected: str = ""
    custom: str = ""
    presets: dict[str, str]


@router.put("/image-gen-presets/prompt-constraints")
async def set_prompt_constraints(body: PromptConstraints):
    if len(body.presets) > 50 or any(not key.strip() or len(key) > 80 or key == "__custom__" or len(value) > 4000 for key, value in body.presets.items()) or len(body.custom) > 4000:
        raise HTTPException(422, "最多50个约束预设；名称1–80字符，内容最多4000字符")
    if body.selected and body.selected != "__custom__" and body.selected not in body.presets:
        raise HTTPException(422, "选中的约束预设不存在")
    path = config_loader.get_config_path()
    cfg = read_config_file(path)
    cfg.setdefault("image_gen", {})["prompt_constraints"] = body.model_dump()
    write_config_file(path, cfg)
    config_loader.reload_config()
    return body.model_dump()


@router.put("/image-gen-presets/default-preset")
async def set_image_default(body: DefaultPreset):
    path = config_loader.get_config_path()
    cfg = read_config_file(path)
    block = cfg.setdefault("image_gen", {})
    name = body.default_preset.strip()
    if name and name not in block.get("presets", {}):
        raise HTTPException(422, "默认生图连接不存在")
    block["default_preset"] = name
    write_config_file(path, cfg)
    config_loader.reload_config()
    return {"default_preset": name}


@router.post("/image-gen-presets/presets/{name}/test")
async def test_image_preset(name: str):
    """Explicit billable generation; discard image and never create a chat turn."""
    from core.tools.image_gen import request_image
    preset = image_gen.catalog()["presets"].get(name)
    if not preset:
        raise HTTPException(404, "生图连接不存在")
    if not image_gen.connection_ready(preset):
        raise HTTPException(422, "请先填写服务地址和模型")
    started = time.perf_counter()
    try:
        raw = await request_image("A small blue circle on a white background.", {"name": name, **preset})
        # A base64 error page is not a successful image probe.
        import io
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as img:
            if img.width * img.height > 40_000_000:
                raise ValueError("image too large")
            img.verify()
        return {"ok": True, "duration_ms": int((time.perf_counter() - started) * 1000), "bytes": len(raw)}
    except Exception:
        # Neither provider error bodies nor connection strings are safe to echo.
        return {"ok": False, "duration_ms": int((time.perf_counter() - started) * 1000),
                "error_category": "generation_failed", "message": "生成测试失败，请检查地址、密钥、模型与图片协议"}
