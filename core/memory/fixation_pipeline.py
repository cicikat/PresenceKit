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
from pathlib import Path
from typing import Literal

from core.error_handler import log_error
from core.safe_write import rotate_jsonl_if_needed, safe_append_jsonl, safe_write_json, safe_write_text
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)

# ── 阈值常量 ──────────────────────────────────────────────────────────────────
_HIGH_STRENGTH_THRESHOLD = 0.6     # episodic strength 达到此值算"高强度"
_CONSOLIDATE_MIN_HIGH = 5          # 高强度 episodic 数量门槛（条件 1）
_CONSOLIDATE_MIN_STRENGTH_ACC = 4.0  # 累积 strength 门槛（条件 2）
_CONSOLIDATE_MIN_HOURS = 24        # 时间门槛（小时，条件 3）
_CONSOLIDATE_MIN_EPISODIC_COUNT = 3  # 条件 3 生效时最少 episodic 数

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
  "raw_facts": ["事实陈述1（用户说了/做了什么）", "事实2", "事实3"],
  "topic_keywords": ["话题词1", "话题词2", "话题词3"],
  "emotion_peak": "neutral/happy/sad/gentle/surprised/angry 中选一个",
  "emotion_texture": "最有重量的情绪质感描述，20字以内，可留空",
  "emotion_arc": "情绪流动方向，10字以内，可留空",
  "user_state": "用户当时的状态短语，如 stressed_about_work / tired",
  "narrative_summary": "一句自然语言描述这段时期发生了什么，15字以内，供{char_name}回忆用",
  "strength": 0到1之间的浮点数（事件越重要、情绪越强则越高）
}}
重要：用第三人称客观陈述，不要使用文学化语言，不要写动作描写。"""

_IDENTITY_SYSTEM_PROMPT = """\
你是一个客观分析器，负责归纳用户的稳定行为模式。
你不是任何角色，不要带角色立场。
你将看到一份"旧版印象"和一些"最近发生的事"，请基于这些，
输出 8 个维度的最新判断。

