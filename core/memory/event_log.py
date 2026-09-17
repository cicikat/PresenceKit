"""
不可变事件日志系统
─────────────────────────────────────────────────────
每次对话结束后，把"用户说了什么、角色回了什么"追加到
按天分割的 Markdown 日志文件里，永不修改已有内容。

存储结构：
  data/runtime/memory/{char_id}/{user_id}/event_log/{date}.md  ← canonical 写入与读取
  data/event_log/{user_id}/{date}.md                          ← 旧 uid-only 兼容读
  data/runtime/memory/global/{user_id}/legacy_event_log_owner.json
      ← 冻结的历史默认角色（首次兼容读时写入，不随 active / character.default 改认领）

日志格式（每次对话块）：
  ## 14:23
  **用户**：我今天很累
  **角色**：（走过来把外套搭在你肩上）先坐着
  > emotion:gentle intensity:1
  ---
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.sandbox import get_paths, safe_user_id
from core.data_paths import DEFAULT_CHAR_ID
from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)


_HIGH_INTENSITY_WORDS = {"心疼", "难过", "哭", "气死", "开心", "喜欢", "想你", "爱你"}
_MED_INTENSITY_WORDS  = {"想", "记得", "担心", "等你", "在意"}

_TURN_ID_RE = re.compile(r"turn_id:(\S+)")
_SPEAKER_META_RE = re.compile(r"^>\s*.*\bspeaker:(\w+)")


def _legacy_event_log_dir(user_id: str) -> Path:
    """物理 uid-only 旧目录。不表示任何角色对该目录有读取资格。"""
    return get_paths()._p("event_log") / safe_user_id(user_id)


def _legacy_owner_record_path(user_id: str) -> Path:
    """Per-owner freeze of which character may read the uid-only tree."""
    return get_paths()._p("runtime", "memory", "global", safe_user_id(user_id), "legacy_event_log_owner.json")


def _configured_default_char_id() -> str:
    """Live `character.default`. Distinct from the frozen historical owner."""
    from core.config_loader import get_config

    default = str((get_config().get("character") or {}).get("default") or "").strip()
    return default or DEFAULT_CHAR_ID


def _read_frozen_legacy_owner(user_id: str) -> str | None:
    path = _legacy_owner_record_path(user_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log_error("event_log.legacy_owner.read", e)
        return None
    if not isinstance(payload, dict):
        return None
    owner = str(payload.get("char_id") or "").strip()
    return owner or None


def _freeze_legacy_owner(user_id: str, char_id: str) -> str:
    """Persist historical default ownership once. Never reassigns on later switches."""
    require_character_id(char_id)
    existing = _read_frozen_legacy_owner(user_id)
    if existing:
        return existing
    record = {
        "char_id": char_id,
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "source": "configured_default_at_first_compatible_read",
    }
    if not safe_write_json(_legacy_owner_record_path(user_id), record, keep_bak=False):
        logger.warning(
            "[event_log] failed to freeze legacy owner uid=%s char=%s; using in-memory claim only",
            user_id,
            char_id,
        )
    return char_id


def historical_legacy_event_log_char_id(user_id: str) -> str | None:
    """Return the frozen historical default character for this owner's uid-only logs.

    Distinct from:
      - live `character.default` (may change after freeze)
      - current active character (never used to claim old data)
    Returns None when this owner has no uid-only event_log tree.
    """
    uid = safe_user_id(user_id)
    if not _legacy_event_log_dir(uid).is_dir():
        return _read_frozen_legacy_owner(uid)
    frozen = _read_frozen_legacy_owner(uid)
    if frozen:
        return frozen
    return _freeze_legacy_owner(uid, _configured_default_char_id())


def may_read_legacy_event_log(user_id: str, char_id: str) -> bool:
    """True only for the frozen historical default character of this owner."""
    require_character_id(char_id)
    owner = historical_legacy_event_log_char_id(user_id)
    return owner is not None and owner == char_id


def _legacy_read_dir_if_eligible(user_id: str, char_id: str) -> Path | None:
    """Return the uid-only directory only when this character is the frozen owner."""
    if not may_read_legacy_event_log(user_id, char_id):
        return None
    old = _legacy_event_log_dir(user_id)
    return old if old.is_dir() else None


def _event_log_write_dir(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """写目录：始终写新布局 runtime/memory/{char_id}/{uid}/event_log/。"""
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(user_id), char_id)
    return resolve_path(scope, "event_log")


def _event_log_read_dir(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """读目录：始终返回 canonical 桶。uid-only 兼容由 _legacy_read_dir_if_eligible 单独授权。"""
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    return resolve_path(MemoryScope.reality_scope(uid, char_id), "event_log")


def _day_file_read(user_id: str, date: datetime, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """读：指定日期的 canonical 日文件。旧路径仅在该角色有资格时作为不存在时的兼容源。"""
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    date_str = date.strftime("%Y-%m-%d")
    new = resolve_path(MemoryScope.reality_scope(uid, char_id), "event_log") / f"{date_str}.md"
    if new.exists():
        return new
    old_dir = _legacy_read_dir_if_eligible(uid, char_id)
    if old_dir is not None:
        return old_dir / f"{date_str}.md"
    return new


def _day_file_write(user_id: str, date: datetime, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """写：指定日期日志文件，始终写新布局，保证目录存在。"""
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(user_id), char_id)
    d = resolve_path(scope, "event_log")
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{date.strftime('%Y-%m-%d')}.md"


def _full_log_file_write(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """写：full_log.md，始终写新布局。"""
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(user_id), char_id)
    d = resolve_path(scope, "event_log")
    d.mkdir(parents=True, exist_ok=True)
    return d / "full_log.md"


def _ensure_dir(user_id: str, *, char_id: str = DEFAULT_CHAR_ID):
    """确保用户日志写入目录存在（写新布局）。"""
    _event_log_write_dir(user_id, char_id=char_id).mkdir(parents=True, exist_ok=True)


def _calc_intensity(content: str, emotion: str) -> int:
    if any(w in content for w in _HIGH_INTENSITY_WORDS):
        intensity = 2
    elif any(w in content for w in _MED_INTENSITY_WORDS):
        intensity = 1
    else:
        intensity = 0
    if emotion != "neutral" and intensity == 0:
        intensity = 1
    return intensity


def _clip_sentence(text: str, limit: int = 60) -> str:
    """在 limit 字符内按句末标点截断；无标点则硬截并加省略号。"""
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    for punct in "。！？；":
        idx = clipped.rfind(punct)
        if idx > 0:
            return clipped[:idx + 1]
    return clipped + "…"


def _parse_intensity(block_lines: list) -> int:
    """从块行列表里读取 > emotion: 行的 intensity 值，没有则返回 0"""
    for line in reversed(block_lines):
        stripped = line.strip()
        if stripped.startswith("> emotion:"):
            for part in stripped.split():
                if part.startswith("intensity:"):
                    try:
                        return int(part.split(":")[1])
                    except (ValueError, IndexError):
                        pass
    return 0


def _split_blocks(text: str) -> list:
    """把日志文本按 ## HH:MM 时间块切分，返回 list[list[str]]"""
    from core.memory.event_log_source import split_blocks
    return split_blocks(text)


