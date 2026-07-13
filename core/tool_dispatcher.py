"""
工具调度模块
管理所有内置工具的注册、权限校验、执行和结果返回。
工具结果注入 prompt，不直接拼接进回复。

工具实现独立在 core/tools/ 子包中：
  core/tools/weather.py   — 天气查询
  core/tools/web_search.py — DuckDuckGo 搜索
设备控制和定时器逻辑较简单，直接写在此模块内。
"""

import logging
import platform
import subprocess
from typing import Callable

from core.config_loader import get_config
from core.character_name_provider import get_active_char_name
from core.error_handler import log_error
from core.tools.garden_tools import water_garden

logger = logging.getLogger(__name__)

# ─── 工具注册表 ────────────────────────────────────────────────────────────────
_TOOL_REGISTRY: dict[str, dict] = {}

# 定时任务回调：由 main.py 注入，用于 set_timer 发送 QQ 消息
_send_callback: Callable | None = None

# 工具失败时的友好兜底文案
_TOOL_FALLBACKS = {
    "weather": "天气信息暂时获取不到",
    "web_search": "网络暂时有些不稳定，没搜到",
    "get_time": "时间获取出了点问题",
    "add_reminder": "备忘录暂时写不进去，稍后再试",
    "read_diary": "日记暂时读不到",
    "read_watch": "身体数据暂时读取不到",
}


def register_send_callback(callback: Callable):
    """注入发送消息的回调函数（由 main.py 在初始化时调用）"""
    global _send_callback
    _send_callback = callback


# ─── 内联工具实现（设备控制、定时器）─────────────────────────────────────────

async def _device_shutdown(delay_seconds: int = 60) -> str:
    try:
        system = platform.system()
        if system == "Windows":
            subprocess.Popen(["shutdown", "/s", "/t", str(delay_seconds)])
        elif system in ("Linux", "Darwin"):
            subprocess.Popen(["shutdown", "-h", f"+{delay_seconds // 60}"])
        else:
            return f"不支持的系统平台：{system}"
        return f"已设置 {delay_seconds} 秒后关机"
    except Exception as e:
        log_error("tool.device_shutdown", e)
        return "关机命令执行失败"


async def _device_sleep() -> str:
    try:
        system = platform.system()
        if system == "Windows":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
        elif system == "Darwin":
            subprocess.Popen(["pmset", "sleepnow"])
        elif system == "Linux":
            subprocess.Popen(["systemctl", "suspend"])
        else:
            return f"不支持的系统平台：{system}"
        return "设备即将进入睡眠状态"
    except Exception as e:
        log_error("tool.device_sleep", e)
        return "睡眠命令执行失败"


# ─── 工具注册 ──────────────────────────────────────────────────────────────────

async def _get_current_time() -> str:
    from datetime import datetime
    now = datetime.now()
    week = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    return now.strftime(f"%Y年%m月%d日 %H:%M 星期{week}")


async def _add_reminder_wrapper(user_id: str, content: str, remind_at: str) -> str:
    from core.tools.reminder import add_reminder
    return add_reminder(user_id, content, remind_at)


def _weather_wrapper(city: str):
    from core.tools.weather import get_weather
    return get_weather(city)


def _web_search_wrapper(query: str, uid: str | None = None, char_id: str | None = None):
    from core.tools.web_search import search
    return search(query, uid=uid, char_id=char_id)


async def _read_diary_wrapper(user_id: str, date: str = "") -> str:
    from core.tools.diary_tool import read_diary_for_user
    return await read_diary_for_user(user_id, date_str=date)


async def _read_watch_wrapper(user_id: str, query: str = "") -> str:
    from core.tools.watch_tool import read_watch_for_user
    return read_watch_for_user(user_id, query)


async def _search_diary_wrapper(user_id: str, query: str = "") -> str:
    from core.tools.diary_search import search_diary_for_user
    return await search_diary_for_user(user_id, query)


async def _get_profile_wrapper(user_id: str) -> str:
    """召回用户画像。"""
    from core.memory import user_profile
    profile = user_profile.load(user_id)
    if not profile:
        return "暂无用户画像"
    parts = []
    if profile.get("nickname"):
        parts.append(f"称呼：{profile['nickname']}")
    if profile.get("location"):
        parts.append(f"位置：{profile['location']}")
    facts = profile.get("important_facts", [])
    if facts:
        parts.append("已知信息：" + "；".join(str(f) for f in facts[:10]))
    return "\n".join(parts) if parts else "暂无详细信息"


async def _get_episodic_wrapper(user_id: str, topic: str = "") -> str:
    """召回情景记忆。"""
    from core.memory.episodic_memory import retrieve, format_for_prompt
    memories = retrieve(user_id=user_id, topic=topic, top_k=3)
    return format_for_prompt(memories, char_name=get_active_char_name()) if memories else "暂无相关记忆"


import json as _json
import time as _time
from pathlib import Path as _Path

# ─── 全局模式闸 ─────────────────────────────────────────────────────────────────

_MODE_RESTRICTED_CATEGORIES: frozenset[str] = frozenset({"desktop", "system"})
_DANGER_MODE_TTL_SECONDS: int = 7200  # 2 小时后自动回 safe


