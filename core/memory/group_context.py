"""
群聊上下文模块（框架尚未开发，仅有基础聊天功能）
维护每个群最近 N 条消息流（N = config.memory.group_context_lines）
每个角色单独保留群聊上下文，不继承旧共享缓存。
"""

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from core.config_loader import get_config
from core.error_handler import log_error
from core.sandbox import get_paths

logger = logging.getLogger(__name__)


def _keyword_overlap(text: str, query: str) -> float:
    """2-gram 字符重叠率（中文友好，无需分词库）。"""
    if not text or not query:
        return 0.0
    clean = re.compile(r"[^\w一-鿿]")
    t = clean.sub("", text)
    q = clean.sub("", query)
    if not t or not q:
        return 0.0
    t_grams = {t[i : i + 2] for i in range(len(t) - 1)} | set(t)
    q_grams = {q[i : i + 2] for i in range(len(q) - 1)} | set(q)
    if not q_grams:
        return 0.0
    return len(t_grams & q_grams) / len(q_grams)


def relevance_score(msg: dict, *, query_text: str, tags: set, char_name: str) -> float:
    """为单条群消息计算与当前对话的相关性分（fail-safe：异常返回 0）。"""
    try:
        text = str(msg.get("content") or "")
        score = 0.0
        score += _keyword_overlap(text, query_text) * 1.0
        if any(t.split(".")[-1] in text for t in tags):
            score += 0.5
        if char_name and char_name in text:
            score += 0.8
        return score
    except Exception:
        return 0.0


def _ctx_path(group_id: str, *, char_id: str | None = None) -> Path:
    """返回该群的上下文文件路径"""
    d = get_paths().group_context(char_id=char_id)
    from core.data_paths import safe_user_id
    group_id = safe_user_id(group_id)
    return d / f"{group_id}.json"


def _load_raw(group_id: str, *, char_id: str | None = None) -> list[dict]:
    """从磁盘读取原始消息列表，出错返回空列表"""
    path = _ctx_path(group_id, char_id=char_id)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        log_error("group_context._load_raw", e)
    return []


def append(group_id: str, sender_name: str, content: str, *, char_id: str | None = None):
    """
    追加一条群消息记录

    参数:
        group_id:    群号字符串
        sender_name: 发言者昵称
        content:     消息内容
    """
    cfg = get_config()
    max_lines = cfg.get("memory", {}).get("group_context_lines", 50)

    messages = _load_raw(group_id, char_id=char_id)
    messages.append({
        "sender_name": sender_name,
        "content": content,
        "ts": time.time(),
        "timestamp": datetime.now().strftime("%m-%d %H:%M"),
    })

    # 裁剪到最大条数
    if len(messages) > max_lines:
        messages = messages[-max_lines:]

    _save(group_id, messages, char_id=char_id)


def get_recent(group_id: str | None, *, char_id: str | None = None) -> list[dict]:
    """
    获取最近的群消息列表

    私聊时 group_id 传 None，直接返回空列表
    返回格式：[{"sender_name": "...", "content": "...", "timestamp": "..."}, ...]
    """
    if not group_id:
        return []
    msgs = _load_raw(group_id, char_id=char_id)
    msgs.sort(key=lambda m: float(m.get("ts") or 0))
    return msgs


def _save(group_id: str, messages: list[dict], *, char_id: str | None = None):
    """把消息列表写回磁盘"""
    path = _ctx_path(group_id, char_id=char_id)
    try:
        from core.safe_write import safe_write_json
        if not safe_write_json(path, messages):
            raise OSError("group context save failed")
    except Exception as e:
        log_error("group_context._save", e)


def clear(group_id: str, *, char_id: str | None = None):
    """清空指定群的上下文（admin 用）"""
    _save(group_id, [], char_id=char_id)


class GroupContext:
    """群聊上下文类，封装模块级函数，供外部按类方式导入使用"""

    def append(self, group_id: str, sender_name: str, content: str, *, char_id: str | None = None):
        append(group_id, sender_name, content, char_id=char_id)

    def get_recent(self, group_id: str | None, *, char_id: str | None = None) -> list[dict]:
        return get_recent(group_id, char_id=char_id)

    def clear(self, group_id: str, *, char_id: str | None = None):
        clear(group_id, char_id=char_id)