def _block_key(block_lines: list) -> str:
    """块级去重键：优先用 turn_id，否则用有效行拼接。"""
    for line in block_lines:
        m = _TURN_ID_RE.search(line)
        if m:
            return f"turn_id:{m.group(1)}"
    sig = [
        line.strip()
        for line in block_lines
        if line.strip() and line.strip() != "---" and not line.strip().startswith("> emotion:")
    ]
    return "\n".join(sig)


def _merge_day_texts(text_a: str, text_b: str) -> str:
    """合并同一天两处路径的日志文本，按时间排序并去重。"""
    seen: set = set()
    merged: list = []

    for block in _split_blocks(text_a) + _split_blocks(text_b):
        key = _block_key(block)
        if key and key not in seen:
            seen.add(key)
            merged.append(block)

    def _block_time(block: list) -> str:
        first = block[0] if block else ""
        return first[3:].strip() if first.startswith("## ") else ""

    merged.sort(key=_block_time)
    return "\n".join("\n".join(block) for block in merged)


def _read_day_union(new_dir: Path, old_dir: Path | None, date_str: str) -> str:
    """
    Union 读取 canonical 桶与（仅当调用方已授权）uid-only 旧目录中同一天的日志。
    只匹配 YYYY-MM-DD.md，不读 .gz 归档。old_dir 为 None 时不读旧树。
    """
    new_file = new_dir / f"{date_str}.md"
    old_file = (old_dir / f"{date_str}.md") if old_dir is not None else None

    text_new = ""
    text_old = ""
    try:
        if new_file.exists():
            text_new = new_file.read_text(encoding="utf-8").strip()
    except Exception as e:
        log_error("event_log._read_day_union.new", e)
    try:
        if old_file is not None and old_file.exists():
            text_old = old_file.read_text(encoding="utf-8").strip()
    except Exception as e:
        log_error("event_log._read_day_union.old", e)

    if text_new and text_old:
        return _merge_day_texts(text_new, text_old)
    return text_new or text_old


