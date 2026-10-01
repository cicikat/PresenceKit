"""Named image connections and purpose routes.

``image_presets.presets`` holds named connections (``kind: vision|ocr``).
``image_presets.routes`` maps purposes to those names.

When the named block is absent or has no presets, connections and routes are
synthesized from ``vision:``, ``image_recognition:`` and
``phone_control_vision:`` so runtime semantics stay unchanged.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from urllib.parse import urlsplit

from core import config_loader


def get_config() -> dict:
    """Look the loader up at call time.

    Binding ``config_loader.get_config`` into this module at import time made the
    repo-wide ``monkeypatch.setattr("core.config_loader.get_config", ...)`` a
    no-op here: routes resolved against the real sandbox config while the test
    believed it had supplied one.
    """
    return config_loader.get_config()

PURPOSES = (
    "chat_upload",
    "life_diet",
    "life_cart",
    "life_bill",
    "phone_automation",
    "video_call",
    "video_call_tool",
    "screen",
)
# Real-world camera frames may leave only to a loopback vision model, whichever
# chain asks: periodic video-call observation or the on-demand fresh-frame tool.
CAMERA_PURPOSES = ("video_call", "video_call_tool")
LOOPBACK_ONLY_PURPOSES = frozenset(CAMERA_PURPOSES)
# Every screenshot chain — the on-demand tool (desktop and phone), the shadow
# ingress, and phone automation — shares one route, and may name one fallback.
SCREEN_PURPOSES = ("screen",)
FALLBACK_PURPOSES = frozenset(SCREEN_PURPOSES)
VISION_KINDS = frozenset({"vision"})
OCR_KINDS = frozenset({"ocr"})
KINDS = VISION_KINDS | OCR_KINDS
ALIASES = ("general", "ocr", "phone")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

_VISION_FIELDS = (
    "kind", "enabled", "provider", "api_protocol", "model", "base_url", "api_key",
)
_OCR_FIELDS = (
    "kind", "provider", "api_protocol", "model", "base_url", "endpoint_url", "api_key",
)
_VISION_PROTOCOLS = frozenset({"chat_completions", "responses", "anthropic_messages"})
_OCR_PROTOCOLS = frozenset({"chat_completions", "glm_layout_parsing"})


def catalog(config: dict | None = None) -> dict:
    """Return ``{presets, routes, synthesized}`` without mutating config."""
    config = get_config() if config is None else config
    block = config.get("image_presets")
    stored_presets = block.get("presets") if isinstance(block, dict) else None
    if isinstance(stored_presets, dict) and stored_presets:
        presets = {
            name: _normalize_preset(payload)
            for name, payload in stored_presets.items()
            if isinstance(name, str) and isinstance(payload, dict)
        }
        routes = _default_routes(config, presets)
        stored_routes = block.get("routes") if isinstance(block.get("routes"), dict) else {}
        for purpose, name in stored_routes.items():
            if purpose in PURPOSES and isinstance(name, str) and name in presets:
                routes[purpose] = name
        _inherit_routes(routes, stored_routes)
        fallbacks = _read_fallbacks(block, presets, routes)
        return {"presets": presets, "routes": routes, "fallbacks": fallbacks,
                "synthesized": False}
    synthesized = _synthesize(config)
    _inherit_routes(synthesized["routes"], {})
    return {**synthesized, "fallbacks": {}, "synthesized": True}


def _inherit_routes(routes: dict, stored_routes: dict) -> None:
    """An unset derived route follows its parent; an explicit empty stays off."""
    if "video_call_tool" not in stored_routes and not routes.get("video_call_tool"):
        routes["video_call_tool"] = routes.get("video_call", "")
    # phone_automation predates the unified screen route; until the owner saves
    # the new row once, keep their phone-automation model answering screenshots.
    if "screen" not in stored_routes and not routes.get("screen"):
        routes["screen"] = routes.get("phone_automation", "")


def _read_fallbacks(block: dict, presets: dict, routes: dict) -> dict:
    """Second connection per purpose; never the primary, never a non-vision kind."""
    stored = block.get("fallbacks") if isinstance(block.get("fallbacks"), dict) else {}
    chosen = {}
    for purpose, name in stored.items():
        if purpose not in FALLBACK_PURPOSES or not isinstance(name, str):
            continue
        if name not in presets or name == routes.get(purpose):
            continue
        if presets[name].get("kind") != "vision":
            continue
        chosen[purpose] = name
    return chosen


def snapshot(config: dict | None = None) -> dict:
    """Admin/observation view: masked keys, purpose table, ready flags."""
    config = get_config() if config is None else config
    cat = catalog(config)
    presets = {
        name: _mask_preset(preset)
        for name, preset in cat["presets"].items()
    }
    purposes = []
    for purpose in PURPOSES:
        name = cat["routes"].get(purpose, "")
        preset = cat["presets"].get(name, {})
        if purpose in LOOPBACK_ONLY_PURPOSES:
            ready = video_call_ready(preset)[0]
        else:
            ready = connection_ready(preset) if preset else False
        fallback = cat["fallbacks"].get(purpose, "")
        purposes.append({
            "purpose": purpose,
            "connection": name,
            "kind": preset.get("kind", ""),
            "ready": ready,
            "fallback": fallback,
            "fallback_ready": connection_ready(cat["presets"].get(fallback)) if fallback else False,
            "supports_fallback": purpose in FALLBACK_PURPOSES,
            "group": ("camera" if purpose in CAMERA_PURPOSES
                      else "screen" if purpose in SCREEN_PURPOSES else "other"),
            "is_local": bool(preset.get("is_local")) if preset else False,
            "fallback_is_local": bool(cat["presets"].get(fallback, {}).get("is_local")) if fallback else False,
            "source": "image_presets" if not cat["synthesized"] else "legacy",
        })
    return {
        "synthesized": cat["synthesized"],
        "presets": presets,
        "routes": dict(cat["routes"]),
        "fallbacks": dict(cat["fallbacks"]),
        "purposes": purposes,
    }


def resolve_purpose(purpose: str, config: dict | None = None) -> dict:
    if purpose not in PURPOSES:
        raise KeyError(purpose)
    config = get_config() if config is None else config
    cat = catalog(config)
    name = cat["routes"].get(purpose)
    if not name or name not in cat["presets"]:
        raise KeyError(purpose)
    preset = deepcopy(cat["presets"][name])
    return {
        "purpose": purpose,
        "name": name,
        "kind": preset.get("kind", "vision"),
        "config": preset,
        "ready": connection_ready(preset),
        "synthesized": cat["synthesized"],
    }


def resolve_purpose_chain(purpose: str, config: dict | None = None) -> list[dict]:
    """Ordered connections to try: the primary first, then its fallback if any.

    Screenshot chains walk this instead of calling :func:`resolve_purpose` once,
    so a local model that is down hands off to a remote one. An empty list means
    nothing is routed — callers must fail loudly rather than reach for a default.
    """
    if purpose not in PURPOSES:
        raise KeyError(purpose)
    config = get_config() if config is None else config
    cat = catalog(config)
    chain: list[dict] = []
    try:
        chain.append({**resolve_purpose(purpose, config), "route_role": "primary"})
    except KeyError:
        pass
    name = cat["fallbacks"].get(purpose)
    if name and name in cat["presets"]:
        preset = deepcopy(cat["presets"][name])
        chain.append({"purpose": purpose, "name": name, "route_role": "fallback",
                      "kind": preset.get("kind", "vision"), "config": preset,
                      "ready": connection_ready(preset),
                      "synthesized": cat["synthesized"]})
    return chain


def screen_vision_chain(config: dict | None = None) -> list[dict]:
    """The screen route's vision connections, primary first; ``[]`` when unrouted.

    Shared by every screenshot chain (shadow ingress, on-demand tool, phone
    automation) so none of them keeps its own copy of the route lookup. Gates
    such as ``visual_perception.enabled`` stay with the caller.
    """
    try:
        chain = resolve_purpose_chain("screen", config)
    except KeyError:
        return []
    return [row for row in chain if row.get("kind") == "vision"]


def aiohttp_error_category(exc: BaseException) -> str:
    """Classify a raw-aiohttp vision failure the way llm_client classifies its own.

    The screenshot chains talk to their models over plain aiohttp rather than the
    OpenAI SDK, so they cannot reuse ``error_category_for_exception``; without a
    shared vocabulary the fallback decision would differ per chain.
    """
    import asyncio

    import aiohttp
    from core.llm_protocol import _http_error_category

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return _http_error_category(status)
    if isinstance(exc, aiohttp.ClientConnectionError):
        return "connection_error"
    if isinstance(exc, aiohttp.ClientError):
        return "protocol_incompatible"
    return "error"


def should_try_fallback(error_category: str) -> bool:
    """Reuse the text-model failover rules so one policy covers both.

    A reply the model did produce but we rejected (bad JSON, schema mismatch) is
    not a reason to pay a second provider for the same image.
    """
    from core.llm_failover import FAILOVER_ELIGIBLE
    return error_category in FAILOVER_ELIGIBLE


def resolve_connection(name: str, config: dict | None = None) -> dict:
    """Resolve a saved connection name or a general/ocr/phone alias."""
    if not isinstance(name, str) or not name:
        raise KeyError(name)
    config = get_config() if config is None else config
    cat = catalog(config)
    presets = cat["presets"]
    if name in presets:
        preset = deepcopy(presets[name])
        return {"name": name, **preset}
    if name == "phone":
        routed = cat["routes"].get("phone_automation")
        if routed in presets:
            preset = deepcopy(presets[routed])
            return {"name": routed, **preset}
        merged = _phone_merge(config)
        return {"name": "phone", **merged}
    if name in ("general", "ocr"):
        kind = "ocr" if name == "ocr" else "vision"
        for preset_name, preset in presets.items():
            if preset.get("kind") == kind:
                return {"name": preset_name, **deepcopy(preset)}
        if name == "general":
            return {"name": "general", **_general_from_vision(config)}
        return {"name": "ocr", **_ocr_from_recognition(config)}
    raise KeyError(name)


def connection_ready(preset: dict | None) -> bool:
    if not isinstance(preset, dict):
        return False
    kind = preset.get("kind") or "vision"
    if kind == "ocr":
        try:
            _ocr_request_url(preset)
        except ValueError:
            return False
        if not preset.get("model"):
            return False
        if preset.get("api_protocol", "glm_layout_parsing") == "glm_layout_parsing":
            return bool(preset.get("api_key"))
        return True
    if preset.get("enabled") is False:
        return False
    return bool(preset.get("base_url") and preset.get("model"))


def video_call_ready(preset: dict | None) -> tuple[bool, str]:
    """Video frames may use only an enabled loopback Chat Completions vision model."""
    if not preset or preset.get("kind") != "vision":
        return False, "vision_connection_required"
    if not connection_ready(preset):
        return False, "connection_not_ready"
    if preset.get("api_protocol") != "chat_completions":
        return False, "chat_completions_required"
    parsed = urlsplit(str(preset.get("base_url") or ""))
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        return False, "loopback_http_required"
    return True, ""


def referencing_purposes(name: str, config: dict | None = None) -> list[str]:
    """Purposes that would break if ``name`` were deleted, fallbacks included."""
    cat = catalog(config)
    refs = [purpose for purpose, conn in cat["routes"].items() if conn == name]
    refs += [f"{purpose}:fallback" for purpose, conn in cat["fallbacks"].items()
             if conn == name and purpose not in refs]
    return refs


def _ocr_settings(config: dict | None) -> dict:
    from core.image_recognition import settings as ocr_settings
    try:
        return ocr_settings(config)
    except TypeError:
        return ocr_settings()


def cache_signature(purpose: str = "chat_upload", config: dict | None = None) -> str:
    config = get_config() if config is None else config
    cat = catalog(config)
    if cat["synthesized"] and purpose == "chat_upload":
        cfg = _ocr_settings(config)
        connection = cfg if cfg.get("mode") == "ocr" else config.get("vision", {})
        fields = {key: connection.get(key) for key in (
            "enabled", "provider", "api_protocol", "model", "base_url", "endpoint_url",
        )}
        fields["mode"] = cfg.get("mode", "vision")
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    try:
        route = resolve_purpose(purpose, config)
        connection = route["config"]
        kind = route["kind"]
        name = route["name"]
    except KeyError:
        connection, kind, name = {}, "vision", ""
    fields = {key: connection.get(key) for key in (
        "enabled", "provider", "api_protocol", "model", "base_url", "endpoint_url",
    )}
    fields.update(purpose=purpose, kind=kind, name=name)
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("连接名须以字母开头，仅含字母、数字、下划线和短横线")
    return name


def validate_preset(payload: dict) -> dict:
    kind = payload.get("kind") or "vision"
    if kind not in KINDS:
        raise ValueError("kind 必须是 vision 或 ocr")
    preset = _normalize_preset({"kind": kind, **payload})
    if kind == "ocr":
        if preset.get("api_protocol") not in _OCR_PROTOCOLS:
            raise ValueError("Unsupported OCR API protocol")
        if preset.get("endpoint_url") or preset.get("base_url"):
            _ocr_request_url(preset)
    elif preset.get("api_protocol") not in _VISION_PROTOCOLS:
        raise ValueError("Unsupported vision API protocol")
    return preset


def bootstrap_from_legacy(config: dict) -> dict:
    """Return a persistable ``image_presets`` block copied from legacy slots."""
    synthesized = _synthesize(config)
    return {
        "presets": deepcopy(synthesized["presets"]),
        "routes": dict(synthesized["routes"]),
    }


def _synthesize(config: dict) -> dict:
    presets: dict[str, dict] = {
        "general": _general_from_vision(config),
        "ocr": _ocr_from_recognition(config),
    }
    overlay = config.get("phone_control_vision")
    if isinstance(overlay, dict) and overlay:
        presets["phone"] = _phone_merge(config)
    routes = _default_routes(config, presets)
    return {"presets": presets, "routes": routes}


def _default_routes(config: dict, presets: dict) -> dict:
    vision_name = "general" if "general" in presets else _first_kind(presets, "vision")
    ocr_name = "ocr" if "ocr" in presets else _first_kind(presets, "ocr")
    phone_name = "phone" if "phone" in presets else vision_name
    chat = ocr_name if _legacy_upload_mode(config) == "ocr" and ocr_name else vision_name
    return {
        "chat_upload": chat or vision_name or ocr_name or next(iter(presets), ""),
        "life_diet": vision_name or next(iter(presets), ""),
        "life_cart": vision_name or next(iter(presets), ""),
        "life_bill": ocr_name or next(iter(presets), ""),
        "phone_automation": phone_name or vision_name or next(iter(presets), ""),
        "video_call": "",
        "video_call_tool": "",
        "screen": "",
    }


def _legacy_upload_mode(config: dict) -> str:
    block = config.get("image_recognition") if isinstance(config.get("image_recognition"), dict) else {}
    mode = block.get("mode") or "vision"
    return "ocr" if mode == "ocr" else "vision"


def _general_from_vision(config: dict) -> dict:
    vision = dict(config.get("vision") or {})
    return _normalize_preset({
        "kind": "vision",
        "enabled": vision.get("enabled", False),
        "provider": vision.get("provider", ""),
        "api_protocol": vision.get("api_protocol", "chat_completions"),
        "model": vision.get("model", ""),
        "base_url": vision.get("base_url", ""),
        "api_key": vision.get("api_key", ""),
    })


def _ocr_from_recognition(config: dict) -> dict:
    cfg = _ocr_settings(config)
    return _normalize_preset({
        "kind": "ocr",
        "provider": cfg.get("provider", ""),
        "api_protocol": cfg.get("api_protocol", "glm_layout_parsing"),
        "model": cfg.get("model", ""),
        "base_url": cfg.get("base_url", ""),
        "endpoint_url": cfg.get("endpoint_url", ""),
        "api_key": cfg.get("api_key", ""),
    })


def _phone_merge(config: dict) -> dict:
    general = _general_from_vision(config)
    dedicated = dict(config.get("phone_control_vision") or {})
    merged = dict(general)
    merged.update({k: v for k, v in dedicated.items() if v is not None and v != ""})
    merged["kind"] = "vision"
    return _normalize_preset(merged)


def _first_kind(presets: dict, kind: str) -> str:
    for name, preset in presets.items():
        if preset.get("kind") == kind:
            return name
    return ""


def _normalize_preset(payload: dict) -> dict:
    kind = payload.get("kind") or "vision"
    if kind == "ocr":
        protocol = payload.get("api_protocol") or "glm_layout_parsing"
        if protocol not in _OCR_PROTOCOLS:
            protocol = "glm_layout_parsing"
        return {
            "kind": "ocr",
            "provider": str(payload.get("provider") or ""),
            "api_protocol": protocol,
            "model": str(payload.get("model") or ""),
            "base_url": str(payload.get("base_url") or ""),
            "endpoint_url": str(payload.get("endpoint_url") or ""),
            "api_key": str(payload.get("api_key") or ""),
            "is_local": _as_is_local(payload.get("is_local")),
        }
    protocol = payload.get("api_protocol") or "chat_completions"
    if protocol not in _VISION_PROTOCOLS:
        protocol = "chat_completions"
    enabled = payload.get("enabled")
    return {
        "kind": "vision",
        "enabled": True if enabled is None else bool(enabled),
        "provider": str(payload.get("provider") or ""),
        "api_protocol": protocol,
        "model": str(payload.get("model") or ""),
        "base_url": str(payload.get("base_url") or ""),
        "api_key": str(payload.get("api_key") or ""),
        "is_local": _as_is_local(payload.get("is_local")),
    }


def _as_is_local(value) -> bool:
    """Explicit, hand-ticked privacy attribution: only a real True counts (fail-closed).

    Never inferred from base_url; loopback inference is only an admin-UI default hint.
    """
    return value is True


def _ocr_request_url(cfg: dict) -> str:
    protocol = cfg.get("api_protocol")
    if protocol not in _OCR_PROTOCOLS:
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


def _mask_preset(preset: dict) -> dict:
    view = dict(preset)
    key = view.get("api_key") or ""
    view["has_api_key"] = bool(key)
    if not key:
        view["api_key"] = ""
    elif len(key) <= 8:
        view["api_key"] = "***"
    else:
        view["api_key"] = key[:4] + "***" + key[-4:]
    view["ready"] = connection_ready(preset)
    return view
