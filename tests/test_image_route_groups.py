"""Camera and screen route groups: single-connection cameras, screen fallback chains."""
import pytest

from core.image_presets import (CAMERA_PURPOSES, FALLBACK_PURPOSES, SCREEN_PURPOSES,
                                catalog, referencing_purposes, resolve_purpose_chain,
                                should_try_fallback, snapshot)

LOCAL = {"kind": "vision", "enabled": True, "api_protocol": "chat_completions",
         "model": "local-vl", "base_url": "http://127.0.0.1:11434/v1"}
REMOTE = {"kind": "vision", "enabled": True, "api_protocol": "chat_completions",
          "model": "remote-vl", "base_url": "https://vision.example/v1", "api_key": "k"}
OCR = {"kind": "ocr", "api_protocol": "chat_completions", "model": "ocr",
       "base_url": "https://ocr.example/v1"}


def _cfg(routes=None, fallbacks=None, presets=None):
    return {"image_presets": {
        "presets": presets or {"local": dict(LOCAL), "remote": dict(REMOTE), "ocr": dict(OCR)},
        "routes": routes if routes is not None else {"screen": "local"},
        **({"fallbacks": fallbacks} if fallbacks is not None else {}),
    }}


def test_screen_is_one_route_for_every_screenshot_chain():
    """按需截图、shadow 截图和手机自动化共用 screen，不各自散配置。"""
    assert "screen" in SCREEN_PURPOSES and "screen" in FALLBACK_PURPOSES
    chain = resolve_purpose_chain("screen", _cfg())
    assert [row["name"] for row in chain] == ["local"]
    assert chain[0]["route_role"] == "primary"


def test_screen_falls_back_to_a_second_connection():
    chain = resolve_purpose_chain("screen", _cfg(fallbacks={"screen": "remote"}))
    assert [(row["name"], row["route_role"]) for row in chain] == [
        ("local", "primary"), ("remote", "fallback")]
    assert chain[1]["config"]["model"] == "remote-vl"


def test_camera_routes_take_no_fallback():
    """现实影像只许发给本机一个模型，兜底会把它送出网。"""
    for purpose in CAMERA_PURPOSES:
        assert purpose not in FALLBACK_PURPOSES
    cfg = _cfg(routes={"video_call": "local"}, fallbacks={"video_call": "remote"})
    assert catalog(cfg)["fallbacks"] == {}
    assert [row["name"] for row in resolve_purpose_chain("video_call", cfg)] == ["local"]


def test_fallback_rejects_the_primary_itself_and_non_vision_kinds():
    assert catalog(_cfg(fallbacks={"screen": "local"}))["fallbacks"] == {}
    assert catalog(_cfg(fallbacks={"screen": "ocr"}))["fallbacks"] == {}
    assert catalog(_cfg(fallbacks={"screen": "absent"}))["fallbacks"] == {}


def test_screen_inherits_phone_automation_until_saved_once():
    """并入前手机自动化已配好的模型不能因为改版就停摆。"""
    inherited = catalog(_cfg(routes={"phone_automation": "remote"}))
    assert inherited["routes"]["screen"] == "remote"
    explicit = catalog(_cfg(routes={"phone_automation": "remote", "screen": "local"}))
    assert explicit["routes"]["screen"] == "local"
    disabled = catalog(_cfg(routes={"phone_automation": "remote", "screen": ""}))
    assert disabled["routes"]["screen"] == ""
    assert resolve_purpose_chain("screen", _cfg(routes={"phone_automation": "remote",
                                                       "screen": ""})) == []


def test_only_transport_failures_pay_for_a_second_model():
    for category in ("connection_error", "timeout", "upstream_unavailable",
                     "upstream_rate_limited", "geo_blocked"):
        assert should_try_fallback(category) is True
    for category in ("invalid", "invalid_response", "auth_error", "bad_request", ""):
        assert should_try_fallback(category) is False


def test_a_fallback_only_connection_still_blocks_deletion():
    refs = referencing_purposes("remote", _cfg(fallbacks={"screen": "remote"}))
    assert refs == ["screen:fallback"]


def test_snapshot_groups_purposes_and_reports_the_fallback():
    view = snapshot(_cfg(fallbacks={"screen": "remote"}))
    rows = {row["purpose"]: row for row in view["purposes"]}
    assert rows["screen"]["group"] == "screen"
    assert rows["screen"]["supports_fallback"] is True
    assert rows["screen"]["fallback"] == "remote" and rows["screen"]["fallback_ready"] is True
    assert rows["video_call"]["group"] == "camera"
    assert rows["video_call"]["supports_fallback"] is False
    assert rows["chat_upload"]["group"] == "other"
    assert view["fallbacks"] == {"screen": "remote"}


def test_unknown_purpose_still_raises():
    with pytest.raises(KeyError):
        resolve_purpose_chain("not_a_purpose", _cfg())
