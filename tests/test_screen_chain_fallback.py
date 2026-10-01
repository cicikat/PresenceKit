"""Screenshot chains walk the screen route: primary first, fallback on transport failure."""
import pytest

from core.perception import vlm_client
from core.phone_control import vision_client

LOCAL = {"kind": "vision", "enabled": True, "api_protocol": "chat_completions",
         "model": "local-vl", "base_url": "http://127.0.0.1:11434/v1"}
REMOTE = {"kind": "vision", "enabled": True, "api_protocol": "chat_completions",
          "model": "remote-vl", "base_url": "https://vision.example/v1", "api_key": "k"}


def _cfg(*, fallback=True, gate=True):
    cfg = {
        "image_presets": {
            "presets": {"local": dict(LOCAL), "remote": dict(REMOTE)},
            "routes": {"screen": "local"},
            **({"fallbacks": {"screen": "remote"}} if fallback else {}),
        },
        "visual_perception": {"enabled": gate, "timeout_s": 7},
    }
    return cfg


@pytest.fixture
def config(monkeypatch):
    holder = {"cfg": _cfg()}
    monkeypatch.setattr("core.config_loader.get_config", lambda: holder["cfg"])
    return holder


def test_screen_route_chain_is_empty_behind_a_closed_privacy_gate(config):
    config["cfg"] = _cfg(gate=False)
    assert vlm_client.screen_route_chain() == []
    # The privacy gate is only the shadow chain's; phone control has its own consent.
    assert [row["name"] for row in vision_client.phone_vision_chain()] == ["local", "remote"]


def test_screen_route_chain_carries_both_connections_and_the_timeout(config):
    chain = vlm_client.screen_route_chain()
    assert [row["name"] for row in chain] == ["local", "remote"]
    assert all(row["config"]["timeout_s"] == 7 for row in chain)


@pytest.mark.asyncio
async def test_shadow_chain_falls_back_when_the_primary_is_unreachable(config, monkeypatch):
    seen = []
    observation = object()

    async def fake(cfg, image, hint):
        seen.append(cfg["model"])
        if cfg["model"] == "local-vl":
            return None, "error", "connection_error"
        return observation, None, ""

    monkeypatch.setattr(vlm_client, "_describe_once", fake)
    assert await vlm_client.describe_with_status(b"img") == (observation, None)
    assert seen == ["local-vl", "remote-vl"]


@pytest.mark.asyncio
async def test_shadow_chain_does_not_repay_for_an_invalid_reply(config, monkeypatch):
    seen = []

    async def fake(cfg, image, hint):
        seen.append(cfg["model"])
        return None, "invalid", "invalid_response"

    monkeypatch.setattr(vlm_client, "_describe_once", fake)
    assert await vlm_client.describe_with_status(b"img") == (None, "invalid")
    assert seen == ["local-vl"]


@pytest.mark.asyncio
async def test_shadow_chain_without_a_fallback_reports_the_primary_failure(config, monkeypatch):
    config["cfg"] = _cfg(fallback=False)

    async def fake(cfg, image, hint):
        return None, "error", "timeout"

    monkeypatch.setattr(vlm_client, "_describe_once", fake)
    assert await vlm_client.describe_with_status(b"img") == (None, "error")


@pytest.mark.asyncio
async def test_phone_chain_falls_back_on_transport_failure_only(config, monkeypatch):
    seen = []
    action = vision_client.NextAction(status="done", action=None, reasoning="")

    async def fake(cfg, content):
        seen.append(cfg["model"])
        if cfg["model"] == "local-vl":
            return None, "error", "timeout"
        return action, None, ""

    monkeypatch.setattr(vision_client, "_decide_once", fake)
    kwargs = dict(task="t", package_name="p", screen_title="s", nodes=[], screenshot_base64=None)
    assert await vision_client.decide_next_action(**kwargs) == (action, None)
    assert seen == ["local-vl", "remote-vl"]

    seen.clear()

    async def invalid(cfg, content):
        seen.append(cfg["model"])
        return None, "invalid", "invalid_response"

    monkeypatch.setattr(vision_client, "_decide_once", invalid)
    assert await vision_client.decide_next_action(**kwargs) == (None, "invalid")
    assert seen == ["local-vl"]


@pytest.mark.asyncio
async def test_phone_chain_unrouted_is_unconfigured(config):
    config["cfg"] = {"image_presets": {"presets": {"local": dict(LOCAL)}, "routes": {"screen": ""},
                                       "fallbacks": {}}}
    # An explicit empty screen route stays off even though a preset exists.
    result = await vision_client.decide_next_action(
        task="t", package_name="p", screen_title="s", nodes=[], screenshot_base64=None)
    assert result == (None, "unconfigured")
