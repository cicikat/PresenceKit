import logging
import random
import re
import time
from datetime import datetime
from hashlib import sha1

from core.error_handler import log_error
from core.scheduler.loop import _is_ready, _mark, _owner_id, _pipeline_send, _cfg, _user_talked_today, _last_trigger, _char_name

logger = logging.getLogger(__name__)

_LAST_WEATHER_DETAIL: dict | None = None

# Keep spontaneous recall close to episodic_memory.retrieve(top_k=3) semantics
# without calling retrieve() from the read-only proposer path.
_SPONTANEOUS_RECALL_TOP_K = 3


async def _check_morning(force: bool = False):
    """早安触发：7-9点，且用户今天还没说过话。force=True 跳过时间和对话检查"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send(force=force):
        return
    cfg = _cfg()
    if not cfg.get("morning_greeting", True):
        return
    if not _is_ready("morning_greeting"):
        return

    if not force:
        now = datetime.now()
        if not (7 <= now.hour < 9):
            return
        oid = _owner_id()
        if oid and _user_talked_today(oid):
            return

    await _pipeline_send(f"（清晨，{_char_name()}看了看时间，想着你应该快起床了）", trigger_name="morning_greeting")
    _mark("morning_greeting")
    logger.info("[scheduler] 早安消息已发送")


async def _check_night(force: bool = False):
    """晚安催睡：23点后。force=True 跳过时间检查"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send(force=force):
        return
    cfg = _cfg()
    if not cfg.get("night_reminder", True):
        return
    if not _is_ready("night_reminder"):
        return

    if not force:
        now = datetime.now()
        if now.hour < 23:
            return

    await _pipeline_send(f"（深夜，{_char_name()}看了眼时间）", trigger_name="night_reminder")
    _mark("night_reminder")
    logger.info("[scheduler] 晚安消息已发送")


def propose_morning_greeting(ctx: dict | None = None):
    """Shadow proposal for morning_greeting; read-only and does not mark cooldown."""
    cfg = _cfg()
    if not cfg.get("morning_greeting", True):
        return None
    now = _proposal_now(ctx)
    if not (7 <= now.hour < 9):
        return None
    from core.scheduler.rhythm import daytime_window_ratio, is_present

    if not is_present(_proposal_ts(ctx, now)):
        return None
    oid = _owner_id()
    if oid and _user_talked_today(oid):
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="morning_greeting",
        urgency=urgency_in_tier(UrgencyTier.DAILY_RHYTHM, daytime_window_ratio(now, 7, 9)),
        topic_source="random",
        requires_state=[TriggerState.QUIET, TriggerState.RESTLESS],
        bypass_state_machine=False,
        execute=_make_prompt_execute(
            "morning_greeting",
            lambda: f"（清晨，{_char_name()}看了看时间，想着你应该快起床了）",
        ),
    )


def propose_night_reminder(ctx: dict | None = None):
    """Shadow proposal for night_reminder; read-only and does not mark cooldown."""
    cfg = _cfg()
    if not cfg.get("night_reminder", True):
        return None
    now = _proposal_now(ctx)
    from core.scheduler.rhythm import in_night_window, is_present, night_window_ratio, triggered_on_logical_day

    if not in_night_window(now):
        return None
    if not is_present(_proposal_ts(ctx, now)):
        return None
    if triggered_on_logical_day("night_reminder", now):
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="night_reminder",
        urgency=urgency_in_tier(UrgencyTier.DAILY_RHYTHM, night_window_ratio(now)),
        topic_source="random",
        requires_state=[TriggerState.QUIET, TriggerState.RESTLESS],
        bypass_state_machine=False,
        execute=_make_prompt_execute(
            "night_reminder",
            lambda: f"（深夜，{_char_name()}看了眼时间）",
        ),
    )


def propose_daily_journal(ctx: dict | None = None):
    """Shadow proposal for daily_journal; read-only and does not mark cooldown."""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return None
    now = _proposal_now(ctx)
    if now.hour < 23:
        return None
    oid = _owner_id()
    if not oid:
        return None
    from core.scheduler.rhythm import night_window_ratio, quiet_floor_elapsed, triggered_on_logical_day

    if not quiet_floor_elapsed(oid, _proposal_ts(ctx, now)):
        return None
    if triggered_on_logical_day("daily_journal", now):
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="daily_journal",
        urgency=urgency_in_tier(UrgencyTier.DAILY_RHYTHM, night_window_ratio(now)),
        topic_source="diary",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_prompt_execute(
            "daily_journal",
            lambda: "（深夜，他回想起今天和你说的话，提笔写下此刻的感受，并且一想到你，就忍不住写了很多）",
            search_query="今天",
            after_send=_write_inner_daily_journal,
        ),
    )


