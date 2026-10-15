"""Turn-local schema discovery, after all existing exposure filters.

These protocol controls deliberately are not dispatcher business tools: they
cannot execute actions, grant permissions, or produce completion evidence.
"""
from __future__ import annotations

from core.prompt_language import text as _pt

from copy import deepcopy


CATEGORIES = {
    "browser": "网页浏览与浏览器操作",
    "desktop": "电脑桌面与应用操作",
    "artifacts": "交给用户看/下载的文件：新建、查找、读取、续写和修改以前交付的文件（不是你自己的笔记）",
    "fs": "文件与工作区操作",
    "info": "外部信息与资料：时间、天气、网页搜索、小红书、用户文档、持久分段概要与文档笔记的读取/补充/修改、屏幕与摄像头",
    "life": "一起生活的小事：花园、喝一杯、听歌、玩具文件",
    "mcp": "已授权的外部 MCP 服务",
    "memory": "回想你们之间过去发生的事：日记、情景记忆、事件、用户资料",
    "phone_control": "手机控制",
    "qzone": "QQ 空间：查看好友动态/说说/评论，发布、评论与点赞",
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
    "qzone": "查看或发布 QQ 空间说说、评论和点赞",
    "life": "花园、喝一杯、听歌这类一起生活的小事",
    "artifacts": "给用户做文件，或查找、续写、修改你以前交付的文件",
    "desktop": "电脑上的操作",
    "browser": "打开网页或操作浏览器",
    "fs": "查找或操作工作区文件",
    "phone_control": "操作已连接手机",
    "mcp": "按需使用当前获准的外部服务",
    "self_management": "需要调整已授权由你管理的功能",
    "system": "查询或操作系统设备",
}
PREFIX = "load_tools_"


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
        listed = "、".join(names)
        if category != "mcp":
            purposes = []
            for schema in self.groups[category]:
                function = schema.get("function") or schema
                description = " ".join(str(function.get("description", "")).split())[:80]
                if description:
                    purposes.append(f"{function.get('name', '')}：{description}")
            if purposes:
                listed += _pt('。用途：') + "；".join(purposes)
        text = (_pt('含：') + f'{listed}' + '。') if listed else ""
        if category == "self":
            text += _pt('需要积累MCP用法时，可以在自己的空间维护笔记或能力表，记录真实用途、调用结果与限制；使用前重新核对当前连接和授权，旧笔记不证明能力仍可用。')
        note = self.notes.get(category)
        return f"{text}{note}" if note else text

    def routing_hint(self) -> str:
        """Direction table: which entry fits which kind of intent (exposed ones only)."""
        parts = [f"{_pt(ROUTES[c])} → {c}" for c in ROUTES if c in self.groups]
        return "；".join(parts) + "。" if parts else ""

    def schemas(self) -> list[dict]:
        result = []
        for category, description in CATEGORIES.items():
            description = _pt(description)
            if category not in self.groups:
                continue
            if category in self.loaded:
                result.extend(self.groups[category])
            else:
                result.append({"type": "function", "function": {
                    "name": PREFIX + category,
                    "description": ((_pt('加载') + f'{description}' + _pt('的工具定义。') + f'{self._contents(category)}' + _pt('只发现工具，不执行任何业务操作；下一轮才能调用具体工具。'))),
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                }})
        return deepcopy(result)

    def load(self, name: str, arguments: object) -> tuple[str, bool]:
        category = name.removeprefix(PREFIX)
        if not name.startswith(PREFIX) or category not in self.groups or arguments != {}:
            return _pt('工具分类加载失败：入口不可用或参数非法。未执行任何业务操作。'), False
        if category in self.loaded:
            return _pt('该分类已加载。请使用当前提供的具体工具。未执行任何业务操作。'), False
        self.loaded.add(category)
        return (_pt('已加载 ') + f'{category}' + _pt(' 的工具定义，下一轮可调用具体工具。未执行任何业务操作。')), True
