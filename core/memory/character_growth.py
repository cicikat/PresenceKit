"""
角色对用户的认知文件（read-only legacy surface）
─────────────────────────────────────────────────────
角色对每个用户维护一个"认知 Markdown 文件"，
记录他觉得重要的事情、用户的特点、两人的重要时刻。

存储位置：
  data/character_growth/角色_{user_id}.md

当前状态（R8-E2）：
  写入链 update() / should_update() 已退役删除。
  load() 保留为 get_growth 工具的只读兼容面。
  写入链已迁移到 consolidate_to_identity + trait_tracker_update slow_queue task。
"""

from pathlib import Path

from core.error_handler import log_error
from core.sandbox import get_paths


def _growth_root() -> Path:
    return get_paths().character_growth()


def _growth_file(character_name: str, user_id: str) -> Path:
    """返回认知文件路径，文件名格式：角色_{user_id}.md"""
    safe_char = "".join(c for c in character_name if c.isalnum() or c in "-_")
    safe_user = "".join(c for c in user_id if c.isalnum() or c in "-_")
    return _growth_root() / f"{safe_char}_{safe_user}.md"


def load(character_name: str, user_id: str) -> str:
    """
    读取角色对该用户的认知文件内容。
    文件不存在时返回空字符串，不报错。

    参数：
        character_name - 角色名（如"叶瑄"）
        user_id        - 用户 QQ 号

    返回：
        认知文件的文本内容，空则返回 ""
    """
    path = _growth_file(character_name, user_id)
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        log_error("character_growth.load", e)
    return ""


class CharacterGrowth:
    """
    CharacterGrowth 类封装，供外部按类方式导入使用。
    update() / should_update() 已于 R8-E2 退役。
    """

    def load(self, character_name: str, user_id: str) -> str:
        return load(character_name, user_id)
