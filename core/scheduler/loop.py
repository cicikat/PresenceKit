"""
主动行为调度器 — 冷却管理、核心工具函数、主循环
"""

import asyncio
import logging
import random
import time
from datetime import datetime, date
from typing import Optional

from core.error_handler import log_error
from core.migration import for_read
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)

# ── Pipeline 注入：统一由 pipeline_registry 持有 ─────────────────────────────
# scheduler 不维护自己的 _pipeline；调用方直接用 pipeline_registry.register()。
# （Brief 35：set_pipeline() 兼容壳已删，main.py 调用点改直用 pipeline_registry.register()。）


# ── 冷却时间（秒）────────────────────────────────────────────────────────────
_COOLDOWNS: dict[str, int] = {
    "morning_greeting":      8 * 3600,   # 早安：8小时（日触发一次）
    "night_reminder":        5 * 3600,   # 晚安：5小时
    "random_message":        4 * 3600,   # 随机日间：4小时冷却，带4小时保底
    "hr_high":              30 * 60,     # 心率>100：30分钟
    "hr_critical":          60 * 60,     # 心率>120：1小时
    "sleep_end":             2 * 3600,   # 睡眠结束：2小时
    "weather_alert":         6 * 3600,   # 特殊天气：6小时
    "period_reminder":      24 * 3600,   # 生理期关心：24小时
    "diary_reminder":       20 * 3600,   # 日记提醒：20小时
    "diary_inject":          6 * 3600,   # 日记注入：6小时
    "daily_journal":         1 * 3600,   # 每日手账：1小时冷却（深夜触发）
    "diary_share_reminder":  8 * 3600,   # 日记分享提醒：8小时
    "activity_remind":      20 * 3600,   # 运动提醒：20小时
    "topic_followup":       24 * 3600,   # 未完结话题追问：24小时
    "birthday_midnight": 365 * 24 * 3600,
    "birthday_eve":        20 * 3600,
    "birthday_afternoon":  20 * 3600,
    "birthday_night":      20 * 3600,
    "timenode":            20 * 3600,
    "festival":            20 * 3600,
    "holiday_boost":        2 * 3600,
    "episodic_decay":      20 * 3600,   # 情景记忆衰减：20小时
    "inner_diary_write":    2 * 3600,   # 角色内心日记静默写入：2小时冷却（幂等靠文件存在性）
    "spontaneous_recall":   4 * 3600,   # 主动回忆：4小时冷却
    "dlq_monitor":         24 * 3600,   # DLQ 扫描：24小时
    "log_maintenance":     24 * 3600,   # forensic 日志归档/滚动：24小时
    "episodic_sweep":      30 * 60,     # mid_term 老化扫描：30分钟
    "garden_water":       300 * 60,     # 花园自动浇水：300分钟
    "garden_daily":        24 * 3600,   # 花园每日扫描：harvest/vase 状态
    "garden_bloom":         8 * 3600,   # 开花发言冷却（同株短期不重复）
    "coplay_commentary":    300,        # 陪玩主动开口：≥5分钟一次（Brief 41 D5）
    "garden_harvest_expired": 4 * 3600,
    "garden_handle_ask":    4 * 3600,
    "garden_handle_gift":   4 * 3600,
    "garden_handle_self":   4 * 3600,
    "garden_vase_wilted":   4 * 3600,
    "hidden_state_decay":         12 * 3600,       # 用户隐性状态衰减：12小时
    "hidden_state_consolidate":   7 * 24 * 3600,   # 基线收敛：7天
    "overflow":              3 * 3600,   # 理由累积溢出：3小时
    "presence_nag":          2 * 3600,   # 存在感弹窗：2小时最多一次
    "dream_exit":           60 * 60,     # 出梦主动开口：一梦一次，1小时冷却兜底
    "letter_writer":         7 * 24 * 3600,  # 真实邮件：7天最多一封
}

# 冷却跟踪 {trigger_name: last_unix_timestamp}
_last_trigger: dict[str, float] = {}


def _cooldown_key(name: str, char_id: str | None = None) -> str:
    """Return a per-character key when char_id is explicit; legacy callers stay global."""
    return f"{char_id}:{name}" if char_id else name

def _migrate_scheduler_state_once():
    """一次性迁移：拆分旧 scheduler_state.json → cooldowns + user_state，完成后删旧文件。"""
    old_path = get_paths()._p("scheduler_state.json")
    if not old_path.exists():
        return
    try:
        import json
        data = json.loads(old_path.read_text(encoding="utf-8"))
        from core.safe_write import safe_write_json as _swj
        cooldowns_path = get_paths().scheduler_cooldowns()
        if not cooldowns_path.exists():
            cooldowns_path.parent.mkdir(parents=True, exist_ok=True)
            _swj(cooldowns_path, {"triggers": data.get("triggers", {})})
        user_path = get_paths().scheduler_user_state()
        if not user_path.exists():
            user_path.parent.mkdir(parents=True, exist_ok=True)
            user_data = {k: v for k, v in data.items() if k != "triggers"}
            _swj(user_path, user_data)
        old_path.unlink()
        logger.info("[scheduler] scheduler_state.json 已拆分迁移 → cooldowns + user_state")
    except Exception as e:
        logger.warning("[scheduler] scheduler_state 迁移失败: %s", e)


