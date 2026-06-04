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
from core.safe_write import safe_write_json
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)

# 近场承载对话连续性，必须优先保留最近几轮的上下文。
NEAR_K = 5

# 内容越长，越可能包含具体事件、约束或连续叙述。
LENGTH_SIGNAL_WEIGHT = 1.0
# 具体名词/实体能指向人、地点、作品、工具等可复用事实。
ENTITY_SIGNAL_WEIGHT = 1.4
# 问句通常携带用户当前需要回答的显性意图。
QUESTION_SIGNAL_WEIGHT = 1.0
# 数字/日期常对应时间、数量、进度等精确事实。
NUMBER_DATE_SIGNAL_WEIGHT = 1.2
# tag_rules 命中说明内容触发了现有 prompt 层关注的话题。
TAG_SIGNAL_WEIGHT = 1.3
# 情绪词提示这一轮更可能影响关系、状态或后续照护。
EMOTION_SIGNAL_WEIGHT = 1.1
# B 档信号将来接 mid_term/episodic 就绪状态，v1 固定为 0。
READY_SIGNAL_WEIGHT = 1.0
# 单组总分上限：tag/emotion 等信号存在有意双算，clamp 总分防止多信号叠加让单组分数失控、在远场择优里碾压其他轮次
TURN_SCORE_CAP = 5.0

# 长度分用字符数封顶，避免超长闲聊压过其他高价值信号。
LENGTH_SCORE_CHAR_CAP = 120
# 实体分只计少量命中，避免同类实体重复堆分。
ENTITY_SCORE_CAP = 3
# tag 分只计少量命中，避免多标签话题无限放大。
TAG_SCORE_CAP = 3
ENTITY_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}(?:学院|大学|公司|医院|小区|公园|车站|机场|项目|系统|模型|代码|文档|日记|工具|游戏|城市)"
)
QUESTION_PATTERN = re.compile(r"[?？]|吗|嘛|么|什么|怎么|为什么|哪|谁|几|多少|是不是|能不能|要不要")
NUMBER_DATE_PATTERN = re.compile(r"\d|[一二三四五六七八九十百千万]+(?:点|次|天|年|月|日|分钟|小时)|今天|昨天|明天|上午|下午|晚上|凌晨|周[一二三四五六日天]")


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


def _sanitize_assistant_message(content: str, uid: str = "") -> str:
    """
    对过长的 assistant 回复做风格脱敏，保留台词，删除括号内动作描写。

    规则：
    - 总长度 ≤ 80 字：原样保留
    - 超过 80 字：括号内容 ≤8 字保留，>8 字删除
    - 删除后如果为空（说明全是动作描写），返回截断到80字的原文
    - 继续检测并过滤第三人称叙事腔
    """
    if not content or len(content) <= 80:
        return content

    kept_parens: list[str] = []
    stripped_parens: list[str] = []

    def _strip_long_parens(match: re.Match) -> str:
        inner = match.group(0)
        paren_content = inner[1:-1]
        if len(paren_content) <= 8:
            kept_parens.append(inner)
            return inner
        stripped_parens.append(inner)
        return ''

    cleaned = re.sub(r'[（(][^）)]*[）)]', _strip_long_parens, content)
    cleaned = cleaned.strip()

    if kept_parens or stripped_parens:
        logger.debug(
            json.dumps(
                {"ts": time.time(), "uid": uid, "kept_parens": kept_parens, "stripped_parens": stripped_parens},
                ensure_ascii=False,
            )
        )

    if not cleaned:
        return content[:80] + "..."

    cleaned = _strip_third_person_narrative(cleaned)
    return cleaned


def _group_turns(history: list[dict]) -> list[list[dict]]:
    """把平铺 history 按 turn-group 分组；同一 _turn_id 的连续段不拆开。"""
    groups: list[list[dict]] = []
    seen_turn_ids: set[str] = set()
    i = 0
    while i < len(history):
        entry = history[i]
        turn_id = entry.get("_turn_id")

        if turn_id is not None:
            if turn_id in seen_turn_ids:
                logger.warning(f"[short_term_weight] non_adjacent_turn_id turn_id={turn_id}")
            group = [entry]
            i += 1
            while i < len(history) and history[i].get("_turn_id") == turn_id:
                group.append(history[i])
                i += 1
            groups.append(group)
            seen_turn_ids.add(turn_id)
            continue

        if (
            entry.get("role") == "user"
            and i + 1 < len(history)
            and history[i + 1].get("_turn_id") is None
            and history[i + 1].get("role") == "assistant"
        ):
            groups.append([entry, history[i + 1]])
            i += 2
            continue

        groups.append([entry])
        i += 1

    return groups