def _current_mode() -> str:
    """读 data/runtime/meta_mode.json，返回 'safe' 或 'danger'。
    expires_at 过期或文件不存在 → safe。
    """
    try:
        from core.sandbox import get_paths
        p = get_paths().meta_mode()
        if not p.exists():
            return "safe"
        data = _json.loads(p.read_text(encoding="utf-8"))
        mode = data.get("mode", "safe")
        if mode != "danger":
            return "safe"
        expires_at = data.get("expires_at")
        if expires_at is not None and _time.time() > expires_at:
            return "safe"
        return "danger"
    except Exception:
        return "safe"


def _mode_gate(tool_name: str) -> str | None:
    """返回拒绝文案（模式闸命中），或 None（放行）。"""
    spec = _TOOL_REGISTRY.get(tool_name, {})
    if spec.get("category") in _MODE_RESTRICTED_CATEGORIES and _current_mode() != "danger":
        return "现在是安全模式，我不能操作你的电脑。要先在设置里开启危险模式哦。"
    return None


def _is_desktop_active() -> bool:
    """优先看 WS 连接，没连接才看文件 mtime fallback（5分钟内）。"""
    from channels import desktop_ws
    if desktop_ws.is_connected():
        return True
    import time
    from core.sandbox import get_paths
    f = get_paths().channel_queue()
    if not f.exists():
        return False
    return (time.time() - f.stat().st_mtime) < 300


async def _push_desktop_action(action: dict) -> str:
    """推送桌面/设备动作：优先 WS + ack（桌宠、设备任一成功即算成功），失败降级到文件队列。"""
    from channels import desktop_ws, device_ws
    targets = [w for w in (desktop_ws, device_ws) if w.is_connected()]
    if not targets and not _is_desktop_active():
        return "端离线，动作未执行"
    # 路径 1：WS push + 等 ack，任一目标 ack 成功即返回
    for w in targets:
        ok, err = await w.push_action_and_wait(action, timeout=5.0)
        if ok:
            return "ok"
        logger.warning(f"[_push_desktop_action] WS ack 失败: {err}，尝试下一目标/降级到文件")
    # 路径 2：文件队列 fallback
    try:
        from core.sandbox import get_paths
        _actions_file = get_paths().agent_actions()
        _actions_file.parent.mkdir(parents=True, exist_ok=True)
        queue = []
        if _actions_file.exists():
            queue = _json.loads(_actions_file.read_text(encoding="utf-8"))
        queue.append(action)
        _actions_file.write_text(
            _json.dumps(queue, ensure_ascii=False), encoding="utf-8"
        )
        return "ok"
    except Exception as e:
        return f"写入失败: {e}"


async def _desktop_minimize_wrapper(window: str = "") -> str:
    result = await _push_desktop_action({"type": "minimize_window", "window": window})
    return f"已请求最小化窗口「{window}」" if result == "ok" else result


async def _desktop_open_url_wrapper(url: str = "") -> str:
    result = await _push_desktop_action({"type": "open_url", "url": url})
    return f"已请求打开网址：{url}" if result == "ok" else result


async def _desktop_play_pause_wrapper() -> str:
    result = await _push_desktop_action({"type": "media_play_pause"})
    return "已请求播放/暂停媒体" if result == "ok" else result


async def _desktop_notify_wrapper(title: str = "", message: str = "") -> str:
    if not title:
        title = get_active_char_name()
    result = await _push_desktop_action({"type": "show_notify", "title": title, "message": message})
    return f"已发送通知：{message}" if result == "ok" else result