def append(
    user_id: str,
    role: str,
    content: str,
    emotion: str = "neutral",
    intensity: int = 0,
    turn_id: str | None = None,
    trigger_name: str = "",
    *,
    char_id: str = DEFAULT_CHAR_ID,
    source: str = "",
) -> bool:
    """
    追加一条对话记录到当天日志和 full_log.md。
    永不修改已有内容，只追加。

    参数：
        user_id      - 用户 QQ 号
        role         - "user" 或 "assistant"
        content      - 消息内容
        emotion      - 情绪标签（仅 assistant 有效）
        intensity    - 情绪强度覆盖（0-2），传入时不再自动计算
        turn_id      - 来自 fixation_pipeline.capture_turn 的血缘 ID（可选）
        trigger_name - scheduler 触发源名（非空时追加 trigger: 字段到 meta，仅 assistant 有效）
        char_id      - 决定写入哪个角色桶（默认 "yexuan"）
        source       - 外部信息来源标记（受控值：web / dream_echo / coplay；空 = 普通轮）。
                       非空时追加 source: 字段到 meta（user/assistant 行同步）。Brief 79 §1：
                       堵住 event_log_salvage 抢救链绕过固化隔离的通路，见 event_log_salvage
                       的过滤逻辑。
    """
    from core.config_loader import _char_name
    char_name = _char_name()
    role_label = "用户" if role == "user" else char_name

    now = datetime.now()
    time_str = now.strftime("%H:%M")

    line = f"**{role_label}**：{content}\n"
    header = f"\n## {time_str}\n" if role == "user" or trigger_name else ""

    if role == "assistant":
        _intensity = _calc_intensity(content, emotion)
        meta = f"> emotion:{emotion} intensity:{_intensity} speaker:assistant"
        if turn_id:
            meta += f" turn_id:{turn_id}"
        if trigger_name:
            meta += f" trigger:{trigger_name}"
        if source:
            meta += f" source:{source}"
        footer = meta + "\n---\n"
    else:
        _meta = "> speaker:user"
        if turn_id:
            _meta += f" turn_id:{turn_id}"
        if source:
            _meta += f" source:{source}"
        footer = _meta + "\n"

    chunk = header + line + footer

    try:
        _ensure_dir(user_id, char_id=char_id)

        day_path = _day_file_write(user_id, now, char_id=char_id)
        if not _already_appended(day_path, line, turn_id):
            with open(day_path, "a", encoding="utf-8") as f:
                f.write(chunk)

        full_path = _full_log_file_write(user_id, char_id=char_id)
        if not _already_appended(full_path, line, turn_id):
            with open(full_path, "a", encoding="utf-8") as f:
                f.write(chunk)
        return True

    except Exception as e:
        log_error("event_log.append", e)
        from core import silent_failure
        silent_failure.note("event_log.append", e)
        return False


def _already_appended(path: Path, line: str, turn_id: str | None) -> bool:
    if not turn_id or not path.exists():
        return False
    try:
        text = path.read_text(encoding="utf-8")
        return line in text and f"turn_id:{turn_id}" in text
    except Exception:
        return False