def _load_scheduler_state():
    """启动时从 scheduler_cooldowns.json 读回冷却状态。"""
    _migrate_scheduler_state_once()
    try:
        import json
        p = get_paths().scheduler_cooldowns()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            triggers = d.get("triggers", {})
            _last_trigger.update({k: float(v) for k, v in triggers.items()})
            logger.info(f"[scheduler] 冷却状态已恢复，{len(triggers)} 个触发器")
    except Exception as e:
        logger.warning(f"[scheduler] 冷却状态读取失败: {e}")

_load_scheduler_state()


# 上次主动分享日记的时间戳（由 diary_tool 调用 mark_diary_shared 更新）
def _get_last_diary_share() -> float:
    try:
        import json
        p = get_paths().scheduler_user_state()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return float(d.get("last_diary_share", 0))
    except Exception:
        pass
    return 0.0


_last_diary_share: float = _get_last_diary_share()

# 调度器启动时间戳（用于冷启动保护）
_scheduler_start_time: float = time.time()

# sensor_aware 上次 tick 时间戳（模块级，重启清零）
_last_sensor_aware_tick: float = 0.0

# 上次用户消息时间戳（用于调度抢占检查）
_last_user_message_time: float = 0.0

# 调度器 task 句柄
_scheduler_task: Optional[asyncio.Task] = None

# ── Trigger-only reality reply outlet: allowed / rejected kind values ─────────
#
# _pipeline_send is the LOW-TRUST STIMULUS / TRIGGER-ONLY REALITY REPLY outlet.
# It is NOT a general event bus.  Only the four stimulus kinds listed below are
# permitted to enter the run_llm → record_assistant_turn → fanout path through
# this function.  Kinds from other subsystems (tool, activity, plugin, dream)
# must NOT be routed here; they have their own pipelines.

_TRIGGER_OUTLET_ALLOWED_KINDS: frozenset[str] = frozenset(
    {"trigger", "sensor", "scheduled", "wake"}
)
_TRIGGER_OUTLET_REJECTED_KINDS: frozenset[str] = frozenset(
    {"tool", "activity", "plugin", "dream"}
)


def _assert_trigger_outlet_kind(kind: str) -> None:
    """Raise ValueError if kind is not permitted through the trigger-only outlet."""
    if kind in _TRIGGER_OUTLET_REJECTED_KINDS:
        raise ValueError(
            f"[_pipeline_send] kind={kind!r} is explicitly rejected in the "
            f"trigger-only reality reply outlet. "
            f"Allowed: {_TRIGGER_OUTLET_ALLOWED_KINDS}"
        )
    if kind not in _TRIGGER_OUTLET_ALLOWED_KINDS:
        raise ValueError(
            f"[_pipeline_send] unknown kind={kind!r} rejected (not in allowed set). "
            f"Allowed: {_TRIGGER_OUTLET_ALLOWED_KINDS}"
        )


# 高优先级触发器（active_window_behavior="exempt"）— 与 POLICY_TABLE 对齐。
# R2-C: runtime safety-net helpers (_legacy_active_window_blocks/_legacy_dnd_blocks) 已删除；
# 此常量仅用于文档和测试断言（验证 POLICY_TABLE exempt 集合对齐），不参与运行时决策。
_HIGH_PRIORITY_TRIGGERS: frozenset[str] = frozenset({
    "birthday_midnight",
    "birthday_eve",
    "birthday_afternoon",
    "birthday_night",
    "period_reminder",
    "hr_critical",
})


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def _cfg() -> dict:
    from core.config_loader import get_config
    return get_config().get("scheduler", {})


def _cfg_retention() -> dict:
    from core.config_loader import get_config
    return get_config().get("retention", {})


def _is_ready(name: str, *, char_id: str | None = None) -> bool:
    """检查触发器是否已度过冷却期（正式冷却 + 失败退避 attempt-cooldown 均需通过，A4）。"""
    elapsed = time.time() - _last_trigger.get(_cooldown_key(name, char_id), 0)
    if elapsed < _COOLDOWNS.get(name, 3600):
        return False
    return _attempt_cooldown_ready(name, char_id=char_id)


# ── A4: 失败退避（attempt-cooldown）──────────────────────────────────────────
# 发送失败（sent=False：DUPLICATE / Dream Guard / LLM 空回复 / 质量门拒发等）不应让
# proposer 下个 tick 立即重新报名重试一遍完整 pipeline（RC4）。内存态即可：退避窗口
# 相对重启周期很短，重启清零可接受。
_ATTEMPT_BACKOFF_INITIAL_SECS = 15 * 60
_attempt_backoff_secs: dict[str, float] = {}
_attempt_cooldown_until: dict[str, float] = {}