async def _play_song_wrapper(song_name: str = "", artist: str = "") -> str:
    """调网易云搜索API获取歌曲ID并推送播放动作。"""
    try:
        import aiohttp
        query = f"{song_name} {artist}".strip()
        url = "https://music.163.com/api/search/get"
        params = {
            "s": query,
            "type": 1,
            "limit": 1,
            "offset": 0,
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://music.163.com",
        }
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(
            connector=connector, trust_env=False
        ) as session:
            async with session.get(
                url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json(content_type=None)

        songs = data.get("result", {}).get("songs", [])
        if not songs:
            return f"未找到「{song_name}」，请确认歌名是否正确"

        song = songs[0]
        song_id = str(song.get("id", ""))
        real_name = song.get("name", song_name)
        artists = "/".join(a.get("name", "") for a in song.get("artists", []))

        result = await _push_desktop_action({
            "type": "play_netease",
            "song_id": song_id,
        })
        return f"已找到「{real_name}」by {artists}（ID:{song_id}），正在播放" if result == "ok" else result
    except Exception as e:
        return f"搜索失败: {e}"


async def _exit_yandere_wrapper() -> str:
    import json
    from pathlib import Path
    from core.config_loader import get_config
    emerald_path = get_config().get("emerald_desktop", {}).get("path", "")
    if not emerald_path:
        logger.warning("[exit_yandere] config.yaml 未配置 emerald_desktop.path，跳过")
        return "未配置旧 Emerald-desktop 路径"
    signal_file = Path(emerald_path) / "data" / "yandere_exit.signal"
    signal_file.parent.mkdir(parents=True, exist_ok=True)
    signal_file.write_text(json.dumps({"exit": True}), encoding="utf-8")
    return f"{get_active_char_name()}平静下来了"


async def _toy_vibrate_wrapper(
    intensity: float = 0.5,
    duration_ms: int = 1000,
    device_index: int | None = None,
) -> str:
    from core.tools.hardware_tools import toy_vibrate
    return await toy_vibrate(
        intensity=intensity,
        duration_ms=duration_ms,
        device_index=device_index,
    )


async def _toy_stop_wrapper(device_index: int | None = None) -> str:
    from core.tools.hardware_tools import toy_stop
    return await toy_stop(device_index=device_index)


async def _toy_pattern_wrapper(
    pattern_name: str = "gentle",
    device_index: int | None = None,
) -> str:
    from core.tools.hardware_tools import toy_pattern
    return await toy_pattern(pattern_name=pattern_name, device_index=device_index)


async def _read_toy_file_wrapper(file_key: str) -> str:
    from core.tools.toybox import read_toy_file
    return read_toy_file(file_key=file_key)


async def _write_toy_file_wrapper(
    file_key: str,
    content: str,
    mode: str = "overwrite",
) -> str:
    from core.tools.toybox import write_toy_file
    return write_toy_file(file_key=file_key, content=content, mode=mode)


async def _peek_screen_content_wrapper() -> str:
    from core.tools.screen_peek import peek_screen_content
    return await peek_screen_content()


async def _fs_list_wrapper(path: str | None = None, depth: int = 1) -> str:
    from core.tools.fs_browse import fs_list
    return fs_list(path=path, depth=depth)


async def _fs_read_wrapper(path: str) -> str:
    from core.tools.fs_browse import fs_read
    return fs_read(path=path)


_TOOL_REGISTRY["get_time"] = {
    "func": _get_current_time,
    "description": "获取当前准确时间，当用户询问时间、日期时调用.不确定时间时优先调用此工具,禁止猜测。",
    "dangerous": False,
    "category": "info",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "examples": ["几点了", "现在几点", "今天几号", "星期几"],
    "keywords": ["几点", "时间", "几号", "星期"],
}

_TOOL_REGISTRY["add_reminder"] = {
    "func": _add_reminder_wrapper,
    "description": (
    "添加一条备忘录，在指定时间提醒用户。"
    "当用户说'提醒我X点做Y'、'X时间记得Y'、'帮我记一下'时使用。"
    ),
    "dangerous": False,
    "category": "info",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "要提醒的事项内容",
            },
            "remind_at": {
                "type": "string",
                "description": "提醒时间，格式：HH:MM 或 MM-DD HH:MM 或 YYYY-MM-DD HH:MM",
            },
        },
        "required": ["content", "remind_at"],
    },
    "examples": ["提醒我8点吃药", "明天下午三点记得开会", "帮我记一下"],
    "keywords": ["提醒", "记得", "帮我记"],
    "trace_args": ["remind_at"],
}

_TOOL_REGISTRY["weather"] = {
    "func": _weather_wrapper,
    "description": "查询指定城市的当前天气。用户没有指定城市时，使用用户画像中的location字段，默认城市为杭州。",
    "dangerous": False,
    "category": "info",
    "parameters": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称，如 '北京' 或 'Beijing'"},
        },
        "required": ["city"],
    },
    "examples": ["今天天气怎么样", "明天下雨吗", "外面冷不冷", "几度"],
    "keywords": ["天气", "下雨", "气温", "几度", "冷不冷", "热不热"],
    "trace_args": ["city"],
}

_TOOL_REGISTRY["device_shutdown"] = {
    "func": _device_shutdown,
    "description": "关闭设备（电脑关机）",
    "dangerous": True,
    "category": "system",
    "parameters": {
        "type": "object",
        "properties": {
            "delay_seconds": {
                "type": "integer",
                "description": "延迟多少秒后关机，默认60秒",
            },
        },
        "required": [],
    },
}

_TOOL_REGISTRY["device_sleep"] = {
    "func": _device_sleep,
    "description": "让设备进入睡眠/休眠状态",
    "dangerous": True,
    "category": "system",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

_TOOL_REGISTRY["web_search"] = {
    "func": _web_search_wrapper,
    "description": "在网上查找信息，当你想确认某件事或帮用户找资料时使用",
    "dangerous": False,
    "category": "info",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或问题"},
        },
        "required": ["query"],
    },
    "examples": ["帮我搜一下", "查一下这个", "去网上看看"],
    "keywords": ["搜一下", "查一下", "去网上", "帮我搜", "帮我查"],
    "trace_args": ["query"],
}

_TOOL_REGISTRY["read_diary"] = {
    "func": _read_diary_wrapper,
    "description": (
        "当用户主动请求让{char}查看、阅读或评价自己的日记时调用。"
        "用户会拧巴，除了描述写日记的情况外，即使是只提到了日记也要调用。"
    ),
    "dangerous": False,
    "category": "info",
    "persist": True,
    "examples": [
        "帮我看看今天的日记",
        "你来读读我写的日记",
        "评价一下我最近的日记",
        "把我的日记给你看",
        "读一下我4月10号写的",
    ],
    "keywords": ["看日记", "读日记", "日记给你看", "日记给你", "日记读一下", "日记你看看"],
    "parameters": {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "要读的日期，如'4月10日'、'04-10'，不填则读今天",
            },
        },
        "required": [],
    },
    "trace_args": ["date"],
}

