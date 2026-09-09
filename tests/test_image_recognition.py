"""OCR protocol, routing, configuration, and neighboring vision regressions."""
import io
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from PIL import Image

from core import image_recognition as mod


def test_endpoint_is_exact_and_protocol_is_explicit():
    cfg = mod.settings({})
    cfg["endpoint_url"] = "https://ocr.example/custom/layout_parsing?version=2"
    assert mod.endpoint(cfg) == cfg["endpoint_url"]
    cfg.update(api_protocol="chat_completions", base_url="https://ocr.example/v1/")
    assert mod.endpoint(cfg) == "https://ocr.example/v1/chat/completions"
    cfg["api_protocol"] = "unknown"
    with pytest.raises(ValueError):
        mod.endpoint(cfg)


@pytest.mark.parametrize("url", ["file:///tmp/image", "https://user:secret@example.com/api", "https://example.com/api#fragment", ""])
def test_invalid_endpoint_rejected(url):
    with pytest.raises(ValueError):
        mod.endpoint({"api_protocol": "glm_layout_parsing", "endpoint_url": url})


def fake_transport(monkeypatch, body, status=200):
    response = MagicMock(status=status)
    response.json = AsyncMock(return_value=body)
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    session = MagicMock()
    session.post.return_value = request
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    monkeypatch.setattr(mod.aiohttp, "ClientSession", MagicMock(return_value=context))
    monkeypatch.setattr(mod, "get_aiohttp_proxy", lambda: None)
    return session


@pytest.mark.asyncio
async def test_glm_posts_file_without_chat_payload_and_records_safe_audit(monkeypatch):
    from core import api_call_log
    audit = MagicMock()
    monkeypatch.setattr(api_call_log, "append", audit)
    session = fake_transport(monkeypatch, {"md_results": "# Document\ntext"})
    cfg = mod.settings({"image_recognition": {"api_key": "fixture-secret"}})
    assert await mod.recognize_ocr("data:image/png;base64,fixture", cfg) == "# Document\ntext"
    args, kwargs = session.post.call_args
    assert args == (mod.GLM_ENDPOINT,)
    assert kwargs["json"] == {"model": "glm-ocr", "file": "data:image/png;base64,fixture"}
    assert kwargs["headers"] == {"Authorization": "Bearer fixture-secret"}
    assert kwargs["allow_redirects"] is False
    assert audit.call_args.kwargs["ok"] is True
    assert "fixture-secret" not in str(audit.call_args)
    assert "Document" not in str(audit.call_args)


@pytest.mark.asyncio
async def test_ocr_chat_protocol_and_empty_text(monkeypatch):
    session = fake_transport(monkeypatch, {"choices": [{"message": {"content": "recognized"}}]})
    cfg = mod.settings({"image_recognition": {"api_protocol": "chat_completions", "base_url": "https://local.example/v1"}})
    assert await mod.recognize_ocr("image", cfg) == "recognized"
    assert session.post.call_args.args[0].endswith("/v1/chat/completions")
    assert "messages" in session.post.call_args.kwargs["json"]
    fake_transport(monkeypatch, {"md_results": ""})
    assert await mod.recognize_ocr("image", mod.settings({"image_recognition": {"api_key": "fixture"}})) == "[OCR: no text detected]"


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status", [({"error": {"message": "private"}}, 200), ({}, 200), ({"md_results": "ignored"}, 401)])
async def test_failures_do_not_become_text_or_leak_provider_body(monkeypatch, body, status):
    fake_transport(monkeypatch, body, status)
    with pytest.raises(ValueError, match="OCR request failed") as error:
        await mod.recognize_ocr("image", mod.settings({"image_recognition": {"api_key": "fixture"}}))
    assert "private" not in str(error.value)


def test_glm_missing_key_is_not_ready():
    assert mod.view({"image_recognition": {"mode": "ocr"}})["effective"] is False


def png(color):
    out = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_upload_multimage_cache_model_switch_and_reread(sandbox, monkeypatch):
    from core import config_loader, llm_client, media_processor
    cfg = {"image_recognition": {"mode": "ocr"}}
    monkeypatch.setattr(config_loader, "get_config", lambda: cfg)
    monkeypatch.setattr(mod, "get_config", lambda: cfg)
    monkeypatch.setattr(media_processor, "_build_vision_prompt", lambda: "describe")
    ocr = AsyncMock(side_effect=["first / literal text", "second", "new first", "new second", "reread"])
    chat = AsyncMock(return_value="scene")
    monkeypatch.setattr(mod, "recognize_ocr", ocr)
    monkeypatch.setattr(llm_client, "chat", chat)
    items = [(png("red"), "one.png"), (png("blue"), "two.png")]
    assert await media_processor.ingest_image_bytes(items) == ["first / literal text", "second"]
    assert await media_processor.ingest_image_bytes(items) == ["first / literal text", "second"]
    assert ocr.await_count == 2
    cfg["image_recognition"]["model"] = "another-ocr"
    assert await media_processor.ingest_image_bytes(items) == ["new first", "new second"]
    assert await media_processor.reread_cached_image(media_processor._hash_bytes(items[0][0])) == "reread"
    chat.assert_not_awaited()
    cfg["image_recognition"]["mode"] = "vision"
    assert await media_processor.ingest_image_bytes(items[:1]) == ["scene"]
    assert chat.await_args.kwargs["use_vision"] is True


@pytest.mark.asyncio
async def test_admin_save_read_masking_and_phone_inheritance(tmp_path, monkeypatch):
    from admin.routers import settings_llm as admin
    from core import config_loader, llm_client
    from core.phone_control.vision_client import get_phone_control_vision_config
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"vision": {"model": "screen-model", "api_key": "vision-secret", "enabled": True,
                                              "base_url": "https://vision.example/v1"}}), encoding="utf-8")
    read = lambda: yaml.safe_load(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(admin, "CONFIG_FILE", path)
    monkeypatch.setattr(admin, "get_config", read)
    monkeypatch.setattr(config_loader, "get_config", read)
    monkeypatch.setattr(config_loader, "reload_config", read)
    monkeypatch.setattr(llm_client, "reload_client", AsyncMock())
    result = await admin.update_image_recognition(admin.ImageRecognitionUpdate(mode="ocr", api_key="ocr-secret"))
    assert result["effective"] is True
    assert "ocr-secret" not in str(result)
    await admin.update_image_recognition(admin.ImageRecognitionUpdate(api_key=" "))
    assert read()["image_recognition"]["api_key"] == "ocr-secret"
    assert get_phone_control_vision_config()["model"] == "screen-model"
    assert get_phone_control_vision_config()["api_key"] == "vision-secret"
    result = await admin.update_vision_params(admin.VisionParamsUpdate(api_key="", base_url="https://changed.example/v1"))
    assert read()["vision"]["api_key"] == "vision-secret"
    assert "vision-secret" not in str(result)
    assert result["vision"]["base_url"] == "https://changed.example/v1"


@pytest.mark.asyncio
async def test_invalid_admin_config_does_not_write(tmp_path, monkeypatch):
    from admin.routers import settings_llm as admin
    from fastapi import HTTPException
    path = tmp_path / "config.yaml"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(admin, "CONFIG_FILE", path)
    with pytest.raises(HTTPException) as error:
        await admin.update_image_recognition(admin.ImageRecognitionUpdate(endpoint_url="file:///bad"))
    assert error.value.status_code == 422
    assert path.read_text(encoding="utf-8") == "{}"