def _record_attempt_failure(name: str, *, char_id: str | None = None) -> None:
    """记一次失败尝试：首次退避 15min，此后每次失败翻倍，封顶该触发器自身 _COOLDOWNS。"""
    key = _cooldown_key(name, char_id)
    cap = float(_COOLDOWNS.get(name, 3600))
    prev = _attempt_backoff_secs.get(key, 0.0)
    nxt = _ATTEMPT_BACKOFF_INITIAL_SECS if prev <= 0 else min(prev * 2, cap)
    _attempt_backoff_secs[key] = nxt
    _attempt_cooldown_until[key] = time.time() + nxt


def _clear_attempt_backoff(name: str, *, char_id: str | None = None) -> None:
    """成功发送后清除退避状态，下次失败重新从 15min 起算。"""
    key = _cooldown_key(name, char_id)
    _attempt_backoff_secs.pop(key, None)
    _attempt_cooldown_until.pop(key, None)


def _attempt_cooldown_ready(name: str, *, char_id: str | None = None) -> bool:
    key = _cooldown_key(name, char_id)
    return time.time() >= _attempt_cooldown_until.get(key, 0.0)


def _persist_cooldowns() -> None:
    """Persist _last_trigger to scheduler_cooldowns.json. Fail-soft."""
    try:
        import json
        from core.safe_write import safe_write_json
        p = get_paths().scheduler_cooldowns()
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if p.exists():
            existing = json.loads(p.read_text(encoding="utf-8"))
        existing["triggers"] = _last_trigger
        safe_write_json(p, existing)
    except Exception as e:
        logger.warning(f"[scheduler] 冷却状态写入失败: {e}")


def _mark(name: str, *, char_id: str | None = None):
    """记录触发时间，同时持久化到 scheduler_cooldowns.json。"""
    _last_trigger[_cooldown_key(name, char_id)] = time.time()
    _persist_cooldowns()


# A2/B 全局主动间隔（jitter 一次性采样、只增不减）与当日发送预算已收口到
# core/scheduler/proactive_ledger.py 的 can_send() / record_send()；不在 loop.py
# 重复维护一份 next_allowed_ts 状态。sensor_aware.handle_tick()、gating._decide()、
# execution.execute_prompt() 均直接调用 proactive_ledger。

# A1: 上次记录到日志的 effective gap 值，变化时才重新打印，避免刷屏。
_last_logged_gap: float | None = None


def _log_effective_gap_if_changed() -> None:
    """A1: config 热加载后，让 "生效没生效" 直接在日志里可见。

    每次调用都会经过 get_config() 的 mtime 检查（core/config_loader.py），磁盘变化
    会在 ≤ 一次调用内被读到；这里只在数值变化时打印，cheap 到可以每 tick 调用。
    """
    global _last_logged_gap
    gap = float(_cfg().get("global_proactive_min_gap_seconds", 90 * 60))
    if gap != _last_logged_gap:
        logger.info("[scheduler] effective global_proactive_min_gap_seconds=%s", gap)
        _last_logged_gap = gap


def _owner_id() -> str:
    return str(_cfg().get("owner_id", "")).strip()


def _char_name() -> str:
    """角色显示名。始终从 pipeline_registry 的活跃 character card 取。
    pipeline 未注册或 character 属性缺失时 fail-loud（不读 config）。
    """
    from core import pipeline_registry
    pl = pipeline_registry.get()
    if pl is not None:
        char = getattr(pl, "character", None)
        if char is not None:
            name = getattr(char, "name", None)
            if name:
                return name
    raise RuntimeError(
        "[scheduler] _char_name(): pipeline 未注册或 character 未加载；"
        "scheduler 触发器在 pipeline 初始化前不应运行"
    )


async def _send(content: str, behavior: dict | None = None):
    """广播到所有活跃通道。"""
    oid = _owner_id()
    if not oid:
        logger.warning("[scheduler] owner_id 未配置，跳过发送")
        return
    from channels.registry import broadcast
    await broadcast(content, oid, behavior=behavior)


def mark_user_active():
    global _last_user_message_time
    _last_user_message_time = time.time()


def _user_active_recently(window_seconds: int = 120) -> bool:
    return (time.time() - _last_user_message_time) < window_seconds