async def _check_random_message(force: bool = False):
    """随机日间消息：10-18点，每天随机触发一次。force=True 跳过时间和概率检查"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send(force=force):
        return
    cfg = _cfg()
    if not cfg.get("random_message", True):
        return
    if not _is_ready("random_message"):
        return

    if not force:
        now = datetime.now()
        if not (10 <= now.hour < 18):
            return
        # 保底逻辑：今天10点后超过4小时没有主动消息，必定触发
        last = _last_trigger.get("random_message", 0)
        hours_since = (time.time() - last) / 3600
        if hours_since < 4:
            # 4小时内触发过，走概率
            if random.random() > (1 / 240):
                return
        # 超过4小时未触发，直接放行（保底）

    oid = _owner_id()
    _picked_key = ""

    try:
        from core.memory.event_log import get_highlights
        from core.scheduler.last_mentioned import topic_key_for
        highlights = get_highlights(oid, days=2)
        if highlights:
            import random
            items = [h.strip() for h in highlights.split("\n") if h.strip()]
            if items:
                picked = random.choice(items)
            else:
                picked = highlights
            _picked_key = topic_key_for(picked)
            context_hint = f"（{_char_name()}想到了一件事：{picked}）"
        else:
            context_hint = ""
    except Exception:
        context_hint = ""

    prompt = _build_random_message_prompt(context_hint)
    await _pipeline_send(prompt, trigger_name="random_message")
    _mark("random_message")
    if _picked_key:
        from core.scheduler.last_mentioned import mark_recent_topic
        mark_recent_topic(_picked_key, "random", dry_run=False)
    logger.info("[scheduler] 随机日间消息已发送")


def propose_random_message(ctx: dict | None = None):
    ctx = ctx or {}
    cfg = _cfg()
    if not cfg.get("random_message", True):
        return None
    now = _proposal_now(ctx)
    if not (10 <= now.hour < 18):
        return None
    oid = _owner_id()
    if not oid:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.rhythm import silence_ratio
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="random_message",
        urgency=urgency_in_tier(UrgencyTier.FILLER, silence_ratio(oid, _proposal_ts(ctx, now))),
        topic_source="random",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_random_message_execute(oid),
    )


async def _check_weather(force: bool = False):
    """天气联动：多场景触发，有氛围感"""
    from core.config_loader import get_config
    from core.scheduler.execution import legacy_tick_should_send

    if not get_config().get("tools", {}).get("weather", {}).get("enabled", True):
        return
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return
    legacy_send = legacy_tick_should_send(force=force)
    if legacy_send and not _is_ready("weather_alert"):
        return
    if not force:
        now = datetime.now()
        if not (8 <= now.hour < 21):
            return

    oid = _owner_id()
    if not oid:
        return

    try:
        from core.memory.user_profile import load as _load_profile
        location = _load_profile(oid).get("location", "")
        if not location:
            return

        from core.tools.weather import get_weather_detail
        w = await get_weather_detail(location)
        if not w:
            return
        _remember_weather_detail(w)

        now      = datetime.now()
        desc = w["desc"]
        temp = w["temp_c"]
        prompt = _weather_prompt(w, now, location)

        if not legacy_send:
            return
        if prompt:
            await _pipeline_send(prompt, trigger_name="weather_alert")
            _mark("weather_alert")
            logger.info(f"[scheduler] 天气触发: {desc} {temp}°C")
        else:
            logger.debug(f"[scheduler] 天气无需触发: {desc} {temp}°C")

    except Exception as e:
        log_error("scheduler._check_weather", e)


def _remember_weather_detail(detail: dict) -> None:
    global _LAST_WEATHER_DETAIL
    _LAST_WEATHER_DETAIL = {**detail, "received_at": time.time()}


def get_last_weather_detail() -> dict | None:
    return dict(_LAST_WEATHER_DETAIL) if _LAST_WEATHER_DETAIL else None


def _classify_weather(detail: dict, now: datetime) -> tuple[str, float] | None:
    temp = detail["temp_c"]
    humidity = detail["humidity"]
    precip = detail["precip_mm"]
    cloud = detail["cloud_cover"]
    wind = detail["wind_kmph"]
    desc = detail["desc"]
    is_day = detail["is_day"]
    uv = detail["uv_index"]

    if any(k in desc for k in ("暴雨", "大雨", "雷暴", "雷阵雨")) or precip > 10:
        return "heavy", min(1.0, max(0.0, precip / 30))
    if temp >= 30:
        return "heavy", min(1.0, (temp - 30) / 10)
    if temp <= -5:
        return "heavy", min(1.0, (-5 - temp) / 15)
    if any(k in desc for k in ("雾", "霾", "大雾")):
        return "light", 0.6
    if any(k in desc for k in ("小雨", "毛毛雨", "阵雨")) and precip > 0:
        return "light", min(1.0, max(0.2, precip / 5))
    if wind > 40:
        return "light", min(1.0, (wind - 40) / 40)
    if cloud < 20 and is_day and uv >= 6 and 11 <= now.hour < 14:
        return "light", min(1.0, (uv - 6) / 5)
    if cloud < 30 and 17 <= now.hour < 19:
        return "light", 0.5
    if humidity > 85 and any(k in desc for k in ("晴", "多云")):
        return "light", min(1.0, (humidity - 85) / 15)
    return None


def propose_weather_alert(ctx: dict | None = None):
    return _propose_weather_alert(ctx, required_severity="heavy")


def propose_weather_alert_light(ctx: dict | None = None):
    return _propose_weather_alert(ctx, required_severity="light")


def _propose_weather_alert(ctx: dict | None = None, required_severity: str = "heavy"):
    ctx = ctx or {}
    from core.config_loader import get_config
    if not get_config().get("tools", {}).get("weather", {}).get("enabled", True):
        return None
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return None
    now = _proposal_now(ctx)
    if not (8 <= now.hour < 21):
        return None
    detail = ctx.get("weather_detail") or get_last_weather_detail()
    if not detail:
        return None
    now_ts = _proposal_ts(ctx, now)
    if now_ts - float(detail.get("received_at") or now_ts) > 6 * 3600:
        return None
    try:
        classified = _classify_weather(detail, now)
    except Exception:
        return None
    if classified is None:
        return None
    severity, ratio = classified
    if severity != required_severity:
        return None
    location = _weather_location()
    if not location:
        return None
    prompt = _weather_prompt(detail, now, location)
    if not prompt:
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.rhythm import daytime_window_ratio
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    tier = UrgencyTier.WINDOW_EVENT if severity == "heavy" else UrgencyTier.REACTIVE
    required_state = [TriggerState.QUIET, TriggerState.RESTLESS] if severity == "heavy" else [TriggerState.QUIET]
    urgency_ratio = max(float(ratio), daytime_window_ratio(now, 8, 21)) if severity == "heavy" else float(ratio)
    return TriggerProposal(
        trigger_name="weather_alert",
        urgency=urgency_in_tier(tier, urgency_ratio),
        topic_source="random",
        requires_state=required_state,
        bypass_state_machine=False,
        execute=_make_prompt_execute(
            "weather_alert",
            lambda detail=detail, now=now, location=location: _weather_prompt(detail, now, location) or "",
            reads_cache_ok=bool(detail),
        ),
    )


async def _check_daily_journal():
    """每日手账：23点后，读取今天event_log，让角色写一段心理活动发给你"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send():
        return
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return
    if not _is_ready("daily_journal"):
        return
    now = datetime.now()
    if now.hour < 23:
        return
    oid = _owner_id()
    if not oid:
        return
    try:
        await _pipeline_send(
            "（深夜，他回想起今天和你说的话，提笔写下此刻的感受，并且一想到你，就忍不住写了很多）",
            search_query="今天",
            trigger_name="daily_journal",
        )

        # 存储角色的日记到内心文档
        try:
            from pathlib import Path
            import asyncio

            from core.sandbox import get_paths
            from core.scheduler.rhythm import logical_day
            diary_dir = get_paths().yexuan_inner_diary()
            diary_dir.mkdir(parents=True, exist_ok=True)

            from core import llm_client
            from core.memory.event_log import get_recent_days

            char_name = _char_name()
            oid = _owner_id()
            today_log = get_recent_days(oid, days=1)

            if today_log:
                # 第一次调用：客观分析器写事件层
                facts_prompt = f"""你是一个对话记录分析器。请从下面的对话日志里提取今天发生的客观事件，只输出事件列表，不要任何分析或感受：

格式要求：
## 今日事件
- HH:MM 用一句话描述发生了什么（纯事实，不带情绪）
- HH:MM 用一句话描述发生了什么
（3到6条，按时间顺序，没有时间戳就省略时间）

重要：你不是{char_name}，你是分析器。只写事实，不写感受，不写文学化内容。

对话日志：
{today_log[:800]}"""

                facts_content = await llm_client.chat(
                    messages=[{"role": "user", "content": facts_prompt}],
                    max_tokens_override=200,
                )

                # 第二次调用：角色视角写感受层
                feeling_prompt = f"""你是{char_name}，请用第一人称写今天的感受，不超过150字。

今天发生的事：
{facts_content}

要求：
- 用{char_name}自己的语气，可以文学化
- 只写感受和心理活动，不要重复叙述事件
- 不要标题，直接写内容"""

                feeling_content = await llm_client.chat(
                    messages=[{"role": "user", "content": feeling_prompt}],
                    max_tokens_override=250,
                )

                from core.integrity_check import check_diary_facts
                _issues = check_diary_facts(facts_content)
                if _issues:
                    logger.warning(f"[daily_journal] 事件层未通过规则纠察，跳过写入: {_issues}")
                    facts_content = ""  # 清空事件层，感受层仍然正常写入

                if facts_content or feeling_content:
                    today = logical_day().strftime("%Y-%m-%d")
                    diary_file = diary_dir / f"{today}.md"
                    parts = [f"# {today}\n"]
                    if facts_content:
                        parts.append(facts_content.strip())
                    if feeling_content:
                        parts.append(f"\n## 今日感受\n{feeling_content.strip()}")
                    diary_file.write_text("\n".join(parts) + "\n", encoding="utf-8")
                    logger.info(f"[scheduler] 角色日记已存储（双层）: {today}")
        except Exception as e:
            from core.error_handler import log_error
            log_error("scheduler._check_daily_journal.diary", e)

        _mark("daily_journal")
        logger.info("[scheduler] 每日手账已发送")
    except Exception as e:
        log_error("scheduler._check_daily_journal", e)


