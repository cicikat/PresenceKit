from tests.fixtures.public_assets import TEST_CHAR_ID
"""/phone_control/status 和 /phone_control/debug/start 两个只读诊断 + 调试端点。

- status：手机端能力页用来判断"角色是否已授权 phone_control 工具"+"视觉模型是否已配置"，
  不应该依赖真实 yexuan.json/config.yaml 的具体内容——这里全部 monkeypatch 隔离。
- debug/start：跳过 LLM 判断和 chat 二次确认的测试入口，但必须走跟真实调用路径完全相同的
  danger-mode 门禁（tool_dispatcher._current_mode），不能因为是"调试端点"就放宽。
"""
import pytest

from admin.routers import phone_control as pc


class _FakeChar:
    def __init__(self, presence_ext):
        self.presence_ext = presence_ext


def _patch_default_exposure(monkeypatch):
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {
            "tool_exposure": {
                "path_a": {"categories": ["info", "desktop"]},
                "path_c": {"categories": ["info", "desktop", "memory"]},
            },
        },
    )


@pytest.mark.asyncio
async def test_status_reports_enabled_when_category_present(monkeypatch):
    _patch_default_exposure(monkeypatch)
    monkeypatch.setattr(
        "admin.routers.character._active_character_id", lambda: TEST_CHAR_ID,
    )
    monkeypatch.setattr(
        "core.character_loader.load",
        lambda char_id: _FakeChar({"tool_categories_path_c": ["info", "phone_control"]}),
    )
    monkeypatch.setattr(
        "core.phone_control.vision_client.phone_vision_chain",
        lambda: [{"config": {"base_url": "https://example.com", "model": "glm-4.6v", "api_key": "k"}}],
    )

    result = await pc.phone_control_status(auth=None)
    assert result == {
        "tool_enabled": True,
        "path_a_enabled": False,
        "path_c_enabled": True,
        "vision_configured": True,
        "char_id": TEST_CHAR_ID,
    }


@pytest.mark.asyncio
async def test_status_reports_disabled_when_category_missing(monkeypatch):
    _patch_default_exposure(monkeypatch)
    monkeypatch.setattr(
        "admin.routers.character._active_character_id", lambda: TEST_CHAR_ID,
    )
    monkeypatch.setattr(
        "core.character_loader.load",
        lambda char_id: _FakeChar({"model_routing": "default"}),  # 没有 tool_categories
    )
    monkeypatch.setattr(
        "core.phone_control.vision_client.phone_vision_chain",
        lambda: [{"config": {"base_url": "", "model": "", "api_key": ""}}],
    )

    result = await pc.phone_control_status(auth=None)
    assert result == {
        "tool_enabled": False,
        "path_a_enabled": False,
        "path_c_enabled": False,
        "vision_configured": False,
        "char_id": TEST_CHAR_ID,
    }


@pytest.mark.asyncio
async def test_status_handles_character_load_failure(monkeypatch):
    _patch_default_exposure(monkeypatch)
    monkeypatch.setattr(
        "admin.routers.character._active_character_id", lambda: TEST_CHAR_ID,
    )

    def _raise(char_id):
        raise FileNotFoundError("no such character")

    monkeypatch.setattr("core.character_loader.load", _raise)
    monkeypatch.setattr(
        "core.phone_control.vision_client.phone_vision_chain",
        lambda: [{"config": {"base_url": "https://example.com", "model": "glm-4.6v"}}],
    )

    result = await pc.phone_control_status(auth=None)
    assert result["tool_enabled"] is False
    assert result["path_a_enabled"] is False
    assert result["path_c_enabled"] is False
    assert result["vision_configured"] is True


@pytest.mark.asyncio
async def test_status_uses_path_c_global_exposure_when_character_has_no_legacy_field(monkeypatch):
    monkeypatch.setattr("admin.routers.character._active_character_id", lambda: "character")
    monkeypatch.setattr("core.character_loader.load", lambda _char_id: _FakeChar({}))
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"tool_exposure": {"path_c": {"categories": ["phone_control"]}}},
    )
    monkeypatch.setattr(
        "core.phone_control.vision_client.phone_vision_chain",
        lambda: [{"config": {"base_url": "https://example.com", "model": "vision"}}],
    )

    result = await pc.phone_control_status(auth=None)

    assert result["tool_enabled"] is True
    assert result["path_a_enabled"] is False
    assert result["path_c_enabled"] is True


@pytest.mark.asyncio
async def test_debug_start_blocked_in_safe_mode(monkeypatch):
    import core.tool_dispatcher as td

    monkeypatch.setattr(td, "_current_mode", lambda: "safe")

    result = await pc.phone_control_debug_start(body={"task": "帮我点杯奶茶"}, auth=None)
    assert result["ok"] is False
    assert "安全模式" in result["message"]


@pytest.mark.asyncio
async def test_debug_start_rejects_empty_task(monkeypatch):
    import core.tool_dispatcher as td
    from fastapi import HTTPException

    monkeypatch.setattr(td, "_current_mode", lambda: "danger")

    with pytest.raises(HTTPException) as excinfo:
        await pc.phone_control_debug_start(body={"task": "  "}, auth=None)
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_debug_start_calls_real_wrapper_in_danger_mode(monkeypatch):
    import core.tool_dispatcher as td

    monkeypatch.setattr(td, "_current_mode", lambda: "danger")
    monkeypatch.setattr(
        "admin.routers.character._active_character_id", lambda: TEST_CHAR_ID,
    )

    captured = {}

    async def _fake_wrapper(task, *, user_id, char_id):
        captured["task"] = task
        captured["user_id"] = user_id
        captured["char_id"] = char_id
        return "已经把任务派给手机了"

    monkeypatch.setattr(td, "_phone_control_start_wrapper", _fake_wrapper)

    result = await pc.phone_control_debug_start(
        body={"task": "帮我点杯奶茶", "user_id": "u1"}, auth=None,
    )
    assert result == {"ok": True, "message": "已经把任务派给手机了"}
    assert captured == {"task": "帮我点杯奶茶", "user_id": "u1", "char_id": TEST_CHAR_ID}


@pytest.mark.asyncio
async def test_debug_start_falls_back_to_owner_id_when_missing(monkeypatch):
    import core.tool_dispatcher as td

    monkeypatch.setattr(td, "_current_mode", lambda: "danger")
    monkeypatch.setattr(
        "admin.routers.character._active_character_id", lambda: TEST_CHAR_ID,
    )
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "123456789"}},
    )

    captured = {}

    async def _fake_wrapper(task, *, user_id, char_id):
        captured["user_id"] = user_id
        return "ok"

    monkeypatch.setattr(td, "_phone_control_start_wrapper", _fake_wrapper)

    await pc.phone_control_debug_start(body={"task": "帮我点杯奶茶"}, auth=None)
    assert captured["user_id"] == "123456789"