async def _pipeline_send(
    prompt: str,
    search_query: str = "",
    trigger_name: str = "",
    behavior: dict | None = None,
    behavior_factory=None,
    fanout="all",
    output_mode: str = "speak",   # "speak" | "return"
    record_turn: bool = True,
    kind: str = "scheduled",   # stimulus kind — must be in _TRIGGER_OUTLET_ALLOWED_KINDS
    char_id: str | None = None,
    recall_policy: str = "seed",   # C: "seed" | "anchored" | "none" — see fetch_context()
) -> Optional[str]:
    """LOW-TRUST STIMULUS / TRIGGER-ONLY REALITY REPLY outlet.

    This function is the single exit point for scheduler/sensor/wake triggers entering
    the run_llm → record_assistant_turn → fanout path.  It is NOT a general event bus.
    Only kinds in _TRIGGER_OUTLET_ALLOWED_KINDS ("trigger", "sensor", "scheduled", "wake")
    are accepted; any other kind raises ValueError before touching the LLM pipeline.

    Pipeline 未注入时降级直接发送 prompt 原文并打 warning。
    search_query 指定时用于 fetch_context，否则用 prompt。
    trigger_name 用于优先级判断：高优先级触发器不受活跃窗口限制。
    output_mode="return"：post_process 写记忆，但不调 _send，返回 reply 文本。
    output_mode="speak"（默认）：发送后返回 reply 文本；被策略拦截或失败时返回 None。

    P1 gate：perceive_event 统一入口（Dream Guard + TTL dedup），通过后用 conversation_lock
    覆盖 fetch_context → build_prompt → run_llm → record_assistant_turn，与 desktop_wake
    Path B 和 owner chat 串行，避免同 uid 并发 LLM。perceive_event=true 日志标识已通过 gate。
    """
    # Kind guard: reject disallowed / unknown kinds before any work is done.
    _assert_trigger_outlet_kind(kind)

    # R2-C: active-window and DND decisions are authoritative in gating._decide().
    # _pipeline_send is execution-only; it does not re-gate proposer-path winners.
    oid = _owner_id()
    if not oid:
        logger.warning("[scheduler._pipeline_send] owner_id 未配置，跳过")
        return None

    try:
        from core.pipeline_registry import get as _get_pipeline
        _pipeline = _get_pipeline()
        if _pipeline is None:
            logger.warning("[scheduler._pipeline_send] pipeline 未注入，降级直接发送")
            if output_mode != "return":
                fallback_behavior = behavior_factory(prompt) if behavior_factory else behavior
                await _send(prompt, behavior=fallback_behavior)
                return prompt
            return None

        # ── perceive_event gate ───────────────────────────────────────────────
        # Dream Guard (fail-closed) + TTL dedup: same trigger within 60s → DUPLICATE.
        # Payload uses only trigger_name so the hash is stable across calls in the
        # same time bucket.  correlation_id is logged for tracing but not hashed.
        import uuid as _uuid
        correlation_id = str(_uuid.uuid4())
        resolved_char_id = char_id or _active_char_id_or_none()

        from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus
        pe_event = PerceiveEvent(
            source="scheduler",
            uid=oid,
            channel="system",
            kind=kind,
            char_id=resolved_char_id,
            payload={"trigger_name": trigger_name},
        )
        pe_result = await receive_perceive_event(pe_event)
        if pe_result.status != PerceiveStatus.ACCEPTED:
            logger.info(
                "[scheduler._pipeline_send] gate=%s trigger=%s uid=%s char_id=%s "
                "correlation_id=%s dedupe_key=%s",
                pe_result.status, trigger_name, oid, resolved_char_id,
                correlation_id, pe_result.dedupe_key,
            )
            return None

        logger.info(
            "[scheduler._pipeline_send] perceive_event=true trigger=%s uid=%s char_id=%s "
            "correlation_id=%s event_id=%s",
            trigger_name, oid, resolved_char_id, correlation_id, pe_result.event_id,
        )

        # Audit extras: threaded into record_assistant_turn → post_process → capture_turn
        # → _write_trigger_audit_log so the audit record carries tracing provenance.
        _audit_extras: dict = {
            "event_id": pe_result.event_id,
            "dedupe_key": pe_result.dedupe_key,
            "gate_result": pe_result.status,
            "dream_guard_status": "ALLOW",
            "source": "scheduler",
            "kind": kind,
            "did_generate_reply": True,
        }

        from core.scheduler.triggers.birthday import _is_birthday_period
        if _is_birthday_period():
            prompt = prompt + "\n（今天是她的生日，4月24日）"
        _states = ["在思考", "在翻阅她的日记", "在想她说过的话", "在看窗外", "在灵体出游"]
        try:
            from core.character_name_provider import get_char_name
            prompt = prompt + f"\n（{get_char_name(resolved_char_id)}此刻{random.choice(_states)}）"
        except Exception as _cn_err:
            logger.warning("[scheduler._pipeline_send] 角色名不可用，跳过状态提示: %s", _cn_err)

        # ── N1: turn-level scope freeze ──────────────────────────────────────
        # Resolve active character exactly once per trigger turn; fetch_context /
        # build_prompt / post_process all consume this frozen scope so a mid-turn
        # character switch cannot split reads and writes across two characters.
        try:
            if resolved_char_id:
                from core.memory.scope import MemoryScope
                _frozen_scope = MemoryScope.reality_scope(oid, resolved_char_id)
            else:
                _frozen_scope = _pipeline._current_reality_scope(oid)
        except (ValueError, RuntimeError) as _scope_err:
            logger.error(
                "[scheduler._pipeline_send] scope freeze 失败，本次触发中止 trigger=%s: %s",
                trigger_name, _scope_err,
            )
            return None

        # ── conversation_lock: fetch_context → build_prompt → run_llm → record ──
        # bypass_gate=True because we already hold conversation_lock here.
        from core.conversation_gate import conversation_lock as _conv_lock
        async with _conv_lock(oid):
            context = await _pipeline.fetch_context(
                oid, search_query or prompt, frozen_scope=_frozen_scope,
                recall_policy=recall_policy,
            )
            # Tag this build_prompt() call as proactive so prompt_capture records
            # the trigger origin, seed prompt, and search query alongside the layers.
            try:
                from core.observe.prompt_capture import set_capture_origin as _set_capture_origin
                _set_capture_origin({
                    "origin": "proactive",
                    "trigger_name": trigger_name,
                    "seed_prompt": prompt,
                    "search_query": search_query or "",
                    "recall_policy": recall_policy,
                })
            except Exception:
                pass
            messages, _ = _pipeline.build_prompt(
                oid, prompt, context, char_id=_frozen_scope.character_id
            )
            reply = await _pipeline.run_llm(messages, is_proactive=True)
            if reply:
                try:
                    from core.observe.prompt_capture import update_llm_output as _upd_prompt_out
                    _upd_prompt_out(oid, reply)
                except Exception:
                    pass
                # 在 assistant 回复落盘前写一条最低权重的 user stub，
                # 让后续轮次的 LLM 有上下文锚点（"角色上次是因为 X 主动说话的"）。
                # _source="trigger_stub" 使 _score_turn_group 对其评 0 分，远场不占位。
                try:
                    from core.memory import short_term as _st_stub
                    _stub_char = _frozen_scope.character_id if _frozen_scope else "yexuan"
                    _stub_content = f"[触发: {trigger_name}]" if trigger_name else "[系统触发]"
                    _st_stub.append(oid, "user", _stub_content, char_id=_stub_char, source="trigger_stub")
                except Exception as _stub_err:
                    logger.warning("[scheduler._pipeline_send] stub write failed: %s", _stub_err)

                turn_result = None
                if record_turn:
                    from core.turn_sink import TurnSource, record_assistant_turn
                    from core.write_envelope import stamp_sensor, stamp_trigger
                    if trigger_name == "sensor_aware":
                        source = TurnSource.SENSOR
                        _envelope = stamp_sensor()
                    elif trigger_name in ("hr_high", "hr_critical", "sleep_end"):
                        source = TurnSource.WATCH
                        _envelope = stamp_sensor()
                    else:
                        source = TurnSource.TRIGGER
                        _envelope = stamp_trigger()
                    resolved_behavior = behavior_factory(reply) if behavior_factory else behavior
                    turn_result = await record_assistant_turn(
                        assistant_text=reply,
                        uid=oid,
                        source=source,
                        trigger_name=trigger_name or "scheduler",
                        fanout=[] if output_mode == "return" else fanout,
                        payload={"behavior": resolved_behavior} if resolved_behavior else None,
                        bypass_gate=True,  # already inside conversation_lock
                        pipeline=_pipeline,
                        envelope=_envelope,
                        audit_extras=_audit_extras,
                        frozen_scope=_frozen_scope,
                    )
                if output_mode == "return":
                    return reply
                if turn_result and turn_result.fanout_failures:
                    logger.warning(
                        "[scheduler._pipeline_send] fanout 部分失败: %s",
                        turn_result.fanout_failures,
                    )
                return reply
            else:
                logger.warning("[scheduler._pipeline_send] LLM 返回空内容")
    except Exception as e:
        log_error("scheduler._pipeline_send", e)
    return None


