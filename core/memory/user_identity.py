"""
user_identity — 用户稳定行为模式存储。
按用户存储（不按角色），描述用户 8 个行为维度，文件格式 YAML。
不接入慢任务，不修改 prompt_builder。
"""

import logging
import shutil
from pathlib import Path

import yaml

from core.memory.locks import uid_lock
from core.safe_write import safe_write_text
from core.sandbox import get_paths

logger = logging.getLogger(__name__)

IDENTITY_DIMENSIONS = [
    ("trust_pattern",     "信任建立模式"),
    ("emotion_expression","情绪表达方式"),
    ("help_seeking",      "求助风格"),
    ("stress_response",   "压力反应模式"),
    ("intimacy_comfort",  "亲密舒适度"),
    ("sleep_pattern",     "作息模式"),
    ("topic_preference",  "话题偏好"),
    ("self_relation",     "自我关系"),
]

_VALID_KEYS = {k for k, _ in IDENTITY_DIMENSIONS}
_REQUIRED_FIELDS = {"text", "confidence", "evidence_count", "last_updated"}


def _identity_file(user_id: str) -> Path:
    d = get_paths().user_identity_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{user_id}.yaml"


async def load(user_id: str) -> dict:
    """读取用户身份文件，返回通过校验的维度 dict。

    文件不存在返回空 dict。
    未知维度 key 记 warning 并跳过；维度数据缺必要字段同样 warning 并跳过整个维度。
    不带检索语义，不更新 strength。
    """
    path = _identity_file(user_id)
    async with uid_lock(user_id):
        if not path.exists():
            return {}
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning(f"[user_identity] 读取失败 uid={user_id}: {e}")
            return {}

    result = {}
    for key, value in raw.items():
        if key not in _VALID_KEYS:
            logger.warning(f"[user_identity] 未知维度 key={key!r}，跳过 uid={user_id}")
            continue
        if not isinstance(value, dict):
            logger.warning(f"[user_identity] 维度 {key} 数据格式非 dict，跳过 uid={user_id}")
            continue
        missing = _REQUIRED_FIELDS - value.keys()
        if missing:
            logger.warning(
                f"[user_identity] 维度 {key} 缺字段 {missing}，跳过 uid={user_id}"
            )
            continue
        value.setdefault("counter_evidence_count", 0)
        value.setdefault("last_conflict_at", 0.0)
        result[key] = value

    return result


async def save(user_id: str, identity_dict: dict) -> bool:
    """写入用户身份文件，写入前备份现有文件为 .yaml.bak。

    identity_dict 中不在 IDENTITY_DIMENSIONS 的 key 记 warning 并过滤掉；
    允许部分维度未填，不报错。
    成功返回 True，失败返回 False（不抛异常）。
    """
    path = _identity_file(user_id)

    filtered = {}
    for key, value in identity_dict.items():
        if key not in _VALID_KEYS:
            logger.warning(f"[user_identity] save 跳过未知维度 key={key!r} uid={user_id}")
            continue
        filtered[key] = value

    async with uid_lock(user_id):
        try:
            if path.exists():
                bak = path.parent / (path.name + ".bak")
                shutil.copy2(path, bak)
        except Exception as e:
            logger.warning(f"[user_identity] 备份失败 uid={user_id}: {e}")

        text = yaml.dump(
            filtered,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        return safe_write_text(path, text)


async def format_for_prompt(user_id: str, min_confidence: float = 0.5) -> str:
    """返回 confidence >= min_confidence 的维度描述，按 IDENTITY_DIMENSIONS 顺序拼接。

    每条前缀 "- "，以换行符连接。空结果（无维度或全不达标）返回 ""。
    框架句由 prompt_builder 负责，本函数不添加。
    """
    identity = await load(user_id)
    if not identity:
        return ""

    lines = []
    for key, _ in IDENTITY_DIMENSIONS:
        dim = identity.get(key)
        if dim is None:
            continue
        if dim.get("confidence", 0.0) < min_confidence:
            continue
        text = dim.get("text", "").strip()
        if text:
            lines.append(f"- {text}")

    return "\n".join(lines)