_TOOL_REGISTRY["read_watch"] = {
    "func": _read_watch_wrapper,
    "description": "当用户或{char}想了解用户的睡眠、心率、运动等身体数据时调用。可以查最近记录或历史趋势。",
    "dangerous": False,
    "category": "memory",
    "persist": True,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "查询类型：睡眠/心率/运动/最近，不填返回综合摘要",
            },
        },
        "required": [],
    },
    "trace_args": ["query"],
}

_TOOL_REGISTRY["search_diary"] = {
    "func": _search_diary_wrapper,
    "description": "按主题或关键词检索用户最近30天的日记内容。当{char}想回忆用户写过的某个话题、情绪、事件时主动调用，不需要用户明确要求。",
    "dangerous": False,
    "category": "memory",
    "persist": True,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，如'失眠'、'焦虑'、'考试'，不填返回最近日记片段",
            },
        },
        "required": [],
    },
    "trace_args": ["query"],
}

_TOOL_REGISTRY["desktop_minimize"] = {
    "func": _desktop_minimize_wrapper,
    "description": "最小化用户电脑上的某个窗口。当{char}觉得用户应该休息、或者用户在看让{char}不开心的东西时可以调用。",
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {
            "window": {
                "type": "string",
                "description": "窗口标题关键词，如「Steam」「游戏」「视频」",
            },
        },
        "required": ["window"],
    },
    "examples": ["最小化微信", "关掉这个窗口"],
    "keywords": ["最小化", "关掉窗口"],
    "trace_args": ["window"],
}

_TOOL_REGISTRY["desktop_open_url"] = {
    "func": _desktop_open_url_wrapper,
    "description": "在用户电脑上打开一个网址。{char}想分享内容、帮用户查东西时使用。",
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要打开的完整URL，如 https://music.163.com",
            },
        },
        "required": ["url"],
    },
    "examples": ["打开bilibili", "帮我开一下知乎"],
    "keywords": ["打开", "开一下"],
    "trace_args": ["url"],
}

_TOOL_REGISTRY["desktop_play_pause"] = {
    "func": _desktop_play_pause_wrapper,
    "description": "控制用户电脑的媒体播放/暂停。{char}想让用户听音乐或暂停音乐时使用。",
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "examples": ["暂停音乐", "继续播放", "暂停一下"],
    "keywords": ["暂停音乐", "继续播放", "暂停一下"],
}

_TOOL_REGISTRY["desktop_notify"] = {
    "func": _desktop_notify_wrapper,
    "description": "向用户发送一条系统通知。{char}有重要的事想提醒用户时使用，比如该吃饭了、该休息了。",
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "通知标题",
            },
            "message": {
                "type": "string",
                "description": "通知内容",
            },
        },
        "required": ["message"],
    },
    "examples": ["发个通知", "弹个提醒"],
    "keywords": ["发通知", "弹提醒"],
}

_TOOL_REGISTRY["play_song"] = {
    "func": _play_song_wrapper,
    "description": "搜索并在网易云音乐播放指定歌曲。用户说「放一首xx」「我要听xx」「播放xx」「帮我点xx」时调用。会自动搜索歌曲ID并播放，无需用户提供ID。",
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {
            "song_name": {
                "type": "string",
                "description": "歌曲名称",
            },
            "artist": {
                "type": "string",
                "description": "歌手名，可选",
            },
        },
        "required": ["song_name"],
    },
    "examples": ["放一首歌", "听周杰伦", "播放稻香"],
    "keywords": ["放歌", "听歌", "播放", "放一首"],
    "trace_args": ["song_name"],
}

_TOOL_REGISTRY["get_profile"] = {
    "func": _get_profile_wrapper,
    "description": "获取用户的基本信息和重要事实。当{char}需要了解用户的基本情况时调用。",
    "dangerous": False,
    "category": "memory",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

_TOOL_REGISTRY["get_episodic"] = {
    "func": _get_episodic_wrapper,
    "description": "召回与当前话题相关的情景记忆片段。当{char}想起某段往事或需要回忆过去时调用。",
    "dangerous": False,
    "category": "memory",
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "相关话题关键词，如'失眠'、'考试'、'吵架'",
            },
        },
        "required": [],
    },
    "trace_args": ["topic"],
}

