"""Regression coverage for the shared desktop/mobile on-demand TTS contract."""

from unittest.mock import AsyncMock, patch

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient


VALID_TOKEN = "tts-auto-play-test-secret"


def _auth():
    return {"Authorization": f"Bearer {VALID_TOKEN}"}


def test_auto_play_round_trips_mobile_and_legacy_desktop(tmp_path, monkeypatch):
    import admin.routers.settings_misc as settings

    config_path = tmp_path / "config.yaml"
    config_path.write_text("tts:\n  enabled: true\n  desktop_enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_FILE", config_path)
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    monkeypatch.setattr(settings, "get_config", lambda: yaml.safe_load(config_path.read_text(encoding="utf-8")) or {})

    app = FastAPI()
    app.include_router(settings.router)
    client = TestClient(app)
    with patch("core.config_loader.reload_config", return_value=None):
        response = client.post(
            "/settings/tts-auto-play",
            json={"mobile": True, "chat": True},
            headers=_auth(),
        )
        assert response.status_code == 200
        assert response.json()["mobile"] is True
        assert response.json()["chat"] is True

        response = client.post("/settings/tts-desktop", json={"enabled": True}, headers=_auth())
        assert response.status_code == 200

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["tts"]["auto_play"] == {"mobile": True, "chat": True, "desktop_pet": True}
    response = client.get("/settings/tts-auto-play", headers=_auth())
    assert response.status_code == 200
    assert response.json() == {
        "chat": True,
        "dream": False,
        "video_call": False,
        "desktop_pet": True,
        "mobile": True,
    }


def test_synthesize_accepts_mobile_and_strips_parenthetical_narration(monkeypatch):
    import admin.routers.settings_misc as settings

    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)
    monkeypatch.setattr(settings, "get_config", lambda: {"tts": {"enabled": True}})
    synthesize = AsyncMock(return_value=b"wav")
    monkeypatch.setattr("core.output.voice_adapter.synthesize", synthesize)

    app = FastAPI()
    app.include_router(settings.router)
    client = TestClient(app)
    response = client.post(
        "/tts/synthesize",
        json={"text": "（雨声渐近）你好。(她停了停) 晚安。", "scene": "mobile"},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert synthesize.await_args.args[:2] == ("你好。 晚安。", "neutral")


async def test_mobile_queue_marks_voice_availability(sandbox, monkeypatch, enabled_client_channels):
    from channels.mobile import MobileChannel

    monkeypatch.setattr("channels.mobile._is_tts_available", lambda: True)
    channel = MobileChannel()
    await channel.send("hello", "owner")
    queued = channel._load_queue()
    assert queued[0]["voice_available"] is True