def _ready_signal_bonus(turn_id) -> float:
    # // B 档：将来按 turn_id join 已就绪的 mid_term/episodic 信号，缺失按 0；v1 关闭
    return 0.0


def _score_turn_group(group: list[dict]) -> tuple[float, dict]:
    """在已 sanitize 的 content 上计算 turn-group 信息量分数。"""
    text = "\n".join(str(msg.get("content") or "") for msg in group)
    compact_text = re.sub(r"\s+", "", text)

    length_score = min(len(compact_text) / LENGTH_SCORE_CHAR_CAP, 1.0) * LENGTH_SIGNAL_WEIGHT
    entity_hits = set(ENTITY_PATTERN.findall(text))
    entity_score = min(len(entity_hits), ENTITY_SCORE_CAP) / ENTITY_SCORE_CAP * ENTITY_SIGNAL_WEIGHT
    question_score = (QUESTION_SIGNAL_WEIGHT if QUESTION_PATTERN.search(text) else 0.0)
    number_date_score = (NUMBER_DATE_SIGNAL_WEIGHT if NUMBER_DATE_PATTERN.search(text) else 0.0)

    try:
        from core.tag_rules import get_tags
        tag_hits = get_tags(text)
    except Exception as e:
        log_error("short_term._score_turn_group.tag_rules", e)
        tag_hits = set()
    tag_score = min(len(tag_hits), TAG_SCORE_CAP) / TAG_SCORE_CAP * TAG_SIGNAL_WEIGHT

    emotion_hits = {tag for tag in tag_hits if tag.startswith("emotion.")}
    emotion_score = (EMOTION_SIGNAL_WEIGHT if emotion_hits else 0.0)
    turn_id = next((msg.get("_turn_id") for msg in group if msg.get("_turn_id") is not None), None)
    ready_score = _ready_signal_bonus(turn_id) * READY_SIGNAL_WEIGHT

    parts = {
        "length": round(length_score, 4),
        "entity": round(entity_score, 4),
        "question": round(question_score, 4),
        "number_date": round(number_date_score, 4),
        "tag": round(tag_score, 4),
        "emotion": round(emotion_score, 4),
        "ready": round(ready_score, 4),
    }
    total = round(min(sum(parts.values()), TURN_SCORE_CAP), 4)
    return total, parts


def _log_turn_group_score(user_id, group: list[dict], selected: bool, total: float | None = None, parts: dict | None = None) -> None:
    if total is None or parts is None:
        total, parts = _score_turn_group(group)
    turn_id = next((msg.get("_turn_id") for msg in group if msg.get("_turn_id") is not None), None)
    logger.debug(
        f"[short_term_weight] uid={user_id} turn_id={turn_id} "
        f"total={total:.4f} parts={parts} selected={selected}"
    )


def _history_path(user_id: str, *, char_id: str = "yexuan") -> Path:
    uid = safe_user_id(user_id)
    return get_paths().user_memory_root(uid, char_id=char_id) / "history.json"


def _history_write_path(user_id: str, *, char_id: str = "yexuan") -> Path:
    """写路径：始终写新布局。"""
    uid = safe_user_id(user_id)
    p = get_paths().user_memory_root(uid, char_id=char_id) / "history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(user_id: str, *, char_id: str = "yexuan") -> list[dict]:
    """
    读取用户的短期对话历史（完整历史，不做截断）

    返回格式：[{"role": "user"/"assistant", "content": "..."}, ...]
    文件不存在时返回空列表
    """
    path = _history_path(user_id, char_id=char_id)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            # 风格脱敏：防止 history 里的塌缩样本被 ds 自模仿
            for msg in data:
                if msg.get("role") == "assistant":
                    msg["content"] = _sanitize_assistant_message(msg.get("content", ""), uid=user_id)
            return data
    except Exception as e:
        log_error("short_term.load", e)
    return []


