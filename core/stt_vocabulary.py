"""Owner-configured pronunciation hints and exact transcription corrections."""
from __future__ import annotations

import re
from typing import Any

MAX_ENTRIES = 32


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    entries = payload.get("entries") or []
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError("STT 自定义词最多 32 条")
    cleaned = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("STT 自定义词格式错误")
        heard = str(item.get("heard") or "").strip()
        canonical = str(item.get("canonical") or "").strip()
        if not 2 <= len(heard) <= 60 or not 1 <= len(canonical) <= 40:
            raise ValueError("每条需填写 2–60 字的发音/误识别词与 1–40 字的正确写法")
        if any(ord(ch) < 32 for ch in heard + canonical) or "=>" in heard + canonical:
            raise ValueError("自定义词不能包含控制字符或分隔符")
        cleaned.append({"heard": heard, "canonical": canonical})
    return {"enabled": payload.get("enabled") is True, "entries": cleaned}


def settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if config is None:
        from core.config_loader import get_config
        config = get_config()
    try:
        return validate(config.get("stt_vocabulary") or {})
    except ValueError:
        return {"enabled": False, "entries": []}


def prompt(config: dict[str, Any] | None = None) -> str:
    block = settings(config)
    if not block["enabled"]:
        return ""
    pairs = [f"{item['canonical']}（读音或常见误写：{item['heard']}）" for item in block["entries"]]
    return ("以下是语音中的专有名词，请按实际听到的内容转写：" + "；".join(pairs))[:1200] if pairs else ""


def hotwords(config: dict[str, Any] | None = None) -> str:
    block = settings(config)
    if not block["enabled"]:
        return ""
    return ", ".join(dict.fromkeys(item["canonical"] for item in block["entries"]))[:800]


def correct(text: str, config: dict[str, Any] | None = None) -> str:
    block = settings(config)
    if not block["enabled"] or not text:
        return text
    result = text
    for item in sorted(block["entries"], key=lambda entry: len(entry["heard"]), reverse=True):
        heard = item["heard"]
        if heard.isascii():
            pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(heard) + r"(?![A-Za-z0-9])", re.IGNORECASE)
        else:
            pattern = re.compile(re.escape(heard))
        result = pattern.sub(lambda _match: item["canonical"], result)
    return result
