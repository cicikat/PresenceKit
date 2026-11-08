import io
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from admin.routers import image_generation_controls as controls, settings_llm, character
from core import image_gen


@pytest.fixture
def config(monkeypatch, tmp_path):
    cfg = {"image_gen": {"enabled": False, "presets": {
        "one": {"base_url": "https://original.invalid/v1", "model": "image-model", "api_key": "fixture-secret"},
        "two": {"base_url": "https://second.invalid/v1", "model": "other-model"},
    }}}
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(controls.config_loader, "get_config", lambda: cfg)
    monkeypatch.setattr(controls.config_loader, "get_config_path", lambda: path)
    monkeypatch.setattr(controls.config_loader, "reload_config", lambda: None)
    monkeypatch.setattr(controls, "read_config_file", lambda p: cfg)
    monkeypatch.setattr(controls, "write_config_file", lambda p, value: None)
    monkeypatch.setattr(settings_llm, "get_config", lambda: cfg)
    monkeypatch.setattr(settings_llm, "read_config_file", lambda p: cfg)
    monkeypatch.setattr(settings_llm, "write_config_file", lambda p, value: None)
    return cfg


@pytest.mark.asyncio
async def test_discovery_never_forwards_saved_key_to_changed_host(config, monkeypatch):
    remote = AsyncMock(return_value={"status": "ok", "models": ["image-model"]})
    monkeypatch.setattr("core.model_discovery.discover", remote)
    req = controls.Discovery(base_url="https://changed.invalid/v1", preset_name="one")
    assert (await controls.discover_image_models(req))["status"] == "key_required"
    remote.assert_not_called()
    req.base_url = "https://original.invalid/v1"
    await controls.discover_image_models(req)
    assert remote.call_args.args[1] == "fixture-secret"


@pytest.mark.asyncio
async def test_default_routing_and_key_redaction(config):
    await controls.set_image_default(controls.DefaultPreset(default_preset="two"))
    config["image_gen"]["enabled"] = True
    assert image_gen.resolve_preset(None)["name"] == "two"
    snapshot = await settings_llm.get_image_gen_presets(auth=None)
    assert "fixture-secret" not in str(snapshot)
    assert snapshot["presets"]["one"]["has_api_key"]
    with pytest.raises(HTTPException) as exc:
        await settings_llm.delete_image_gen_preset("two", auth=None)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_probe_works_when_global_off_and_does_not_create_artifacts(config, monkeypatch, sandbox):
    from core.tools import chat_artifacts
    raw = io.BytesIO()
    Image.new("RGB", (2, 2)).save(raw, format="PNG")
    remote = AsyncMock(return_value=raw.getvalue())
    monkeypatch.setattr("core.tools.image_gen.request_image", remote)
    chat_artifacts.begin_turn_collection()
    result = await controls.test_image_preset("one")
    assert result["ok"] and result["bytes"] == len(raw.getvalue())
    assert chat_artifacts.drain_turn_artifacts() == []
    remote.side_effect = RuntimeError("api_key=fixture-secret provider body")
    result = await controls.test_image_preset("one")
    assert not result["ok"] and "fixture-secret" not in str(result)


@pytest.mark.asyncio
async def test_probe_rejects_base64_nonimage(config, monkeypatch):
    monkeypatch.setattr("core.tools.image_gen.request_image", AsyncMock(return_value=b"not-an-image"))
    assert not (await controls.test_image_preset("one"))["ok"]


@pytest.mark.asyncio
async def test_changed_address_requires_fresh_key(config):
    with pytest.raises(HTTPException) as exc:
        await settings_llm.upsert_image_gen_preset("one", settings_llm.ImageGenPresetUpsert(base_url="https://changed.invalid/v1", api_key="  "), auth=None)
    assert exc.value.status_code == 422


def test_all_controls_require_admin():
    app = FastAPI()
    app.include_router(controls.router)
    client = TestClient(app)
    for path, body in [("/image-gen-presets/discover", {"base_url": "https://example.invalid/v1"}),
                       ("/image-gen-presets/presets/one/test", {})]:
        assert client.post(path, json=body).status_code == 401
    assert client.put("/image-gen-presets/default-preset", json={"default_preset": "one"}).status_code == 401


def test_character_list_binding_preserves_explicit_block():
    assert character.AssetBindingsUpdate(image_gen=[]).model_dump(exclude_unset=True) == {"image_gen": []}
    assert character._resolve_asset_bindings("fixture", {"image_gen": []})["image_gen"] == []

@pytest.mark.asyncio
async def test_user_constraints_are_selected_editable_and_can_be_disabled(config, monkeypatch):
    await controls.set_prompt_constraints(controls.PromptConstraints(selected="Painterly", custom="", presets={"Painterly": "expressive brushwork"}))
    assert image_gen.constrained_prompt("a tree").endswith("expressive brushwork")
    await controls.set_prompt_constraints(controls.PromptConstraints(selected="__custom__", custom="no lettering", presets={"Painterly": "changed"}))
    assert image_gen.constrained_prompt("a tree").endswith("no lettering")
    await controls.set_prompt_constraints(controls.PromptConstraints(selected="", custom="no lettering", presets={"Painterly": "changed"}))
    assert image_gen.constrained_prompt("a tree") == "a tree"


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [False, True])
async def test_actual_generation_and_edits_requests_include_user_constraint(config, monkeypatch, reference):
    from core.tools.image_gen import request_image
    import base64
    import httpx
    config["image_gen"]["prompt_constraints"] = {"selected": "Painterly", "presets": {"Painterly": "rich chiaroscuro"}}
    preset = image_gen.catalog()["presets"]["one"]
    preset["extra_params"] = {"prompt": "must not override constraint"}
    captured = []
    class Client:
        async def post(self, url, **kw):
            captured.append(kw)
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"image").decode()}]}, request=httpx.Request("POST", url))
        async def aclose(self): pass
    monkeypatch.setattr("core.tools.image_gen.make_model_http_client", lambda *a: Client())
    await request_image("a tree", {"name": "one", **preset}, image_bytes=(b"reference", "image/png", "test") if reference else None)
    prompt = captured[0]["data" if reference else "json"]["prompt"]
    assert prompt == "a tree\n\nUser visual constraints:\nrich chiaroscuro"