def get_history(user_id: str, max_turns: int | None = None, *, char_id: str = "yexuan") -> list[dict]:
    """
    读取用户的短期对话历史，支持按轮数截断。

    参数：
        user_id   - 用户 QQ 号
        max_turns - 最多返回多少轮（一轮 = user + assistant 各一条）
                    None 时从 config.yaml 的 context.max_turns 读取，
                    再 fallback 到 memory.short_term_rounds，默认 20
        char_id   - 角色桶 id（默认 "yexuan"，生产调用方须显式传入）

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

    history = load(user_id, char_id=char_id)
    # 每轮 = 2 条消息（user + assistant）
    max_msgs = max_turns * 2
    return history[-max_msgs:] if len(history) > max_msgs else history


def load_for_prompt(user_id, *, budget_rounds=None, near_k=NEAR_K, char_id: str = "yexuan") -> list[dict]:
    """读取已 sanitize 的 short_term，并按 turn-group 加权选择 prompt 子集。"""
    raw = load(user_id, char_id=char_id)
    groups = _group_turns(raw)
    if budget_rounds is None:
        cfg = get_config()
        budget = cfg.get("memory", {}).get("short_term_rounds", 20)
    else:
        budget = budget_rounds
    budget = max(int(budget), 0)
    near_k = max(int(near_k), 0)

    if len(groups) <= budget:
        for group in groups:
            _log_turn_group_score(user_id, group, True)
        return raw

    near_count = min(near_k, budget)
    near_start = len(groups) - near_count
    selected_indexes = set(range(near_start, len(groups))) if near_count else set()
    remaining_budget = budget - near_count

    scored: list[tuple[float, int, dict]] = []
    for idx, group in enumerate(groups[:near_start]):
        total, parts = _score_turn_group(group)
        scored.append((total, idx, parts))

    scored.sort(key=lambda item: (-item[0], item[1]))
    for _, idx, _ in scored[:remaining_budget]:
        selected_indexes.add(idx)

    scored_parts = {idx: (total, parts) for total, idx, parts in scored}
    for idx, group in enumerate(groups):
        if idx in scored_parts:
            total, parts = scored_parts[idx]
            _log_turn_group_score(user_id, group, idx in selected_indexes, total=total, parts=parts)
        else:
            _log_turn_group_score(user_id, group, idx in selected_indexes)

    selected: list[dict] = []
    for idx, group in enumerate(groups):
        if idx in selected_indexes:
            selected.extend(group)
    return selected


def append(user_id: str, role: str, content: str, turn_id: str | None = None, *, char_id: str = "yexuan") -> bool:
    """
    追加一条消息到历史记录，并裁剪到最大轮数

    role: "user" 或 "assistant"
    每两条（一问一答）算一轮，实际保留 short_term_rounds * 2 条消息
    turn_id 来自 fixation_pipeline.capture_turn，写入 _turn_id 字段供血缘追踪
    char_id 决定写入哪个角色桶（默认 "yexuan"）
    """
    cfg = get_config()
    max_rounds = cfg.get("memory", {}).get("short_term_disk_rounds", cfg.get("memory", {}).get("short_term_rounds", 20))
    max_msgs = max_rounds * 2  # 每轮 = user + assistant

    history = load(user_id, char_id=char_id)
    if turn_id and any(
        item.get("_turn_id") == turn_id and item.get("role") == role
        for item in history
    ):
        return True

    entry: dict = {"role": role, "content": content, "timestamp": time.time()}
    if turn_id:
        entry["_turn_id"] = turn_id
    history.append(entry)

    # 超出上限时，从头部移除最早的消息
    if len(history) > max_msgs:
        history = history[-max_msgs:]

    return _save(user_id, history, char_id=char_id)


def _save(user_id: str, history: list[dict], *, char_id: str = "yexuan") -> bool:
    """把历史记录写回磁盘"""
    path = _history_write_path(user_id, char_id=char_id)
    try:
        return safe_write_json(path, history)
    except Exception as e:
        log_error("short_term._save", e)
        return False


def clear(user_id: str, *, char_id: str = "yexuan"):
    """清空指定用户的短期历史（admin 用）"""
    _save(user_id, [], char_id=char_id)


class ShortTermMemory:
    """短期记忆类，封装模块级函数，供外部按类方式导入使用"""

    def load(self, user_id: str, *, char_id: str = "yexuan") -> list[dict]:
        return load(user_id, char_id=char_id)

    def get_history(self, user_id: str, max_turns: int | None = None, *, char_id: str = "yexuan") -> list[dict]:
        return get_history(user_id, max_turns, char_id=char_id)

    def append(self, user_id: str, role: str, content: str, turn_id: str | None = None, *, char_id: str = "yexuan"):
        append(user_id, role, content, turn_id=turn_id, char_id=char_id)

    def clear(self, user_id: str, *, char_id: str = "yexuan"):
        clear(user_id, char_id=char_id)
