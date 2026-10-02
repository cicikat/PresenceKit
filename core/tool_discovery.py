"""Turn-local schema discovery, after all existing exposure filters.

These protocol controls deliberately are not dispatcher business tools: they
cannot execute actions, grant permissions, or produce completion evidence.
"""
from __future__ import annotations

from copy import deepcopy


CATEGORIES = {
    "browser": "网页浏览与浏览器操作",
    "desktop": "电脑桌面与应用操作",
    "artifacts": "做一份文件交给用户看/下载（不是你自己的笔记）",
    "fs": "文件与工作区操作",
    "info": "此刻的外部信息：时间、天气、网页搜索、小红书、用户文档、屏幕与摄像头",
    "life": "一起生活的小事：花园、喝一杯、听歌、玩具文件",
    "mcp": "已授权的外部 MCP 服务",
    "memory": "回想你们之间过去发生的事：日记、情景记忆、事件、用户资料",
    "phone_control": "手机控制",
    "schedule": "到点提醒与日程：答应了以后某个时间要做/要提醒的事",
    "self": "你自己的空间：想长期记住、整理、积累的东西（笔记、资料表、自制工具、给自己定的习惯 AGENT.md）",
    "self_management": "已授予角色的自身能力管理",
    "system": "系统与设备管理",
}
# One line per category: when to reach for it. Only categories that are actually
# exposed this turn are rendered (see routing_hint()).
ROUTES = {
    "self": "想长期记住或整理自己的东西",
    "memory": "想起以前的事",
    "schedule": "答应了到点提醒",
    "info": "查此刻的信息",
    "life": "花园、喝一杯、听歌这类一起生活的小事",
    "artifacts": "给用户做一份文件",
    "desktop": "电脑上的操作",
}
PREFIX = "load_tools_"
MAX_LISTED_TOOLS = 24


class ToolDiscovery:
    def __init__(self, schemas: list[dict], registry: dict, notes: dict[str, str] | None = None):
        # notes: optional per-category suffix for the entry description (e.g. the
        # character's own recipe names). Presentation only; never grants anything.
        self.notes = dict(notes or {})
        self.groups: dict[str, list[dict]] = {}
        for schema in schemas:
            name = (schema.get("function") or schema).get("name", "")
            category = registry.get(name, {}).get("category")
            if category in CATEGORIES and not name.startswith(PREFIX):
                self.groups.setdefault(category, []).append(deepcopy(schema))
        self.loaded: set[str] = set()

    def _contents(self, category: str) -> str:
        """List the tool names behind one entry so folding hides definitions, not existence.

        Without this the model only sees a category label and cannot tell that,
        say, a heart-rate reader lives behind ``memory`` — which made several
        exposed tools effectively unreachable.
        """
        names = [(schema.get("function") or schema).get("name", "") for schema in self.groups[category]]
        names = [name for name in names if name]
        shown, rest = names[:MAX_LISTED_TOOLS], max(0, len(names) - MAX_LISTED_TOOLS)
        listed = "、".join(shown) + (f" 等 {len(names)} 个" if rest else "")
        text = f"含：{listed}。" if listed else ""
        note = self.notes.get(category)
        return f"{text}{note}" if note else text

    def routing_hint(self) -> str:
        """Direction table: which entry fits which kind of intent (exposed ones only)."""
        parts = [f"{ROUTES[c]} → {c}" for c in ROUTES if c in self.groups]
        return "；".join(parts) + "。" if parts else ""

    def schemas(self) -> list[dict]:
        result = []
        for category, description in CATEGORIES.items():
            if category not in self.groups:
                continue
            if category in self.loaded:
                result.extend(self.groups[category])
            else:
                result.append({"type": "function", "function": {
                    "name": PREFIX + category,
                    "description": (f"加载{description}的工具定义。{self._contents(category)}"
                                    "只发现工具，不执行任何业务操作；下一轮才能调用具体工具。"),
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                }})
        return deepcopy(result)

    def load(self, name: str, arguments: object) -> tuple[str, bool]:
        category = name.removeprefix(PREFIX)
        if not name.startswith(PREFIX) or category not in self.groups or arguments != {}:
            return "工具分类加载失败：入口不可用或参数非法。未执行任何业务操作。", False
        if category in self.loaded:
            return "该分类已加载。请使用当前提供的具体工具。未执行任何业务操作。", False
        self.loaded.add(category)
        return f"已加载 {category} 的工具定义，下一轮可调用具体工具。未执行任何业务操作。", True