_TOOL_REGISTRY["exit_yandere"] = {
    "func": _exit_yandere_wrapper,
    "description": "当{char}决定从病娇状态平静下来时调用，通常是用户说了让她安心的话之后。由{char}自主判断是否调用，不需要用户明确要求。",
    "dangerous": False,
    "category": "system",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

_TOOL_REGISTRY["water_garden"] = {
    "func": water_garden,
    "description": "用户催{char}去浇花、关心花园、问花长得怎么样并暗示该浇水时调用。无参数。{char}会按当前心情挑对应的那株花浇一次。",
    "dangerous": False,
    "category": "info",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "examples": ["你今天去浇花了吗", "快去浇花", "花园里的花怎么样了", "能不能现在去浇一下"],
    "keywords": ["浇花", "花园", "浇水"],
}

_TOOL_REGISTRY["peek_screen_content"] = {
    "func": _peek_screen_content_wrapper,
    "description": (
        "{char}主动查看用户当前窗口的屏幕文字内容（如 Obsidian 文档、代码文件正文等）。"
        "看到窗口标题后，若好奇或在意，可自主调用。无需用户提出。"
        "功能未开启或冷却中时自动返回提示，不会出错。"
    ),
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "examples": ["看看她在写什么", "翻一下那篇内容", "你好奇她在写的东西吗"],
    "keywords": ["看看内容", "翻一下", "看她在写什么", "屏幕内容"],
}

_TOOL_REGISTRY["toy_vibrate"] = {
    "func": _toy_vibrate_wrapper,
    "description": "控制已连接的 Intiface 振动设备。仅在用户明确要求振动并给出或接受强度、时长时调用。",
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {
            "intensity": {"type": "number", "description": "振动强度 0.0~1.0"},
            "duration_ms": {"type": "integer", "description": "持续毫秒数，最多 30000"},
            "device_index": {"type": "integer", "description": "可选设备索引"},
        },
        "required": [],
    },
    "examples": ["让玩具轻轻振动一下", "让连接的设备振动一秒"],
    "keywords": ["玩具振动", "设备振动"],
}

_TOOL_REGISTRY["toy_stop"] = {
    "func": _toy_stop_wrapper,
    "description": "立即停止已连接的 Intiface 设备。用户要求停止时应优先调用。",
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {
            "device_index": {"type": "integer", "description": "可选设备索引"},
        },
        "required": [],
    },
    "examples": ["停止玩具", "让设备停下"],
    "keywords": ["停止玩具", "设备停下"],
}

_TOOL_REGISTRY["toy_pattern"] = {
    "func": _toy_pattern_wrapper,
    "description": "让已连接的 Intiface 振动设备执行预设模式（gentle/pulse/wave/long）。仅在用户明确要求时调用。",
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {
            "pattern_name": {
                "type": "string",
                "enum": ["gentle", "pulse", "wave", "long"],
            },
            "device_index": {"type": "integer", "description": "可选设备索引"},
        },
        "required": [],
    },
    "examples": ["让玩具用波浪模式振动", "让设备执行轻柔模式"],
    "keywords": ["玩具模式", "波浪振动"],
    "trace_args": ["pattern_name"],
}

_TOOL_REGISTRY["read_toy_file"] = {
    "func": _read_toy_file_wrapper,
    "description": (
        "读取你们的玩具项目文件，可以随便读读写写画画。"
        "只能用 file_key 选择思考笔记、愿望清单或涂鸦板，不能读取系统文件。"
    ),
    "dangerous": False,
    "category": "desktop",
    "persist": True,
    "parameters": {
        "type": "object",
        "properties": {
            "file_key": {
                "type": "string",
                "enum": ["diary", "wishlist", "doodle"],
                "description": "diary=思考笔记，wishlist=愿望清单，doodle=涂鸦板",
            },
        },
        "required": ["file_key"],
    },
    "examples": ["读一下我们的思考笔记", "看看愿望清单", "打开涂鸦板看看"],
    "keywords": ["思考笔记", "愿望清单", "涂鸦板"],
    "trace_args": ["file_key"],
}

_TOOL_REGISTRY["write_toy_file"] = {
    "func": _write_toy_file_wrapper,
    "description": (
        "写入你们的玩具项目文件，可以随便写写画画。"
        "只能用 file_key 选择思考笔记、愿望清单或涂鸦板，不能修改系统文件。"
    ),
    "dangerous": False,
    "category": "desktop",
    "parameters": {
        "type": "object",
        "properties": {
            "file_key": {
                "type": "string",
                "enum": ["diary", "wishlist", "doodle"],
                "description": "diary=思考笔记，wishlist=愿望清单，doodle=涂鸦板",
            },
            "content": {
                "type": "string",
                "description": "要写入的纯文本内容，最多 4000 字",
            },
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "description": "overwrite 覆盖写入，append 追加写入",
            },
        },
        "required": ["file_key", "content"],
    },
    "examples": ["在思考笔记里写一句话", "把这个加到愿望清单", "在涂鸦板上画点文字"],
    "keywords": ["写进思考笔记", "加到愿望清单", "写在涂鸦板"],
}

_TOOL_REGISTRY["fs_list"] = {
    "func": _fs_list_wrapper,
    "description": (
        "列出 config.fs_access.allow_roots 允许范围内某个目录下的文件和子目录，"
        "只读。省略 path 时返回允许浏览的入口目录列表。想看文件内容用 fs_read。"
    ),
    "dangerous": False,
    "category": "fs",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要浏览的绝对路径，省略则返回允许浏览的根目录列表",
            },
            "depth": {
                "type": "integer",
                "enum": [1, 2],
                "description": "列出的目录深度，1 或 2，默认 1",
            },
        },
        "required": [],
    },
    "examples": ["看看这个目录里有什么", "列一下这个文件夹"],
    "keywords": ["列目录", "看看目录", "文件夹里有什么"],
    "trace_args": ["path"],
}

