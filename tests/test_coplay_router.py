"""
tests/test_coplay_router.py — Brief 54-A：coplay 启用链路修缮的 router 测试。

覆盖：
  ① GET /coplay/state 响应新增 enabled 字段（读 config.coplay.enabled 值）
  ② GET /coplay/state 响应新增 last_probe 调试字段：未探测过时为 None，
     watcher 记录过一次探测后能读到 {running_app_id, matched_process, ts}
  ③ POST /coplay/arm 在 coplay.enabled=false 时返回 409，且不落 armed 状态
  ④ POST /coplay/arm 在 coplay.enabled 缺省/true 时照常成功 arm
"""

from unittest.mock import patch

import pytest

from admin.routers import coplay as coplay_router
from core.coplay import session, watcher

UID = "u_router_test"
CHAR = "yexuan"


@pytest.fixture(autouse=True)
def _fixed_owner_and_char():
    """把 _owner_uid / _active_char_id 钉死为测试常量，不依赖真实 config.yaml。"""
    with patch.object(coplay_router, "_owner_uid", return_value=UID), \
         patch.object(coplay_router, "_active_char_id", return_value=CHAR):
        yield


@pytest.fixture(autouse=True)
def _reset_last_probe():
    watcher._last_probe.clear()
    yield
    watcher._last_probe.clear()


@pytest.fixture
def _coplay_cfg_section():
    """保存/还原 config['coplay']，避免污染其他测试文件（跨测试共享单例）。"""
    import core.config_loader as cl

    cfg = cl.get_config()
    original = cfg.get("coplay")
    cfg["coplay"] = {
        "enabled": True,
        "poll_interval": 0,
        "game_whitelist": [],
        "steam_library_paths": [],
        "pet_launch_cmd": "",
    }
    yield cfg["coplay"]
    if original is None:
        cfg.pop("coplay", None)
    else:
        cfg["coplay"] = original


@pytest.mark.asyncio
async def test_state_get_reports_enabled_true(sandbox, _coplay_cfg_section):
    result = await coplay_router.coplay_state_get(_auth="dummy")
    assert result["enabled"] is True


@pytest.mark.asyncio
async def test_state_get_reports_enabled_false(sandbox, _coplay_cfg_section):
    _coplay_cfg_section["enabled"] = False
    result = await coplay_router.coplay_state_get(_auth="dummy")
    assert result["enabled"] is False


@pytest.mark.asyncio
async def test_state_get_enabled_defaults_true_when_key_absent(sandbox):
    """coplay 配置块整体缺失时，enabled 语义仍是"默认允许"（Brief 54-A 拍板）。"""
    import core.config_loader as cl

    cfg = cl.get_config()
    original = cfg.pop("coplay", None)
    try:
        result = await coplay_router.coplay_state_get(_auth="dummy")
        assert result["enabled"] is True
    finally:
        if original is not None:
            cfg["coplay"] = original


@pytest.mark.asyncio
async def test_state_get_last_probe_none_before_any_tick(sandbox, _coplay_cfg_section):
    result = await coplay_router.coplay_state_get(_auth="dummy")
    assert result["last_probe"] is None


@pytest.mark.asyncio
async def test_state_get_last_probe_reflects_watcher_tick(sandbox, _coplay_cfg_section):
    session.arm(UID, char_id=CHAR)

    with patch.object(watcher, "_read_steam_running_appid", return_value="42"), \
         patch.object(watcher, "_maybe_launch_pet"):
        await watcher.tick(UID, char_id=CHAR)

    result = await coplay_router.coplay_state_get(_auth="dummy")
    assert result["last_probe"] is not None
    assert result["last_probe"]["running_app_id"] == "42"
    assert result["last_probe"]["matched_process"] is None
    assert isinstance(result["last_probe"]["ts"], float)


@pytest.mark.asyncio
async def test_arm_returns_409_when_deployment_disabled(sandbox, _coplay_cfg_section):
    _coplay_cfg_section["enabled"] = False

    with pytest.raises(Exception) as exc_info:
        await coplay_router.coplay_arm(_auth="dummy")

    # FastAPI HTTPException
    assert getattr(exc_info.value, "status_code", None) == 409
    assert "禁用" in str(getattr(exc_info.value, "detail", ""))
    assert session.read_state(UID, char_id=CHAR)["status"] == session.CoplayStatus.OFF.value


@pytest.mark.asyncio
async def test_arm_succeeds_when_enabled_true(sandbox, _coplay_cfg_section):
    result = await coplay_router.coplay_arm(_auth="dummy")
    assert result == {"ok": True, "status": session.CoplayStatus.ARMED.value}
    assert session.read_state(UID, char_id=CHAR)["status"] == session.CoplayStatus.ARMED.value


@pytest.mark.asyncio
async def test_arm_succeeds_when_coplay_key_absent(sandbox):
    """coplay 配置块缺失时按默认允许处理，仍能 arm 成功。"""
    import core.config_loader as cl

    cfg = cl.get_config()
    original = cfg.pop("coplay", None)
    try:
        result = await coplay_router.coplay_arm(_auth="dummy")
        assert result["ok"] is True
    finally:
        if original is not None:
            cfg["coplay"] = original
