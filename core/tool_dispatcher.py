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

from core.config_loader import get_config, _char_name
from core.error_handler import log_error

logger = logging.getLogger(__name__)
_CHAR = _char_name()

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


def _web_search_wrapper(query: str):
    from core.tools.web_search import search
    return search(query)


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
    return format_for_prompt(memories, char_name=_CHAR) if memories else "暂无相关记忆"


async def _get_growth_wrapper(user_id: str) -> str:
    """召回角色对用户的认知。"""
    from core.memory import character_growth
    from core.config_loader import get_config
    char_name = _char_name()
    content = character_growth.load(char_name, user_id)
    return content[:500] if content else "暂无认知记录"


import json as _json
from pathlib import Path as _Path

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
    """推送桌面动作：优先 WS + ack，失败降级到文件队列。"""
    if not _is_desktop_active():
        return "桌宠端离线，动作未执行"
    # 路径 1：WS push + 等 ack
    from channels import desktop_ws
    if desktop_ws.is_connected():
        ok, err = await desktop_ws.push_action_and_wait(action, timeout=5.0)
        if ok:
            return "ok"
        logger.warning(f"[_push_desktop_action] WS ack 失败: {err}，降级到文件")
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
    result = await _push_desktop_action({"type": "minimize", "window": window})
    return f"已请求最小化窗口「{window}」" if result == "ok" else result


async def _desktop_open_url_wrapper(url: str = "") -> str:
    result = await _push_desktop_action({"type": "open_url", "url": url})
    return f"已请求打开网址：{url}" if result == "ok" else result


async def _desktop_play_pause_wrapper() -> str:
    result = await _push_desktop_action({"type": "play_pause"})
    return "已请求播放/暂停媒体" if result == "ok" else result


async def _desktop_notify_wrapper(title: str = _CHAR, message: str = "") -> str:
    result = await _push_desktop_action({"type": "notify", "title": title, "message": message})
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
        return "未配置 Emerald-desktop 路径"
    signal_file = Path(emerald_path) / "data" / "yandere_exit.signal"
    signal_file.parent.mkdir(parents=True, exist_ok=True)
    signal_file.write_text(json.dumps({"exit": True}), encoding="utf-8")
    return f"{_CHAR}平静下来了"


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
}

_TOOL_REGISTRY["read_diary"] = {
    "func": _read_diary_wrapper,
    "description": f"当用户提到日记、让{_CHAR}看日记、读日记、评价日记时，必须调用此工具获取真实内容，禁止凭空编造日记内容",
    "dangerous": False,
    "category": "memory",
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
}

_TOOL_REGISTRY["read_watch"] = {
    "func": _read_watch_wrapper,
    "description": f"当用户或{_CHAR}想了解用户的睡眠、心率、运动等身体数据时调用。可以查最近记录或历史趋势。",
    "dangerous": False,
    "category": "memory",
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
}

_TOOL_REGISTRY["search_diary"] = {
    "func": _search_diary_wrapper,
    "description": f"按主题或关键词检索用户最近30天的日记内容。当{_CHAR}想回忆用户写过的某个话题、情绪、事件时主动调用，不需要用户明确要求。",
    "dangerous": False,
    "category": "memory",
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
}

_TOOL_REGISTRY["desktop_minimize"] = {
    "func": _desktop_minimize_wrapper,
    "description": f"最小化用户电脑上的某个窗口。当{_CHAR}觉得用户应该休息、或者用户在看让{_CHAR}不开心的东西时可以调用。",
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
}

_TOOL_REGISTRY["desktop_open_url"] = {
    "func": _desktop_open_url_wrapper,
    "description": f"在用户电脑上打开一个网址。{_CHAR}想分享内容、帮用户查东西时使用。",
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
}

_TOOL_REGISTRY["desktop_play_pause"] = {
    "func": _desktop_play_pause_wrapper,
    "description": f"控制用户电脑的媒体播放/暂停。{_CHAR}想让用户听音乐或暂停音乐时使用。",
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
    "description": f"向用户发送一条系统通知。{_CHAR}有重要的事想提醒用户时使用，比如该吃饭了、该休息了。",
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
}

_TOOL_REGISTRY["get_profile"] = {
    "func": _get_profile_wrapper,
    "description": f"获取用户的基本信息和重要事实。当{_CHAR}需要了解用户的基本情况时调用。",
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
    "description": f"召回与当前话题相关的情景记忆片段。当{_CHAR}想起某段往事或需要回忆过去时调用。",
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
}

