"""Upload OCR transport, independent of screen/phone vision connections."""
from __future__ import annotations

import hashlib
import json
import time
from urllib.parse import urlsplit

import aiohttp

from core.config_loader import get_config
from core.proxy_config import get_aiohttp_proxy

GLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
PROTOCOLS = {"chat_completions", "glm_layout_parsing"}


def settings(config: dict | None = None) -> dict:
    config = get_config() if config is None else config
    return {"mode": "vision", "api_protocol": "glm_layout_parsing",
            "provider": "glm", "model": "glm-ocr", "endpoint_url": GLM_ENDPOINT,
            "base_url": "", "api_key": "", **config.get("image_recognition", {})}


def endpoint(cfg: dict) -> str:
    protocol = cfg.get("api_protocol")
    if protocol not in PROTOCOLS:
        raise ValueError("Unsupported OCR API protocol")
    address = (cfg.get("endpoint_url") if protocol == "glm_layout_parsing" else cfg.get("base_url")) or ""
    parsed = urlsplit(address)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("OCR address must be an HTTP(S) URL without credentials or fragment")
    if protocol == "chat_completions":
        if parsed.query:
            raise ValueError("Chat Base URL must not contain a query")
        return address.rstrip("/") + "/chat/completions"
    return address


def view(config: dict | None = None) -> dict:
    config = get_config() if config is None else config
    cfg = settings(config)
    result = {k: cfg[k] for k in ("mode", "provider", "api_protocol", "model", "base_url", "endpoint_url")}
    result["has_api_key"] = bool(cfg.get("api_key"))
    try:
        result["request_url"] = endpoint(cfg)
        configured = bool(cfg.get("model")) and (cfg["api_protocol"] != "glm_layout_parsing" or bool(cfg.get("api_key")))
    except ValueError:
        result["request_url"] = ""
        configured = False
    result["configured"] = configured
    vision = config.get("vision", {})
    result["effective"] = (configured if cfg["mode"] == "ocr" else
                           bool(vision.get("enabled") and vision.get("base_url") and vision.get("model")))
    result["state"] = "ready_not_tested" if result["effective"] else "not_configured"
    return result


def cache_signature(config: dict | None = None) -> str:
    config = get_config() if config is None else config
    cfg = settings(config)
    connection = cfg if cfg["mode"] == "ocr" else config.get("vision", {})
    fields = {key: connection.get(key) for key in ("enabled", "provider", "api_protocol", "model", "base_url", "endpoint_url")}
    fields["mode"] = cfg["mode"]
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


async def recognize_ocr(image_uri: str, cfg: dict | None = None) -> str:
    cfg = settings() if cfg is None else cfg
    started = time.monotonic()
    ok = False
    error = ""
    try:
        url = endpoint(cfg)
        if not cfg.get("model"):
            raise ValueError("OCR model is missing")
        if cfg["api_protocol"] == "glm_layout_parsing" and not cfg.get("api_key"):
            raise ValueError("GLM OCR API key is missing")
        if cfg["api_protocol"] == "glm_layout_parsing":
            payload = {"model": cfg["model"], "file": image_uri}
        else:
            payload = {"model": cfg["model"], "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Text Recognition:"},
                {"type": "image_url", "image_url": {"url": image_uri}},
            ]}]}
        headers = {"Authorization": "Bearer " + cfg["api_key"]} if cfg.get("api_key") else {}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(url, json=payload, headers=headers, proxy=get_aiohttp_proxy(), allow_redirects=False) as response:
                if response.status != 200:
                    error = f"http_{response.status}"
                    raise ValueError(f"OCR HTTP {response.status}")
                body = await response.json()
        if not isinstance(body, dict) or body.get("error"):
            raise ValueError("OCR provider error")
        if cfg["api_protocol"] == "glm_layout_parsing":
            result = body.get("md_results")
        else:
            choices = body.get("choices") or []
            if choices and choices[0].get("finish_reason") == "length":
                raise ValueError("OCR response was truncated")
            result = choices[0].get("message", {}).get("content") if choices else None
        if not isinstance(result, str):
            raise ValueError("Invalid OCR response schema")
        ok = True
        return result.strip() or "[OCR: no text detected]"
    except Exception as exc:
        error = error or type(exc).__name__
        raise ValueError(f"OCR request failed ({error})") from None
    finally:
        from core import api_call_log
        api_call_log.append(caller="image_ocr", purpose="ocr", provider=str(cfg.get("provider", "")),
                            model=str(cfg.get("model", "")), protocol=str(cfg.get("api_protocol", "")),
                            duration_ms=int((time.monotonic() - started) * 1000), ok=ok, error_category=error)
