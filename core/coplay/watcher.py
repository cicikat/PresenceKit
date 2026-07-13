"""
core/coplay/watcher.py — Brief 39: 游戏检测 + 自动拉起桌宠。

armed 状态下轮询：
  1. Steam 当前游戏：注册表 HKCU\\Software\\Valve\\Steam\\RunningAppID。
     ⚠️ 遗留不确定处（见 docs/coplay-design-and-briefs-20260710.md §五-1）：
     未在真实 Steam 客户端环境验证该键在当前版本是否存在、是否实时更新。
     读取失败（键不存在/非 Windows/值解析异常）一律 fail-open，退回纯进程名白名单
     匹配，不因为读注册表失败而阻塞整条陪玩链路。
  2. appid → 游戏名：从 config.coplay.steam_library_paths 逐个查找
     steamapps/appmanifest_<appid>.acf，正则提取 "name" 字段。Valve 的 .acf 格式
     是规整的 key-value 文本，不需要完整 VDF 解析器；解析失败 fail-open，
     退回占位名 "App {appid}"。
  3. 非 Steam 游戏：config.coplay.game_whitelist（[{name, process_name}, ...]）
     靠 psutil 进程名匹配。

检测到游戏 → session.enter_active(...)；追踪中的游戏进程消失 → session.enter_closing(...)。
active 期间若 desktop_ws 未连接，用 config.coplay.pet_launch_cmd（任意 shell 命令，
可以是打包后的 exe 路径，也可以是 "npm run dev" 之类的开发态命令——取决于
PresenceKit-desktop 当前部署形态，用户自己在 config.yaml 里配好）spawn 桌宠进程。
fail-open：拉不起来只记日志，不抛异常，不影响陪玩状态机。

轮询频率：由 config.coplay.poll_interval（秒）节流，但受制于
core/scheduler/loop.py 主循环本身固定 60 秒一次 tick 的节奏——poll_interval < 60
时实际生效粒度是 60s（游戏检测延迟几十秒可接受，不在本 brief 解决这个架构上限）。
"""

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from core.config_loader import get_config
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

_ACF_NAME_RE = re.compile(r'"name"\s*"([^"]*)"', re.IGNORECASE)

_last_poll_ts: dict[str, float] = {}

# 上一次 tick 探测到的原始信号（running_app_id / matched_process）+ 时间戳，
# 按 uid 分桶，进程内存、不持久化（重启丢失可接受，fail-open）。
# 供 /coplay/state 的 last_probe 调试字段读取（Brief 54-A §4：不用开 DEBUG 日志
# 也能从前端设置页看到检测链路探测到了什么）。
_last_probe: dict[str, dict[str, Any]] = {}

# detect_running_game() 的副作用寄存位：本次探测到的原始信号，供 _record_probe
# 读取写 DEBUG 日志 + 落 _last_probe，不影响 detect_running_game() 本身的返回值语义。
_last_raw_signals: dict[str, str | None] = {"running_app_id": None, "matched_process": None}


def _coplay_cfg() -> dict[str, Any]:
    return get_config().get("coplay", {}) or {}


def get_last_probe(uid: str) -> dict[str, Any] | None:
    """供 /coplay/state 读取上一次 tick 的探测信号。未探测过时返回 None（fail-open）。"""
    return _last_probe.get(uid)


def _poll_ready(uid: str) -> bool:
    _raw_interval = _coplay_cfg().get("poll_interval")
    interval = float(_raw_interval) if _raw_interval is not None else 10.0
    now = time.time()
    if now - _last_poll_ts.get(uid, 0.0) < interval:
        return False
    _last_poll_ts[uid] = now
    return True


def _read_steam_running_appid() -> str | None:
    """读取 HKCU\\Software\\Valve\\Steam\\RunningAppID。

    返回 None 时含义是"当前没有可信的 Steam 游戏信号"——调用方应退回白名单
    进程匹配，不是"陪玩不可用"。
    """
    try:
        import winreg
    except ImportError:
        return None  # 非 Windows 平台

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "RunningAppID")
    except (FileNotFoundError, OSError) as e:
        logger.debug("[coplay_watcher] RunningAppID 读取失败（fail-open）: %s", e)
        return None

    appid = str(value).strip()
    if not appid or appid == "0":
        return None  # 0 = 当前没有游戏在跑
    return appid


def _resolve_appid_to_name(appid: str, library_paths: list[str]) -> str | None:
    for lib in library_paths:
        manifest = Path(lib) / "steamapps" / f"appmanifest_{appid}.acf"
        try:
            text = manifest.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = _ACF_NAME_RE.search(text)
        if m:
            return m.group(1)
    return None


