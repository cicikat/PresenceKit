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

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
MAX_PROMPT_CHARS = 8_000
MAX_IMAGE_BYTES = 25 * 1024 * 1024


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
        "provider_kind": str(payload.get("provider_kind") or "openai_compatible"),
        "base_url": str(payload.get("base_url") or ""),
        "api_key": str(payload.get("api_key") or ""),
        "model": str(payload.get("model") or ""),
        "extra_params": deepcopy(extra) if isinstance(extra, dict) else {},
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
    return {"presets": presets, "enabled": bool(block.get("enabled"))}


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
        result.append({"name": name, "description": preset.get("description") or ""})
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
        parts.append(f"{row['name']}：{desc}" if desc else str(row["name"]))
    return "可选图像预设（用 preset 参数指定，省略用默认）：{}。".format("；".join(parts))


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
        raise ValueError(f"未知的图像生成预设：{wanted}。")
    allowed = _char_allowlist(char_id)
    if allowed is not None and wanted not in allowed:
        raise ValueError(f"当前角色不可以使用图像生成预设：{wanted}。")
    if not connection_ready(preset):
        raise ValueError(f"图像生成预设 {wanted} 连接未配置。")
    return {"name": wanted, **deepcopy(preset)}


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("预设名须以字母开头，仅含字母、数字、下划线和短横线")
    return name
