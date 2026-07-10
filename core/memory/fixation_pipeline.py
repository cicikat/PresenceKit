"""
core/memory/fixation_pipeline.py — 信息固化显式 pipeline

三个具名 job，每个有明确触发条件、输入、输出、幂等保证、可观测日志：

  capture_turn         — 同步写 short_term + event_log（含 turn_id 血缘）
  summarize_to_midterm — LLM 压缩单轮到 mid_term（slow_queue handler）
  reflect_to_episodic  — mid_term 列表 → episodic entry（slow_queue handler）

晋升关系：turn → mid_term → episodic → identity
所有 IO 走 core/sandbox.get_paths()，写入用 core/safe_write，锁用 core/memory/locks。
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.safe_write import rotate_jsonl_if_needed, safe_append_jsonl, safe_write_json, safe_write_text
from core.sandbox import get_paths
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

# ── 阈值常量 ──────────────────────────────────────────────────────────────────
_HIGH_STRENGTH_THRESHOLD = 0.6     # episodic strength 达到此值算"高强度"
_CONSOLIDATE_MIN_HIGH = 5          # 高强度 episodic 数量门槛（条件 1）
_CONSOLIDATE_MIN_STRENGTH_ACC = 4.0  # 累积 strength 门槛（条件 2）
_CONSOLIDATE_MIN_HOURS = 24        # 时间门槛（小时，条件 3）
_CONSOLIDATE_MIN_EPISODIC_COUNT = 3  # 条件 3 生效时最少 episodic 数
_CLOSURE_MATCH_WINDOW_SECONDS = 72 * 3600
_RESOLVED_STRENGTH_FLOOR = 0.2

# fixation_state 字段默认值（扩展了 4 个基本字段 + high_strength_since_last）
_STATE_DEFAULTS: dict = {
    "last_consolidated_at": 0.0,
    "episodic_since_last": 0,
    "high_strength_since_last": 0,   # 本字段为扩展，spec JSON 不含但向后兼容
    "strength_accumulated": 0.0,
    "last_sweep_at": 0.0,
}

# ── LLM prompt 模板 ────────────────────────────────────────────────────────────
_REFLECT_PROMPT_TEMPLATE = """\
你是一个对话记录分析器。请分析下面这些近期对话摘要，提炼出一条情景记忆，只输出JSON，不要有任何多余文字：
{{
  "raw_facts": ["事实陈述1（{pronoun}说了/做了什么）", "事实2", "事实3"],
  "topic_keywords": ["话题词1", "话题词2", "话题词3"],
  "emotion_peak": "neutral/happy/sad/gentle/surprised/angry 中选一个",
  "emotion_texture": "最有重量的情绪质感描述，20字以内，可留空",
  "emotion_arc": "情绪流动方向，10字以内，可留空",
  "user_state": "用户当时的状态短语，如 stressed_about_work / tired",
  "narrative_summary": "一句自然语言描述这段时期发生了什么，15字以内，供{char_name}回忆用",
  "is_closure": true/false,
  "closure_keywords": ["被结束或更新的事情关键词，如西瓜、考试；is_closure为false时为空数组"],
  "temporal_ref": "future/past/none 中选一个",
  "event_time_hint": "明天/周末/下周三/具体日期，无则空字符串",
  "strength": 0到1之间的浮点数（事件越重要、情绪越强则越高）
}}
完结/更新判定：用户明确表示先前提过的事情已经完成、结束、取消或状态已更新时，is_closure=true，
例如“吃完了”“考完了”“不去了”“已经到了”；closure_keywords 只列被结束或更新的事情关键词。
时间判定：主要指向未来的计划或事件时 temporal_ref=future，并原样提取简短 event_time_hint；
主要回顾过去时 temporal_ref=past；没有明确时间指向时 temporal_ref=none 且 event_time_hint 为空。
重要：用第三人称客观陈述，不要使用文学化语言，不要写动作描写。"""

_IDENTITY_SYSTEM_PROMPT = """\
你是一个客观分析器，负责归纳用户的稳定行为模式。
你不是任何角色，不要带角色立场。
你将看到一份"旧版印象"和一些"最近发生的事"，请基于这些，
输出 9 个维度的最新判断。

9 个维度：
- trust_pattern（信任建立模式）
- emotion_expression（情绪表达方式）
- help_seeking（求助风格）
- stress_response（压力反应模式）
- intimacy_comfort（亲密舒适度）
- sleep_pattern（作息模式）
- topic_preference（话题偏好）
- self_relation（自我关系）
- address_style（称呼习惯：她平时怎么称呼角色/自己，有没有固定的爱称、昵称、角色化称呼，如"主人"等）

规则：
1. 每个维度的 text 字段必须是第三人称"她"开头的短句，30-60 字，自然口语，不要心理学术语。
2. 严禁出现具体日期、时间戳、"上周""3 月 15 日"等时间锚点。可以用"经常""偶尔""有时"等频次词。
3. 如果某个维度没有新证据或证据不足以判断，沿用旧版本的 text 不动，last_updated 可以保留旧值。如果是全新维度从未判断过，text 留空字符串。
4. confidence 是你对此判断的把握度（0-1）。把握度低就写低，不要硬给高分。
5. evidence_count 是你"看到了多少条相关 episode 才得出这个结论"，老实给数字。
6. 只把"跨多条 episode 反复出现"的特征写成模式。如果某个特征只在一两条 episode 里出现过，不要写成稳定人格，宁可留空或保持旧判断。
7. 用户的单次情绪爆发、玩笑、自嘲、角色扮演式表达，不得固化为稳定人格。判断标准不是"这次是不是认真的"（你无法判断），而是"这个特征是否反复出现"。只出现一次的，一律不固化。
8. 如果新证据与旧版印象冲突（比如旧版说"慢热"，但最近多次快速信任），不要直接推翻旧判断，而是：保留 text 但降低 confidence，并在返回的 counter_evidence_count 里反映冲突次数。让矛盾积累，而不是非黑即白地翻转。你报告的 counter_evidence_count 只需反映【本批新证据中】与旧判断冲突的次数，不需要累加历史——历史累积由系统负责。
9. 如果你判断旧版印象的某个维度已经明显不再成立（新证据反复与之相反），不要继续保留旧 text，直接基于新证据重写该维度的 text，并把 counter_evidence_count 报告为 0（视为建立了全新判断）。