_TOOL_REGISTRY["fs_read"] = {
    "func": _fs_read_wrapper,
    "description": (
        "读取 config.fs_access.allow_roots 允许范围内的文本文件内容，只读。"
        "只支持文本类扩展名，超大或二进制文件会返回提示而不是内容。"
    ),
    "dangerous": False,
    "category": "fs",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要读取的文件绝对路径",
            },
        },
        "required": ["path"],
    },
    "examples": ["读一下这个文件", "打开看看这个文档写了什么"],
    "keywords": ["读文件", "打开这个文件", "看看这个文档"],
    "trace_args": ["path"],
}


# ─── N7: 快速路径风险标记 helper ──────────────────────────────────────────────
#
# 规则（保守优先）：
#   高风险 / side_effect=True ：
#     - 所有 dangerous=True 的工具（device_shutdown / device_sleep）
#     - desktop 控制类（向外推送动作：minimize / open_url / play_pause / notify / play_song）
#     - 写状态的工具（add_reminder / water_garden / exit_yandere）
#   低风险 / side_effect=False：
#     - 纯读类（get_time / weather / web_search / read_diary /
#               read_watch / search_diary / get_profile / get_episodic）

_SIDE_EFFECT_TOOLS: frozenset[str] = frozenset({
    # desktop 控制类 —— 会向桌宠端推送外部动作
    "desktop_minimize",
    "desktop_open_url",
    "desktop_play_pause",
    "desktop_notify",
    "play_song",
    "toy_vibrate",
    "toy_stop",
    "toy_pattern",
    "write_toy_file",
    # 写状态的工具
    "add_reminder",
    "water_garden",
    "exit_yandere",
    # system 类（dangerous=True，冗余标注，保证兜底）
    "device_shutdown",
    "device_sleep",
})


def is_side_effect_tool(tool_name: str) -> bool:
    """返回该工具是否有副作用（会写状态或向外部发动作）。

    优先复用 registry 中的 dangerous=True 标记，
    再对照 _SIDE_EFFECT_TOOLS 白名单兜底。
    """
    spec = _TOOL_REGISTRY.get(tool_name, {})
    if spec.get("dangerous", False):
        return True
    return tool_name in _SIDE_EFFECT_TOOLS


def tool_fast_path_risk(tool_name: str) -> str:
    """返回工具在快速路径下的风险等级字符串。

    "high" — 有副作用（写状态 / 控制外部 / 媒体控制 / 提醒 / 花园等）
    "low"  — 纯读类（时间 / 天气 / 搜索 / 日记阅读等）
    """
    return "high" if is_side_effect_tool(tool_name) else "low"


# ─── 对外接口 ──────────────────────────────────────────────────────────────────

def _is_tool_enabled(tool_name: str) -> bool:
    """检查 config.yaml tools 配置中工具是否启用（默认启用）。
    优先查 tools.<tool_name>.enabled，再回退到旧的 group 键。
    """
    cfg = get_config().get("tools", {})
    if tool_name in cfg:
        v = cfg[tool_name]
        if isinstance(v, dict):
            return v.get("enabled", True)
        return bool(v)
    group = tool_name
    if tool_name in ("device_shutdown", "device_sleep"):
        group = "device_control"
    elif tool_name == "set_timer":
        group = "timer"
    elif tool_name == "add_reminder":
        group = "reminder"
    return cfg.get(group, {}).get("enabled", True)


def get_tools_schema(categories: list[str] | None = None, *, char_id: str | None = None) -> list[dict]:
    """返回已启用工具的 OpenAI function_calling 格式 schema。
    categories: 若提供，仅返回该分类内的工具；None 返回全部。
    """
    char_name = get_active_char_name()
    schemas = []
    for name, info in _TOOL_REGISTRY.items():
        if not _is_tool_enabled(name):
            continue
        if categories is not None and info.get("category") not in categories:
            continue
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": info["description"].replace("{char}", char_name),
                "parameters": info["parameters"],
            },
        })
    if char_id is not None:
        from core.growth.mcp_proficiency import filter_schemas
        schemas = filter_schemas(schemas, char_id=char_id)
    return schemas


def format_tool_capability_note(categories: list[str] | None = None) -> str:
    """从 registry 派生已启用工具名称列表，供 prompt 注入。
    categories: 若提供，仅包含该分类的工具；None 返回全部已启用工具。
    工具名来自 _TOOL_REGISTRY，不手写，不列出 registry 中不存在的工具。
    """
    names = [
        name
        for name, info in _TOOL_REGISTRY.items()
        if _is_tool_enabled(name)
        and (categories is None or info.get("category") in categories)
    ]
    if not names:
        return ""
    return "可用工具：" + "、".join(names)