def _active_char_id_or_none() -> str | None:
    """Return the active character id from active_prompt_assets.json, or None if unavailable."""
    try:
        import json as _j
        data = _j.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        cid = (data.get("active_character") or "").strip()
        return cid or None
    except Exception:
        return None


def _all_observation_paths() -> list:
    """Return observation file Paths for every known character (v1 layout: scan runtime/characters/).

    In legacy layout returns the single yexuan file (legacy has one char).
    In v1 layout, returns one path per char directory that has an observations file.
    Returns [] on scan error rather than falling back to a hardcoded char.
    """
    from core.data_paths import _LAYOUT_CHARACTER_INNER
    paths = get_paths()
    if _LAYOUT_CHARACTER_INNER == "legacy":
        # Legacy layout has only one character (yexuan); no per-char dirs exist.
        p = paths.observations(char_id="yexuan")
        return [p] if p.exists() else []
    try:
        chars_dir = paths._p("runtime", "characters")
        if not chars_dir.exists():
            return []
        result = []
        for char_dir in chars_dir.iterdir():
            if not char_dir.is_dir():
                continue
            obs = char_dir / "inner" / "observations.jsonl"
            if obs.exists():
                result.append(obs)
        return result
    except Exception as e:
        logger.warning("[scheduler._all_observation_paths] scan failed, returning []: %s", e)
        return []


def _user_talked_today(user_id: str, *, char_id: str | None = None) -> bool:
    """检查用户今天在事件日志中是否有记录。

    char_id 未传时尝试读 active_prompt_assets.json；仍无则 warning + 返回 False（不 fallback yexuan）。
    """
    resolved = char_id or _active_char_id_or_none()
    if not resolved:
        logger.warning("[scheduler._user_talked_today] char_id 未知，跳过检查")
        return False
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    today = date.today().strftime("%Y-%m-%d")
    uid = safe_user_id(user_id)
    scope = MemoryScope.reality_scope(uid, resolved)
    new_p = resolve_path(scope, "event_log") / f"{today}.md"
    old_p = get_paths()._p("event_log") / uid / f"{today}.md"
    p = for_read(new_p, old_p)
    return p.exists() and p.stat().st_size > 10


