"""Named image connections, purpose routing, and legacy synthesis."""
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import HTTPException

from core.image_presets import catalog, resolve_purpose, resolve_connection, referencing_purposes


LEGACY = {
    "vision": {
        "enabled": True, "provider": "glm", "model": "glm-4v-flash",
        "base_url": "https://vision.example/v1", "api_key": "vision-secret",
    },
    "image_recognition": {
        "mode": "vision", "provider": "glm", "api_protocol": "glm_layout_parsing",
        "model": "glm-ocr", "endpoint_url": "https://ocr.example/layout", "api_key": "ocr-secret",
    },
}


def test_synthesizes_legacy_slots_without_named_block():
    cat = catalog(LEGACY)
    assert cat["synthesized"] is True
    assert set(cat["presets"]) >= {"general", "ocr"}
    assert cat["routes"]["chat_upload"] == "general"
    assert cat["routes"]["life_diet"] == "general"
    assert cat["routes"]["life_cart"] == "general"
    assert cat["routes"]["life_bill"] == "ocr"
    assert cat["routes"]["phone_automation"] == "general"
    assert resolve_purpose("chat_upload", LEGACY)["kind"] == "vision"
    assert resolve_purpose("life_bill", LEGACY)["kind"] == "ocr"


def test_chat_upload_follows_legacy_ocr_mode():
    cfg = {**LEGACY, "image_recognition": {**LEGACY["image_recognition"], "mode": "ocr"}}
    assert catalog(cfg)["routes"]["chat_upload"] == "ocr"
    assert resolve_purpose("chat_upload", cfg)["kind"] == "ocr"


def test_named_routes_override_synthesis():
    cfg = {
        **LEGACY,
        "image_presets": {
            "presets": {
                "scene": {"kind": "vision", "enabled": True, "model": "scene-v", "base_url": "https://a.example/v1", "api_key": "k"},
                "receipts": {"kind": "ocr", "api_protocol": "chat_completions", "model": "ocr-mini", "base_url": "https://b.example/v1"},
            },
            "routes": {
                "chat_upload": "receipts",
                "life_diet": "scene",
                "life_cart": "scene",
                "life_bill": "receipts",
                "phone_automation": "scene",
            },
        },
    }
    assert catalog(cfg)["synthesized"] is False
    assert resolve_purpose("chat_upload", cfg)["name"] == "receipts"
    assert resolve_purpose("life_diet", cfg)["name"] == "scene"
    assert resolve_connection("general", cfg)["name"] == "scene"
    assert resolve_connection("ocr", cfg)["name"] == "receipts"


