"""Recall the most recent followable topic from event_log."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from core.error_handler import log_error
from core.safe_write import safe_write_json
from core.migration import for_read
from core.sandbox import get_paths, safe_user_id


# TODO(policy.yaml): move the lookback window into scheduler policy.
RECENT_EVENT_LOG_DAYS = 3

# TODO(policy.yaml): move topic-level refollow suppression into scheduler policy.
TOPIC_REFOLLOW_WINDOW_SECONDS = 3 * 24 * 3600

# TODO(policy.yaml): move filler memory-level refollow suppression into scheduler policy.
RECALL_REFOLLOW_WINDOW_SECONDS = 12 * 3600

# recent_topics soft-downweight parameters (Phase 1)
FULL_RECOVER_SECONDS = 6 * 3600   # 6h: base_decay goes 0→1 over this window
REPEAT_K = 0.3                    # repeat_penalty = 1/(1 + REPEAT_K * speak_count)
CROSS_SOURCE_RELAX = 1.3          # freshness boost when source differs from last_source
MIN_FRESHNESS = 0.05              # floor: never fully silence a topic
MAX_RECENT_TOPICS = 200           # hard cap on entries per recent_topics / shadow dict

_USER_PREFIX = "**用户**："
_DATE_RE = re.compile(r"^#\s*(\d{4}-\d{2}-\d{2})\s*$")
_TIME_RE = re.compile(r"^##\s*(\d{2}:\d{2})\s*$")
_SPEAKER_RE = re.compile(r"^\*\*(.+?)\*\*：(.*)$")
_PUNCT_RE = re.compile(r"[\s\t\r\n，。！？!?、,.；;：:\"'“”‘’（）()\[\]【】<>《》…—-]+")
_FILLER_RE = re.compile(r"^(我|她|你|叶瑄|嗯|啊|唔|那个|就是|不是|然后|所以|但是|可是)+")

_FOLLOWABLE_HINTS = (
    "最近",
    "今天",
    "明天",
    "后天",
    "之后",
    "以后",
    "未来",
    "后来",
    "还没",
    "没定",
    "进展",
    "计划",
    "准备",
    "打算",
    "正在",
    "想",
    "要",
    "会",
    "等",
    "继续",
    "实习",
    "作业",
    "考试",
    "项目",
    "功能",
    "权限",
    "部署",
    "测试",
    "修",
    "写",
    "申请",
    "面试",
    "毕业",
)

_LOW_SIGNAL_PHRASES = (
    "不许再带动作描写",
    "只允许输出对话",
)


@dataclass(frozen=True)
class LastMentionedTopic:
    topic: str
    topic_key: str
    context: str
    user_text: str
    assistant_text: str
    mentioned_at: str
    age_seconds: float
    score: float


@dataclass(frozen=True)
class _Turn:
    date: str
    time_text: str
    ts: float
    order: int
    user_text: str
    assistant_text: str
    raw: str


def _active_char_id_or_none() -> str | None:
    """Return the active character id from active_prompt_assets.json, or None if unavailable."""
    try:
        import json as _j
        data = _j.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        cid = (data.get("active_character") or "").strip()
        return cid or None
    except Exception:
        return None


def recall_last_mentioned(
    user_id: str,
    *,
    now: datetime | None = None,
    days: int = RECENT_EVENT_LOG_DAYS,
    dry_run: bool = False,
    char_id: str | None = None,
) -> LastMentionedTopic | None:
    """Return the best last-mentioned event_log topic, ranked by recency × freshness.

    char_id 未传时尝试读 active_prompt_assets.json；仍无则返回 None（不 fallback yexuan）。
    """
    resolved = char_id or _active_char_id_or_none()
    if not resolved:
        return None

    now_dt = now or datetime.now()
    text = _read_recent_event_log(user_id, days=days, now=now_dt, char_id=resolved)
    if not text.strip():
        return None

    candidates = [
        topic
        for turn in _parse_event_log_turns(text)
        if (topic := _topic_from_turn(turn, now_dt)) is not None
    ]
    ordered = _rank_last_mentioned_candidates(candidates, now=now_dt, dry_run=dry_run)
    return ordered[0] if ordered else None


def is_recently_followed(
    topic_key: str,
    *,
    now_ts: float | None = None,
    window_seconds: int = TOPIC_REFOLLOW_WINDOW_SECONDS,
    shadow: bool = False,
) -> bool:
    if not topic_key:
        return False
    return _is_recently_in_state(
        _topic_state_key(shadow),
        topic_key,
        now_ts=now_ts,
        window_seconds=window_seconds,
    )


def mark_topic_followed(topic_key: str, *, now_ts: float | None = None) -> None:
    _mark_state_map(
        "followed_topics",
        topic_key,
        now_ts=now_ts,
        prune_window_seconds=TOPIC_REFOLLOW_WINDOW_SECONDS,
    )
    _now = datetime.fromtimestamp(now_ts) if now_ts is not None else None
    mark_recent_topic(topic_key, "followup", now=_now)


def mark_topic_followed_shadow(topic_key: str, *, now_ts: float | None = None) -> None:
    _mark_state_map(
        "followed_topics_shadow",
        topic_key,
        now_ts=now_ts,
        prune_window_seconds=TOPIC_REFOLLOW_WINDOW_SECONDS,
    )
    _now = datetime.fromtimestamp(now_ts) if now_ts is not None else None
    mark_recent_topic(topic_key, "followup", now=_now, dry_run=True)


def load_followed_topics() -> dict[str, float]:
    return _load_state_map("followed_topics")


def load_followed_topics_shadow() -> dict[str, float]:
    return _load_state_map("followed_topics_shadow")


def is_recently_recalled(
    memory_key: str,
    *,
    now_ts: float | None = None,
    window_seconds: int = RECALL_REFOLLOW_WINDOW_SECONDS,
    shadow: bool = False,
) -> bool:
    if not memory_key:
        return False
    return _is_recently_in_state(
        _memory_state_key(shadow),
        memory_key,
        now_ts=now_ts,
        window_seconds=window_seconds,
    )


def mark_memory_recalled(memory_key: str, *, now_ts: float | None = None) -> None:
    _mark_state_map(
        "recalled_memories",
        memory_key,
        now_ts=now_ts,
        prune_window_seconds=RECALL_REFOLLOW_WINDOW_SECONDS,
    )


def mark_memory_recalled_shadow(memory_key: str, *, now_ts: float | None = None) -> None:
    _mark_state_map(
        "recalled_memories_shadow",
        memory_key,
        now_ts=now_ts,
        prune_window_seconds=RECALL_REFOLLOW_WINDOW_SECONDS,
    )


def load_recalled_memories() -> dict[str, float]:
    return _load_state_map("recalled_memories")


def load_recalled_memories_shadow() -> dict[str, float]:
    return _load_state_map("recalled_memories_shadow")


def compute_topic_freshness(
    topic_key: str,
    source: str,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> float:
    """Return a [MIN_FRESHNESS, 1.0] weight for topic_key under the given source.

    New topics return 1.0. Recently spoken topics are dampened; cross-source
    calls get a CROSS_SOURCE_RELAX boost so the same topic can surface via a
    different trigger sooner.
    """
    if not topic_key:
        return 1.0
    now_dt = now or datetime.now()
    state_key = _recent_topics_key(dry_run)
    recent = _read_scheduler_state().get(state_key, {})
    if not isinstance(recent, dict):
        return 1.0
    entry = recent.get(topic_key)
    if not isinstance(entry, dict):
        return 1.0
    try:
        last_spoken_at = datetime.fromisoformat(str(entry["last_spoken_at"])).timestamp()
        speak_count = max(0, int(entry.get("speak_count", 0)))
        last_source = str(entry.get("last_source", ""))
    except (KeyError, ValueError, TypeError):
        return 1.0
    elapsed = now_dt.timestamp() - last_spoken_at
    base_decay = min(1.0, max(0.0, elapsed / FULL_RECOVER_SECONDS))
    repeat_penalty = 1.0 / (1.0 + REPEAT_K * speak_count)
    freshness = max(MIN_FRESHNESS, base_decay * repeat_penalty)
    if last_source and last_source != source:
        freshness = min(1.0, freshness * CROSS_SOURCE_RELAX)
    return freshness


def mark_recent_topic(
    topic_key: str,
    source: str,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> None:
    """Record that topic_key was spoken from source; increments speak_count."""
    if not topic_key:
        return
    now_dt = now or datetime.now()
    state_key = _recent_topics_key(dry_run)
    state = _read_scheduler_state()
    recent = state.get(state_key)
    if not isinstance(recent, dict):
        recent = {}
    entry = recent.get(topic_key)
    if not isinstance(entry, dict):
        entry = {}
    try:
        speak_count = max(0, int(entry.get("speak_count", 0)))
    except (TypeError, ValueError):
        speak_count = 0
    entry["last_spoken_at"] = now_dt.isoformat(timespec="seconds")
    entry["speak_count"] = speak_count + 1
    entry["last_source"] = source
    recent[str(topic_key)] = entry
    state[state_key] = _prune_recent_topics(recent)
    safe_write_json(get_paths().scheduler_user_state(), state)


def _is_recently_in_state(
    state_key: str,
    key: str,
    *,
    now_ts: float | None,
    window_seconds: int,
) -> bool:
    marked_at = _load_state_map(state_key).get(key)
    if marked_at is None:
        return False
    return (now_ts if now_ts is not None else time.time()) - marked_at < window_seconds


def _mark_state_map(
    state_key: str,
    key: str,
    *,
    now_ts: float | None = None,
    prune_window_seconds: int,
) -> None:
    if not key:
        return
    ts = float(now_ts if now_ts is not None else time.time())
    state = _read_scheduler_state()
    marked = state.get(state_key)
    if not isinstance(marked, dict):
        marked = {}
    marked[str(key)] = ts
    state[state_key] = _prune_state_map(
        marked,
        now_ts=ts,
        window_seconds=prune_window_seconds,
    )
    safe_write_json(get_paths().scheduler_user_state(), state)


def _load_state_map(state_key: str) -> dict[str, float]:
    marked = _read_scheduler_state().get(state_key, {})
    if not isinstance(marked, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in marked.items():
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def _topic_state_key(shadow: bool) -> str:
    return "followed_topics_shadow" if shadow else "followed_topics"


def _memory_state_key(shadow: bool) -> str:
    return "recalled_memories_shadow" if shadow else "recalled_memories"


def _recent_topics_key(dry_run: bool) -> str:
    return "recent_topics_shadow" if dry_run else "recent_topics"


def topic_key_for(topic: str) -> str:
    normalized = _PUNCT_RE.sub("", topic.strip().lower())
    normalized = _FILLER_RE.sub("", normalized)
    return normalized[:40]


def _rank_last_mentioned_candidates(
    candidates: list[LastMentionedTopic],
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> list[LastMentionedTopic]:
    """Rank by recency × topic freshness (descending).

    Recency is the primary signal (newer = higher); topic freshness from
    recent_topics softly demotes topics that were spoken recently without
    fully excluding them. Specificity is not factored here to preserve
    the expected time-ordering when no recent_topics are marked.
    """
    if not candidates:
        return []
    now_dt = now or datetime.now()
    _ref = float(RECENT_EVENT_LOG_DAYS * 24 * 3600)

    def _weighted(item: LastMentionedTopic) -> float:
        recency = 1.0 - min(item.age_seconds / _ref, 1.0)
        freshness = compute_topic_freshness(item.topic_key, "followup", now=now_dt, dry_run=dry_run)
        return recency * freshness

    return sorted(candidates, key=_weighted, reverse=True)


def _read_recent_event_log(user_id: str, *, days: int, now: datetime, char_id: str) -> str:
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)

    parts: list[str] = []
    for i in range(days):
        target_day = now - timedelta(days=i)
        date_str = target_day.strftime('%Y-%m-%d')
        new_path = resolve_path(scope, "event_log") / f"{date_str}.md"
        old_path = get_paths()._p("event_log") / uid / f"{date_str}.md"
        path = for_read(new_path, old_path)
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(f"# {target_day.strftime('%Y-%m-%d')}\n{text}")
        except Exception as e:
            log_error("scheduler.last_mentioned.read_event_log", e)
    parts.reverse()
    return "\n\n".join(parts)


def _parse_event_log_turns(text: str) -> list[_Turn]:
    turns: list[_Turn] = []
    current_date = ""
    current_time = ""
    current_lines: list[str] = []
    order = 0

    def flush() -> None:
        nonlocal order, current_lines
        if not current_lines:
            return
        turn = _turn_from_block(current_date, current_time, order, current_lines)
        order += 1
        current_lines = []
        if turn is not None:
            turns.append(turn)

    for line in text.splitlines():
        date_match = _DATE_RE.match(line)
        if date_match:
            flush()
            current_date = date_match.group(1)
            current_time = ""
            continue
        time_match = _TIME_RE.match(line)
        if time_match:
            flush()
            current_time = time_match.group(1)
            current_lines = [line]
            continue
        current_lines.append(line)
    flush()
    return turns


def _turn_from_block(date_text: str, time_text: str, order: int, lines: list[str]) -> _Turn | None:
    user_parts: list[str] = []
    assistant_parts: list[str] = []
    current_role = ""

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "---" or stripped.startswith(">") or _TIME_RE.match(stripped):
            continue
        speaker_match = _SPEAKER_RE.match(stripped)
        if speaker_match:
            speaker, content = speaker_match.groups()
            if speaker == "用户":
                current_role = "user"
                if content.strip():
                    user_parts.append(content.strip())
            else:
                current_role = "assistant"
                if content.strip():
                    assistant_parts.append(content.strip())
            continue
        if current_role == "user":
            user_parts.append(stripped)
        elif current_role == "assistant":
            assistant_parts.append(stripped)

    user_text = "\n".join(user_parts).strip()
    if not user_text:
        return None

    ts = _timestamp_for(date_text, time_text, order)
    return _Turn(
        date=date_text,
        time_text=time_text,
        ts=ts,
        order=order,
        user_text=user_text,
        assistant_text="\n".join(assistant_parts).strip(),
        raw="\n".join(lines).strip(),
    )


def _topic_from_turn(turn: _Turn, now: datetime) -> LastMentionedTopic | None:
    user_text = _clean_text(turn.user_text)
    if not _is_followable_user_text(user_text):
        return None
    topic = _extract_topic(user_text)
    topic_key = topic_key_for(topic)
    if not topic_key:
        return None
    age_seconds = max(0.0, now.timestamp() - turn.ts) if turn.ts > 0 else 0.0
    freshness = 1.0 - min(age_seconds / max(RECENT_EVENT_LOG_DAYS * 24 * 3600, 1), 1.0)
    specificity = min(len(topic_key) / 18, 1.0)
    score = round(max(0.0, min(1.0, 0.45 + freshness * 0.35 + specificity * 0.2)), 3)
    return LastMentionedTopic(
        topic=topic,
        topic_key=topic_key,
        context=_format_context(turn),
        user_text=user_text,
        assistant_text=_clean_text(turn.assistant_text),
        mentioned_at=f"{turn.date} {turn.time_text}".strip(),
        age_seconds=age_seconds,
        score=score,
    )


def _is_followable_user_text(text: str) -> bool:
    if len(text.strip()) < 6:
        return False
    if any(phrase in text for phrase in _LOW_SIGNAL_PHRASES):
        return False
    if any(hint in text for hint in _FOLLOWABLE_HINTS):
        return True
    return len(text) >= 14 and ("我" in text or "..." in text or "……" in text)


def _extract_topic(text: str) -> str:
    first_clause = re.split(r"[。！？!?；;\n]", text, maxsplit=1)[0].strip()
    first_clause = re.sub(r"^我(未来|最近|今天|明天|后天|之后|以后)?(不是)?(想|要|准备|打算|计划|正在)?", "", first_clause)
    first_clause = first_clause.strip(" ，。！？!?、：:；;…")
    if not first_clause:
        first_clause = text.strip()
    return first_clause[:30]


def _format_context(turn: _Turn) -> str:
    parts = [f"{turn.date} {turn.time_text}".strip(), f"用户：{_clean_text(turn.user_text)}"]
    assistant = _clean_text(turn.assistant_text)
    if assistant:
        parts.append(f"叶瑄：{assistant}")
    return "\n".join(part for part in parts if part)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _timestamp_for(date_text: str, time_text: str, order: int) -> float:
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M").timestamp() + order * 0.001
    except ValueError:
        return float(order)


def _read_scheduler_state() -> dict:
    path = get_paths().scheduler_user_state()
    if not path.exists():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        log_error("scheduler.last_mentioned.read_state", e)
        return {}
    return data if isinstance(data, dict) else {}


def _prune_state_map(
    marked: dict,
    *,
    now_ts: float | None = None,
    window_seconds: int,
) -> dict[str, float]:
    now = now_ts if now_ts is not None else time.time()
    max_age = window_seconds * 4
    result: dict[str, float] = {}
    for key, value in marked.items():
        try:
            ts = float(value)
        except (TypeError, ValueError):
            continue
        if now - ts <= max_age:
            result[str(key)] = ts
    return result


def _prune_followed_topics(followed: dict, *, now_ts: float | None = None) -> dict[str, float]:
    return _prune_state_map(
        followed,
        now_ts=now_ts,
        window_seconds=TOPIC_REFOLLOW_WINDOW_SECONDS,
    )


def _prune_recent_topics(recent: dict) -> dict:
    """Retain the MAX_RECENT_TOPICS most-recent entries; drop oldest first.

    Entries with unparseable last_spoken_at get sort key 0 and are dropped first.
    """
    if len(recent) <= MAX_RECENT_TOPICS:
        return recent

    def _ts(entry: object) -> float:
        try:
            return datetime.fromisoformat(str(entry.get("last_spoken_at", ""))).timestamp()  # type: ignore[union-attr]
        except (ValueError, TypeError, AttributeError):
            return 0.0

    sorted_items = sorted(recent.items(), key=lambda kv: _ts(kv[1]), reverse=True)
    return dict(sorted_items[:MAX_RECENT_TOPICS])
