"""
短期记忆模块
保留最近 N 轮对话（N = config.memory.short_term_rounds）
持久化到 data/history/{user_id}.json（别认错了，history才是短期记忆读取，event_log不是)
"""

import json
import logging
import re
import time
from pathlib import Path

from core.config_loader import get_config
from core.error_handler import log_error
from core.sandbox import get_paths

logger = logging.getLogger(__name__)


def _strip_third_person_narrative(text: str) -> str:
    """
    检测第三人称小说叙事腔并做句子级过滤，保留对话句。
    调用方应保证 len(text) > 80。
    """
    first30 = text[:30]
    he_she_count = first30.count('他') + first30.count('她')

    has_trigger = (
        he_she_count >= 2
        or bool(re.search(r'——不是.{1,30}?，是', text))
        or bool(re.search(r'那种.{1,30}?的.{1,30}?[，。]', text))
    )

    if not has_trigger:
        return text

    parts = re.split(r'([。！？\n])', text)
    result_parts = []
    for i in range(0, len(parts), 2):
        sentence = parts[i]
        delimiter = parts[i + 1] if i + 1 < len(parts) else ''

        stripped_s = sentence.lstrip()
        discard = (
            stripped_s.startswith('他') or stripped_s.startswith('她')
            or bool(re.search(r'——不是.{1,30}?，是', sentence))
            or bool(re.search(r'那种.{1,30}?的', sentence))
        )

        if not discard:
            result_parts.append(sentence)
            if delimiter:
                result_parts.append(delimiter)

    result = ''.join(result_parts).strip('，。！？\n 　')

    non_punct_len = len(re.sub(r'[^\w]', '', result, flags=re.UNICODE))
    if non_punct_len < 10:
        return '...'

    return result


def _sanitize_assistant_message(content: str) -> str:
    """
    对过长的 assistant 回复做风格脱敏，保留台词，删除括号内动作描写。

    规则：
    - 总长度 ≤ 80 字：原样保留
    - 超过 80 字：删除所有 () 和 （） 包围的内容
    - 删除后如果为空（说明全是动作描写），返回截断到80字的原文
    - 继续检测并过滤第三人称叙事腔
    """
    if not content or len(content) <= 80:
        return content

    cleaned = re.sub(r'[（(][^）)]*[）)]', '', content)
    cleaned = cleaned.strip()

    if not cleaned:
        return content[:80] + "..."

    cleaned = _strip_third_person_narrative(cleaned)
    return cleaned


def _history_path(user_id: str) -> Path:
    """返回该用户的历史文件路径"""
    d = get_paths().history()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{user_id}.json"


def load(user_id: str) -> list[dict]:
    """
    读取用户的短期对话历史（完整历史，不做截断）

    返回格式：[{"role": "user"/"assistant", "content": "..."}, ...]
    文件不存在时返回空列表
    """
    path = _history_path(user_id)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            # 风格脱敏：防止 history 里的塌缩样本被 ds 自模仿
            for msg in data:
                if msg.get("role") == "assistant":
                    msg["content"] = _sanitize_assistant_message(msg.get("content", ""))
            return data
    except Exception as e:
        log_error("short_term.load", e)
    return []


def get_history(user_id: str, max_turns: int | None = None) -> list[dict]:
    """
    读取用户的短期对话历史，支持按轮数截断。

    参数：
        user_id   - 用户 QQ 号
        max_turns - 最多返回多少轮（一轮 = user + assistant 各一条）
                    None 时从 config.yaml 的 context.max_turns 读取，
                    再 fallback 到 memory.short_term_rounds，默认 20

    返回：
        截断后的消息列表，格式同 load()
    """
    if max_turns is None:
        cfg = get_config()
        # 优先读 context.max_turns，没有则读旧的 memory.short_term_rounds
        max_turns = (
            cfg.get("context", {}).get("max_turns")
            or cfg.get("memory", {}).get("short_term_rounds", 20)
        )

    history = load(user_id)
    # 每轮 = 2 条消息（user + assistant）
    max_msgs = max_turns * 2
    return history[-max_msgs:] if len(history) > max_msgs else history


def append(user_id: str, role: str, content: str, turn_id: str | None = None):
    """
    追加一条消息到历史记录，并裁剪到最大轮数

    role: "user" 或 "assistant"
    每两条（一问一答）算一轮，实际保留 short_term_rounds * 2 条消息
    turn_id 来自 fixation_pipeline.capture_turn，写入 _turn_id 字段供血缘追踪
    """
    cfg = get_config()
    max_rounds = cfg.get("memory", {}).get("short_term_rounds", 20)
    max_msgs = max_rounds * 2  # 每轮 = user + assistant

    history = load(user_id)
    entry: dict = {"role": role, "content": content, "timestamp": time.time()}
    if turn_id:
        entry["_turn_id"] = turn_id
    history.append(entry)

    # 超出上限时，从头部移除最早的消息
    if len(history) > max_msgs:
        history = history[-max_msgs:]

    _save(user_id, history)


def _save(user_id: str, history: list[dict]):
    """把历史记录写回磁盘"""
    path = _history_path(user_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("short_term._save", e)


def clear(user_id: str):
    """清空指定用户的短期历史（admin 用）"""
    _save(user_id, [])


class ShortTermMemory:
    """短期记忆类，封装模块级函数，供外部按类方式导入使用"""

    def load(self, user_id: str) -> list[dict]:
        return load(user_id)

    def get_history(self, user_id: str, max_turns: int | None = None) -> list[dict]:
        return get_history(user_id, max_turns)

    def append(self, user_id: str, role: str, content: str, turn_id: str | None = None):
        append(user_id, role, content, turn_id=turn_id)

    def clear(self, user_id: str):
        clear(user_id)