def mark_diary_shared():
    global _last_diary_share
    _last_diary_share = time.time()
    try:
        import json
        p = get_paths().scheduler_user_state()
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if p.exists():
            existing = json.loads(p.read_text(encoding="utf-8"))
        existing["last_diary_share"] = _last_diary_share
        from core.safe_write import safe_write_json as _swj
        _swj(p, existing)
    except Exception as e:
        log_error("scheduler.mark_diary_shared", e)


# ── sensor_aware tick
async def _check_sensor_aware():
    global _last_sensor_aware_tick
    sa_cfg = _cfg().get("sensor_aware", {})
    if not sa_cfg.get("enabled", False):
        return
    interval = sa_cfg.get("tick_interval_seconds", 30)
    now = time.time()
    if now - _last_sensor_aware_tick < interval:
        return
    _last_sensor_aware_tick = now
    from core.scheduler.triggers.sensor_aware import get_last_decision, handle_tick
    try:
        await handle_tick()
        oid = _owner_id()
        if oid:
            decision = get_last_decision()
            event_count = int(decision.get("candidates_count") or 0)
            from core.scheduler.state_machine import feed_sensor_tick

            feed_sensor_tick(oid, event_count)
    except Exception:
        logger.exception("[scheduler] sensor_aware_tick 失败")


# ── 资产 retention 与日志归档维护（每24小时执行一次）
async def _check_log_maintenance():
    if not _is_ready("log_maintenance"):
        return
    oid = _owner_id()
    if not oid:
        return
    ret = _cfg_retention()
    # 每个 GC 步骤独立保护：单步失败不阻塞其余步骤，也不导致 loop 挂掉
    try:
        from core.memory.event_log import cleanup_event_log
        cleanup_event_log(oid)
    except Exception as e:
        log_error("scheduler.log_maintenance.event_log", e)
    try:
        from core.tools.reminder import prune_done_reminders
        prune_done_reminders(oid, cutoff_days=int(ret.get("reminders", {}).get("prune_done_days", 30)))
    except Exception as e:
        log_error("scheduler.log_maintenance.reminders", e)
    try:
        from core.dream.dream_log import prune_archive
        prune_archive(max_files=int(ret.get("dreams_archive", {}).get("max_files", 200)))
    except Exception as e:
        log_error("scheduler.log_maintenance.dreams_archive", e)
    try:
        from core.media_processor import gc_inbox, gc_image_cache
        gc_inbox(max_age_days=int(ret.get("inbox", {}).get("max_age_days", 7)))
        _ic = ret.get("image_cache", {})
        gc_image_cache(
            max_age_days=int(_ic.get("max_age_days", 30)),
            max_files=int(_ic.get("max_files", 500)),
        )
    except Exception as e:
        log_error("scheduler.log_maintenance.media", e)
    try:
        from core.memory.observation_compaction import compact_observations
        _obs_max_raw = int(ret.get("observations", {}).get("max_raw", 100))
        for _obs_path in _all_observation_paths():
            try:
                compact_observations(_obs_path, max_raw=_obs_max_raw)
            except Exception as _e:
                log_error(f"scheduler.log_maintenance.observations[{_obs_path}]", _e)
    except Exception as e:
        log_error("scheduler.log_maintenance.observations", e)
    _mark("log_maintenance")  # 无论各步是否失败，都标记以免 24h 内重复触发


