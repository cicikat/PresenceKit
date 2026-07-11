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
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.safe_write import safe_write_json
from core.sandbox import safe_user_id
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

# 近场承载对话连续性，必须优先保留最近几轮的上下文。
NEAR_K = 10

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
# 多位角色参与同一 turn，通常比单一发言者更值得保留。
SPEAKER_DIVERSITY_WEIGHT = 0.6
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


def _default_speaker_id(role: str, char_id: str) -> str:
    """为旧调用补齐 speaker_id；owner 是单用户系统中的唯一人类发言者。"""
    if role == "user":
        return "owner"
    if role == "assistant":
        return char_id
    return role


def _speaker_id(entry: dict) -> str:
    """读取 entry 的发言人；旧数据按 role 提供稳定兼容值。"""
    value = entry.get("speaker_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return str(entry.get("role") or "unknown")


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
    # trigger_stub 是系统写入的锚点条目，信息量极低，固定评 0 分让它在远场选择中被淘汰
    if any(msg.get("_source") == "trigger_stub" for msg in group):
        return 0.0, {"trigger_stub": True}
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
    assistant_speakers = {
        _speaker_id(msg)
        for msg in group
        if msg.get("role") == "assistant"
    }
    speaker_diversity_score = (
        SPEAKER_DIVERSITY_WEIGHT if len(assistant_speakers) > 1 else 0.0
    )
    turn_id = next((msg.get("_turn_id") for msg in group if msg.get("_turn_id") is not None), None)
    ready_score = _ready_signal_bonus(turn_id) * READY_SIGNAL_WEIGHT

    parts = {
        "length": round(length_score, 4),
        "entity": round(entity_score, 4),
        "question": round(question_score, 4),
        "number_date": round(number_date_score, 4),
        "tag": round(tag_score, 4),
        "emotion": round(emotion_score, 4),
        "speaker_diversity": round(speaker_diversity_score, 4),
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


def _history_path(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(safe_user_id(user_id), char_id)
    return resolve_path(scope, "history")


def _history_write_path(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """写路径：始终写新布局。"""
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(safe_user_id(user_id), char_id)
    p = resolve_path(scope, "history")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
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
                role = str(msg.get("role") or "")
                if not isinstance(msg.get("speaker_id"), str) or not msg["speaker_id"].strip():
                    msg["speaker_id"] = _default_speaker_id(role, char_id)
                if msg.get("role") == "assistant":
                    msg["content"] = _sanitize_assistant_message(msg.get("content", ""), uid=user_id)
            return data
    except Exception as e:
        log_error("short_term.load", e)
    return []


def get_history(user_id: str, max_turns: int | None = None, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
    """
    读取用户的短期对话历史，支持按轮数截断。

    参数：
        user_id   - 用户 QQ 号
        max_turns - 最多返回多少轮（一轮 = user + assistant 各一条）
                    None 时从 config.yaml 的 memory.short_term_rounds 读取（owner），
                    再 fallback 到 context.max_turns（deprecated alias），默认 20
        char_id   - 角色桶 id（默认 "yexuan"，生产调用方须显式传入）

    返回：
        截断后的消息列表，格式同 load()
    """
    if max_turns is None:
        cfg = get_config()
        # owner: memory.short_term_rounds；context.max_turns 是 deprecated alias
        max_turns = (
            cfg.get("memory", {}).get("short_term_rounds")
            or cfg.get("context", {}).get("max_turns")  # deprecated alias
            or 20
        )

    history = load(user_id, char_id=char_id)
    groups = _group_turns(history)
    max_turns = max(int(max_turns), 0)
    if max_turns == 0:
        return []
    if len(groups) <= max_turns:
        return history
    return [entry for group in groups[-max_turns:] for entry in group]


def load_for_prompt(user_id, *, budget_rounds=None, near_k=NEAR_K, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
    """读取已 sanitize 的 short_term，并按 turn-group 加权选择 prompt 子集。"""
    raw = load(user_id, char_id=char_id)
    # trigger_stub 是系统触发锚点（内容含内部 trigger_name 明文），绝不能投影进 prompt。
    # 此前仅靠 _score_turn_group 评 0 分淘汰，但近场 NEAR_K 与 ≤budget 全量两条路径
    # 都绕过评分，导致 [触发: xxx] 被当成用户消息喂给 LLM。这里在入口统一剔除，
    # 覆盖所有下游路径；磁盘上的 stub 仍保留供记忆血缘使用（get_history 不受影响）。
    raw = [m for m in raw if m.get("_source") != "trigger_stub"]
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

    # 时间衰减：越靠近近场的组额外获得 recency bonus，防止远古高分轮挤掉次新中分轮。
    # 最老 idx=0 → bonus≈0；紧邻近场 idx=near_start-1 → bonus≈1.6。
    # 综合分 = 信息分×0.6 + 时间分×1.6，使"中等信息量但最近"与"高信息量但很旧"基本持平。
    _denom = max(near_start - 1, 1)
    scored_by_recency = sorted(
        scored,
        key=lambda item: (-(item[0] * 0.6 + (item[1] / _denom) * 1.6), item[1]),
    )
    for _, idx, _ in scored_by_recency[:remaining_budget]:
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


def append(
    user_id: str,
    role: str,
    content: str,
    turn_id: str | None = None,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    source: str | None = None,
    speaker_id: str | None = None,
) -> bool:
    """
    追加一条消息到历史记录，并裁剪到最大轮数

    role: OpenAI 兼容角色，当前 reality history 使用 "user" / "assistant"
    speaker_id: 实际发言人；user 默认 owner，assistant 默认当前 char_id
    同一 _turn_id 的所有发言算一轮，裁剪时绝不拆组
    turn_id 来自 fixation_pipeline.capture_turn，写入 _turn_id 字段供血缘追踪
    char_id 决定写入哪个角色桶（默认 "yexuan"）
    source: 可选来源标记，写入 _source 字段（如 "trigger_stub" 表示系统触发锚点）
    """
    cfg = get_config()
    max_rounds = cfg.get("memory", {}).get("short_term_disk_rounds", cfg.get("memory", {}).get("short_term_rounds", 20))
    max_rounds = max(int(max_rounds), 0)
    resolved_speaker_id = str(speaker_id or "").strip() or _default_speaker_id(role, char_id)

    history = load(user_id, char_id=char_id)
    if turn_id and any(
        item.get("_turn_id") == turn_id
        and item.get("role") == role
        and _speaker_id(item) == resolved_speaker_id
        and item.get("content") == content
        for item in history
    ):
        return True

    entry: dict = {
        "role": role,
        "speaker_id": resolved_speaker_id,
        "content": content,
        "timestamp": time.time(),
    }
    if turn_id:
        entry["_turn_id"] = turn_id
    if source:
        entry["_source"] = source
    history.append(entry)

    # 超出上限时按完整 turn-group 移除，不能截出孤儿发言。
    groups = _group_turns(history)
    if max_rounds == 0:
        history = []
    elif len(groups) > max_rounds:
        history = [item for group in groups[-max_rounds:] for item in group]

    return _save(user_id, history, char_id=char_id)


def _save(user_id: str, history: list[dict], *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """把历史记录写回磁盘"""
    path = _history_write_path(user_id, char_id=char_id)
    try:
        return safe_write_json(path, history)
    except Exception as e:
        log_error("short_term._save", e)
        from core import silent_failure
        silent_failure.note("short_term.save", e)
        return False


_FILLER_CHARS = set("嗯啊呃哦唔哈")
_FILLER_TRAILING_PUNCT = set("。，、！？…～~,.!?")


def is_filler_prefix(prefix: str) -> bool:
    """判断句首前缀是否为填充词开头（嗯/啊/呃/哦/唔/哈，可跟标点如 。，…～）。"""
    if not prefix:
        return False
    if prefix[0] not in _FILLER_CHARS:
        return False
    return all(ch in _FILLER_TRAILING_PUNCT for ch in prefix[1:])


def detect_reply_homogeneity_prefix(
    history: list[dict],
    *,
    recent_n: int = 6,
    prefix_len: int = 2,
    min_hits: int = 3,
) -> str | None:
    """
    检测近 recent_n 条 assistant 消息的句首是否高度重复，返回命中的原始前缀 P（不做任何文案包装）。
    ≥ min_hits 条共享相同 prefix_len 字句首才算命中；否则返回 None。
    供 build() 侧历史投影去同质与 pipeline 侧输出端校验重试复用，两处必须用同一份检测结果。
    """
    from collections import Counter

    assistant_msgs = [
        msg["content"].strip()
        for msg in history
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), str) and msg["content"].strip()
    ][-recent_n:]

    if len(assistant_msgs) < min_hits:
        return None

    prefixes = [m[:prefix_len] for m in assistant_msgs if len(m) >= prefix_len]
    if not prefixes:
        return None

    top_prefix, count = Counter(prefixes).most_common(1)[0]
    if count >= min_hits:
        return top_prefix
    return None


def detect_reply_homogeneity(
    history: list[dict],
    *,
    recent_n: int = 6,
    prefix_len: int = 2,
    min_hits: int = 3,
) -> str | None:
    """
    检测近 recent_n 条 assistant 消息的句首是否高度重复，返回软提示供 prompt 注入；否则返回 None。
    仅做统计，不修改 history 内容，不绕过 _sanitize_assistant_message。
    填充词前缀（嗯/啊/呃/哦/唔/哈等）命中时用不复读字面的文案，避免 prime 模型再次输出同一个词；
    其余前缀沿用引用式文案。
    """
    prefix = detect_reply_homogeneity_prefix(
        history, recent_n=recent_n, prefix_len=prefix_len, min_hits=min_hits
    )
    if not prefix:
        return None
    if is_filler_prefix(prefix):
        return (
            "（你最近几条开头都是同一个语气词，这次第一个字直接进正文——"
            "从动作、称呼或要说的事本身开始。）"
        )
    return f'（近几轮回复开头连续用了「{prefix}」，禁止以相同句首开头，自然地换个切入方式。）'


def detect_reply_length_collapse(
    history: list[dict],
    *,
    short_max: int = 60,
    recent_n_long: int = 4,
    recent_n_short: int = 7,
) -> str | None:
    """
    字数挡位简化为长/短两挡，非对称触发：短句难触发、长句易触发
    （模型爱往注水长句坍缩，短句反而更像活人；5 挡太密会限制模型）。

    - 近 recent_n_long 条全部为长句（字符数 >= short_max）→ 命中，返回长句版提示（引导收短）。
    - 否则近 recent_n_short 条全部为短句（字符数 < short_max）→ 命中，返回通用打破惯性提示。
    仅做统计，不修改 history 内容，不硬裁不硬扩输出。
    """
    assistant_msgs = [
        msg["content"].strip()
        for msg in history
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), str) and msg["content"].strip()
    ]

    recent_long = assistant_msgs[-recent_n_long:]
    if len(recent_long) >= recent_n_long and all(len(m) >= short_max for m in recent_long):
        return "（最近几条都挺长，这次收短——去掉铺垫和水词，捡最有劲的一两句说。）"

    recent_short = assistant_msgs[-recent_n_short:]
    if len(recent_short) >= recent_n_short and all(len(m) < short_max for m in recent_short):
        return "（最近几条回复长度都差不多，这次故意打破这个长度惯性——长了就收一收，短了就多说点，别再照上面的长度来。）"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# 反坍缩提示持久化倒计时（Brief 54-B）
#
# 背景：detect_reply_length_collapse() 本身无状态——每轮都是重新用 history 窗口判断，
# 一旦窗口不再满足条件（哪怕只因中间插了一条稍短的回复），提示当场撤销，模型下一轮
# 立刻弹回长文/密文。这里加一层 per (char_id, uid) 的内存倒计时：一旦触发，"继续注入
# 提示"这件事延续 hint_rounds 轮（默认 3，含触发的当轮），每轮衰减 1；倒计时期间再次
# 触发 → 重置为满值（不是叠加，也不提前清零），避免来回振荡。重启丢失可接受。
#
# 分段坍缩（新增维度）判定用的是 capture_turn() 收到的原始 reply 文本——在
# scrub_reality_output_text 和 _sanitize_assistant_message 之前，因为两者都可能吃掉
# 换行/正文，破坏"是否分段"的判定依据；历史一旦落盘重读就已经被 _sanitize_assistant_message
# 处理过，不能反过来用 history 判断分段信号。因此分段维度的信号采集（note_segment_collapse_signal）
# 和长度维度的检测+衰减（get_anti_collapse_hint）拆成两个入口，分别在生成落盘时/下一轮组
# prompt 时调用，但两个维度的倒计时衰减统一在 get_anti_collapse_hint() 里做，确保"每轮调用
# 一次就衰减一次"的节奏一致（调用方是 build()，每轮一次）。
# ─────────────────────────────────────────────────────────────────────────────

_ANTI_COLLAPSE_HINT_STATE: dict[str, dict] = {}

DEFAULT_HINT_ROUNDS = 3
DEFAULT_SEGMENT_MIN_LEN = 40
DEFAULT_SEGMENT_RECENT_N = 2

_SEGMENT_HINT_TEXT = (
    "（最近几条回复都挤成一大段没有换行，超过两句就空一行分段，别把话都堆在一起。）"
)


def _anti_collapse_state_key(user_id: str, char_id: str) -> str:
    return f"{char_id}:{user_id}"


def _get_anti_collapse_state(user_id: str, char_id: str) -> dict:
    key = _anti_collapse_state_key(user_id, char_id)
    return _ANTI_COLLAPSE_HINT_STATE.setdefault(key, {
        "length_remaining": 0,
        "length_text": "",
        "segment_remaining": 0,
        "segment_streak": 0,
    })


def reset_anti_collapse_state(user_id: str | None = None, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    """测试/调试用：清空反坍缩倒计时状态。user_id 为 None 时清空全部（所有角色/用户）。"""
    if user_id is None:
        _ANTI_COLLAPSE_HINT_STATE.clear()
        return
    _ANTI_COLLAPSE_HINT_STATE.pop(_anti_collapse_state_key(user_id, char_id), None)


def note_segment_collapse_signal(
    user_id: str,
    raw_reply: str,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    segment_min_len: int = DEFAULT_SEGMENT_MIN_LEN,
    segment_recent_n: int = DEFAULT_SEGMENT_RECENT_N,
    hint_rounds: int = DEFAULT_HINT_ROUNDS,
) -> None:
    """
    记录一轮 assistant 回复的分段坍缩信号（问题54-B·2）。

    raw_reply 必须是 _sanitize_assistant_message /scrub_reality_output_text 之前的原始文本
    （调用方：`core.memory.fixation_pipeline.capture_turn()` 收到的 `reply` 参数）。
    信号：文本不含 `\\n` 且长度 > segment_min_len 视为一次"未分段"命中；连续 segment_recent_n
    轮命中才把 segment_remaining 重置为 hint_rounds（不足 recent_n 或中途断掉只清零 streak，
    不动 segment_remaining——衰减统一由 get_anti_collapse_hint() 负责）。
    """
    state = _get_anti_collapse_state(user_id, char_id)
    text = (raw_reply or "").strip()
    is_bad = bool(text) and "\n" not in text and len(text) > segment_min_len
    state["segment_streak"] = state["segment_streak"] + 1 if is_bad else 0
    if state["segment_streak"] >= max(int(segment_recent_n), 1):
        state["segment_remaining"] = max(int(hint_rounds), 0)


def get_anti_collapse_hint(
    user_id: str,
    history: list[dict],
    *,
    char_id: str = DEFAULT_CHAR_ID,
    short_max: int = 60,
    recent_n_long: int = 4,
    recent_n_short: int = 7,
    hint_rounds: int = DEFAULT_HINT_ROUNDS,
) -> str | None:
    """
    组装本轮要注入的反坍缩提示文案（长度维度 + 分段维度合并），供 `anti_collapse_hint`
    prompt 层使用。每次 build() 调用一次——两个维度的剩余轮数都在这里衰减一次，
    衰减节奏与 prompt 构建同频；未触发任何维度时返回 None。

    长度维度：detect_reply_length_collapse() 无状态检测命中 → 计数器重置为 hint_rounds
    并记下命中文案；未命中但计数器未归零 → 沿用上一次命中的文案继续倒计时。
    分段维度：计数器由 note_segment_collapse_signal()（生成落盘时调用）驱动写入，这里只读+衰减。
    """
    state = _get_anti_collapse_state(user_id, char_id)

    triggered_text = detect_reply_length_collapse(
        history, short_max=short_max, recent_n_long=recent_n_long, recent_n_short=recent_n_short,
    )
    if triggered_text:
        state["length_remaining"] = max(int(hint_rounds), 0)
        state["length_text"] = triggered_text

    parts: list[str] = []
    if state["length_remaining"] > 0:
        if state["length_text"]:
            parts.append(state["length_text"])
        state["length_remaining"] -= 1

    if state["segment_remaining"] > 0:
        parts.append(_SEGMENT_HINT_TEXT)
        state["segment_remaining"] -= 1

    return " ".join(parts) if parts else None


def clear(user_id: str, *, char_id: str = DEFAULT_CHAR_ID):
    """清空指定用户的短期历史（admin 用）"""
    _save(user_id, [], char_id=char_id)


class ShortTermMemory:
    """短期记忆类，封装模块级函数，供外部按类方式导入使用"""

    def load(self, user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
        return load(user_id, char_id=char_id)

    def get_history(self, user_id: str, max_turns: int | None = None, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
        return get_history(user_id, max_turns, char_id=char_id)

    def append(
        self,
        user_id: str,
        role: str,
        content: str,
        turn_id: str | None = None,
        *,
        char_id: str = DEFAULT_CHAR_ID,
        source: str | None = None,
        speaker_id: str | None = None,
    ) -> bool:
        return append(
            user_id,
            role,
            content,
            turn_id=turn_id,
            char_id=char_id,
            source=source,
            speaker_id=speaker_id,
        )

    def clear(self, user_id: str, *, char_id: str = DEFAULT_CHAR_ID):
        clear(user_id, char_id=char_id)