@pytest.mark.asyncio
async def test_delete_referenced_connection_is_conflict(tmp_path, monkeypatch):
    from admin.routers import settings_llm as admin
    from core import config_loader, llm_client

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "image_presets": {
            "presets": {
                "scene": {"kind": "vision", "enabled": True, "model": "m", "base_url": "https://a.example/v1"},
                "spare": {"kind": "vision", "enabled": True, "model": "n", "base_url": "https://b.example/v1"},
            },
            "routes": {"chat_upload": "scene", "life_diet": "scene", "life_cart": "scene",
                       "life_bill": "scene", "phone_automation": "scene"},
        }
    }), encoding="utf-8")
    read = lambda: yaml.safe_load(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(admin, "CONFIG_FILE", path)
    monkeypatch.setattr(admin, "get_config", read)
    monkeypatch.setattr(config_loader, "reload_config", read)
    monkeypatch.setattr(llm_client, "reload_client", AsyncMock())
    with pytest.raises(HTTPException) as error:
        await admin.delete_image_preset("scene", auth=None)
    assert error.value.status_code == 409
    assert "chat_upload" in str(error.value.detail)
    assert "scene" in read()["image_presets"]["presets"]


@pytest.mark.asyncio
async def test_unreferenced_connection_can_be_deleted(tmp_path, monkeypatch):
    from admin.routers import settings_llm as admin
    from core import config_loader, llm_client

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "image_presets": {
            "presets": {
                "scene": {"kind": "vision", "enabled": True, "model": "m", "base_url": "https://a.example/v1"},
                "spare": {"kind": "vision", "enabled": True, "model": "n", "base_url": "https://b.example/v1"},
            },
            "routes": {"chat_upload": "scene", "life_diet": "scene", "life_cart": "scene",
                       "life_bill": "scene", "phone_automation": "scene"},
        }
    }), encoding="utf-8")
    read = lambda: yaml.safe_load(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(admin, "CONFIG_FILE", path)
    monkeypatch.setattr(admin, "get_config", read)
    monkeypatch.setattr(config_loader, "reload_config", read)
    monkeypatch.setattr(llm_client, "reload_client", AsyncMock())
    result = await admin.delete_image_preset("spare", auth=None)
    assert "spare" not in result["presets"]
    assert "spare" not in read()["image_presets"]["presets"]


@pytest.mark.asyncio
async def test_named_life_diet_uses_routed_vision(monkeypatch):
    from core import life_records, llm_client
    cfg = {
        "life_records": {"enabled": True},
        "image_presets": {
            "presets": {
                "scene": {"kind": "vision", "enabled": True, "model": "diet-v", "base_url": "https://a.example/v1", "api_key": "k"},
                "receipts": {"kind": "ocr", "api_protocol": "chat_completions", "model": "ocr-mini", "base_url": "https://b.example/v1"},
            },
            "routes": {
                "chat_upload": "scene", "life_diet": "scene", "life_cart": "scene",
                "life_bill": "receipts", "phone_automation": "scene",
            },
        },
    }
    monkeypatch.setattr(life_records, "get_config", lambda: cfg)
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    settings = life_records.settings()
    assert settings["recognition_routes"]["diet"]["effective"]
    assert settings["recognition_routes"]["bill"]["effective"]
    captured = {}

    async def fake_chat(*args, **kwargs):
        captured.update(kwargs)
        return "一碗米饭"

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    monkeypatch.setattr(life_records, "get", lambda owner, rid: {"category": "diet"})
    result = await life_records.recognize({"owner": "o", "id": "r", "mime": "image/png", "data": b"x"})
    assert captured.get("vision_purpose") == "life_diet"
    assert captured.get("use_vision") is True
    assert "米饭" in result["recognition_description"]


def test_aliases_still_resolve_after_rename():
    cfg = {
        "image_presets": {
            "presets": {
                "scene": {"kind": "vision", "enabled": True, "model": "m", "base_url": "https://a.example/v1"},
                "receipts": {"kind": "ocr", "api_protocol": "chat_completions", "model": "ocr", "base_url": "https://b.example/v1"},
            },
            "routes": {"phone_automation": "scene"},
        }
    }
    assert resolve_connection("general", cfg)["model"] == "m"
    assert resolve_connection("ocr", cfg)["kind"] == "ocr"
    assert resolve_connection("phone", cfg)["name"] == "scene"
    assert resolve_connection("scene", cfg)["name"] == "scene"
    assert "phone_automation" in referencing_purposes("scene", cfg)


def _local_vision_cfg(purpose="chat_upload", **extra):
    return {
        "image_presets": {
            "presets": {"local": {"kind": "vision", "enabled": True, "model": "qwen-vl",
                                  "base_url": "http://127.0.0.1:8000/v1",
                                  "api_protocol": "chat_completions", **extra}},
            "routes": {purpose: "local"},
        },
    }


def test_any_loopback_vision_route_bypasses_the_proxy(monkeypatch):
    """`_local_only` 此前只给 video_call；全局代理会吞掉 127.0.0.1 的图片上传请求。"""
    from core import llm_client

    monkeypatch.setattr("core.config_loader.get_config", lambda: _local_vision_cfg())
    assert llm_client._resolve_vision_config("chat_upload")["_local_only"] is True
    # 远端连接不应被误标为直连。
    remote = _local_vision_cfg()
    remote["image_presets"]["presets"]["local"]["base_url"] = "https://vision.example/v1"
    monkeypatch.setattr("core.config_loader.get_config", lambda: remote)
    assert "_local_only" not in llm_client._resolve_vision_config("chat_upload")


@pytest.mark.asyncio
async def test_missing_vision_route_never_falls_back_to_the_text_model(monkeypatch):
    """静默回落会把图片块发给主聊天模型，看起来本地模型在用，实际从未打到它。"""
    from core import llm_client

    monkeypatch.setattr(llm_client, "_resolve_vision_config", lambda purpose=None: {})
    with pytest.raises(llm_client.VisionRouteError) as exc:
        await llm_client.chat([{"role": "user", "content": "x"}], use_vision=True,
                              vision_purpose="chat_upload")
    assert exc.value.reason == "vision_route_unavailable"


@pytest.mark.asyncio
async def test_vision_failure_reason_reaches_the_caller(monkeypatch):
    """非 video_call 分支此前把异常吞成空串，真实原因只剩在 api_call_log。"""
    from openai import APIConnectionError
    from core import llm_client, media_processor

    monkeypatch.setattr(llm_client, "_resolve_vision_config",
                        lambda purpose=None: {"enabled": True, "model": "m",
                                              "base_url": "http://127.0.0.1:8000/v1",
                                              "_local_only": True})

    class _Client:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    raise APIConnectionError(request=None)

    monkeypatch.setattr(llm_client, "_get_vision_client", lambda cfg=None: _Client)
    with pytest.raises(llm_client.VisionRouteError) as exc:
        await llm_client.chat([{"role": "user", "content": "x"}], use_vision=True,
                              vision_purpose="chat_upload")
    assert exc.value.reason == "connection_error"
    # 调用方把原因带进 vision_failed，而不是笼统「未返回结果」。
    assert issubclass(media_processor.MediaIngestError, Exception)


@pytest.mark.asyncio
async def test_unsupported_vision_protocol_is_rejected_not_mislogged(monkeypatch):
    """vision 分支此前忽略 preset 的 api_protocol，一律按 chat.completions 发出。"""
    from core import llm_client

    monkeypatch.setattr(llm_client, "_resolve_vision_config",
                        lambda purpose=None: {"enabled": True, "model": "m",
                                              "base_url": "https://a.example/v1",
                                              "api_protocol": "anthropic_messages"})
    monkeypatch.setattr(llm_client, "_get_vision_client", lambda cfg=None: object())
    with pytest.raises(llm_client.VisionRouteError) as exc:
        await llm_client.chat([{"role": "user", "content": "x"}], use_vision=True,
                              vision_purpose="chat_upload")
    assert "anthropic_messages" in exc.value.reason