def get_probe_prompt(location: str) -> str:
    """动态从注册表构建探针 prompt，新增工具自动同步，无需手动维护。"""
    lines = [
        "只输出工具调用或空字符串，不要任何其他文字或思考过程。",
        "你作为工具调度器。根据用户消息判断是否调用工具。",
        f"用户位置：{location}。",
        "【工具使用原则】"
        "工具是获取事实或执行操作的手段，不是表达情绪的方式。"
        "只有当用户明确提出需要读取、查询、搜索、打开、提醒、发送、生成、修改、读日记相关等与工具有关的操作时，才考虑调用工具。"
        "不要为了显得主动而调用工具。"
        "不要用工具结果替代陪伴回应；工具结果只能补充事实。"
        "\n可用工具：",
    ]
    char_name = get_active_char_name()
    for name, spec in _TOOL_REGISTRY.items():
        if spec.get("category") not in ("info", "desktop"):
            continue
        examples = spec.get("examples", [])
        desc = spec.get("description", "").replace("{char}", char_name)
        example_str = " / ".join(examples) if examples else "（无示例）"
        lines.append(f"- {name}: {desc}\n  触发例句: {example_str}")
    lines.append("\n以上都不符合 → 输出空字符串，不调用任何工具")
    return "\n".join(lines)


_EXECUTE_ALLOWED_ORIGINS: frozenset[str] = frozenset({"user_live", "assistant_intent", "assistant_loop"})
_OWNER_ONLY_HARDWARE_TOOLS: frozenset[str] = frozenset({
    "toy_vibrate",
    "toy_stop",
    "toy_pattern",
})


def tool_loop_active(uid: str) -> bool:
    """Brief 28 · Path C 总闸：tool_loop.enabled + owner 真实私聊 + chat preset 为
    function_calling 模式，三者同时成立才为真。

    群聊 / scheduler trigger / 梦境 / Stage 的调用点在到达这个判断之前就已经走了别的分支
    （群聊在 main.py 里提前 return），本函数只需要核对 uid 是否为 owner。
    main.py 与 admin/routers/chat.py 用同一个 helper 判断"是否跳过探针"和"是否走
    run_agentic_loop"，两处判断必须一致，故抽在这里而不是各自内联。
    """
    cfg = get_config().get("tool_loop", {})
    if not cfg.get("enabled", False):
        return False
    owner_id = str(get_config().get("scheduler", {}).get("owner_id") or "")
    if not owner_id or str(uid) != owner_id:
        return False
    from core.model_registry import get_model_client
    try:
        mc = get_model_client("chat")
    except Exception:
        return False
    return mc.tool_call_mode == "function_calling"


