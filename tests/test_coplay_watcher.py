"""
tests/test_coplay_watcher.py — Brief 39 验收：模拟进程列表的 watcher 单元测试
+ arm → 检测到游戏 → active → 关游戏 → closing 全链路。

Steam 注册表读取 / psutil 进程扫描 / 桌宠 spawn 全部打桩，不依赖真实机器环境。
"""

from unittest.mock import MagicMock, patch

import pytest

from core.coplay import session, watcher

UID = "u1"
CHAR = "yexuan"


@pytest.fixture(autouse=True)
def _reset_poll_throttle():
    """watcher._poll_ready 按 uid 记时间戳节流；每个测试前清空，避免跨测试互相饿死。"""
    watcher._last_poll_ts.clear()
    yield
    watcher._last_poll_ts.clear()


@pytest.fixture(autouse=True)
def _reset_last_probe():
    """watcher._last_probe 按 uid 记探测结果（Brief 54-A）；每个测试前后清空。"""
    watcher._last_probe.clear()
    yield
    watcher._last_probe.clear()


@pytest.fixture(autouse=True)
def _enable_coplay():
    """get_config() 是跨测试共享的进程级单例——直接改字典会漏到其他测试文件，
    这里保存/还原 'coplay' 小节，避免污染。"""
    import core.config_loader as cl

    cfg = cl.get_config()
    _original = cfg.get("coplay")
    cfg["coplay"] = {
        "enabled": True,
        "poll_interval": 0,  # 测试里不需要等
        "game_whitelist": [{"name": "Some Game", "process_name": "somegame.exe"}],
        "steam_library_paths": [],
        "pet_launch_cmd": "",
    }
    yield
    if _original is None:
        cfg.pop("coplay", None)
    else:
        cfg["coplay"] = _original


def _fake_process(name: str):
    p = MagicMock()
    p.info = {"name": name}
    return p


def test_detect_running_game_via_whitelist(sandbox):
    with patch.object(watcher, "_read_steam_running_appid", return_value=None), \
         patch("psutil.process_iter", return_value=[_fake_process("somegame.exe")]):
        found = watcher.detect_running_game()
    assert found == {"game_id": "somegame", "game_name": "Some Game"}


def test_detect_running_game_none_when_no_match(sandbox):
    with patch.object(watcher, "_read_steam_running_appid", return_value=None), \
         patch("psutil.process_iter", return_value=[_fake_process("notepad.exe")]):
        found = watcher.detect_running_game()
    assert found is None


def test_steam_appid_takes_priority_and_resolves_name(tmp_path, sandbox):
    lib = tmp_path / "steamlib"
    (lib / "steamapps").mkdir(parents=True)
    (lib / "steamapps" / "appmanifest_123.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"123"\n\t"name"\t\t"Cool Game"\n}\n', encoding="utf-8",
    )
    import core.config_loader as cl
    cl.get_config()["coplay"]["steam_library_paths"] = [str(lib)]

    with patch.object(watcher, "_read_steam_running_appid", return_value="123"):
        found = watcher.detect_running_game()
    assert found == {"game_id": "steam:123", "game_name": "Cool Game"}


def test_steam_appid_missing_manifest_falls_back_to_placeholder_name(sandbox):
    with patch.object(watcher, "_read_steam_running_appid", return_value="999"):
        found = watcher.detect_running_game()
    assert found == {"game_id": "steam:999", "game_name": "App 999"}


@pytest.mark.asyncio
async def test_full_lifecycle_arm_detect_active_exit_closing(sandbox):
    session.arm(UID, char_id=CHAR)

    with patch.object(watcher, "_read_steam_running_appid", return_value=None), \
         patch("psutil.process_iter", return_value=[_fake_process("somegame.exe")]), \
         patch.object(watcher, "_maybe_launch_pet"):
        await watcher.tick(UID, char_id=CHAR)

    state = session.read_state(UID, char_id=CHAR)
    assert state["status"] == session.CoplayStatus.ACTIVE.value
    assert state["game_id"] == "somegame"

    # 游戏进程消失 → closing
    with patch.object(watcher, "_read_steam_running_appid", return_value=None), \
         patch("psutil.process_iter", return_value=[]), \
         patch.object(watcher, "_maybe_launch_pet"):
        await watcher.tick(UID, char_id=CHAR)

    state = session.read_state(UID, char_id=CHAR)
    assert state["status"] == session.CoplayStatus.CLOSING.value


@pytest.mark.asyncio
async def test_tick_noop_when_coplay_disabled(sandbox):
    import core.config_loader as cl
    cl.get_config()["coplay"]["enabled"] = False
    session.arm(UID, char_id=CHAR)

    with patch("psutil.process_iter", return_value=[_fake_process("somegame.exe")]):
        await watcher.tick(UID, char_id=CHAR)

    assert session.read_state(UID, char_id=CHAR)["status"] == session.CoplayStatus.ARMED.value


