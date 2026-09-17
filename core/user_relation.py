"""
用户关系管理模块
读取 relations.yaml（经 DataPaths 路由），提供用户关系查询接口
支持运行时热重载（admin 修改后不需要重启）
"""

import logging
from pathlib import Path

import yaml

from core.error_handler import log_error

logger = logging.getLogger(__name__)

# 缓存的关系配置（字典）
_relations_cache: dict | None = None


def _load_relations() -> dict:
    """从磁盘读取 relations.yaml，出错时返回只有 default 的最小配置"""
    from core.sandbox import get_paths
    global _relations_cache
    try:
        with open(get_paths().relations(), "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _relations_cache = data.get("relations", {})
    except FileNotFoundError:
        logger.warning("[user_relation] relations.yaml 不存在，使用默认配置")
        _relations_cache = {}
    except Exception as e:
        log_error("user_relation._load_relations", e)
        if _relations_cache is None:
            _relations_cache = {}
    return _relations_cache


def _get_relations() -> dict:
    """获取关系配置（懒加载单例）"""
    if _relations_cache is None:
        _load_relations()
    return _relations_cache or {}


# 当没有为用户单独配置时，使用的默认关系
_BUILTIN_DEFAULT = {
    "role": "stranger",
    "nickname": None,
    "priority": 1,
    "permissions": {
        "agent_control": False,
        "image_gen": False,
    },
    "extra_prompt": "",
}


def get_relation(user_id: str) -> dict:
    """
    获取指定用户的关系配置

    查找顺序：
    1. 用户 ID 精确匹配
    2. relations.yaml 中的 "default" 配置
    3. 内置默认配置（stranger）
    """
    relations = _get_relations()
    user_id_str = str(user_id)

    if user_id_str in relations:
        # 用内置默认填充缺失字段
        config = dict(_BUILTIN_DEFAULT)
        config.update(relations[user_id_str])
        # 权限字段也要合并
        default_perms = dict(_BUILTIN_DEFAULT["permissions"])
        default_perms.update(config.get("permissions") or {})
        config["permissions"] = default_perms
        return config

    if "default" in relations:
        config = dict(_BUILTIN_DEFAULT)
        config.update(relations["default"])
        default_perms = dict(_BUILTIN_DEFAULT["permissions"])
        default_perms.update(config.get("permissions") or {})
        config["permissions"] = default_perms
        return config

    return dict(_BUILTIN_DEFAULT)


def has_configured_relation(user_id: str) -> bool:
    """是否存在真实配置的关系数据（用户专属条目或 relations.yaml 的全局 default 段）。

    False 表示 get_relation() 返回的是硬编码兜底 _BUILTIN_DEFAULT（stranger/无称呼/
    无 extra_prompt），不是管理员或用户真实录入的关系状态——调用方（prompt_builder
    的关系层）据此判断"没有信息就别说，胜过注入陌生人误导角色对 owner 冷淡"（Brief 97 §5）。
    """
    relations = _get_relations()
    return str(user_id) in relations or "default" in relations


def has_permission(user_id: str, permission_name: str) -> bool:
    """
    检查用户是否拥有指定权限

    permission_name: 如 "agent_control", "image_gen"
    """
    relation = get_relation(user_id)
    perms = relation.get("permissions") or {}
    return bool(perms.get(permission_name, False))


def get_extra_prompt(user_id: str) -> str:
    """获取该用户的额外提示词，没有则返回空字符串"""
    relation = get_relation(user_id)
    return relation.get("extra_prompt") or ""


def reload():
    """
    热重载关系配置
    admin 修改 relations.yaml 后调用此函数，无需重启
    """
    _load_relations()
    logger.info("[user_relation] relations.yaml 已热重载")


class UserRelation:
    """用户关系类，封装模块级函数，供外部按类方式导入使用"""

    def get_relation(self, user_id: str) -> dict:
        return get_relation(user_id)

    def has_permission(self, user_id: str, permission_name: str) -> bool:
        return has_permission(user_id, permission_name)

    def get_extra_prompt(self, user_id: str) -> str:
        return get_extra_prompt(user_id)

    def reload(self):
        reload()
