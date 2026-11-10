"""Named image-generation presets for the character-callable generate_image tool.

``image_gen.presets`` holds named generation connections (OpenAI-compatible
POST /images/generations). Each preset carries a hand-written description and
a show_to_llm flag; the LLM picks a preset by name at call time. Per-character
allowlist lives in ``presence_ext.image_gen`` (list of names) on the card.
"""
from __future__ import annotations

import re
from copy import deepcopy

from core import config_loader
from core.prompt_language import choose

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MAX_PROMPT_CHARS = 8_000
MAX_IMAGE_BYTES = 25 * 1024 * 1024


class ImagePresetError(ValueError):
    """A preset selection failure that can safely be explained to the model."""

_IMAGE_REQUEST_RE = re.compile(
    r"画(?:一|两|几)?张|画一幅|帮我画|给我画|给(?:我|你)做(?:一)?张(?:图|海报|头像|壁纸|插画)|生图|出图|图生图|文生图|"
    r"(?:生成|绘制|制作|创作|画出).{0,12}(?:图像|图片|插画|肖像|头像|壁纸|海报)|"
    r"(?:改|编辑|重画|重绘|修改).{0,8}(?:这张图|那张图|参考图|图片|插画)|"
    r"\b(?:draw|generate|create|paint|edit|redraw|illustrate)\b.{0,55}\b(?:image|picture|portrait|illustration|artwork|poster)\b",
    re.I | re.S,
)


def looks_like_image_request(content: object) -> bool:
    """Only controls first-round presentation; never grants or executes a tool."""
    if isinstance(content, list):
        content = "\n".join(str(block.get("text") or "") for block in content
                            if isinstance(block, dict) and block.get("type") in {"text", "input_text"})
    return isinstance(content, str) and bool(_IMAGE_REQUEST_RE.search(content))


def get_config() -> dict:
    return config_loader.get_config()


def _block(config: dict | None = None) -> dict:
    config = get_config() if config is None else config
    block = config.get("image_gen") if isinstance(config, dict) else None
    return block if isinstance(block, dict) else {}


def tools_enabled() -> bool:
    return bool(_block().get("enabled"))


def _normalize_preset(payload: dict) -> dict:
    extra = payload.get("extra_params")
    return {
        "description": str(payload.get("description") or ""),
        "show_to_llm": payload.get("show_to_llm", True) is True,
        "supports_edits": payload.get("supports_edits", True) is True,
        "provider_kind": str(payload.get("provider_kind") or "openai_compatible"),
        "base_url": str(payload.get("base_url") or ""),
        "api_key": str(payload.get("api_key") or ""),
        "model": str(payload.get("model") or ""),
        "extra_params": deepcopy(extra) if isinstance(extra, dict) else {},
        "test_model": str(payload.get("test_model") or ""),
        "test_extra_params": deepcopy(payload.get("test_extra_params") or {}),
    }


def catalog(config: dict | None = None) -> dict:
    """Return ``{presets, enabled}`` without mutating config."""
    block = _block(config)
    stored = block.get("presets") if isinstance(block.get("presets"), dict) else {}
    presets = {
        name: _normalize_preset(payload)
        for name, payload in stored.items()
        if isinstance(name, str) and NAME_RE.fullmatch(name) and isinstance(payload, dict)
    }
    return {"presets": presets, "enabled": bool(block.get("enabled")),
            "default_preset": str(block.get("default_preset") or ""),
            "prompt_constraints": constraint_settings(config)}


def constraint_settings(config: dict | None = None) -> dict:
    block = _block(config).get("prompt_constraints") or {}
    return {"selected": str(block.get("selected") or ""),
            "custom": str(block.get("custom") or ""),
            "presets": dict(block.get("presets") or {})}


def constrained_prompt(prompt: str, config: dict | None = None) -> str:
    """User-controlled visual requirements apply to generation and reference edits."""
    settings = constraint_settings(config)
    selected = settings["selected"]
    if not selected:
        return prompt
    text = settings["custom"] if selected == "__custom__" else settings["presets"].get(selected)
    if text is None:
        raise ValueError("选中的生图提示词约束预设不存在，请重新选择。")
    text = str(text).strip()
    return prompt + "\n\nUser visual constraints:\n" + text if text else prompt


def connection_ready(preset: dict | None) -> bool:
    if not isinstance(preset, dict):
        return False
    return bool(preset.get("base_url") and preset.get("model"))


def exposed_preset_names(char_id: str | None = None, config: dict | None = None) -> list[dict]:
    """Ready presets visible to the LLM for a character, in config order."""
    cat = catalog(config)
    if not cat["enabled"]:
        return []
    allowed = _char_allowlist(char_id)
    result = []
    for name, preset in cat["presets"].items():
        if not preset.get("show_to_llm"):
            continue
        if allowed is not None and name not in allowed:
            continue
        if not connection_ready(preset):
            continue
        result.append({"name": name, "description": preset.get("description") or "",
                       "supports_edits": preset.get("supports_edits", True) is True})
    return result


