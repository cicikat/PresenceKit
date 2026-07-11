"""
不可变事件日志系统
─────────────────────────────────────────────────────
每次对话结束后，把"用户说了什么、角色回了什么"追加到
按天分割的 Markdown 日志文件里，永不修改已有内容。

存储结构：
  data/event_log/{user_id}/2026-04-15.md   ← AI 读取（按天）
  data/event_log/{user_id}/full_log.md     ← 供用户导出，AI 不读

日志格式（每次对话块）：
  ## 14:23
  **用户**：我今天很累
  **角色**：（走过来把外套搭在你肩上）先坐着
  > emotion:gentle intensity:1
  ---
"""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.migration import for_read
from core.sandbox import get_paths, safe_user_id
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)


_HIGH_INTENSITY_WORDS = {"心疼", "难过", "哭", "气死", "开心", "喜欢", "想你", "爱你"}
_MED_INTENSITY_WORDS  = {"想", "记得", "担心", "等你", "在意"}

_TURN_ID_RE = re.compile(r"turn_id:(\S+)")
_SPEAKER_META_RE = re.compile(r"^>\s*.*\bspeaker:(\w+)")


def _event_log_write_dir(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """写目录：始终写新布局 runtime/memory/{char_id}/{uid}/event_log/。"""
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(user_id), char_id)
    return resolve_path(scope, "event_log")


def _event_log_read_dir(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """读目录：新目录存在时读新，否则降级旧路径。"""
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    new = resolve_path(scope, "event_log")
    old = get_paths()._p("event_log") / uid
    # for_read() reads bytes — unsuitable for directories; check with is_dir() instead.
    return new if new.is_dir() else old


def _day_file_read(user_id: str, date: datetime, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """读：指定日期日志文件，新存在读新，否则降级旧路径。"""
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    date_str = date.strftime("%Y-%m-%d")
    scope = MemoryScope.reality_scope(uid, char_id)
    new = resolve_path(scope, "event_log") / f"{date_str}.md"
    old = get_paths()._p("event_log") / uid / f"{date_str}.md"
    return for_read(new, old)


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
    blocks: list = []
    current: list = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


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


def _read_day_union(new_dir: Path, old_dir: Path, date_str: str) -> str:
    """
    Union 读取新旧两处目录中同一天的日志文件。
    只匹配 YYYY-MM-DD.md，不读 .gz 归档。
    """
    new_file = new_dir / f"{date_str}.md"
    old_file = old_dir / f"{date_str}.md"

    text_new = ""
    text_old = ""
    try:
        if new_file.exists():
            text_new = new_file.read_text(encoding="utf-8").strip()
    except Exception as e:
        log_error("event_log._read_day_union.new", e)
    try:
        if old_file.exists():
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
    """
    from core.config_loader import _char_name
    char_name = _char_name()
    role_label = "用户" if role == "user" else char_name

    now = datetime.now()
    time_str = now.strftime("%H:%M")

    line = f"**{role_label}**：{content}\n"
    header = f"\n## {time_str}\n" if role == "user" else ""

    if role == "assistant":
        _intensity = _calc_intensity(content, emotion)
        meta = f"> emotion:{emotion} intensity:{_intensity} speaker:assistant"
        if turn_id:
            meta += f" turn_id:{turn_id}"
        if trigger_name:
            meta += f" trigger:{trigger_name}"
        footer = meta + "\n---\n"
    else:
        _meta = "> speaker:user"
        if turn_id:
            _meta += f" turn_id:{turn_id}"
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
    同时读取新路径 memory/{char_id}/{uid}/event_log/ 与旧路径 event_log/{uid}/，
    对每天的内容做 union 合并（按 turn_id 或全行去重）。
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
    old_dir = get_paths()._p("event_log") / uid

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
            if _el_hits:
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
                                matched.append((_score_recall(_el_sem_sim, relevance, strength_norm, decay), _seg_role, _clip_sentence(body, 60), days_ago))
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
                        matched.append((_score_recall(_el_sem_sim, relevance, strength_norm, decay), cur_role, _clip_sentence(body, 60), days_ago))

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
    selected = [(s, r, t, d) for s, r, t, d in matched[:5] if s >= MIN_SCORE]
    result_str = "\n".join(_render_card(r, t, d) for _, r, t, d in selected) if selected else ""
    if return_trace:
        trace_items = [
            {"score": round(s, 4), "role": r, "snippet": t[:80], "event_day": d}
            for s, r, t, d in selected
        ]
        return result_str, trace_items
    return result_str


def get_highlights(user_id: str, days: int = 2, max_lines: int = 5) -> str:
    """
    从最近N天日志里提取有内容密度的片段，供碎碎念使用。
    优先选：包含具体事物/情感词的用户发言，跳过纯短句和系统行。
    角色回复 intensity >= 2 的块额外加分。
    """
    recent_text = get_recent_days(user_id, days=days)
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
    """列出该用户/角色下所有存在按天日志文件的日期（YYYY-MM-DD），新旧路径 union，按日期降序。

    只统计按天分割文件（不含 full_log.md / .gz 归档），供管理面板浏览后按需 DELETE。
    """
    require_character_id(char_id)
    uid = safe_user_id(user_id)
    new_dir = _event_log_read_dir(user_id, char_id=char_id)
    old_dir = get_paths()._p("event_log") / uid
    dates: set[str] = set()
    for d in (new_dir, old_dir):
        try:
            if d.is_dir():
                for f in d.glob("*.md"):
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem):
                        dates.add(f.stem)
        except Exception as e:
            log_error("event_log.list_days", e)
    return sorted(dates, reverse=True)


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


def cleanup_event_log(user_id: str) -> None:
    """归档超出窗口的按天文件，并对 full_log.md 按大小滚动。
    按天文件：>= day_archive_days 天的 .md → .md.gz（search 窗口 30 天不受影响）。
    full_log.md：超过 full_log_max_size_mb → gzip 归档 + 清空。
    """
    from core.config_loader import get_config
    from core.safe_write import archive_old_day_files, rotate_jsonl_if_needed

    cfg = get_config().get("forensic_logs", {}).get("event_log", {})
    cutoff_days = int(cfg.get("day_archive_days", 30))
    dir_path = _event_log_write_dir(user_id)
    archived = archive_old_day_files(dir_path, cutoff_days=cutoff_days)
    if archived:
        logger.info("[event_log] 已归档 %d 个按天文件 (uid=%s)", archived, user_id)

    full_log = _full_log_file_write(user_id)
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

    def get_recent_days(self, user_id: str, days: int = 3) -> str:
        return get_recent_days(user_id, days)

    async def search(self, user_id: str, query: str, llm_client=None) -> str:
        return await search(user_id, query, llm_client)
