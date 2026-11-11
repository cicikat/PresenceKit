import base64
import json

import pytest

from core import image_gen
from core.tools import chat_artifacts
from core.tools.image_gen import generate_image

CFG = {
    "image_gen": {
        "enabled": True,
        "presets": {
            "flux-pro": {
                "description": "氛围感强",
                "show_to_llm": True,
                "supports_edits": True,
                "provider_kind": "openai_compatible",
                "base_url": "https://img.example/v1",
                "api_key": "k",
                "model": "flux-pro",
            },
            "no-edits": {
                "description": "不支持参考图",
                "show_to_llm": True,
                "supports_edits": False,
                "base_url": "https://img.example/v1",
                "api_key": "k",
                "model": "no-edits",
            },
        },
    }
}


class FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"data": [{"b64_json": base64.b64encode(b"\x89PNGout").decode()}]}


class FakeClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, headers=None, json=None, files=None, data=None):
        self.calls.append({"url": url, "json": json, "files": files, "data": data})
        return FakeResp()

    async def aclose(self):
        pass


def test_store_has_sha256(sandbox):
    chat_artifacts.begin_turn_collection()
    payload = chat_artifacts.store_generated_image(b"\x89PNG" + b"y" * 16, ext=".png", user_id="u1", char_id="c1")
    record = chat_artifacts.get_artifact_record(payload["id"], uid="u1", char_id="c1")
    assert len(record["sha256"]) == 64
    assert record["kind"] == "image"


def test_reference_mutual_exclusion():
    with pytest.raises(ValueError, match="只能传其一"):
        image_gen.image_reference_bytes(user_id="u1", char_id="c1", sha256="a" * 64, artifact_id="b" * 32)
    with pytest.raises(ValueError, match="只能传其一"):
        image_gen.image_reference_bytes(user_id="u1", char_id="c1")


def test_reference_from_artifact(sandbox):
    chat_artifacts.begin_turn_collection()
    payload = chat_artifacts.store_generated_image(b"\x89PNGref", ext=".png", user_id="u1", char_id="c1")
    data, mime, source = image_gen.image_reference_bytes(user_id="u1", char_id="c1", artifact_id=payload["id"])
    assert data == b"\x89PNGref"
    assert mime == "image/png"
    assert source == "artifact"


def test_reference_from_sha256(monkeypatch):
    from core.chat_media import ChatMediaRecord

    record = ChatMediaRecord(
        sha256="a" * 64, filename="x.png", mime="image/png",
        path=type("P", (), {"read_bytes": lambda self: b"\x89PNGsha"})(),
        source="inbox", size=8, uid="u1", char_id="c1",
    )
    monkeypatch.setattr("core.chat_media.resolve_chat_media", lambda sha256, uid, char_id: record)
    data, mime, source = image_gen.image_reference_bytes(user_id="u1", char_id="c1", sha256="a" * 64)
    assert data == b"\x89PNGsha"
    assert source == "inbox"


def test_discovery_marks_no_edits():
    note = image_gen.discovery_note(config=CFG)
    assert "不支持参考图" in note


@pytest.mark.asyncio
async def test_generate_with_reference_uses_edits(monkeypatch, sandbox):
    monkeypatch.setattr("core.config_loader.get_config", lambda: CFG)
    chat_artifacts.begin_turn_collection()
    payload = chat_artifacts.store_generated_image(b"\x89PNGref", ext=".png", user_id="u1", char_id="c1")
    fake = FakeClient()
    monkeypatch.setattr("core.tools.image_gen.make_model_http_client", lambda *a, **k: fake)
    result = await generate_image("改成夜色", reference_artifact_id=payload["id"], user_id="u1", char_id="c1")
    assert json.loads(result.raw_data)["status"] == "generated"
    call = fake.calls[0]
    assert call["url"].endswith("/images/edits")
    assert call["files"]["image"][1] == b"\x89PNGref"
    assert call["data"]["prompt"] == "改成夜色"


@pytest.mark.asyncio
async def test_generate_reference_rejected_when_preset_no_edits(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", lambda: CFG)
    with pytest.raises(ValueError, match="不支持参考图"):
        await generate_image("x", preset="no-edits", reference_sha256="a" * 64, user_id="u1", char_id="c1")


@pytest.mark.asyncio
async def test_fixed_character_reference_is_opt_in_and_scoped(monkeypatch, sandbox):
    from io import BytesIO

    from PIL import Image
    from core import character_image_reference as reference

    monkeypatch.setattr("core.config_loader.get_config", lambda: CFG)
    fake = FakeClient()
    monkeypatch.setattr("core.tools.image_gen.make_model_http_client", lambda *a, **k: fake)
    source = BytesIO()
    Image.new("RGB", (16, 16), "red").save(source, "PNG")
    reference.save("u1", "c1", source.getvalue())
    chat_artifacts.begin_turn_collection()
    await generate_image("landscape", user_id="u1", char_id="c1")
    await generate_image("portrait", use_character_reference=True, user_id="u1", char_id="c1")
    assert fake.calls[0]["url"].endswith("/images/generations")
    assert fake.calls[1]["url"].endswith("/images/edits")
    assert fake.calls[1]["files"]["image"][1] == reference.read("u1", "c1")
    for user, char in [("u2", "c1"), ("u1", "c2")]:
        with pytest.raises(reference.ReferenceError, match="还没有固定"):
            await generate_image("portrait", use_character_reference=True, user_id=user, char_id=char)
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_pin_accepts_only_authorized_current_attachment(monkeypatch, sandbox, tmp_path):
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    from core import character_image_reference as reference
    from core.tool_dispatcher import execute_structured

    source = BytesIO()
    Image.new("RGB", (16, 16), "red").save(source, "PNG")
    upload = tmp_path / "test-upload.png"
    upload.write_bytes(source.getvalue())
    digest = "a" * 64
    monkeypatch.setattr("core.chat_media.resolve_chat_media",
                        lambda *a, **kw: SimpleNamespace(path=upload, size=upload.stat().st_size))
    args = ("pin_character_image_reference", {"sha256": digest}, "u1", "u1", False, None)
    denied = await execute_structured(*args, origin="assistant_loop", char_id="c1")
    assert denied.status == "tool_failed"
    assert not reference.exists("u1", "c1")
    allowed = await execute_structured(
        *args, origin="assistant_loop", char_id="c1", pin_reference_hashes=frozenset({digest}),
    )
    assert allowed.status == "tool_executed"
    upload.unlink()
    assert reference.exists("u1", "c1")
    assert reference.pin_requested("把这张图固定为你的形象参考图")
    assert not reference.pin_requested("不要固定为参考图")