def get_recent_days(
    user_id: str,
    days: int = 3,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    since_ts: float | None = None,
    until_ts: float | None = None,
) -> str:
    """
    读取最近 N 天的日志原文，拼接成一个字符串返回。
    始终读 canonical 桶 memory/{char_id}/{uid}/event_log/。
    仅冻结的历史默认角色可额外 union 旧路径 event_log/{uid}/（按 turn_id 或全行去重）。
    只读按天分割的 YYYY-MM-DD.md 文件，不读 full_log.md 和 .gz 归档。

    参数：
        user_id - 用户 QQ 号
        days    - 往前读几天（含今天），默认 3；since_ts/until_ts 非 None 时忽略此参数
        since_ts / until_ts - Brief 48：非 None 时按这个日期范围（半开区间，本地时区）
          只扫范围内的日文件，不再看 days 参数——查询侧时间意图场景下顺带省 IO。

    返回：
        拼接后的日志文本，空则返回空字符串
    """
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    new_dir = resolve_path(scope, "event_log")
    old_dir = _legacy_read_dir_if_eligible(uid, char_id)

    today = datetime.now()

    if since_ts is not None or until_ts is not None:
        start_date = datetime.fromtimestamp(since_ts).date() if since_ts is not None else today.date()
        # until_ts 是排他上界，取范围内最后一天要减掉 1 秒再取日期，避免多扫一天。
        end_date = (
            datetime.fromtimestamp(until_ts - 1).date() if until_ts is not None else today.date()
        )
        day_list = []
        d = start_date
        while d <= end_date:
            day_list.append(d)
            d += timedelta(days=1)
    else:
        day_list = [(today - timedelta(days=i)).date() for i in range(days)][::-1]

    parts = []
    for target_day in day_list:
        date_str = target_day.strftime("%Y-%m-%d")
        try:
            text = _read_day_union(new_dir, old_dir, date_str)
            if text:
                parts.append(f"# {date_str}\n{text}")
        except Exception as e:
            log_error("event_log.get_recent_days", e)

    return "\n\n".join(parts)


async def search(
    user_id: str,
    query: str,
    llm_client=None,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    return_trace: bool = False,
    query_vec: list | None = None,
    since_ts: float | None = None,
    until_ts: float | None = None,
) -> str | tuple:
    recent_text = get_recent_days(
        user_id, days=30, char_id=char_id, since_ts=since_ts, until_ts=until_ts
    )
    if not recent_text:
        return ("", []) if return_trace else ""
    from core.memory.event_log_source import filter_recallable_text
    recent_text, _source_filtered = filter_recallable_text(recent_text)
    if not recent_text.strip():
        return ("", []) if return_trace else ""

    from core.text_match import ngram_tokens
    from core.config_loader import _char_name
    q = query.strip()
    keywords = ngram_tokens(q)

    if not keywords:
        return ("", []) if return_trace else ""

    # ── X2: overall event-log semantic similarity (single blob per user) ──────
    _el_sem_sim = 0.0
    if query_vec is not None:
        try:
            from core.memory import vector_store as _vs
            from core.memory.vector_store import dist_to_sim as _d2s
            _el_hits = await _vs.query_async(user_id, char_id, query_vec, k=1, sources=["event_log"])
            if _el_hits and str(_el_hits[0][0]).startswith("recent-safe-v2:"):
                _el_sem_sim = _d2s(_el_hits[0][1])
        except Exception as _se:
            logger.debug("[event_log.search] semantic lookup failed: %s", _se)

    from core.memory.vector_store import score_recall as _score_recall

    char_name = _char_name()
    from core.memory.user_facts import get_user_pronoun as _get_pronoun
    _user_pronoun = _get_pronoun(user_id)
    _ROLE_PREFIX_RE = re.compile(
        rf"^\*\*(用户|{re.escape(char_name)})\*\*[:：](.*)$"
    )

    today = datetime.now().date()
    matched: list = []

    current_date = today
    for section in recent_text.split("\n# "):
        if not section.strip():
            continue
        lines = section.splitlines()
        header = lines[0].strip().lstrip("# ").strip()
        try:
            current_date = datetime.strptime(header, "%Y-%m-%d").date()
        except ValueError:
            pass
        days_ago = (today - current_date).days
        decay = 1 / (days_ago + 1)

        for block in _split_blocks("\n".join(lines[1:])):
            intensity = _parse_intensity(block)
            turn_match = next((_TURN_ID_RE.search(line) for line in block if _TURN_ID_RE.search(line)), None)
            turn_id = turn_match.group(1) if turn_match else ""

            # 改动1: 7天外仅保留 intensity>=1 的块
            if days_ago > 7 and intensity < 1:
                continue

            # intensity 归一化到 [0,1]（原始 0/1/2 量纲），供 score_recall 统一量纲
            strength_norm = min(intensity / 2.0, 1.0)

            # P1-1: speaker 元字段优先归属；旧 block 无 speaker 元行时退回 prefix+继承
            _has_speaker_meta = any(_SPEAKER_META_RE.match(l.strip()) for l in block)
            if _has_speaker_meta:
                _pending: list = []
                for line in block:
                    s = line.strip()
                    if not s or s.startswith("#") or s == "---":
                        continue
                    m_meta = _SPEAKER_META_RE.match(s)
                    if m_meta:
                        _seg_role = "user" if m_meta.group(1) == "user" else "assistant"
                        for body in _pending:
                            hit = sum(1 for kw in keywords if kw in body)
                            if hit > 0:
                                relevance = hit / max(len(keywords), 1)
                                matched.append((_score_recall(_el_sem_sim, relevance, strength_norm, decay), _seg_role, _clip_sentence(body, 60), days_ago, turn_id))
                        _pending = []
                    elif s.startswith(">"):
                        continue
                    else:
                        m_pref = _ROLE_PREFIX_RE.match(s)
                        body = m_pref.group(2).strip() if m_pref else s
                        if body:
                            _pending.append(body)
            else:
                # 旧 block：prefix+继承（P0-1 行为）
                cur_role = "assistant"
                for line in block:
                    s = line.strip()
                    if not s or s.startswith("#") or s == "---" or s.startswith(">"):
                        continue
                    m_role = _ROLE_PREFIX_RE.match(s)
                    if m_role:
                        cur_role = "user" if m_role.group(1) == "用户" else "assistant"
                        body = m_role.group(2).strip()
                    else:
                        body = s
                    if not body:
                        continue
                    hit = sum(1 for kw in keywords if kw in body)
                    if hit > 0:
                        relevance = hit / max(len(keywords), 1)
                        matched.append((_score_recall(_el_sem_sim, relevance, strength_norm, decay), cur_role, _clip_sentence(body, 60), days_ago, turn_id))

    def _render_card(role: str, text: str, days_ago: int) -> str:
        coarse = (
            "今天" if days_ago == 0 else
            "昨天" if days_ago == 1 else
            "前几天" if days_ago < 7 else
            f"约{days_ago}天前"
        )
        who = f"{_user_pronoun}提到" if role == "user" else f"{char_name}当时说"
        return f"（{coarse}）{who}：{text}"

    matched.sort(key=lambda x: x[0], reverse=True)
    MIN_SCORE = 0.3  # recalibrated for X2 fusion formula (w_sem+w_kw+w_str=1.0 range)
    selected = [(s, r, t, d, turn_id) for s, r, t, d, turn_id in matched[:5] if s >= MIN_SCORE]
    result_str = "\n".join(_render_card(r, t, d) for _, r, t, d, _turn_id in selected) if selected else ""
    if return_trace:
        trace_items = [
            {"score": round(s, 4), "role": r, "snippet": t[:80], "event_day": d, "turn_id": turn_id}
            for s, r, t, d, turn_id in selected
        ]
        return result_str, trace_items
    return result_str