async def _check_episodic_decay():
    """每日情景记忆衰减，23点后触发。"""
    now = datetime.now()
    if now.hour < 23:
        return
    if not _is_ready("episodic_decay"):
        return
    oid = _owner_id()
    if not oid:
        return
    try:
        from core.memory.episodic_memory import decay_all
        decay_all(oid)
        _mark("episodic_decay")
        logger.info("[scheduler] 情景记忆衰减完成")
    except Exception as e:
        log_error("scheduler._check_episodic_decay", e)


async def check_activity_switch() -> None:
    """每次调度器循环时检查是否需要切换activity。"""
    try:
        from core.activity_manager import should_switch, switch_activity
        if should_switch():
            switch_activity()
    except Exception as e:
        from core.error_handler import log_error
        log_error("scheduler.activity_switch", e)


async def _check_spontaneous_recall():
    """主动回忆：低频随机触发，角色突然想起一段往事。"""
    from core.scheduler.execution import legacy_tick_should_send

    if not legacy_tick_should_send():
        return
    import random
    if not _is_ready("spontaneous_recall"):
        return
    if random.random() > 0.10:
        return
    now = datetime.now()
    if not (14 <= now.hour <= 22):
        return
    oid = _owner_id()
    if not oid:
        return
    try:
        from core.memory.episodic_memory import _load_memories
        memories = _load_memories(oid)
        if not memories:
            return
        candidates = [m for m in memories if m.get("strength", 0) > 0.5]
        if not candidates:
            return
        chosen = random.choice(candidates)
        summary = chosen.get("summary", "")
        feeling = chosen.get("yexuan_feeling", "")
        if not summary:
            return
        prompt = f"（{_char_name()}突然想起了一件事：{summary}，那时他{feeling}）"
        await _pipeline_send(prompt, trigger_name="spontaneous_recall")
        _mark("spontaneous_recall")
        logger.info(f"[scheduler] 主动回忆触发: {summary}")
    except Exception as e:
        log_error("scheduler._check_spontaneous_recall", e)