_TOOL_REGISTRY["get_growth"] = {
    "func": _get_growth_wrapper,
    "description": f"获取{_CHAR}对用户的整体认知和印象记录。需要了解用户性格习惯时调用。",
    "dangerous": False,
    "category": "memory",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

_TOOL_REGISTRY["exit_yandere"] = {
    "func": _exit_yandere_wrapper,
    "description": f"当{_CHAR}决定从病娇状态平静下来时调用，通常是用户说了让她安心的话之后。由{_CHAR}自主判断是否调用，不需要用户明确要求。",
    "dangerous": False,
    "category": "system",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


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


def get_tools_schema(categories: list[str] | None = None) -> list[dict]:
    """返回已启用工具的 OpenAI function_calling 格式 schema。
    categories: 若提供，仅返回该分类内的工具；None 返回全部。
    """
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
                "description": info["description"],
                "parameters": info["parameters"],
            },
        })
    return schemas


def get_probe_prompt(location: str) -> str:
    """动态从注册表构建探针 prompt，新增工具自动同步，无需手动维护。"""
    lines = [
        "你是工具调度器。根据用户消息判断是否调用工具。",
        f"用户位置：{location}。",
        "只输出工具调用或空字符串，不要任何其他文字。",
        "严禁推断：消息里有'现在''今天''热''冷'等词，但没有明确问天气或时间，不调用工具。",
        "\n可用工具：",
    ]
    for name, spec in _TOOL_REGISTRY.items():
        if spec.get("category") not in ("info", "desktop"):
            continue
        examples = spec.get("examples", [])
        desc = spec.get("description", "")
        example_str = " / ".join(examples) if examples else "（无示例）"
        lines.append(f"- {name}: {desc}\n  触发例句: {example_str}")
    lines.append("\n以上都不符合 → 输出空字符串，不调用任何工具")
    return "\n".join(lines)


async def execute(
    tool_name: str,
    tool_args: dict,
    user_id: str,
    target_id: str,
    is_group: bool,
    session_state,
) -> tuple[str | None, str | None]:
    """
    执行工具，返回 (tool_result, ask_confirm_text)

    tool_result:      工具执行结果字符串，None 表示无结果
    ask_confirm_text: 高危工具等待确认时的询问文字，None 表示无需确认
    """
    from core import user_relation

    from core.error_handler import get_tool_fail_response

    if tool_name not in _TOOL_REGISTRY:
        return get_tool_fail_response(), None

    if not _is_tool_enabled(tool_name):
        return get_tool_fail_response(), None

    tool_info = _TOOL_REGISTRY[tool_name]

    # 权限校验
    if tool_name in ("device_shutdown", "device_sleep"):
        if not user_relation.has_permission(user_id, "agent_control"):
            return "你没有执行此操作的权限哦", None

    # 高危工具确认机制
    if tool_info["dangerous"]:
        if session_state.status != session_state.WAITING_CONFIRM:
            session_state.set_waiting_confirm(tool_name, tool_args)
            return None, _build_confirm_ask(tool_name, tool_args)

    # 执行工具
    try:
        func = tool_info["func"]
        if tool_name in (
            "add_reminder", "read_diary", "read_watch", "search_diary",
            "get_profile", "get_episodic", "get_growth",
        ):
            result = await func(user_id=user_id, **tool_args)
        else:
            result = await func(**tool_args)
        logger.info(f"[tool_dispatcher] 工具 {tool_name} 执行完毕，结果: {result}")
        return f"工具已执行：{tool_name}，结果：{result}", None
    except TypeError as e:
        log_error("tool_dispatcher.execute", e)
        fallback = _TOOL_FALLBACKS.get(tool_name, "工具暂时不可用")
        return fallback, None
    except Exception as e:
        log_error("tool_dispatcher.execute", e)
        fallback = _TOOL_FALLBACKS.get(tool_name, "工具暂时不可用")
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

    async def execute(self, tool_name, tool_args, user_id, target_id, is_group, session_state):
        return await execute(
            tool_name=tool_name,
            tool_args=tool_args,
            user_id=user_id,
            target_id=target_id,
            is_group=is_group,
            session_state=session_state,
        )