8 个维度：
- trust_pattern（信任建立模式）
- emotion_expression（情绪表达方式）
- help_seeking（求助风格）
- stress_response（压力反应模式）
- intimacy_comfort（亲密舒适度）
- sleep_pattern（作息模式）
- topic_preference（话题偏好）
- self_relation（自我关系）

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
  ...
}
不要输出任何 JSON 之外的文字。"""


# ═══════════════════════════════════════════════════════════════════════════════
# fixation_state 读写
# ═══════════════════════════════════════════════════════════════════════════════

def _state_read_file(uid: str, *, char_id: str = "yexuan") -> Path:
    safe_uid = safe_user_id(uid)
    return get_paths().user_memory_root(safe_uid, char_id=char_id) / "fixation_state.json"


def _state_write_file(uid: str, *, char_id: str = "yexuan") -> Path:
    """写路径：始终写新布局。"""
    safe_uid = safe_user_id(uid)
    p = get_paths().user_memory_root(safe_uid, char_id=char_id) / "fixation_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_fixation_state(uid: str) -> dict:
    """读取 fixation_state，缺失字段按默认值填充，不阻塞读路径。"""
    path = _state_read_file(uid)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            state = dict(_STATE_DEFAULTS)
            state.update({k: data[k] for k in _STATE_DEFAULTS if k in data})
            return state
    except Exception as e:
        log_error("fixation_pipeline._load_fixation_state", e)
    return dict(_STATE_DEFAULTS)


def _save_fixation_state(uid: str, state: dict) -> None:
    safe_write_json(_state_write_file(uid), state)


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

def capture_turn(
    uid: str,
    user_msg: str,
    reply: str,
    emotion: str = "neutral",
    turn_id: str | None = None,
    trigger_name: str = "",
    envelope=None,
) -> str:
    """
    生成 turn_id，写 short_term + event_log。
    trigger_name 非空时为 scheduler 触发路径：跳过 user 行写入，assistant meta 附加 trigger: 字段。
    幂等：若 short_term 近 4 条已含相同 turn_id，直接返回。

    调用约束：必须在 uid_lock 内、detect_emotion 完成后调用。
    envelope 未传时默认零值（fail-closed）。
    """
    from core.write_envelope import WriteEnvelope
    if envelope is None:
        envelope = WriteEnvelope()

    from core.memory import short_term, event_log

    ts = time.time()
    turn_id = turn_id or f"{uid}_{int(ts * 1000)}"

    if not envelope.can_write_memory:
        return turn_id

    # Scrub reply for both short_term and event_log: keep dialogue only,
    # discard all action / stage-direction / env content.
    from core.reality_output_scrubber import scrub_reality_output_text as _scrub
    _scrubbed_reply = _scrub(reply)

    if trigger_name:
        # scheduler 触发：只写 assistant，跳过 user 行（prompt 是系统注入的情景描述）
        writes = [
            # Skip short_term when scrubber returned nothing (all non-dialogue)
            short_term.append(uid, "assistant", _scrubbed_reply, turn_id=turn_id)
            if _scrubbed_reply is not None else True,
            # event_log also stores scrubbed text — no raw action descriptions persist
            event_log.append(uid, "assistant", _scrubbed_reply, emotion=emotion, turn_id=turn_id, trigger_name=trigger_name)
            if _scrubbed_reply is not None else True,
        ]
    else:
        writes = [
            short_term.append(uid, "user", user_msg, turn_id=turn_id),
            short_term.append(uid, "assistant", _scrubbed_reply, turn_id=turn_id)
            if _scrubbed_reply is not None else True,
            event_log.append(uid, "user", user_msg, turn_id=turn_id),
            event_log.append(uid, "assistant", _scrubbed_reply, emotion=emotion, turn_id=turn_id)
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
        existing = _mt.load(uid)
        if any(e.get("source_turn_id") == turn_id for e in existing):
            logger.debug(f"[fixation] summarize_to_midterm 幂等命中: turn_id={turn_id}")
            return None

    summary = await llm_client.summarize_turn(user_msg, reply, tags=tags)
    if not summary:
        return None

    mid_id = f"mt_{uid}_{int(time.time() * 1000)}"
    async with locks.uid_lock(uid):
        existing = _mt.load(uid)
        if any(e.get("source_turn_id") == turn_id for e in existing):
            logger.debug(f"[fixation] summarize_to_midterm 幂等命中: turn_id={turn_id}")
            return None
        _mt.append(uid, summary, tags=tags, mid_id=mid_id, source_turn_id=turn_id)

    duration_ms = int((time.time() - _ts_start) * 1000)
    _log_fixation("summarize_to_midterm", uid, {
        "mid_id": mid_id, "turn_id": turn_id, "duration_ms": duration_ms,
    }, "ok")

    # eager 触发：情绪显著则立即入队 reflect
    if emotion in ("sad", "angry", "happy"):
        slow_queue.enqueue("reflect_to_episodic", {
            "uid": uid,
            "mid_ids": [mid_id],
            "trigger": "eager",
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
) -> str | None:
    """
    将一批 mid_term 条目合并反思为一条 episodic 记忆。
    幂等：已 promoted 或已生成对应 episodic 的条目跳过。
    完成后更新 fixation_state，如达阈值则入队 consolidate_to_identity。

    返回 ep_id（已写入）或 None（跳过）。
    """
    from core.memory import locks, mid_term as _mt
    from core.memory.episodic_memory import write_episode, _load_memories
    from core import llm_client
    from core.post_process import slow_queue
    from core.config_loader import get_config, _char_name
    from core.llm_output_validator import record_failure, reset as _reset

    _ts_start = time.time()
    mid_ids_set = set(mid_ids)
    ep_id: str | None = None

    async with locks.uid_lock(uid):
        all_events = _mt.load(uid)

        # 只处理请求的、且未晋升的条目
        to_process = [
            e for e in all_events
            if e.get("mid_id") in mid_ids_set and not e.get("promoted_to_episodic_id")
        ]

        if not to_process:
            _log_fixation("reflect_to_episodic", uid, {
                "mid_ids": mid_ids, "trigger": trigger,
            }, "ok", "already promoted")
            return None

        # 幂等：检查 episodic 里是否已有相同 source_mid_ids 的条目
        existing_eps = _load_memories(uid)
        for ep in existing_eps:
            if mid_ids_set & set(ep.get("source_mid_ids", [])):
                _log_fixation("reflect_to_episodic", uid, {
                    "mid_ids": mid_ids, "trigger": trigger,
                }, "ok", "already reflected")
                return None

        # 构造 LLM 输入
        char_name = _char_name()
        summaries_text = "\n".join(
            f"{i+1}. {e.get('summary', '')}"
            for i, e in enumerate(to_process)
        )
        prompt_system = _REFLECT_PROMPT_TEMPLATE.format(char_name=char_name)
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

        # 过滤平淡内容
        if data.get("emotion_peak") == "neutral" and data.get("strength", 0) < 0.4:
            _log_fixation("reflect_to_episodic", uid, {
                "mid_ids": mid_ids, "trigger": trigger,
            }, "ok", "neutral skip")
            return None

        ep_id = f"ep_{int(time.time())}"
        episode: dict = {
            "id": ep_id,
            "timestamp": time.time(),
            "raw_facts": data.get("raw_facts", []),
            "topic_keywords": data.get("topic_keywords", []),
            "emotion_peak": data.get("emotion_peak", "neutral"),
            "emotion_texture": data.get("emotion_texture", ""),
            "emotion_arc": data.get("emotion_arc", ""),
            "user_state": data.get("user_state", ""),
            "narrative_summary": data.get("narrative_summary", ""),
            "strength": data.get("strength", 0.5),
            "retrieval_count": 0,
            "last_retrieved": None,
            # 血缘字段
            "source_mid_ids": [e.get("mid_id") for e in to_process if e.get("mid_id")],
            "consolidated_at": None,
        }

        write_episode(uid, episode)
        _reset(_fail_key)

        # 回写 mid_term：标记已晋升
        for e in to_process:
            if e.get("mid_id"):
                _mt.mark_promoted(uid, e["mid_id"], ep_id)

        # 更新 fixation_state
        strength = episode.get("strength", 0.0)
        state = _load_fixation_state(uid)
        state["episodic_since_last"] = state.get("episodic_since_last", 0) + 1
        if strength >= _HIGH_STRENGTH_THRESHOLD:
            state["high_strength_since_last"] = state.get("high_strength_since_last", 0) + 1
        state["strength_accumulated"] = round(
            state.get("strength_accumulated", 0.0) + strength, 3
        )
        _save_fixation_state(uid, state)

    # uid_lock 释放后检查阈值
    if _should_consolidate(state):
        slow_queue.enqueue("consolidate_to_identity", {"uid": uid})
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


async def consolidate_to_identity(uid: str, llm_client) -> bool:
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
    old_identity = await _ui.load(uid)
    new_episodes = load_unconsolidated(uid)
    user_profile_data = _up.load(uid)

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
        ok = await _ui.save(uid, new_identity)
        if not ok:
            _log_fixation("consolidate_to_identity", uid, {}, "error", "identity 写入失败")
            raise RuntimeError(f"consolidate_to_identity identity 写入失败: uid={uid}")
    else:
        _log_fixation("consolidate_to_identity", uid, {}, "ok", "no dimension updated")

    # 标记 episodes + 重置 fixation_state（uid_lock 内原子操作）
    now = time.time()
    snapshot_ids = {ep.get("id") for ep in new_episodes}

    async with locks.uid_lock(uid):
        all_episodes = _load_memories(uid)
        for ep in all_episodes:
            if ep.get("id") in snapshot_ids and ep.get("consolidated_at") is None:
                ep["consolidated_at"] = now
        _save_memories(uid, all_episodes)

        state = _load_fixation_state(uid)
        state["episodic_since_last"] = 0
        state["high_strength_since_last"] = 0
        state["strength_accumulated"] = 0.0
        state["last_consolidated_at"] = now
        _save_fixation_state(uid, state)

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

async def handler_summarize_to_midterm(payload: dict) -> None:
    await summarize_to_midterm(
        turn_id=payload["turn_id"],
        uid=payload["uid"],
        user_msg=payload["user_content"],
        reply=payload["reply"],
        tags=payload.get("tags", []),
        emotion=payload.get("emotion", "neutral"),
    )


async def handler_capture_turn_retry(payload: dict) -> None:
    from core.memory import locks
    from core.write_envelope import WriteEnvelope, SourceType

    uid = payload["uid"]
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
        )
    logger.info(f"[fixation] capture_turn retry 完成: {payload['turn_id']}")


async def handler_reflect_to_episodic(payload: dict) -> None:
    await reflect_to_episodic(
        uid=payload["uid"],
        mid_ids=payload.get("mid_ids", []),
        trigger=payload.get("trigger", "eager"),
    )


async def handler_consolidate_to_identity(payload: dict) -> None:
    from core import llm_client
    await consolidate_to_identity(uid=payload["uid"], llm_client=llm_client)
