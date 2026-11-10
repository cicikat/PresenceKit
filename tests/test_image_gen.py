import base64
import json
from types import SimpleNamespace

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
                "provider_kind": "openai_compatible",
                "base_url": "https://img.example/v1",
                "api_key": "k",
                "model": "flux-pro",
            },
            "hidden": {"description": "内部用", "show_to_llm": False, "base_url": "https://h", "model": "m2"},
            "broken": {"description": "缺地址", "show_to_llm": True, "model": ""},
        },
    }
}


def test_catalog_hides_keys_and_filters():
    cat = image_gen.catalog(CFG)
    assert cat["enabled"] is True
    assert set(cat["presets"]) == {"flux-pro", "hidden", "broken"}
    rows = image_gen.exposed_preset_names(config=CFG)
    assert [r["name"] for r in rows] == ["flux-pro"]  # hidden by flag, broken by readiness
    note = image_gen.discovery_note(config=CFG)
    assert "flux-pro" in note and "氛围感强" in note and "hidden" not in note


def test_resolve_default_and_unknown():
    r = image_gen.resolve_preset(None, config=CFG)
    assert r["name"] == "flux-pro"
    with pytest.raises(ValueError, match="未知的图像生成预设"):
        image_gen.resolve_preset("nope", config=CFG)
    with pytest.raises(ValueError, match="图像生成未启用"):
        image_gen.resolve_preset(None, config={"image_gen": {"enabled": False, "presets": {}}})


def test_tools_disabled_by_default(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", lambda: {})
    assert image_gen.tools_enabled() is False
    assert image_gen.exposed_preset_names() == []


def test_schema_lists_only_visible_connection_names(monkeypatch):
    from core.tool_dispatcher import get_tools_schema

    monkeypatch.setattr("core.config_loader.get_config", lambda: CFG)
    monkeypatch.setattr(image_gen, "_char_allowlist", lambda _: {"flux-pro", "hidden"})
    schemas = get_tools_schema(["media"], char_id="fixture")
    image = next(row["function"] for row in schemas if row["function"]["name"] == "generate_image")
    assert image["parameters"]["properties"]["preset"]["enum"] == ["flux-pro"]
    assert "preset" not in image["parameters"]["required"]

    monkeypatch.setattr(image_gen, "_char_allowlist", lambda _: set())
    schemas = get_tools_schema(["media"], char_id="fixture")
    assert all(row["function"]["name"] != "generate_image" for row in schemas)


def test_store_image_roundtrip_and_artifact_guards(sandbox):
    chat_artifacts.begin_turn_collection()
    payload = chat_artifacts.store_generated_image(b"\x89PNG" + b"x" * 32, ext=".png", user_id="u1", char_id="c1")
    assert payload["kind"] == "image"
    assert payload["mime"] == "image/png"
    assert payload["previewable"] is True
    assert chat_artifacts.drain_turn_artifacts()[0]["kind"] == "image"
    record = chat_artifacts.get_artifact_record(payload["id"], uid="u1", char_id="c1")
    assert record["kind"] == "image"
    # Text tools refuse binary artifacts
    with pytest.raises(chat_artifacts.ArtifactError):
        chat_artifacts.read_artifact(payload["id"], user_id="u1", char_id="c1")
    with pytest.raises(chat_artifacts.ArtifactError):
        chat_artifacts.update_artifact(payload["id"], "x", user_id="u1", char_id="c1")
    with pytest.raises(chat_artifacts.ArtifactError):
        chat_artifacts.read_artifact_text(record)


@pytest.mark.asyncio
async def test_generate_image_happy(monkeypatch, sandbox):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"\x89PNGdata").decode()}]}

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            assert url.endswith("/images/generations")
            assert json["model"] == "flux-pro"
            return FakeResp()

        async def aclose(self):
            pass

    monkeypatch.setattr("core.config_loader.get_config", lambda: CFG)
    monkeypatch.setattr("core.tools.image_gen.make_model_http_client", lambda *a, **k: FakeClient())
    chat_artifacts.begin_turn_collection()
    result = await generate_image("a cat", user_id="u1", char_id="c1")
    raw = json.loads(result.raw_data)
    assert raw["status"] == "generated"
    assert raw["mime"] == "image/png"
    assert chat_artifacts.drain_turn_artifacts()[0]["kind"] == "image"


@pytest.mark.asyncio
async def test_generate_image_requires_prompt():
    with pytest.raises(ValueError, match="prompt"):
        await generate_image("  ", user_id="u1", char_id="c1")
