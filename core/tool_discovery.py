"""Turn-local schema discovery, after all existing exposure filters.

These protocol controls deliberately are not dispatcher business tools: they
cannot execute actions, grant permissions, or produce completion evidence.
"""
from __future__ import annotations

from copy import deepcopy


CATEGORIES = {
    "browser": "网页浏览与浏览器操作",
    "desktop": "电脑桌面与应用操作",
    "artifacts": "聊天里给用户看的文件",
    "fs": "文件与工作区操作",
    "info": "时间、搜索和外部信息查询",
    "mcp": "已授权的外部 MCP 服务",
    "memory": "日记与记忆查询",
    "phone_control": "手机控制",
    "self_management": "已授予角色的自身能力管理",
    "system": "系统与设备管理",
}
PREFIX = "load_tools_"


class ToolDiscovery:
    def __init__(self, schemas: list[dict], registry: dict):
        self.groups: dict[str, list[dict]] = {}
        for schema in schemas:
            name = (schema.get("function") or schema).get("name", "")
            category = registry.get(name, {}).get("category")
            if category in CATEGORIES and not name.startswith(PREFIX):
                self.groups.setdefault(category, []).append(deepcopy(schema))
        self.loaded: set[str] = set()

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
                    "description": f"加载{description}的工具定义。只发现工具，不执行任何业务操作；下一轮才能调用具体工具。",
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