输出严格 JSON，结构：
{
  "trust_pattern": {"text": "...", "confidence": 0.7, "evidence_count": 12, "counter_evidence_count": 2},
  "emotion_expression": {...},
  ...,
  "address_style": {"text": "...", "confidence": 0.8, "evidence_count": 5, "counter_evidence_count": 0}
}
不要输出任何 JSON 之外的文字。"""


# ═══════════════════════════════════════════════════════════════════════════════
# fixation_state 读写
# ═══════════════════════════════════════════════════════════════════════════════

def _state_read_file(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(uid), char_id)
    return resolve_path(scope, "fixation_state")


def _state_write_file(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    """写路径：始终写新布局。"""
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(uid), char_id)
    p = resolve_path(scope, "fixation_state")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_fixation_state(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict:
    """读取 fixation_state，缺失字段按默认值填充，不阻塞读路径。"""
    path = _state_read_file(uid, char_id=char_id)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            state = dict(_STATE_DEFAULTS)
            state.update({k: data[k] for k in _STATE_DEFAULTS if k in data})
            return state
    except Exception as e:
        log_error("fixation_pipeline._load_fixation_state", e)
    return dict(_STATE_DEFAULTS)


def _save_fixation_state(uid: str, state: dict, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    safe_write_json(_state_write_file(uid, char_id=char_id), state)


def _should_consolidate(state: dict) -> bool:
    """检查是否满足 consolidate_to_identity 触发阈值（满足任一即返回 True）。"""
    high = state.get("high_strength_since_last", 0)
    strength_acc = state.get("strength_accumulated", 0.0)
    last_at = state.get("last_consolidated_at", 0.0)
    since = state.get("episodic_since_last", 0)
    hours_since = (time.time() - last_at) / 3600

    # 条件 1：高强度 episodic 累计 ≥ 5
    if high >= _CONSOLIDATE_MIN_HIGH:
        return True
    # 条件 2：累积 strength ≥ 4.0
    if strength_acc >= _CONSOLIDATE_MIN_STRENGTH_ACC:
        return True
    # 条件 3：距上次固化 ≥ 24h 且 新增 episodic ≥ 3
    if hours_since >= _CONSOLIDATE_MIN_HOURS and since >= _CONSOLIDATE_MIN_EPISODIC_COUNT:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 可观测日志
# ═══════════════════════════════════════════════════════════════════════════════

def _log_fixation(
    job: str,
    uid: str,
    extra: dict,
    status: Literal["ok", "error"],
    detail: str = "",
) -> None:
    record: dict = {"ts": time.time(), "job": job, "uid": uid, "status": status}
    record.update(extra)
    if detail:
        record["detail"] = detail
    path = get_paths().fixation_log()
    safe_append_jsonl(path, record)
    from core.config_loader import get_config
    cfg = get_config().get("forensic_logs", {})
    rotate_jsonl_if_needed(
        path,
        max_bytes=int(cfg.get("max_size_mb", 5) * 1024 * 1024),
        keep_n=int(cfg.get("keep", 5)),
    )
    if status == "error":
        logger.warning(f"[fixation.{job}] uid={uid} error: {detail}")
    else:
        logger.debug(f"[fixation.{job}] uid={uid} ok {extra}")


# ═══════════════════════════════════════════════════════════════════════════════
# 校验函数（内部）
# ═══════════════════════════════════════════════════════════════════════════════

def _validate_episode(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("is_closure"), bool):
        data["is_closure"] = False
    closure_keywords = data.get("closure_keywords")
    if not isinstance(closure_keywords, list):
        data["closure_keywords"] = []
    else:
        data["closure_keywords"] = [
            keyword.strip()
            for keyword in closure_keywords
            if isinstance(keyword, str) and keyword.strip()
        ]
    if data.get("temporal_ref") not in ("future", "past", "none"):
        data["temporal_ref"] = "none"
    if not isinstance(data.get("event_time_hint"), str):
        data["event_time_hint"] = ""
    else:
        data["event_time_hint"] = data["event_time_hint"].strip()
    for key in ("raw_facts", "topic_keywords", "emotion_peak", "strength"):
        if key not in data:
            return False
    if not isinstance(data["raw_facts"], list) or len(data["raw_facts"]) == 0:
        return False
    if not isinstance(data["topic_keywords"], list) or len(data["topic_keywords"]) == 0:
        return False
    if data["emotion_peak"] not in {"neutral", "happy", "sad", "gentle", "surprised", "angry"}:
        return False
    try:
        s = float(data["strength"])
        if not (0.0 <= s <= 1.0):
            return False
    except (TypeError, ValueError):
        return False
    return True


_WEEKDAY_BY_CN = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
}


def _parse_turn_ms(turn_id: str) -> float | None:
    """turn_id 形如 {uid}_{ms}；取尾段毫秒→秒。"""
    try:
        return int(str(turn_id).rsplit("_", 1)[1]) / 1000.0
    except (IndexError, ValueError):
        return None


def _derive_occurred_at(to_process: list[dict], fallback: float) -> float:
    """取这批 mid_term 来源 turn 的最早真实时刻；都拿不到则回退（=反思时刻）。"""
    times = []
    for e in to_process:
        ms = _parse_turn_ms(e.get("source_turn_id") or "")
        if ms is not None:
            times.append(ms)
        elif isinstance(e.get("ts"), (int, float)):
            times.append(float(e["ts"]))
    return min(times) if times else fallback


def _batch_occurred_at(to_process: list[dict], fallback: float) -> float:
    """优先读 mid_term entry 的 occurred_at 字段；旧数据无字段时回退 source_turn_id 解析。"""
    times = [e["occurred_at"] for e in to_process if isinstance(e.get("occurred_at"), (int, float))]
    if times:
        return min(times)
    return _derive_occurred_at(to_process, fallback)


def _parse_event_time_hint(event_time_hint: str, *, now: float | None = None) -> float | None:
    """Conservatively parse a small set of Chinese date hints in local time."""
    if not isinstance(event_time_hint, str) or not event_time_hint.strip():
        return None

    hint = event_time_hint.strip()
    base = datetime.fromtimestamp(time.time() if now is None else now)
    target_date = None

    if "后天" in hint:
        target_date = base.date() + timedelta(days=2)
    elif "明天" in hint:
        target_date = base.date() + timedelta(days=1)
    else:
        days_match = re.search(r"(\d{1,3})\s*天后", hint)
        if days_match:
            target_date = base.date() + timedelta(days=int(days_match.group(1)))

    if target_date is None and "下周末" in hint:
        days_until_next_monday = 7 - base.weekday()
        target_date = base.date() + timedelta(days=days_until_next_monday + 5)

    if target_date is None and re.search(r"(?<!下)(?:这周|本周)?周末", hint):
        if base.weekday() == 6:
            target_date = base.date()
        else:
            days_until_saturday = (5 - base.weekday()) % 7
            target_date = base.date() + timedelta(days=days_until_saturday)

    if target_date is None:
        weekday_match = re.search(r"下周([一二三四五六日天])", hint)
        if weekday_match:
            target_weekday = _WEEKDAY_BY_CN[weekday_match.group(1)]
            days_until_next_monday = 7 - base.weekday()
            target_date = base.date() + timedelta(days=days_until_next_monday + target_weekday)

    if target_date is None:
        date_match = re.search(r"(?:(\d{4})[-/.年])?(\d{1,2})[-/.月](\d{1,2})日?", hint)
        if date_match:
            year = int(date_match.group(1) or base.year)
            try:
                target_date = base.replace(
                    year=year,
                    month=int(date_match.group(2)),
                    day=int(date_match.group(3)),
                ).date()
                if date_match.group(1) is None and target_date < base.date():
                    target_date = target_date.replace(year=year + 1)
            except ValueError:
                return None

    if target_date is None:
        return None
    return datetime.combine(target_date, datetime.min.time()).timestamp()


def _resolve_matching_open_episodes(
    uid: str,
    closure_keywords: list[str],
    new_ep_id: str,
    *,
    char_id: str,
) -> list[str]:
    """在调用方持有 uid_lock 时，关闭近 72 小时内匹配的非核心开放事件。"""
    from core.memory import episodic_memory as _ep

    keywords = [
        keyword.strip()
        for keyword in closure_keywords
        if isinstance(keyword, str) and keyword.strip()
    ]
    if not keywords:
        return []

    now = time.time()
    memories = _ep._load_memories(uid, char_id=char_id)
    closed_ids: list[str] = []
    for mem in memories:
        if mem.get("status", "open") in ("resolved", "elapsed") or mem.get("is_core"):
            continue
        timestamp = mem.get("timestamp", 0)
        if not isinstance(timestamp, (int, float)):
            continue
        age_seconds = now - timestamp
        if age_seconds < 0 or age_seconds > _CLOSURE_MATCH_WINDOW_SECONDS:
            continue
        keywords_text = " ".join(
            str(value)
            for value in (mem.get("topic_keywords") or mem.get("tags", []))
        )
        facts_text = " ".join(str(value) for value in mem.get("raw_facts", []))
        haystack = f"{keywords_text} {facts_text}"
        if not any(keyword in haystack for keyword in keywords):
            continue

        mem["status"] = "resolved"
        mem["resolved_at"] = now
        mem["resolved_by"] = new_ep_id
        try:
            strength = float(mem.get("strength", 0.5))
        except (TypeError, ValueError):
            strength = 0.5
        mem["strength"] = min(strength, _RESOLVED_STRENGTH_FLOOR)
        closed_ids.append(str(mem.get("id", "")))

    if closed_ids:
        _ep._save_memories(uid, memories, char_id=char_id)
        _ep._rebuild_index(uid, memories, char_id=char_id)
        logger.info("episodic_resolved uid=%s closed=%s by=%s", uid, closed_ids, new_ep_id)
    return closed_ids


def _find_core_duplicate(new_keywords: list[str], new_summary: str, existing_eps: list[dict]) -> dict | None:
    """Return the first is_core open episode that overlaps sufficiently with the new episode.

    Overlap is declared when:
    - keyword intersection ≥ 2, OR
    - keyword Jaccard ≥ 0.5, OR
    - narrative_summary character-overlap ≥ 0.7 (using episodic_memory._is_similar)

    Returns the matching episode dict, or None.
    """
    from core.memory.episodic_memory import _is_similar as _ep_similar
    new_kw = set(new_keywords)
    for ep in existing_eps:
        if not ep.get("is_core") or ep.get("status", "open") in ("resolved", "elapsed"):
            continue
        existing_kw = set(ep.get("topic_keywords", []))
        if existing_kw and new_kw:
            intersection = existing_kw & new_kw
            union = existing_kw | new_kw
            kw_jaccard = len(intersection) / len(union) if union else 0.0
            if len(intersection) >= 2 or kw_jaccard >= 0.5:
                return ep
        existing_summary = ep.get("narrative_summary") or ep.get("summary", "")
        if _ep_similar(new_summary, existing_summary, threshold=0.7):
            return ep
    return None


def _validate_growth_content(observer: str) -> bool:
    """校验 observer 段（===FELT=== 前的内容），100~500 字且含 Markdown 标题。"""
    stripped = observer.strip()
    if not stripped:
        return False
    if not (100 <= len(stripped) <= 500):
        return False
    if not re.search(r"^#+ ", stripped, re.MULTILINE):
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Job 1 — capture_turn（同步，在 uid_lock 内、detect_emotion 后调用）
# ═══════════════════════════════════════════════════════════════════════════════

def _write_trigger_audit_log(
    uid: str,
    turn_id: str,
    trigger_name: str,
    reply: str | None,
    emotion: str,
    char_id: str,
    *,
    event_id: str = "",
    dedupe_key: str = "",
    source: str = "",
    kind: str = "",
    dream_guard_status: str = "",
    gate_result: str = "",
    did_generate_reply: bool = True,
) -> None:
    """Write trigger turn metadata to trigger_audit.jsonl (per-uid, under event_log dir).

    Only stores metadata + content hash — never the full generated reply text or prompt.
    Called in place of short_term.append for trigger turns (P0 boundary rule).

    Structured provenance fields (event_id, dedupe_key, gate_result, dream_guard_status,
    source, kind) are populated when threaded from _pipeline_send via audit_extras; they
    default to empty strings for paths that don't thread them (e.g. slow_queue retry).
    """
    import hashlib
    try:
        from core.sandbox import get_paths, safe_user_id as _suid
        content_hash = hashlib.sha256((reply or "").encode()).hexdigest()[:16] if reply else "empty"
        record: dict = {
            "ts": time.time(),
            "uid": uid,
            "char_id": char_id,
            "trigger_name": trigger_name,
            "turn_id": turn_id,
            "emotion": emotion,
            "reply_hash": content_hash,
            "reply_len": len(reply) if reply else 0,
            "did_generate_reply": did_generate_reply,
        }
        # Provenance fields: include only when populated so legacy records stay compact.
        if event_id:
            record["event_id"] = event_id
        if dedupe_key:
            record["dedupe_key"] = dedupe_key
        if source:
            record["source"] = source
        if kind:
            record["kind"] = kind
        if dream_guard_status:
            record["dream_guard_status"] = dream_guard_status
        if gate_result:
            record["gate_result"] = gate_result
        audit_path = get_paths()._p("event_log") / _suid(uid) / "trigger_audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        safe_append_jsonl(audit_path, record)
    except Exception as _e:
        log_error("trigger_audit_log.write", _e)


# 会话型触发器：主动说给用户听、期待用户回应，其 assistant 正文写入 short_term
# 保留对话连续性。纯维护型触发（episodic_sweep / hidden_state_decay 等）不发言，
# 不调用 capture_turn，无需列出。
CONVERSATIONAL_TRIGGERS: frozenset[str] = frozenset({
    # 花园伴生事件
    "garden_bloom", "garden_harvest_expired", "garden_handle_ask",
    "garden_handle_gift", "garden_handle_self", "garden_vase_wilted",
    # 时间类问候 / 碎碎念
    "morning_greeting", "night_reminder", "random_message", "weather_alert",
    "daily_journal", "spontaneous_recall",
    # 日记相关
    "diary_reminder", "diary_share_reminder",
    # 关心 / 追问
    "period_reminder", "topic_followup", "overflow", "presence_nag",
    # 生日系列
    "birthday_midnight", "birthday_eve", "birthday_afternoon", "birthday_night",
    # 时间节点 / 节日
    "timenode", "festival", "holiday_boost",
    # 备忘录到点提醒
    "reminders",
    # Apple Watch 事件
    "hr_critical", "hr_high", "sleep_end",
    # 传感器主动开口
    "sensor_aware",
    # 出梦 / 来信
    "dream_exit", "letter_writer",
})


def capture_turn(
    uid: str,
    user_msg: str,
    reply: str,
    emotion: str = "neutral",
    turn_id: str | None = None,
    trigger_name: str = "",
    envelope=None,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    audit_extras: dict | None = None,
) -> str:
    """
    生成 turn_id，写 short_term + event_log。
    trigger_name 非空时为 scheduler/sensor/watch 触发路径：
      - 会话型触发（trigger_name in CONVERSATIONAL_TRIGGERS）：写 short_term assistant 行
        保留对话连续性，同时写 event_log + trigger_audit_log。
      - 非会话型触发（系统锚点等）：不写 short_term，只写 event_log + trigger_audit_log。
    P0 trigger boundary rule: trigger 的 user_msg 侧永远不是 history；
    会话型触发的 assistant 正文例外，以维持角色主动开口后的上下文连续性。

    调用约束：必须在 uid_lock 内、detect_emotion 完成后调用。
    envelope 未传时默认零值（fail-closed）。
    char_id 决定写入哪个角色桶，生产路径必须显式传入。
    """
    from core.write_envelope import WriteEnvelope
    if envelope is None:
        envelope = WriteEnvelope()

    from core.memory import short_term, event_log

    ts = time.time()
    turn_id = turn_id or f"{uid}_{int(ts * 1000)}"

    if not envelope.can_write_memory:
        return turn_id

    # REALITY_MEMORY authority scrub — capture_turn is the final, authoritative scrub
    # point for all REALITY_MEMORY writes (short_term + event_log).  Upstream callers
    # (main.py QQ paths, turn_sink.record_assistant_turn) may have already pre-scrubbed
    # the same text, but this call must be kept: it is the defense-in-depth guard that
    # blocks action/narration from reaching short_term or event_log even when a new
    # reality outlet is added without upstream pre-scrubbing.
    # Invariants: (1) scrub_reality_output_text is idempotent — double-scrub is safe.
    #             (2) Dream output must never reach capture_turn; dream_pipeline does
    #                 not call this function.
    from core.reality_output_scrubber import scrub_reality_output_text as _scrub
    _scrubbed_reply = _scrub(reply)

    if trigger_name:
        # P0 trigger boundary: non-conversational triggers must NOT enter short_term.
        # Conversational triggers (those that speak to the user and expect a reply)
        # additionally write their assistant reply to short_term so that the next
        # user turn has context.  The forensic audit path is unchanged for all triggers.
        _write_trigger_audit_log(
            uid, turn_id, trigger_name, _scrubbed_reply, emotion, char_id,
            **(audit_extras or {}),
        )
        writes = [
            event_log.append(uid, "assistant", _scrubbed_reply, emotion=emotion, turn_id=turn_id, trigger_name=trigger_name, char_id=char_id)
            if _scrubbed_reply is not None else True,
        ]
        if trigger_name in CONVERSATIONAL_TRIGGERS and _scrubbed_reply is not None:
            writes.append(
                short_term.append(uid, "assistant", _scrubbed_reply, turn_id=turn_id, char_id=char_id)
            )
    else:
        writes = [
            short_term.append(uid, "user", user_msg, turn_id=turn_id, char_id=char_id),
            short_term.append(uid, "assistant", _scrubbed_reply, turn_id=turn_id, char_id=char_id)
            if _scrubbed_reply is not None else True,
            event_log.append(uid, "user", user_msg, turn_id=turn_id, char_id=char_id),
            event_log.append(uid, "assistant", _scrubbed_reply, emotion=emotion, turn_id=turn_id, char_id=char_id)
            if _scrubbed_reply is not None else True,
        ]
    if not all(writes):
        raise RuntimeError(f"capture_turn 写入不完整: turn_id={turn_id} writes={writes}")

    return turn_id


# ═══════════════════════════════════════════════════════════════════════════════
# Job 2 — summarize_to_midterm（slow_queue handler）
# ═══════════════════════════════════════════════════════════════════════════════

async def summarize_to_midterm(
    turn_id: str,
    uid: str,
    user_msg: str,
    reply: str,
    tags: list[str],
    emotion: str = "neutral",
    *,
    char_id: str = DEFAULT_CHAR_ID,
    source: str = "",
    memory_strength: float = 1.0,
    force_reflect: bool = False,
    trigger_name: str = "",
) -> str | None:
    """
    LLM 压缩单轮对话到 mid_term，写入血缘字段。
    幂等：source_turn_id 已存在则跳过。
    完成后检查 eager 触发条件，满足则入队 reflect_to_episodic。

    返回 mid_id（已写入）或 None（跳过）。
    """
    from core.memory import locks, mid_term as _mt
    from core import llm_client
    from core.post_process import slow_queue

    _ts_start = time.time()

    async with locks.uid_lock(uid):
        existing = _mt.load(uid, char_id=char_id)
        if any(e.get("source_turn_id") == turn_id for e in existing):
            logger.debug(f"[fixation] summarize_to_midterm 幂等命中: turn_id={turn_id}")
            return None

    summary = await llm_client.summarize_turn(user_msg, reply, tags=tags)
    if not summary:
        return None

    mid_id = f"mt_{uid}_{int(time.time() * 1000)}"
    async with locks.uid_lock(uid):
        existing = _mt.load(uid, char_id=char_id)
        if any(e.get("source_turn_id") == turn_id for e in existing):
            logger.debug(f"[fixation] summarize_to_midterm 幂等命中: turn_id={turn_id}")
            return None
        append_kwargs = {"char_id": char_id}
        if source:
            append_kwargs["source"] = source
        if memory_strength != 1.0:
            append_kwargs["memory_strength"] = memory_strength
        if trigger_name:
            append_kwargs["is_trigger_turn"] = True
        _mt.append(
            uid,
            summary,
            tags=tags,
            mid_id=mid_id,
            source_turn_id=turn_id,
            occurred_at=_parse_turn_ms(turn_id),
            **append_kwargs,
        )
        from core.memory.provenance_log import append as _prov_append
        _prov_append(
            uid, char_id,
            artifact="mid_term",
            after_gist=summary[:120],
            trigger_signal=(user_msg or "")[:120],
            turn_id=turn_id,
        )

    duration_ms = int((time.time() - _ts_start) * 1000)
    _log_fixation("summarize_to_midterm", uid, {
        "mid_id": mid_id, "turn_id": turn_id, "duration_ms": duration_ms,
    }, "ok")

    # eager 触发：情绪显著或强制反射（如群聊来源）则立即入队 reflect
    if force_reflect or emotion in ("sad", "angry", "happy"):
        slow_queue.enqueue("reflect_to_episodic", {
            "uid": uid,
            "mid_ids": [mid_id],
            "trigger": "eager",
            "char_id": char_id,
            "scope": MemoryScope.reality_scope(str(uid), char_id).to_payload(),
        })
        logger.info(f"[fixation] reflect_to_episodic eager 已入队: uid={uid} emotion={emotion}")

    return mid_id


# ═══════════════════════════════════════════════════════════════════════════════
# Job 3 — reflect_to_episodic（slow_queue handler）
# ═══════════════════════════════════════════════════════════════════════════════

async def reflect_to_episodic(
    uid: str,
    mid_ids: list[str],
    trigger: Literal["eager", "sweep"] = "eager",
    *,
    char_id: str = DEFAULT_CHAR_ID,
) -> str | None:
    """
    将一批 mid_term 条目合并反思为一条 episodic 记忆。
    幂等：已 promoted 或已生成对应 episodic 的条目跳过。
    完成后更新 fixation_state，如达阈值则入队 consolidate_to_identity。

    返回 ep_id（已写入）或 None（跳过）。
    """
    from core.memory import locks, mid_term as _mt
    from core.memory.episodic_memory import write_episode, _load_memories, _save_memories, _rebuild_index
    from core import llm_client
    from core.post_process import slow_queue
    from core.config_loader import get_config
    from core.llm_output_validator import record_failure, reset as _reset

    _ts_start = time.time()
    mid_ids_set = set(mid_ids)
    ep_id: str | None = None

    async with locks.uid_lock(uid):
        all_events = _mt.load(uid, char_id=char_id)

        # 只处理请求的、且未晋升的条目；触发轮（无真实用户输入）不铸造 episodic
        trigger_skipped = [
            e for e in all_events
            if e.get("mid_id") in mid_ids_set
            and not e.get("promoted_to_episodic_id")
            and e.get("is_trigger_turn")
        ]
        if trigger_skipped:
            logger.info(
                "[fixation.reflect_to_episodic] 跳过触发轮 mid_term 条目 uid=%s ids=%s",
                uid, [e.get("mid_id") for e in trigger_skipped],
            )
        to_process = [
            e for e in all_events
            if e.get("mid_id") in mid_ids_set
            and not e.get("promoted_to_episodic_id")
            and not e.get("is_trigger_turn")
        ]

        if not to_process:
            _log_fixation("reflect_to_episodic", uid, {
                "mid_ids": mid_ids, "trigger": trigger,
            }, "ok", "already promoted")
            return None

        # 幂等：检查 episodic 里是否已有相同 source_mid_ids 的条目
        existing_eps = _load_memories(uid, char_id=char_id)
        for ep in existing_eps:
            if mid_ids_set & set(ep.get("source_mid_ids", [])):
                _log_fixation("reflect_to_episodic", uid, {
                    "mid_ids": mid_ids, "trigger": trigger,
                }, "ok", "already reflected")
                return None

        # 构造 LLM 输入
        from core.character_name_provider import get_char_name
        try:
            char_name = get_char_name(char_id)
        except (ValueError, FileNotFoundError):
            char_name = char_id
        summaries_text = "\n".join(
            f"{i+1}. {e.get('summary', '')}"
            for i, e in enumerate(to_process)
        )
        from core.memory.user_facts import get_user_pronoun as _get_pronoun
        prompt_system = _REFLECT_PROMPT_TEMPLATE.format(
            char_name=char_name,
            pronoun=_get_pronoun(uid),
        )
        base_user = f"对话摘要：\n{summaries_text}"

        # LLM 调用（最多 3 次）
        _fail_key = f"reflect_to_episodic_{uid}"
        data = None
        _last_raw = ""
        for attempt in range(3):
            suffix = "" if attempt == 0 else "\n\n上次输出不符合格式要求，请严格只输出JSON。"
            _last_raw = await llm_client.chat(
                messages=[{"role": "user", "content": prompt_system + "\n\n" + base_user + suffix}],
                max_tokens_override=400,
                call_category="consolidation",
            )
            try:
                cleaned = re.sub(r"```json|```", "", _last_raw or "").strip()
                candidate = json.loads(cleaned)
                if _validate_episode(candidate):
                    data = candidate
                    break
            except Exception:
                pass

        if data is None:
            record_failure(_fail_key, (_last_raw or "")[:200], uid)
            _log_fixation("reflect_to_episodic", uid, {
                "mid_ids": mid_ids, "trigger": trigger,
            }, "error", "LLM 解析失败")
            return None

        ep_id = f"ep_{int(time.time() * 1000)}"
        if data.get("is_closure"):
            _resolve_matching_open_episodes(
                uid,
                data.get("closure_keywords", []),
                ep_id,
                char_id=char_id,
            )

        # 过滤平淡内容。closure 已在此前执行，因此中性低强度完结事件也能关闭旧记忆。
        # group 来源（stage 群聊投影）豁免：给底分 0.4，确保群聊事实能进 episodic。
        if data.get("emotion_peak") == "neutral" and data.get("strength", 0) < 0.4:
            _is_group_source = any(
                str(e.get("source", "")).startswith("group:")
                for e in to_process
            )
            if _is_group_source:
                data["strength"] = 0.4
            else:
                _log_fixation("reflect_to_episodic", uid, {
                    "mid_ids": mid_ids, "trigger": trigger,
                }, "ok", "neutral skip")
                return None

        _now = time.time()
        episode: dict = {
            "id": ep_id,
            "timestamp": _now,
            "occurred_at": _batch_occurred_at(to_process, _now),
            "raw_facts": data.get("raw_facts", []),
            "topic_keywords": data.get("topic_keywords", []),
            "emotion_peak": data.get("emotion_peak", "neutral"),
            "emotion_texture": data.get("emotion_texture", ""),
            "emotion_arc": data.get("emotion_arc", ""),
            "user_state": data.get("user_state", ""),
            "narrative_summary": data.get("narrative_summary", ""),
            "temporal_ref": data.get("temporal_ref", "none"),
            "event_time": None,
            "expires_at": None,
            "strength": data.get("strength", 0.5),
            "retrieval_count": 0,
            "last_retrieved": None,
            # 血缘字段
            "source_mid_ids": [e.get("mid_id") for e in to_process if e.get("mid_id")],
            "consolidated_at": None,
        }
        event_time = _parse_event_time_hint(data.get("event_time_hint", ""))
        if event_time is not None:
            episode["event_time"] = event_time
            if episode["temporal_ref"] == "future":
                episode["expires_at"] = event_time + 86400
        sources = sorted({str(e.get("source") or "") for e in to_process if e.get("source")})
        strength_factors = [
            max(0.0, min(1.0, float(e.get("memory_strength", 1.0))))
            for e in to_process
        ]
        if sources:
            episode["source"] = sources[0] if len(sources) == 1 else sources
        if strength_factors:
            episode["strength"] = round(
                float(episode["strength"]) * min(strength_factors),
                3,
            )

        # Patch A — core dedup: if a similar is_core episode already exists, merge
        # rather than creating a recycled clone with a fresh timestamp (which would
        # bypass the age-based guard in retrieve_fallback and restart the loop).
        _dup_ep = _find_core_duplicate(
            episode.get("topic_keywords", []),
            episode.get("narrative_summary", ""),
            existing_eps,
        )
        if _dup_ep is not None:
            _dup_ep["last_retrieved"] = time.time()
            _dup_ep["retrieval_count"] = _dup_ep.get("retrieval_count", 0) + 1
            _save_memories(uid, existing_eps, char_id=char_id)
            _rebuild_index(uid, existing_eps, char_id=char_id)
            _reset(_fail_key)
            logger.info(
                "[fixation.reflect_to_episodic] core 记忆去重合并 uid=%s dup=%s",
                uid, _dup_ep.get("id"),
            )
            _log_fixation("reflect_to_episodic", uid, {
                "mid_ids": mid_ids, "trigger": trigger,
                "core_dedup": _dup_ep.get("id"),
            }, "ok", "core dedup merge")
            ep_id = _dup_ep["id"]
        else:
            write_episode(uid, episode, char_id=char_id)
            _reset(_fail_key)
            from core.memory.provenance_log import append as _prov_append
            _prov_append(
                uid, char_id,
                artifact="episodic",
                after_gist=(episode.get("narrative_summary") or "")[:120],
                trigger_signal=summaries_text[:120],
                turn_id=ep_id,
            )
            # 语义索引（fail-open，不阻塞主流程）
            try:
                from core.memory import vector_store as _vs
                _ep_text = (
                    episode.get("narrative_summary")
                    or episode.get("summary")
                    or " ".join(episode.get("raw_facts") or [])
                ).strip()
                if _ep_text:
                    asyncio.ensure_future(
                        _vs.upsert(uid, char_id, "episodic", ep_id, episode.get("timestamp", 0), _ep_text)
                    )
            except Exception as _vs_e:
                logger.debug("[fixation] vector_store upsert schedule error: %s", _vs_e)

        # 回写 mid_term：标记已晋升（指向新建 ep 或合并目标的 id）
        for e in to_process:
            if e.get("mid_id"):
                _mt.mark_promoted(uid, e["mid_id"], ep_id, char_id=char_id)

        # 更新 fixation_state
        strength = episode.get("strength", 0.0)
        state = _load_fixation_state(uid, char_id=char_id)
        state["episodic_since_last"] = state.get("episodic_since_last", 0) + 1
        if strength >= _HIGH_STRENGTH_THRESHOLD:
            state["high_strength_since_last"] = state.get("high_strength_since_last", 0) + 1
        state["strength_accumulated"] = round(
            state.get("strength_accumulated", 0.0) + strength, 3
        )
        _save_fixation_state(uid, state, char_id=char_id)

    # uid_lock 释放后检查阈值，携带入队时的 char_id 快照
    if _should_consolidate(state):
        slow_queue.enqueue("consolidate_to_identity", {
            "uid": uid,
            "char_id": char_id,
            "scope": MemoryScope.reality_scope(str(uid), char_id).to_payload(),
        })
        logger.info(f"[fixation] consolidate_to_identity 已入队: uid={uid}")

    duration_ms = int((time.time() - _ts_start) * 1000)
    _log_fixation("reflect_to_episodic", uid, {
        "ep_id": ep_id,
        "input_ids": mid_ids,
        "output_id": ep_id,
        "trigger": trigger,
        "duration_ms": duration_ms,
    }, "ok")
    return ep_id


async def _synthesize_identity(
    uid: str,
    old_identity: dict,
    new_episodes: list[dict],
    user_profile_data: dict,
    llm_client,
) -> dict | None:
    """
    输入旧 identity + 待固化 episodic 列表，调 LLM 输出新 identity dict。
    计算最终 confidence（含反例惩罚），不做文件 IO。
    LLM 输出格式错误返回 None，由调用方降级处理。
    """
    from core.memory.user_identity import IDENTITY_DIMENSIONS
    from core.llm_output_validator import record_failure as _rf

    # 格式化旧 identity
    if old_identity:
        old_lines = []
        for key, label in IDENTITY_DIMENSIONS:
            dim = old_identity.get(key)
            if dim and dim.get("text"):
                conf = dim.get("confidence", 0.0)
                old_lines.append(f"- {label}：{dim['text']}（把握度 {conf:.2f}）")
        old_identity_formatted = "\n".join(old_lines) or "（无旧版印象，请基于新证据初次归纳）"
    else:
        old_identity_formatted = "（无旧版印象，请基于新证据初次归纳）"

    # 格式化 episodes
    episodes_lines = []
    for ep in new_episodes:
        summary = ep.get("narrative_summary") or ep.get("summary", "（无摘要）")
        emotion = ep.get("emotion_peak", "neutral")
        strength = ep.get("strength", 0.5)
        episodes_lines.append(f"- {summary}（情绪: {emotion}，强度: {strength:.2f}）")
    episodes_formatted = "\n".join(episodes_lines)

    # 格式化 user profile
    profile_lines = [
        f"- {k}: {v}"
        for k, v in user_profile_data.items()
        if isinstance(v, str) and v
    ]
    user_profile_formatted = "\n".join(profile_lines) if profile_lines else "（暂无基本信息）"

    user_content = (
        f"旧版印象：\n{old_identity_formatted}\n\n"
        f"用户基本事实（仅供参考，这些不是行为模式）：\n{user_profile_formatted}\n\n"
        f"最近发生的事（共 {len(new_episodes)} 条）：\n{episodes_formatted}"
    )

    _fail_key = f"consolidate_to_identity_{uid}"
    _last_raw = ""
    data = None

    for attempt in range(3):
        suffix = "" if attempt == 0 else "\n\n上次输出格式不符，请严格只输出 JSON，不要有任何其他文字。"
        _last_raw = await llm_client.chat(
            [
                {"role": "system", "content": _IDENTITY_SYSTEM_PROMPT},
                {"role": "user", "content": user_content + suffix},
            ],
            max_tokens_override=2000,
            call_category="consolidation",
        )
        _last_raw = (_last_raw or "").strip()
        try:
            cleaned = re.sub(r"```json|```", "", _last_raw).strip()
            candidate = json.loads(cleaned)
            if isinstance(candidate, dict) and candidate:
                if all(
                    isinstance(v, dict) and {"text", "confidence", "evidence_count"} <= v.keys()
                    for v in candidate.values()
                ):
                    data = candidate
                    break
        except Exception:
            pass

    if data is None:
        _rf(_fail_key, (_last_raw or "")[:500], uid)
        return None

    now = time.time()
    result = {}
    for key, _ in IDENTITY_DIMENSIONS:
        dim_raw = data.get(key)
        if dim_raw is None:
            if key in old_identity:
                result[key] = old_identity[key]
            continue

        raw_conf = max(0.0, min(1.0, float(dim_raw.get("confidence", 0.5))))
        ev = max(0, int(dim_raw.get("evidence_count", 0)))
        new_conflict = max(0, int(dim_raw.get("counter_evidence_count", 0)))

        old_dim = old_identity.get(key) or {}
        old_cev = old_dim.get("counter_evidence_count", 0)
        # LLM 重写了 text → 新判断，counter 归零重新计数；沿用旧 text → 累积历史冲突
        if str(dim_raw.get("text", "")) != old_dim.get("text", ""):
            cev = new_conflict
        else:
            cev = old_cev + new_conflict

        evidence_factor = ev / (ev + cev * 2) if (ev + cev) > 0 else 0.0
        maturity_factor = min(ev / 10, 1.0)
        final_conf = round(raw_conf * evidence_factor * maturity_factor, 4)

        if new_conflict > 0:
            last_conflict_at = now
        else:
            last_conflict_at = old_dim.get("last_conflict_at", 0.0)

        result[key] = {
            "text": str(dim_raw.get("text", "")),
            "confidence": final_conf,
            "evidence_count": ev,
            "counter_evidence_count": cev,
            "last_updated": now,
            "last_conflict_at": last_conflict_at,
        }

    return result


async def consolidate_to_identity(uid: str, llm_client, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """
    读取待固化 episodic，调 _synthesize_identity 生成新 identity，
    写入 user_identity.yaml，标记 episodic consolidated_at，重置 fixation_state。
    幂等：consolidated_at 已写的 episodic 不再参与。
    """
    from core.memory import locks
    from core.memory import user_identity as _ui
    from core.memory import user_profile as _up
    from core.memory.episodic_memory import load_unconsolidated, _load_memories, _save_memories
    from core.llm_output_validator import record_failure

    _ts_start = time.time()
    _fail_key = f"consolidate_to_identity_{uid}"

    # 读旧 identity、待处理 episodic、user profile（各自管理自身锁）
    old_identity = await _ui.load(uid, char_id=char_id)
    new_episodes = load_unconsolidated(uid, char_id=char_id)
    user_profile_data = _up.load(uid, char_id=char_id)

    if not new_episodes:
        _log_fixation("consolidate_to_identity", uid, {}, "ok", "no unconsolidated episodes")
        return True

    # LLM 合成（锁外）
    new_identity = await _synthesize_identity(
        uid, old_identity, new_episodes, user_profile_data, llm_client
    )

    if new_identity is None:
        record_failure(_fail_key, "synthesis returned None", uid)
        _log_fixation("consolidate_to_identity", uid, {}, "error", "LLM 合成失败")
        raise RuntimeError(f"consolidate_to_identity LLM 合成失败: uid={uid}")

    # 非空 → 写 identity；空 dict → LLM 判断无更新，跳过写入，仍标记 episodes
    if new_identity:
        ok = await _ui.save(uid, new_identity, char_id=char_id)
        if not ok:
            _log_fixation("consolidate_to_identity", uid, {}, "error", "identity 写入失败")
            raise RuntimeError(f"consolidate_to_identity identity 写入失败: uid={uid}")
        # Provenance: record each dimension whose text changed
        from core.memory.provenance_log import append as _prov_append
        _trigger_signal = "; ".join(
            (ep.get("narrative_summary") or ep.get("summary") or "")[:40]
            for ep in new_episodes[:3]
        )
        for _key, _new_dim in new_identity.items():
            _old_dim = old_identity.get(_key) or {}
            _old_text = _old_dim.get("text", "")
            _new_text = _new_dim.get("text", "")
            if _new_text != _old_text:
                _prov_append(
                    uid, char_id,
                    artifact="identity",
                    field=_key,
                    before_gist=_old_text[:120],
                    after_gist=_new_text[:120],
                    trigger_signal=_trigger_signal,
                )
    else:
        _log_fixation("consolidate_to_identity", uid, {}, "ok", "no dimension updated")

    # 标记 episodes + 重置 fixation_state（uid_lock 内原子操作）
    now = time.time()
    snapshot_ids = {ep.get("id") for ep in new_episodes}

    async with locks.uid_lock(uid):
        all_episodes = _load_memories(uid, char_id=char_id)
        for ep in all_episodes:
            if ep.get("id") in snapshot_ids and ep.get("consolidated_at") is None:
                ep["consolidated_at"] = now
        _save_memories(uid, all_episodes, char_id=char_id)

        state = _load_fixation_state(uid, char_id=char_id)
        state["episodic_since_last"] = 0
        state["high_strength_since_last"] = 0
        state["strength_accumulated"] = 0.0
        state["last_consolidated_at"] = now
        _save_fixation_state(uid, state, char_id=char_id)

    ep_count = len(snapshot_ids)
    duration_ms = int((time.time() - _ts_start) * 1000)
    _log_fixation("consolidate_to_identity", uid, {
        "ep_count": ep_count,
        "duration_ms": duration_ms,
    }, "ok")
    logger.info(f"[fixation] consolidate_to_identity 完成: uid={uid} ep_count={ep_count}")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# slow_queue handler 包装
# ═══════════════════════════════════════════════════════════════════════════════

def _get_scope_from_payload(payload: dict, handler_name: str) -> MemoryScope:
    """从 payload 解析 MemoryScope。

    优先读 payload["scope"]（新格式）；无 scope 时 fallback 到 legacy uid+char_id；
    两者均缺时 WARN + fallback yexuan（DLQ 兼容层）。
    payload["scope"] 存在但解析失败或 domain 非 reality → fail-loud，不 fallback。
    """
    raw = payload.get("scope")
    if raw is not None:
        scope = MemoryScope.from_payload(raw)  # 坏数据 fail-loud
        if scope.domain != "reality":
            raise ValueError(
                f"[fixation.{handler_name}] scope domain must be 'reality', got {scope.domain!r}"
            )
        return scope

    # legacy fallback：旧 payload 无 scope 字段
    uid = payload.get("uid", "unknown")
    char_id = payload.get("char_id")
    if char_id:
        return MemoryScope.reality_scope(str(uid), char_id)

    logger.warning(
        "[fixation.%s] payload 缺少 scope/char_id，使用 legacy DLQ fallback char_id=yexuan "
        "(uid=%s)",
        handler_name, uid,
    )
    return MemoryScope.reality_scope(str(uid), "yexuan")


async def handler_summarize_to_midterm(payload: dict) -> None:
    # D2/X3 isolation: skip mid-term summarization for turns where dream impressions,
    # web-search recall, or an active coplay session were active — prevents
    # non-reality (or not-yet-consolidated coplay) facts from being promoted into
    # episodic/identity via the normal consolidation chain. Coplay's own memory
    # flowback lives in game_log (Brief 42), not mid_term/episodic/identity.
    _echo_reason = (
        "dream_echo" if payload.get("dream_echo")
        else "web_echo" if payload.get("web_echo")
        else "coplay_echo" if payload.get("coplay_echo")
        else None
    )
    if _echo_reason:
        logger.info(
            "[fixation] handler_summarize_to_midterm skipped (%s=True): "
            "turn_id=%s uid=%s",
            _echo_reason, payload.get("turn_id"), payload.get("uid"),
        )
        return
    scope = _get_scope_from_payload(payload, "handler_summarize_to_midterm")
    kwargs = {
        "turn_id": payload["turn_id"],
        "uid": scope.uid,
        "user_msg": payload["user_content"],
        "reply": payload["reply"],
        "tags": payload.get("tags", []),
        "emotion": payload.get("emotion", "neutral"),
        "char_id": scope.character_id,
    }
    if payload.get("source"):
        kwargs["source"] = payload["source"]
    if "memory_strength" in payload:
        kwargs["memory_strength"] = payload["memory_strength"]
    if payload.get("force_reflect"):
        kwargs["force_reflect"] = True
    if payload.get("trigger_name"):
        kwargs["trigger_name"] = payload["trigger_name"]
    await summarize_to_midterm(
        **kwargs,
    )


async def handler_capture_turn_retry(payload: dict) -> None:
    from core.memory import locks
    from core.write_envelope import WriteEnvelope, SourceType

    scope = _get_scope_from_payload(payload, "handler_capture_turn_retry")
    uid = scope.uid
    char_id = scope.character_id
    # retry 只有在原调用拥有写入权限时才会入队，固定用 INGEST 源恢复写入
    _env = WriteEnvelope(
        source=SourceType.INGEST,
        can_write_memory=True,
        can_affect_mood=False,
    )
    async with locks.uid_lock(uid):
        capture_turn(
            uid,
            payload["user_content"],
            payload["reply"],
            payload.get("emotion", "neutral"),
            turn_id=payload["turn_id"],
            trigger_name=payload.get("trigger_name", ""),
            envelope=_env,
            char_id=char_id,
        )
    logger.info(f"[fixation] capture_turn retry 完成: {payload['turn_id']}")


async def handler_reflect_to_episodic(payload: dict) -> None:
    scope = _get_scope_from_payload(payload, "handler_reflect_to_episodic")
    await reflect_to_episodic(
        uid=scope.uid,
        mid_ids=payload.get("mid_ids", []),
        trigger=payload.get("trigger", "eager"),
        char_id=scope.character_id,
    )


async def handler_consolidate_to_identity(payload: dict) -> None:
    from core import llm_client
    scope = _get_scope_from_payload(payload, "handler_consolidate_to_identity")
    await consolidate_to_identity(uid=scope.uid, llm_client=llm_client, char_id=scope.character_id)
