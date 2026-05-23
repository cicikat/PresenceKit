import logging
import random
import re
import time
from datetime import datetime

from core.error_handler import log_error
from core.scheduler.loop import _is_ready, _mark, _owner_id, _pipeline_send, _cfg, _user_talked_today, _last_trigger, _char_name

logger = logging.getLogger(__name__)


async def _check_morning(force: bool = False):
    """早安触发：7-9点，且用户今天还没说过话。force=True 跳过时间和对话检查"""
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
    )


async def _check_random_message(force: bool = False):
    """随机日间消息：10-18点，每天随机触发一次。force=True 跳过时间和概率检查"""
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

    try:
        from core.memory.event_log import get_highlights
        highlights = get_highlights(oid, days=2)
        if highlights:
            import random
            items = [h.strip() for h in highlights.split("\n") if h.strip()]
            if items:
                picked = random.choice(items)
            else:
                picked = highlights
            context_hint = f"（{_char_name()}想到了一件事：{picked}）"
        else:
            context_hint = ""
    except Exception:
        context_hint = ""

    prompt = f"（{_char_name()}在做自己的事，忽然想到你）"
    if context_hint:
        prompt = f"（{_char_name()}在做自己的事，忽然想到你）\n{context_hint}"
    await _pipeline_send(prompt, trigger_name="random_message")
    _mark("random_message")
    logger.info("[scheduler] 随机日间消息已发送")


async def _check_weather(force: bool = False):
    """天气联动：多场景触发，有氛围感"""
    from core.config_loader import get_config
    if not get_config().get("tools", {}).get("weather", {}).get("enabled", True):
        return
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return
    if not _is_ready("weather_alert"):
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

        temp     = w["temp_c"]
        feels    = w["feels_like"]
        humidity = w["humidity"]
        precip   = w["precip_mm"]
        cloud    = w["cloud_cover"]
        wind     = w["wind_kmph"]
        desc     = w["desc"]
        is_day   = w["is_day"]
        uv       = w["uv_index"]
        now      = datetime.now()

        prompt = None

        # 极端天气（最高优先级）
        if any(k in desc for k in ("暴雨", "大雨", "雷暴", "雷阵雨")) or precip > 10:
            prompt = f"（{_char_name()}看了一眼{location}的天气，外面在下大雨）"
        elif temp >= 30:
            prompt = f"（{_char_name()}看到{location}今天{temp}度，皱了皱眉，并把温度告知给你）"
        elif temp <= -5:
            prompt = f"（{_char_name()}看到{location}今天零下{abs(temp)}度，有点担心，并把温度告知给你）"

        # 氛围天气（次优先级）
        elif any(k in desc for k in ("雾", "霾", "大雾")):
            prompt = f"（{_char_name()}看到{location}今天有雾，能见度很低）"
        elif any(k in desc for k in ("小雨", "毛毛雨", "阵雨")) and precip > 0:
            prompt = f"（{_char_name()}注意到{location}在下小雨，有点淅淅沥沥的）"
        elif wind > 40:
            prompt = f"（{_char_name()}看到{location}今天风很大，{wind}km/h）"

        # 好天气氛围（低优先级，只在特定时段触发）
        elif cloud < 20 and is_day and uv >= 6 and 11 <= now.hour < 14:
            prompt = f"（{_char_name()}抬头看了看，{location}今天阳光很好）"
        elif cloud < 30 and 17 <= now.hour < 19:
            prompt = f"（{_char_name()}往窗外看了一眼，{location}傍晚的光很好看）"
        elif humidity > 85 and any(k in desc for k in ("晴", "多云")):
            prompt = f"（{_char_name()}感觉{location}今天有点闷热潮湿）"

        if prompt:
            await _pipeline_send(prompt, trigger_name="weather_alert")
            _mark("weather_alert")
            logger.info(f"[scheduler] 天气触发: {desc} {temp}°C")
        else:
            logger.debug(f"[scheduler] 天气无需触发: {desc} {temp}°C")

    except Exception as e:
        log_error("scheduler._check_weather", e)


async def _check_daily_journal():
    """每日手账：23点后，读取今天event_log，让角色写一段心理活动发给你"""
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

        json_files = list(dlq_dir.glob("*.json"))
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
        import json as _json
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
    except Exception as e:
        log_error("scheduler._check_dlq_monitor", e)

    _mark("dlq_monitor")


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