def get_highlights(user_id: str, days: int = 2, max_lines: int = 5, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    """
    从最近N天日志里提取有内容密度的片段，供碎碎念使用。
    优先选：包含具体事物/情感词的用户发言，跳过纯短句和系统行。
    角色回复 intensity >= 2 的块额外加分。
    """
    recent_text = get_recent_days(user_id, days=days, char_id=char_id)
    if not recent_text:
        return ""

    _EMOTION_HINTS = {"好", "累", "难", "开心", "烦", "怕", "喜欢", "讨厌", "想", "忘", "哭", "笑", "气", "愁"}

    candidates = []
    for block in _split_blocks(recent_text):
        intensity = _parse_intensity(block)
        for line in block:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped == "---" or stripped.startswith("> emotion:"):
                continue
            if not stripped.startswith("**用户**"):
                continue
            content = stripped.replace("**用户**：", "").strip()
            if len(content) < 6:
                continue
            score = sum(1 for w in _EMOTION_HINTS if w in content)
            if len(content) > 15:
                score += 1
            if intensity >= 2:
                score += 2
            candidates.append((score, content))

    candidates.sort(key=lambda x: x[0], reverse=True)
    selected = [c for _, c in candidates[:max_lines]]
    return "；".join(selected) if selected else ""


def list_days(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> list[str]:
    """列出该用户/角色下所有存在按天日志文件的日期（YYYY-MM-DD），按日期降序。

    canonical 桶始终计入。uid-only 旧目录仅冻结的历史默认角色可 union。
    只统计按天分割文件（不含 full_log.md / .gz 归档），供管理面板浏览后按需 DELETE。
    """
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    new_dir = _event_log_read_dir(user_id, char_id=char_id)
    old_dir = _legacy_read_dir_if_eligible(uid, char_id)
    dates: set[str] = set()
    for d in (new_dir, old_dir):
        if d is None:
            continue
        try:
            if d.is_dir():
                for f in d.glob("*.md"):
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem):
                        dates.add(f.stem)
        except Exception as e:
            log_error("event_log.list_days", e)
    return sorted(dates, reverse=True)


def count_real_turns(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> int:
    """统计 full_log.md 中 speaker:user 的行数：lifetime 真实用户轮数。

    不受 short_term 滑窗（20轮）、event_log 按天分片/删除影响，因为 full_log.md
    永不轮转、永不删除。仅供 identity 冷启动观测使用（identity-2，见
    docs/known-issues.md），不接入任何业务判断路径。
    canonical 桶优先；仅历史默认角色在 canonical 缺失时可读 uid-only full_log.md。
    """
    path = _event_log_read_dir(user_id, char_id=char_id) / "full_log.md"
    if not path.exists():
        old_dir = _legacy_read_dir_if_eligible(user_id, char_id)
        path = (old_dir / "full_log.md") if old_dir is not None else path
    if not path.exists():
        return 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        log_error("event_log.count_real_turns", e)
        return 0
    count = 0
    for line in lines:
        m = _SPEAKER_META_RE.match(line.strip())
        if m and m.group(1) == "user":
            count += 1
    return count


def delete_day(user_id: str, date_str: str, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """Delete (unlink) the YYYY-MM-DD.md file for a given day.

    Only removes the new-layout file; does not touch old-layout path or full_log.md.
    Returns True if the file existed and was removed, False if not found.
    """
    try:
        scope = MemoryScope.reality_scope(safe_user_id(user_id), char_id)
        day_file = resolve_path(scope, "event_log") / f"{date_str}.md"
        if not day_file.exists():
            return False
        day_file.unlink()
        logger.info("[event_log] deleted day file date=%s uid=%s char=%s", date_str, user_id, char_id)
        return True
    except Exception as e:
        log_error("event_log.delete_day", e)
        return False


def cleanup_event_log(user_id: str, *, char_id: str) -> None:
    """归档超出窗口的按天文件，并对 full_log.md 按大小滚动。
    按天文件：>= day_archive_days 天的 .md → .md.gz（search 窗口 30 天不受影响）。
    full_log.md：超过 full_log_max_size_mb → gzip 归档 + 清空。
    """
    from core.config_loader import get_config
    from core.safe_write import archive_old_day_files, rotate_jsonl_if_needed

    cfg = get_config().get("forensic_logs", {}).get("event_log", {})
    cutoff_days = int(cfg.get("day_archive_days", 30))
    require_character_id(char_id)
    dir_path = _event_log_write_dir(user_id, char_id=char_id)
    archived = archive_old_day_files(dir_path, cutoff_days=cutoff_days)
    if archived:
        logger.info("[event_log] 已归档 %d 个按天文件 (uid=%s char=%s)", archived, user_id, char_id)

    full_log = _full_log_file_write(user_id, char_id=char_id)
    max_bytes = int(cfg.get("full_log_max_size_mb", 10) * 1024 * 1024)
    keep_n = int(cfg.get("full_log_keep", 3))
    rotate_jsonl_if_needed(full_log, max_bytes=max_bytes, keep_n=keep_n)


class EventLog:
    """
    EventLog 类封装，供外部按类方式导入使用。
    所有方法都代理到模块级函数。
    """

    def append(self, user_id: str, role: str, content: str, emotion: str = "neutral", intensity: int = 0, *, char_id: str = DEFAULT_CHAR_ID):
        append(user_id, role, content, emotion=emotion, intensity=intensity, char_id=char_id)

    def get_recent_days(self, user_id: str, days: int = 3, *, char_id: str = DEFAULT_CHAR_ID) -> str:
        return get_recent_days(user_id, days, char_id=char_id)

    async def search(self, user_id: str, query: str, llm_client=None, *, char_id: str = DEFAULT_CHAR_ID) -> str:
        return await search(user_id, query, llm_client, char_id=char_id)