@pytest.mark.asyncio
async def test_tick_noop_when_off(sandbox):
    with patch("psutil.process_iter", return_value=[_fake_process("somegame.exe")]):
        await watcher.tick(UID, char_id=CHAR)
    assert session.read_state(UID, char_id=CHAR)["status"] == session.CoplayStatus.OFF.value


@pytest.mark.asyncio
async def test_tick_default_enabled_true_when_key_absent(sandbox):
    """coplay.enabled 缺省即"默认允许"（Brief 54-A 消灭双开关，不再是默认关闭）。"""
    import core.config_loader as cl

    cfg = cl.get_config()
    cfg["coplay"].pop("enabled", None)
    session.arm(UID, char_id=CHAR)

    with patch.object(watcher, "_read_steam_running_appid", return_value=None), \
         patch("psutil.process_iter", return_value=[_fake_process("somegame.exe")]), \
         patch.object(watcher, "_maybe_launch_pet"):
        await watcher.tick(UID, char_id=CHAR)

    assert session.read_state(UID, char_id=CHAR)["status"] == session.CoplayStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_tick_records_last_probe_on_armed_whitelist_match(sandbox):
    session.arm(UID, char_id=CHAR)

    with patch.object(watcher, "_read_steam_running_appid", return_value=None), \
         patch("psutil.process_iter", return_value=[_fake_process("somegame.exe")]), \
         patch.object(watcher, "_maybe_launch_pet"):
        await watcher.tick(UID, char_id=CHAR)

    probe = watcher.get_last_probe(UID)
    assert probe is not None
    assert probe["running_app_id"] is None
    assert probe["matched_process"] == "somegame"
    assert isinstance(probe["ts"], float)


@pytest.mark.asyncio
async def test_tick_records_last_probe_on_armed_steam_signal(sandbox):
    session.arm(UID, char_id=CHAR)

    with patch.object(watcher, "_read_steam_running_appid", return_value="123"), \
         patch.object(watcher, "_maybe_launch_pet"):
        await watcher.tick(UID, char_id=CHAR)

    probe = watcher.get_last_probe(UID)
    assert probe is not None
    assert probe["running_app_id"] == "123"
    assert probe["matched_process"] is None


@pytest.mark.asyncio
async def test_tick_records_last_probe_when_nothing_detected(sandbox):
    session.arm(UID, char_id=CHAR)

    with patch.object(watcher, "_read_steam_running_appid", return_value=None), \
         patch("psutil.process_iter", return_value=[_fake_process("notepad.exe")]):
        await watcher.tick(UID, char_id=CHAR)

    probe = watcher.get_last_probe(UID)
    assert probe is not None
    assert probe["running_app_id"] is None
    assert probe["matched_process"] is None


@pytest.mark.asyncio
async def test_tick_does_not_record_probe_when_disabled(sandbox):
    import core.config_loader as cl
    cl.get_config()["coplay"]["enabled"] = False
    session.arm(UID, char_id=CHAR)

    with patch("psutil.process_iter", return_value=[_fake_process("somegame.exe")]):
        await watcher.tick(UID, char_id=CHAR)

    assert watcher.get_last_probe(UID) is None


def test_maybe_launch_pet_spawns_when_disconnected_and_configured():
    import core.config_loader as cl
    cl.get_config()["coplay"]["pet_launch_cmd"] = "echo hi"

    fake_desktop_ws = MagicMock()
    fake_desktop_ws.is_connected.return_value = False
    with patch.dict("sys.modules", {"channels.desktop_ws": fake_desktop_ws}), \
         patch("subprocess.Popen") as mock_popen:
        watcher._maybe_launch_pet()
    mock_popen.assert_called_once()


def test_maybe_launch_pet_skips_when_connected():
    fake_desktop_ws = MagicMock()
    fake_desktop_ws.is_connected.return_value = True
    with patch.dict("sys.modules", {"channels.desktop_ws": fake_desktop_ws}), \
         patch("subprocess.Popen") as mock_popen:
        watcher._maybe_launch_pet()
    mock_popen.assert_not_called()


def test_maybe_launch_pet_fail_open_on_spawn_error():
    import core.config_loader as cl
    cl.get_config()["coplay"]["pet_launch_cmd"] = "echo hi"

    fake_desktop_ws = MagicMock()
    fake_desktop_ws.is_connected.return_value = False
    with patch.dict("sys.modules", {"channels.desktop_ws": fake_desktop_ws}), \
         patch("subprocess.Popen", side_effect=OSError("boom")):
        watcher._maybe_launch_pet()  # must not raise