def _char_allowlist(char_id: str | None) -> set[str] | None:
    if not char_id:
        return None
    try:
        from core.character_loader import load

        ext = load(char_id).presence_ext or {}
        value = ext.get("image_gen")
        if isinstance(value, list):
            return {str(item).strip() for item in value if str(item).strip()}
    except Exception:
        return None
    return None


def discovery_note(char_id: str | None = None, config: dict | None = None) -> str:
    """Suffix for ``load_tools_media`` description: per-preset name + description."""
    rows = exposed_preset_names(char_id, config)
    if not rows:
        return ""
    parts = []
    for row in rows:
        desc = " ".join(str(row.get("description") or "").split())[:200]
        item = f"{row['name']}：{desc}" if desc else str(row["name"])
        if row.get("supports_edits") is False:
            item += "（不支持参考图）"
        parts.append(item)
    default = catalog(config)["default_preset"]
    if not any(row["name"] == default for row in rows):
        default = rows[0]["name"]
    return choose(
        "未指定连接时省略 preset，当前默认连接为 {default}。需要指定时，原样使用以下连接名：{choices}。",
        "Omit preset when no connection was requested. The current default is {default}. When specifying a connection, use its exact name: {choices}.",
    ).format(default=default, choices="；".join(parts))


def resolve_preset(name: str | None, char_id: str | None = None, config: dict | None = None) -> dict:
    """Resolve one preset for execution; raises ValueError with a usable message."""
    cat = catalog(config)
    if not cat["enabled"]:
        raise ValueError("图像生成未启用。")
    presets = cat["presets"]
    if not presets:
        raise ValueError("没有配置任何图像生成预设。")
    wanted = str(name or "").strip()
    if not wanted:
        visible = exposed_preset_names(char_id, config)
        default = cat.get("default_preset")
        if default and any(row["name"] == default for row in visible):
            wanted = default
            visible = [{"name": default}]
        if visible:
            wanted = str(visible[0]["name"])
        else:
            # Fall back to the first ready preset regardless of show_to_llm.
            for candidate, preset in presets.items():
                if connection_ready(preset):
                    wanted = candidate
                    break
            else:
                raise ValueError("没有可用的图像生成预设配置。")
    preset = presets.get(wanted)
    if preset is None:
        raise ImagePresetError("未知的图像生成预设。")
    allowed = _char_allowlist(char_id)
    if allowed is not None and wanted not in allowed:
        raise ImagePresetError("当前角色不可以使用指定的图像生成预设。")
    if not connection_ready(preset):
        raise ImagePresetError("指定的图像生成预设连接未配置。")
    return {"name": wanted, **deepcopy(preset)}


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("预设名须以字母开头，仅含字母、数字、下划线和短横线")
    return name


def image_reference_bytes(
    *,
    user_id: str | None = None,
    char_id: str | None = None,
    sha256: str | None = None,
    artifact_id: str | None = None,
) -> tuple[bytes, str, str]:
    """Resolve reference image bytes from sha256 (media/library) or an artifact.

    Returns (bytes, mime, source_label). Scope is checked fail-closed; the
    model passes only opaque identifiers, never paths.
    """
    from pathlib import Path

    has_sha = bool(str(sha256 or "").strip())
    has_art = bool(str(artifact_id or "").strip())
    if has_sha == has_art:
        raise ValueError("reference_sha256 与 reference_artifact_id 只能传其一。")
    if has_sha:
        from core.chat_media import ChatMediaError, resolve_chat_media

        try:
            record = resolve_chat_media(str(sha256), uid=str(user_id), char_id=str(char_id))
        except ChatMediaError as exc:
            raise ValueError(f"找不到参考图 {str(sha256)[:16]}…：{exc.detail}") from exc
        data = record.path.read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("参考图超过 10MB 上限。")
        mime = record.mime or "image/png"
        return data, mime, record.source
    from core.tools.chat_artifacts import (
        ArtifactError,
        artifact_file_path,
        get_artifact_record,
        resolve_artifact_scope,
    )

    scope = resolve_artifact_scope(str(artifact_id))
    if scope is None:
        raise ValueError("生成图不存在或不在当前账号下。")
    try:
        record = get_artifact_record(scope["id"], uid=scope["uid"], char_id=scope["char_id"])
    except ArtifactError as exc:
        raise ValueError(f"生成图不存在或不在当前账号下。") from exc
    if record is None:
        raise ValueError("生成图不存在或不在当前账号下。")
    path = artifact_file_path(record)
    if not path.exists() or not path.is_file():
        raise ValueError("生成图文件已不存在。")
    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("参考图超过 10MB 上限。")
    ext = Path(str(record.get("filename") or "")).suffix.lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext[1:] if ext else "", "image/png")
    return data, mime, "artifact"