def _scan_whitelist_processes(whitelist: list[dict]) -> dict[str, str] | None:
    """扫描 config.coplay.game_whitelist 里配置的进程。

    命中第一个即返回 {"game_id": <小写进程名（不含 .exe）>, "game_name": <配置的 name>}。
    """
    try:
        import psutil
    except ImportError:
        logger.warning("[coplay_watcher] psutil 未安装，无法扫描非 Steam 游戏白名单")
        return None

    targets: dict[str, str] = {}
    for entry in whitelist:
        proc_name = (entry.get("process_name") or "").strip().lower()
        if not proc_name:
            continue
        if proc_name.endswith(".exe"):
            proc_name = proc_name[:-4]
        targets[proc_name] = entry.get("name") or entry.get("process_name")
    if not targets:
        return None

    try:
        for proc in psutil.process_iter(["name"]):
            try:
                pname = (proc.info.get("name") or "").strip().lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if pname.endswith(".exe"):
                pname = pname[:-4]
            if pname in targets:
                return {"game_id": pname, "game_name": targets[pname]}
    except Exception:
        logger.exception("[coplay_watcher] psutil 进程扫描失败")
    return None


def detect_running_game() -> dict[str, str] | None:
    """返回 {"game_id", "game_name"} 或 None。Steam 信号优先，白名单兜底。

    副作用：把本次探测到的原始信号（running_app_id / 匹配到的白名单进程名）写入
    模块级 `_last_raw_signals`，供 `_record_probe()` 写 DEBUG 日志 + 落
    `_last_probe`（不影响本函数返回值语义，调用方/既有测试无感知）。
    """
    cfg = _coplay_cfg()
    library_paths = cfg.get("steam_library_paths") or []
    whitelist = cfg.get("game_whitelist") or []

    appid = _read_steam_running_appid()
    matched_process: str | None = None
    result: dict[str, str] | None = None

    if appid:
        name = _resolve_appid_to_name(appid, library_paths) or f"App {appid}"
        result = {"game_id": f"steam:{appid}", "game_name": name}
    else:
        found = _scan_whitelist_processes(whitelist)
        if found:
            matched_process = found["game_id"]
            result = found

    _last_raw_signals["running_app_id"] = appid
    _last_raw_signals["matched_process"] = matched_process
    return result


def _record_probe(uid: str) -> None:
    """把 `_last_raw_signals` 记为本次 tick 的探测结果：DEBUG 日志 + `_last_probe`。"""
    running_app_id = _last_raw_signals.get("running_app_id")
    matched_process = _last_raw_signals.get("matched_process")
    logger.debug(
        "[coplay_watcher] probe uid=%s running_app_id=%s matched_process=%s",
        uid, running_app_id, matched_process,
    )
    _last_probe[uid] = {
        "running_app_id": running_app_id,
        "matched_process": matched_process,
        "ts": time.time(),
    }


def _is_tracked_game_still_running(game_id: str) -> bool:
    current = detect_running_game()
    return bool(current and current["game_id"] == game_id)


def _maybe_launch_pet() -> None:
    """active 时若桌宠未连接，spawn pet_launch_cmd。fail-open：异常只记日志。"""
    try:
        from channels import desktop_ws
        if desktop_ws.is_connected():
            return
    except Exception:
        logger.debug("[coplay_watcher] desktop_ws 状态不可读，跳过桌宠拉起判断")
        return

    cmd = (_coplay_cfg().get("pet_launch_cmd") or "").strip()
    if not cmd:
        logger.debug("[coplay_watcher] pet_launch_cmd 未配置，跳过自动拉起桌宠")
        return

    try:
        subprocess.Popen(cmd, shell=True)
        logger.info("[coplay_watcher] 桌宠未连接，已尝试拉起: %s", cmd)
    except Exception:
        logger.exception("[coplay_watcher] 拉起桌宠失败（fail-open，不影响陪玩状态机）")


async def tick(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    """一次 watcher tick。由 core/scheduler/triggers/coplay_watch.py 调用。

    不发言、只推状态机（armed → active / active → closing）+ 拉起桌宠。
    """
    from core.coplay import session

    # coplay.enabled 是部署级"允许陪玩功能"开关，默认 True——运行时唯一开关是
    # 状态机 armed/off（由用户在前端"游戏模式"驱动），这里的判定本身不变，只是
    # 缺省语义从"默认关闭"改成"默认允许"（Brief 54-A，消灭双开关）。
    if not _coplay_cfg().get("enabled", True):
        return
    if not _poll_ready(uid):
        return

    state = session.read_state(uid, char_id=char_id)
    status = state.get("status")

    if status == session.CoplayStatus.ARMED.value:
        found = detect_running_game()
        _record_probe(uid)
        if found:
            session.enter_active(
                uid, game_id=found["game_id"], game_name=found["game_name"], char_id=char_id,
            )
            _maybe_launch_pet()

    elif status == session.CoplayStatus.ACTIVE.value:
        game_id = state.get("game_id") or ""
        still_running = bool(game_id) and _is_tracked_game_still_running(game_id)
        _record_probe(uid)
        if game_id and not still_running:
            session.enter_closing(uid, char_id=char_id)
        else:
            _maybe_launch_pet()  # 游戏中桌宠掉线了也顺手重连