def propose_spontaneous_recall(ctx: dict | None = None):
    ctx = ctx or {}
    now = _proposal_now(ctx)
    if not (14 <= now.hour <= 22):
        return None
    oid = _owner_id()
    if not oid:
        return None
    try:
        from core.memory.episodic_memory import _load_memories

        memories = ctx.get("episodic_memories")
        if memories is None:
            memories = _load_memories(oid)
        if not memories:
            return None
        from core.scheduler import execution as scheduler_execution

        candidates = _spontaneous_recall_candidates(
            memories,
            now_ts=_proposal_ts(ctx, now),
            shadow=scheduler_execution.EXECUTE_MODE == "dry_run",
        )
        if not candidates:
            return None
    except Exception as e:
        log_error("scheduler.propose_spontaneous_recall", e)
        return None

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.rhythm import silence_ratio
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    return TriggerProposal(
        trigger_name="spontaneous_recall",
        urgency=urgency_in_tier(UrgencyTier.FILLER, silence_ratio(oid, _proposal_ts(ctx, now))),
        topic_source="episodic",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_spontaneous_recall_execute(random.choice(candidates)),
    )


async def _check_dlq_monitor():
    """每日扫描 DLQ 目录，文件数 > 0 时 log warning。不发送任何消息，纯观测。"""
    if not _is_ready("dlq_monitor"):
        return

    try:
        from core.sandbox import get_paths
        dlq_dir = get_paths().dead_letter_queue()

        if not dlq_dir.exists():
            _mark("dlq_monitor")
            return

        import json as _json

        json_files = list(dlq_dir.glob("*.json"))

        # ── R8-A: 30-day expiry for legacy DLQ tasks ──────────────────────────
        from core.post_process.slow_queue import LEGACY_TASK_TYPES, is_dlq_task_expired
        expired_dir = dlq_dir / "expired"
        expired_count = 0
        active_files = []
        for _f in json_files:
            _stem_parts = _f.stem.split("_", 1)
            _file_task_type = _stem_parts[1] if len(_stem_parts) == 2 else "unknown"
            if _file_task_type in LEGACY_TASK_TYPES:
                try:
                    _rec = _json.loads(_f.read_text(encoding="utf-8-sig"))
                except Exception:
                    _rec = {}
                _fmtime: float | None = None
                try:
                    _fmtime = _f.stat().st_mtime
                except Exception:
                    pass
                if is_dlq_task_expired(_rec, filename=_f.name, file_mtime=_fmtime):
                    _failed_at = _rec.get("failed_at")
                    _age_days = (time.time() - float(_failed_at)) / 86400.0 if _failed_at else 0.0
                    try:
                        expired_dir.mkdir(parents=True, exist_ok=True)
                        _f.rename(expired_dir / _f.name)
                        expired_count += 1
                        logger.info(
                            "[slow_queue] event=slow_queue_dlq_expired task_type=%s "
                            "age_days=%.1f ttl_days=30 reason=legacy_task_type_over_ttl file=%s",
                            _file_task_type, _age_days, _f.name,
                        )
                    except Exception as _mv_err:
                        logger.warning("[dlq_monitor] 无法归档过期文件 %s: %s", _f.name, _mv_err)
                        active_files.append(_f)
                    continue
            active_files.append(_f)
        if expired_count:
            logger.info("[dlq_monitor] 已归档 %d 个过期 legacy DLQ 任务到 expired/", expired_count)
        json_files = active_files
        # ── end R8-A ───────────────────────────────────────────────────────────

        if not json_files:
            _mark("dlq_monitor")
            return

        # 按 task_type 分组计数，文件名格式：{ms_ts}_{task_type}.json
        from collections import Counter
        type_counts: Counter = Counter()
        for f in json_files:
            parts = f.stem.split("_", 1)
            task_type = parts[1] if len(parts) == 2 else "unknown"
            type_counts[task_type] += 1

        total = len(json_files)
        breakdown = ", ".join(f"{t}: {c}" for t, c in type_counts.most_common())

        # 取最近 3 个文件，读 error 字段首行作摘要
        recent = sorted(json_files, key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        samples = []
        for f in recent:
            try:
                data = _json.loads(f.read_text(encoding="utf-8-sig"))
                err = str(data.get("error", "")).split("\n")[0].strip()
                if err:
                    samples.append(f"[{f.name}] {err}")
            except Exception:
                pass

        sample_str = "; ".join(samples) if samples else "（无法读取错误信息）"
        logger.warning(
            f"DLQ 中有 {total} 个未处理失败任务 ({breakdown})。最近错误样本: {sample_str}"
        )
        # 超出条数上限时删最旧（文件名以 ms_ts 开头，字典序 = 时间序）
        from core.config_loader import get_config
        max_files = int(get_config().get("retention", {}).get("dead_letter_queue", {}).get("max_files", 200))
        if total > max_files:
            oldest = sorted(json_files, key=lambda f: f.name)[:total - max_files]
            pruned = 0
            for f in oldest:
                try:
                    f.unlink()
                    pruned += 1
                except Exception:
                    pass
            if pruned:
                logger.info("[dlq_monitor] 已删除 %d 个最旧 DLQ 文件（上限 %d）", pruned, max_files)
    except Exception as e:
        log_error("scheduler._check_dlq_monitor", e)

    _mark("dlq_monitor")


def _make_prompt_execute(
    trigger_name: str,
    prompt_factory,
    *,
    search_query: str = "",
    reads_cache_ok: bool = True,
    after_send=None,
):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        return await execute_prompt(
            trigger_name=trigger_name,
            prompt_factory=prompt_factory,
            dry_run=dry_run,
            search_query=search_query,
            would_mark=[trigger_name],
            reads_cache_ok=reads_cache_ok,
            after_send=after_send,
        )

    return execute


def _make_random_message_execute(oid: str):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        return await execute_prompt(
            trigger_name="random_message",
            prompt_factory=lambda: _build_random_message_prompt(
                _random_message_context_hint(oid, dry_run=dry_run)
            ),
            dry_run=dry_run,
            would_mark=["random_message"],
        )

    return execute


def _random_message_context_hint(oid: str, *, dry_run: bool = False) -> str:
    try:
        from core.memory.event_log import get_highlights
        from core.scheduler.last_mentioned import (
            compute_topic_freshness,
            mark_recent_topic,
            topic_key_for,
        )

        highlights = get_highlights(oid, days=2)
        if not highlights:
            return ""
        items = [h.strip() for h in highlights.split("\n") if h.strip()]
        if not items:
            return ""

        now = datetime.now()
        pairs = [(item, topic_key_for(item)) for item in items]
        weights = [
            compute_topic_freshness(tk, "random", now=now, dry_run=dry_run) if tk else 1.0
            for _, tk in pairs
        ]
        picked_item, picked_key = random.choices(pairs, weights=weights, k=1)[0]
        if picked_key:
            mark_recent_topic(picked_key, "random", now=now, dry_run=dry_run)
        return f"（{_char_name()}想到了一件事：{picked_item}）"
    except Exception:
        return ""


def _build_random_message_prompt(context_hint: str = "") -> str:
    prompt = f"（{_char_name()}在做自己的事，忽然想到你）"
    if context_hint:
        prompt = f"{prompt}\n{context_hint}"
    return prompt


def _weather_location() -> str:
    oid = _owner_id()
    if not oid:
        return ""
    try:
        from core.memory.user_profile import load as _load_profile

        return str(_load_profile(oid).get("location", "") or "")
    except Exception:
        return ""


def _weather_prompt(detail: dict, now: datetime, location: str) -> str | None:
    temp = detail["temp_c"]
    humidity = detail["humidity"]
    precip = detail["precip_mm"]
    cloud = detail["cloud_cover"]
    wind = detail["wind_kmph"]
    desc = detail["desc"]
    is_day = detail["is_day"]
    uv = detail["uv_index"]

    # 极端天气（最高优先级）
    if any(k in desc for k in ("暴雨", "大雨", "雷暴", "雷阵雨")) or precip > 10:
        return f"（{_char_name()}看了一眼{location}的天气，外面在下大雨）"
    if temp >= 30:
        return f"（{_char_name()}看到{location}今天{temp}度，皱了皱眉，并把温度告知给你）"
    if temp <= -5:
        return f"（{_char_name()}看到{location}今天零下{abs(temp)}度，有点担心，并把温度告知给你）"

    # 氛围天气（次优先级）
    if any(k in desc for k in ("雾", "霾", "大雾")):
        return f"（{_char_name()}看到{location}今天有雾，能见度很低）"
    if any(k in desc for k in ("小雨", "毛毛雨", "阵雨")) and precip > 0:
        return f"（{_char_name()}注意到{location}在下小雨，有点淅淅沥沥的）"
    if wind > 40:
        return f"（{_char_name()}看到{location}今天风很大，{wind}km/h）"

    # 好天气氛围（低优先级，只在特定时段触发）
    if cloud < 20 and is_day and uv >= 6 and 11 <= now.hour < 14:
        return f"（{_char_name()}抬头看了看，{location}今天阳光很好）"
    if cloud < 30 and 17 <= now.hour < 19:
        return f"（{_char_name()}往窗外看了一眼，{location}傍晚的光很好看）"
    if humidity > 85 and any(k in desc for k in ("晴", "多云")):
        return f"（{_char_name()}感觉{location}今天有点闷热潮湿）"
    return None


async def _write_inner_daily_journal() -> None:
    try:
        from core.sandbox import get_paths
        from core.scheduler.rhythm import logical_day

        diary_dir = get_paths().yexuan_inner_diary()
        diary_dir.mkdir(parents=True, exist_ok=True)

        from core import llm_client
        from core.memory.event_log import get_recent_days

        char_name = _char_name()
        oid = _owner_id()
        today_log = get_recent_days(oid, days=1)

        if not today_log:
            return

        facts_prompt = f"""你是一个对话记录分析器。请从下面的对话日志里提取今天发生的客观事件，只输出事件列表，不要任何分析或感受：

格式要求：
## 今日事件
- HH:MM 用一句话描述发生了什么（纯事实，不带情绪）
- HH:MM 用一句话描述发生了什么
（3到6条，按时间顺序，没有时间戳就省略时间）

重要：你不是{char_name}，你是分析器。只写事实，不写感受，不写文学化内容。

对话日志：
{today_log[:800]}"""

        facts_content = await llm_client.chat(
            messages=[{"role": "user", "content": facts_prompt}],
            max_tokens_override=200,
        )

        feeling_prompt = f"""你是{char_name}，请用第一人称写今天的感受，不超过150字。

今天发生的事：
{facts_content}

要求：
- 用{char_name}自己的语气，可以文学化
- 只写感受和心理活动，不要重复叙述事件
- 不要标题，直接写内容"""

        feeling_content = await llm_client.chat(
            messages=[{"role": "user", "content": feeling_prompt}],
            max_tokens_override=250,
        )

        from core.integrity_check import check_diary_facts
        _issues = check_diary_facts(facts_content)
        if _issues:
            logger.warning(f"[daily_journal] 事件层未通过规则纠察，跳过写入: {_issues}")
            facts_content = ""

        if facts_content or feeling_content:
            today = logical_day().strftime("%Y-%m-%d")
            diary_file = diary_dir / f"{today}.md"
            parts = [f"# {today}\n"]
            if facts_content:
                parts.append(facts_content.strip())
            if feeling_content:
                parts.append(f"\n## 今日感受\n{feeling_content.strip()}")
            diary_file.write_text("\n".join(parts) + "\n", encoding="utf-8")
            logger.info(f"[scheduler] 角色日记已存储（双层）: {today}")
    except Exception as e:
        log_error("scheduler._write_inner_daily_journal", e)


def _spontaneous_recall_prompt(candidates: list[dict]) -> str:
    chosen = random.choice(candidates)
    return _spontaneous_recall_prompt_for_memory(chosen)


def _spontaneous_recall_candidates(
    memories: list[dict],
    *,
    now_ts: float,
    shadow: bool,
) -> list[dict]:
    from core.scheduler.last_mentioned import is_recently_recalled

    prepared_memories: list[dict] = []
    for memory in memories:
        if not isinstance(memory, dict):
            continue
        try:
            strength = float(memory.get("strength", 0))
        except (TypeError, ValueError):
            strength = 0.0
        if strength <= 0.5:
            continue
        prepared = _prepare_spontaneous_recall_memory(memory)
        if prepared is None:
            continue
        prepared_memories.append(prepared)

    prepared_memories.sort(
        key=lambda item: (
            float(item.get("strength") or 0.0),
            float(item.get("timestamp") or 0.0),
        ),
        reverse=True,
    )
    recall_window = prepared_memories[:_SPONTANEOUS_RECALL_TOP_K]
    return [
        item for item in recall_window
        if not is_recently_recalled(item["_memory_key"], now_ts=now_ts, shadow=shadow)
    ]


def _prepare_spontaneous_recall_memory(memory: dict) -> dict | None:
    summary = _memory_recall_summary(memory)
    if not summary:
        return None
    memory_key = memory_key_for_recall(memory)
    if not memory_key:
        return None
    prepared = dict(memory)
    prepared["_recall_summary"] = summary
    prepared["_recall_feeling"] = _memory_recall_feeling(memory)
    prepared["_memory_key"] = memory_key
    return prepared


def memory_key_for_recall(memory: dict) -> str:
    raw_id = str(memory.get("id") or "").strip()
    if raw_id:
        return f"episode:{raw_id}"
    basis = _memory_recall_summary(memory)
    if not basis:
        facts = memory.get("raw_facts")
        if isinstance(facts, list):
            basis = " ".join(str(x).strip() for x in facts if str(x).strip())
    normalized = _normalize_memory_key_text(basis)
    if not normalized:
        return ""
    return f"content:{sha1(normalized.encode('utf-8')).hexdigest()[:16]}"


def _memory_recall_summary(memory: dict) -> str:
    for key in ("narrative_summary", "summary"):
        value = str(memory.get(key) or "").strip()
        if value:
            return value
    facts = memory.get("raw_facts")
    if isinstance(facts, list):
        joined = "；".join(str(item).strip() for item in facts if str(item).strip())
        if joined:
            return joined[:80]
    return ""


def _memory_recall_feeling(memory: dict) -> str:
    for key in ("yexuan_feeling", "emotion_texture", "emotion_arc"):
        value = str(memory.get(key) or "").strip()
        if value:
            return value
    return ""


def _normalize_memory_key_text(text: str) -> str:
    return re.sub(r"[\s\t\r\n，。！？!?、,.；;：:\"'“”‘’（）()\[\]【】<>《》…—-]+", "", str(text or "").lower())


def _spontaneous_recall_prompt_for_memory(memory: dict) -> str:
    summary = str(memory.get("_recall_summary") or _memory_recall_summary(memory)).strip()
    feeling = str(memory.get("_recall_feeling") or _memory_recall_feeling(memory)).strip()
    if feeling:
        return f"（{_char_name()}突然想起了一件事：{summary}，那时他{feeling}）"
    return f"（{_char_name()}突然想起了一件事：{summary}）"


def _make_spontaneous_recall_execute(memory: dict):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt
        from core.scheduler.last_mentioned import (
            mark_memory_recalled,
            mark_memory_recalled_shadow,
            mark_recent_topic,
        )

        memory_key = str(memory.get("_memory_key") or memory_key_for_recall(memory))

        def _after_send():
            mark_memory_recalled(memory_key)
            mark_recent_topic(memory_key, "recall")

        result = await execute_prompt(
            trigger_name="spontaneous_recall",
            prompt_factory=lambda: _spontaneous_recall_prompt_for_memory(memory),
            dry_run=dry_run,
            would_mark=["spontaneous_recall"],
            topic_key=memory_key,
            after_send=_after_send,
        )
        if dry_run:
            mark_memory_recalled_shadow(memory_key)
            mark_recent_topic(memory_key, "recall", dry_run=True)
        return result

    return execute


def _proposal_now(ctx: dict | None) -> datetime:
    if ctx and ctx.get("now_dt") is not None:
        return ctx["now_dt"]
    if ctx and ctx.get("now_ts") is not None:
        return datetime.fromtimestamp(float(ctx["now_ts"]))
    return datetime.now()


def _proposal_ts(ctx: dict | None, now: datetime) -> float:
    if ctx and ctx.get("now_ts") is not None:
        return float(ctx["now_ts"])
    return now.timestamp()


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("morning_greeting", propose_morning_greeting)
    register_proposer("night_reminder", propose_night_reminder)
    register_proposer("daily_journal", propose_daily_journal)
    register_proposer("weather_alert_heavy", propose_weather_alert, trigger_names={"weather_alert"})
    register_proposer("weather_alert_light", propose_weather_alert_light, trigger_names={"weather_alert"})
    register_proposer("random_message", propose_random_message)
    register_proposer("spontaneous_recall", propose_spontaneous_recall)


_register_proposers()