# ── 备忘录到点提醒
async def _check_reminders():
    """检查 owner 的备忘录是否有到点条目，有则发送提醒后标记完成"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send():
        return
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return
    oid = _owner_id()
    if not oid:
        return
    try:
        from core.tools.reminder import get_due_reminders, mark_done
        due = get_due_reminders(oid)
        for item in due:
            sent = await _pipeline_send(
                f"备忘录提醒时间到了：{item['content']}，用{_char_name()}的方式提醒她",
                trigger_name="reminders",
            )
            if sent:
                mark_done(oid, item["id"])
                logger.info(f"[scheduler] 备忘录提醒已发送: {item['content']}")
    except Exception as e:
        log_error("scheduler._check_reminders", e)


# ═══════════════════════════════════════════════════════════════════════════════
# 状态查询（供管理面板）
# ═══════════════════════════════════════════════════════════════════════════════

def get_status() -> dict:
    """返回所有触发器的状态信息，含 enabled 状态"""
    from core.config_loader import get_config
    cfg = get_config().get("scheduler", {})

    _TRIGGER_CONFIG_KEYS: dict[str, str] = {
        "morning_greeting":  "morning_greeting",
        "night_reminder":    "night_reminder",
        "random_message":    "random_message",
        "daily_journal":     "daily_journal",
        "period_reminder":   "period_reminder",
        "diary_reminder":    "diary_reminder",
        "diary_inject":      "diary_inject",
        "presence_nag":      "presence_nag",
    }

    now = time.time()
    result = {}
    for name, cooldown in _COOLDOWNS.items():
        last = _last_trigger.get(name, 0)
        elapsed = now - last if last > 0 else cooldown + 1
        remaining = max(0, cooldown - elapsed)
        cfg_key = _TRIGGER_CONFIG_KEYS.get(name)
        enabled = bool(cfg.get(cfg_key, True)) if cfg_key else None
        entry = {
            "last_triggered": (
                datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S")
                if last > 0 else "从未"
            ),
            "cooldown_sec":   cooldown,
            "remaining_sec":  int(remaining),
            "ready":          remaining == 0,
        }
        if enabled is not None:
            entry["enabled"] = enabled
        result[name] = entry
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 手动触发（供管理面板调用）
# ═══════════════════════════════════════════════════════════════════════════════

async def manual_trigger(name: str) -> str:
    """手动触发指定动作（绕过冷却时间和条件检查）。"""
    _last_trigger[name] = 0  # 清零冷却

    try:
        from core.scheduler.triggers.time_based import (
            _check_morning, _check_night, _check_random_message,
        )

        if name == "morning_greeting":
            await _check_morning(force=True)
        elif name == "night_reminder":
            await _check_night(force=True)
        elif name == "random_message":
            await _check_random_message(force=True)
        elif name == "daily_journal":
            oid = _owner_id()
            if not oid:
                return "owner_id 未配置"
            from core.memory.event_log import get_recent_days
            today_log = get_recent_days(oid, days=1)
            log_hint = today_log[:800] if today_log and len(today_log) > 10 else "今天还没有对话记录"
            await _pipeline_send(
                f"（深夜，{_char_name()}回想起今天和你说过的话，提笔写下今天的感受——"
                f"今天的对话内容：{log_hint}）",
                trigger_name="daily_journal",
                recall_policy="none",
            )
            _mark("daily_journal")
        elif name == "period_reminder":
            oid = _owner_id()
            if not oid:
                return "owner_id 未配置"
            from core.memory.user_profile import get_period_info
            from datetime import date as _date
            info = get_period_info(oid)
            last_date_str = info.get("last_period_date")
            if last_date_str:
                last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
                days_elapsed = (_date.today() - last_date).days
                await _pipeline_send(
                    f"（{_char_name()}记得你的生理期已经来了{days_elapsed}天，悄悄关心一下）",
                    trigger_name="period_reminder",
                    search_query="生理期",
                    recall_policy="anchored",
                )
            else:
                await _pipeline_send(
                    f"（{_char_name()}想关心一下你的身体状况）",
                    trigger_name="period_reminder",
                    search_query="生理期",
                    recall_policy="anchored",
                )
            _mark("period_reminder")
        elif name == "diary_reminder":
            oid = _owner_id()
            if not oid:
                return "owner_id 未配置"
            from datetime import date as _date, timedelta
            yesterday = (_date.today() - timedelta(days=1)).strftime("%m月%d日")
            await _pipeline_send(
                f"（{_char_name()}想起来，{yesterday}好像没看到你写日记）",
                trigger_name="diary_reminder",
                recall_policy="none",
            )
            _mark("diary_reminder")
        elif name == "diary_share_reminder":
            oid = _owner_id()
            if not oid:
                return "owner_id not configured"
            await _pipeline_send(
                f"（{_char_name()}想起来，好像很久没看到你的日记了，故作不经意地提一句）",
                trigger_name="diary_share_reminder",
                recall_policy="none",
            )
            _mark("diary_share_reminder")
        elif name == "topic_followup":
            from core.scheduler.triggers.memory import _check_topic_followup
            await _check_topic_followup(force=True)
        elif name == "birthday_midnight":
            from core.scheduler.triggers.birthday import _check_birthday_midnight
            await _check_birthday_midnight(force=True)
        elif name == "birthday_eve":
            from core.scheduler.triggers.birthday import _check_birthday_eve
            await _check_birthday_eve(force=True)
        elif name == "birthday_afternoon":
            from core.scheduler.triggers.birthday import _check_birthday_afternoon
            await _check_birthday_afternoon(force=True)
        elif name == "birthday_night":
            from core.scheduler.triggers.birthday import _check_birthday_night
            await _check_birthday_night(force=True)
        elif name == "timenode":
            from core.scheduler.triggers.timenode import _check_timenode
            await _check_timenode(force=True)
        elif name == "festival":
            from core.scheduler.triggers.festival import _check_festival
            await _check_festival(force=True)
        elif name == "holiday_boost":
            from core.scheduler.triggers.festival import _check_holiday_boost
            await _check_holiday_boost(force=True)
        else:
            return f"未知触发器: {name}"
        # B: manual_trigger 绕过冷却/条件检查属设计，但也该记账（RC5）——否则管理
        # 面板测试触发不会让 gating 看到"刚说过话"，紧接着的自动触发可能背靠背双发。
        from core.scheduler.proactive_ledger import record_send as _ledger_record
        _ledger_record(name, channel="manual", gist=f"[手动触发] {name}")
        return f"{name} 已触发"
    except Exception as e:
        log_error(f"scheduler.manual_trigger.{name}", e)
        return f"{name} 触发失败: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# 主循环 & 启动
# ═══════════════════════════════════════════════════════════════════════════════

async def _loop():
    """调度器主循环，每 60 秒检查一次"""
    logger.info("[scheduler] 调度器已启动，每 60 秒检查一次")
    _log_effective_gap_if_changed()
    _sa = _cfg().get("sensor_aware", {})
    if _sa.get("enabled", False):
        logger.info(
            "[scheduler] sensor_aware ENABLED, tick interval=%ds",
            _sa.get("tick_interval_seconds", 30),
        )
    else:
        logger.info("[scheduler] sensor_aware DISABLED by config")
    while True:
        try:
            cfg = _cfg()
            _log_effective_gap_if_changed()
            if cfg.get("enabled", True):
                from core.scheduler.triggers.time_based import (
                    _check_morning, _check_night, _check_random_message,
                    _check_weather, _check_daily_journal, _check_episodic_decay,
                    _check_inner_diary_write,
                    _check_spontaneous_recall, check_activity_switch,
                    _check_dlq_monitor,
                )
                from core.scheduler.triggers.diary import (
                    _check_diary_reminder, _check_diary_inject, _check_diary_share_reminder,
                )
                from core.scheduler.triggers.period import _check_period
                from core.scheduler.triggers.memory import _check_topic_followup
                from core.scheduler.triggers.birthday import (
                    _check_birthday_midnight, _check_birthday_eve,
                    _check_birthday_afternoon, _check_birthday_night,
                )
                from core.scheduler.triggers.timenode import _check_timenode
                from core.scheduler.triggers.festival import _check_festival, _check_holiday_boost
                from core.scheduler.triggers.episodic_sweep import _check_episodic_sweep
                from core.scheduler.triggers.garden_water import _check_garden_water
                from core.scheduler.triggers.garden_daily import _check_garden_daily
                from core.scheduler.triggers.hidden_state_decay import (
                    _check_hidden_state_decay, _check_hidden_state_consolidate,
                )
                from core.scheduler.triggers.coplay_watch import _check_coplay_watch

                oid = _owner_id()
                if oid:
                    from core.scheduler.gating import run_shadow_tick

                    await run_shadow_tick(oid)

                _trigger_names = [
                    "morning", "night", "random_message", "weather",
                    "reminders", "period", "diary_reminder", "diary_inject",
                    "daily_journal", "episodic_decay", "inner_diary_write",
                    "spontaneous_recall",
                    "diary_share_reminder", "topic_followup",
                    "birthday_midnight", "birthday_eve",
                    "birthday_afternoon", "birthday_night",
                    "timenode", "festival", "holiday_boost",
                    "activity_switch", "dlq_monitor", "log_maintenance",
                    "episodic_sweep", "garden_water", "garden_daily",
                    "hidden_state_decay", "hidden_state_consolidate",
                    "sensor_aware", "coplay_watch",
                ]
                _trigger_results = await asyncio.gather(
                    _check_morning(),
                    _check_night(),
                    _check_random_message(),
                    _check_weather(),
                    _check_reminders(),
                    _check_period(),
                    _check_diary_reminder(),
                    _check_diary_inject(),
                    _check_daily_journal(),
                    _check_episodic_decay(),
                    _check_inner_diary_write(),
                    _check_spontaneous_recall(),
                    _check_diary_share_reminder(),
                    _check_topic_followup(),
                    _check_birthday_midnight(),
                    _check_birthday_eve(),
                    _check_birthday_afternoon(),
                    _check_birthday_night(),
                    _check_timenode(),
                    _check_festival(),
                    _check_holiday_boost(),
                    check_activity_switch(),
                    _check_dlq_monitor(),
                    _check_log_maintenance(),
                    _check_episodic_sweep(),
                    _check_garden_water(),
                    _check_garden_daily(),
                    _check_hidden_state_decay(),
                    _check_hidden_state_consolidate(),
                    _check_sensor_aware(),
                    _check_coplay_watch(),
                    return_exceptions=True,
                )
                for _tname, _tres in zip(_trigger_names, _trigger_results):
                    if isinstance(_tres, Exception):
                        logger.error(
                            "[scheduler] trigger %r raised %s: %s",
                            _tname, type(_tres).__name__, _tres,
                            exc_info=_tres,
                        )
        except Exception as e:
            log_error("scheduler._loop", e)
        await asyncio.sleep(60)


def start() -> asyncio.Task:
    """启动调度器后台 Task，返回 Task 对象供 main.py 管理"""
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_loop())
    logger.info("[scheduler] 调度器 Task 已创建")
    return _scheduler_task


# 暴露给外部（admin/routers/watch.py 通过 scheduler.on_watch_event 调用）
from core.scheduler.triggers.watch import on_watch_event  # noqa: E402