async def execute(
    tool_name: str,
    tool_args: dict,
    user_id: str,
    target_id: str,
    is_group: bool,
    session_state,
    *,
    origin: str,
    char_id: str,
) -> tuple[str | None, str | None]:
    """
    执行工具，返回 (tool_result, ask_confirm_text)

    tool_result:      工具执行结果字符串，None 表示无结果
    ask_confirm_text: 高危工具等待确认时的询问文字，None 表示无需确认

    origin 必填，不在白名单则 fail-closed：返回 (None, None) + 记 warning。
    白名单：user_live（Path A 用户发起）/ assistant_intent（Path B 意图执行，附加门控）/
    assistant_loop（Path C tool loop 自主多步调用，Brief 28）。
    漏传 → TypeError，杜绝静默绕过。
    char_id: 当前活跃角色桶 id，用于 persist=True 工具的已读指纹检查和 short_term 回写。
    """
    if origin not in _EXECUTE_ALLOWED_ORIGINS:
        logger.warning(
            "[tool_dispatcher.execute] 拒绝执行: origin=%r 不在白名单, tool=%s",
            origin, tool_name,
        )
        # 闸门拒绝不落痕迹——这不是角色做过的事（Brief 27 · 2.2）。
        return None, None

    # Brief 27：工具动作痕迹层，execute() 每条 return 前落一条精简痕迹（origin 闸门拒绝除外）。
    def _trace(status: str, digest_source=None) -> None:
        try:
            from core.memory import action_trace
            action_trace.record(
                user_id, char_id,
                tool=tool_name, origin=origin, status=status,
                args_digest=action_trace.build_args_digest(tool_name, tool_args),
                result_digest=action_trace.build_result_digest(tool_name, digest_source),
            )
        except Exception as _at_err:
            logger.debug("[tool_dispatcher] action_trace record error: %s", _at_err)

    from core import user_relation

    from core.error_handler import get_tool_fail_response

    if tool_name not in _TOOL_REGISTRY:
        _fail = get_tool_fail_response()
        _trace("failed", _fail)
        return _fail, None

    # Brief 61 defensive gate. A blocked hallucinated MCP call is not an action,
    # so it deliberately leaves no action_trace record and reveals no level data.
    if tool_name.startswith("mcp__"):
        from core.growth.mcp_proficiency import NEUTRAL_REFUSAL, is_tool_allowed
        if not is_tool_allowed(tool_name, char_id=char_id):
            logger.warning("[tool_dispatcher] MCP proficiency gate denied tool=%s char_id=%s", tool_name, char_id)
            return NEUTRAL_REFUSAL, None

    gate_msg = _mode_gate(tool_name)
    if gate_msg is not None:
        _trace("failed", gate_msg)
        return gate_msg, None

    if not _is_tool_enabled(tool_name):
        _fail = get_tool_fail_response()
        _trace("failed", _fail)
        return _fail, None

    tool_info = _TOOL_REGISTRY[tool_name]

    # 权限校验
    if tool_name in _OWNER_ONLY_HARDWARE_TOOLS:
        owner_id = str(get_config().get("scheduler", {}).get("owner_id") or "")
        if is_group or not owner_id or str(user_id) != owner_id:
            logger.warning(
                "[tool_dispatcher.execute] 拒绝硬件控制: 非 owner 私聊, user_id=%s is_group=%s tool=%s",
                user_id, is_group, tool_name,
            )
            _msg = "硬件控制只允许 owner 私聊触发"
            _trace("failed", _msg)
            return _msg, None

    if tool_name in ("device_shutdown", "device_sleep"):
        if not user_relation.has_permission(user_id, "agent_control"):
            _msg = "你没有执行此操作的权限哦"
            _trace("failed", _msg)
            return _msg, None

    # 高危工具确认机制
    if tool_info["dangerous"]:
        if session_state.status != session_state.WAITING_CONFIRM:
            _ask = _build_confirm_ask(tool_name, tool_args)
            _trace("pending_confirm", _ask)
            session_state.set_waiting_confirm(tool_name, tool_args)
            return None, _ask

    # ── persist 工具：指纹去重检查 ────────────────────────────────────────────
    _is_persist = bool(tool_info.get("persist", False))
    _fingerprint: str | None = None
    if _is_persist:
        try:
            from core.memory.tool_read_log import build_fingerprint, is_recently_read
            _fingerprint = build_fingerprint(tool_name, tool_args)
            if _fingerprint and is_recently_read(user_id, char_id, _fingerprint):
                logger.info(
                    "[tool_dispatcher] persist 工具已读，跳过: tool=%s fp=%s",
                    tool_name, _fingerprint,
                )
                _skip = "（刚读过这个，这次跳过）"
                _trace("ok", _skip)
                return _skip, None
        except Exception as _fp_err:
            logger.debug("[tool_dispatcher] fingerprint check error: %s", _fp_err)

    # 执行工具
    try:
        func = tool_info["func"]
        if tool_name in (
            "add_reminder", "read_diary", "read_watch", "search_diary",
            "get_profile", "get_episodic",
        ):
            result = await func(user_id=user_id, **tool_args)
        elif tool_name == "web_search":
            result = await func(uid=user_id, char_id=char_id, **tool_args)
            # X3: record last search timestamp for autosearch rate-limit
            try:
                import time as _wt
                import json as _wj
                from core.sandbox import get_paths as _wgp
                _wsf = _wgp().web_autosearch_state()
                _wsf.parent.mkdir(parents=True, exist_ok=True)
                _wstate = {}
                if _wsf.exists():
                    _wstate = _wj.loads(_wsf.read_text(encoding="utf-8"))
                _wstate["last_ts"] = _wt.time()
                _wsf.write_text(_wj.dumps(_wstate), encoding="utf-8")
            except Exception:
                pass
        else:
            result = await func(**tool_args)
        logger.info(f"[tool_dispatcher] 工具 {tool_name} 执行完毕，结果: {result}")

        # ── persist 工具：执行成功后记录指纹 + 回写 short_term ───────────────
        if _is_persist and _fingerprint:
            try:
                from core.memory.tool_read_log import record_read, format_read_memo
                record_read(user_id, char_id, _fingerprint)
                memo = format_read_memo(tool_name, tool_args)
                if memo:
                    from core.memory.short_term import append as _st_append, _sanitize_assistant_message
                    sanitized = _sanitize_assistant_message(memo, uid=user_id)
                    _st_append(user_id, "assistant", sanitized, char_id=char_id)
            except Exception as _rec_err:
                logger.warning("[tool_dispatcher] persist record error: %s", _rec_err)

        _trace("ok", result)
        return f"工具已执行：{tool_name}，结果：{result}", None
    except TypeError as e:
        log_error("tool_dispatcher.execute", e)
        fallback = _TOOL_FALLBACKS.get(tool_name, "工具暂时不可用")
        _trace("failed", fallback)
        return fallback, None
    except Exception as e:
        log_error("tool_dispatcher.execute", e)
        fallback = _TOOL_FALLBACKS.get(tool_name, "工具暂时不可用")
        _trace("failed", fallback)
        return fallback, None


def _build_confirm_ask(tool_name: str, tool_args: dict) -> str:
    descriptions = {
        "device_shutdown": f"关机（{tool_args.get('delay_seconds', 60)}秒后）",
        "device_sleep": "让设备进入睡眠",
    }
    action = descriptions.get(tool_name, tool_name)
    return f"你确定要{action}吗？回复\"确认\"来执行，回复其他内容取消。"


class ToolDispatcher:
    """工具调度类封装，供外部按类方式导入使用"""

    def register_send_callback(self, callback):
        register_send_callback(callback)

    def get_tools_schema(self, categories: list[str] | None = None) -> list:
        return get_tools_schema(categories=categories)

    async def execute(self, tool_name, tool_args, user_id, target_id, is_group, session_state, *, origin: str, char_id: str):
        return await execute(
            tool_name=tool_name,
            tool_args=tool_args,
            user_id=user_id,
            target_id=target_id,
            is_group=is_group,
            session_state=session_state,
            origin=origin,
            char_id=char_id,
        )
