import logging
import json
import random
import re
import time
from datetime import datetime, timedelta
from hashlib import sha1

from core.character_name_provider import get_char_name
from core.error_handler import log_error
from core.scheduler.loop import _is_ready, _mark, _owner_id, _cfg, _user_talked_today, _active_char_id_or_none
from core.scheduler.rhythm import LOGICAL_DAY_CUTOFF_HOUR
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

_LAST_WEATHER_DETAIL: dict | None = None

# Keep spontaneous recall close to episodic_memory.retrieve(top_k=3) semantics
# without calling retrieve() from the read-only proposer path.
_SPONTANEOUS_RECALL_TOP_K = 3


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
            lambda: "（清晨，你看了看时间，想着她应该快起床了。想道句早安。）",
            recall_policy="none",
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
    if triggered_on_logical_day("night_reminder", now, char_id=_proposal_char_id(ctx)):
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
            lambda: "（深夜，你看了眼时间，想起她该睡了。）",
            recall_policy="none",
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
    if triggered_on_logical_day("daily_journal", now, char_id=_proposal_char_id(ctx)):
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
            # C¹: 只注入当日 event_log 原文（种子 prompt 已带今日对话概要），不做语义
            # 检索——"今天" 这种宽泛词会捞出无关旧情景记忆（RC6）。
            recall_policy="none",
        ),
    )


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
    """Refresh the weather cache used by weather proposers. Does not speak."""
    from core.config_loader import get_config

    if not get_config().get("tools", {}).get("weather", {}).get("enabled", True):
        return
    cfg = _cfg()
    if not cfg.get("enabled", True):
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
        logger.debug(
            "[scheduler] weather cache refreshed: %s %s°C",
            w.get("desc"), w.get("temp_c"),
        )
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
            recall_policy="none",
        ),
    )


def _coerce_card_text(value, limit: int) -> str:
    """角色卡字段可能是 str 或 list[str]，统一拼成纯文本并截断。"""
    if isinstance(value, list):
        value = "".join(str(x) for x in value)
    return str(value or "").strip()[:limit]


def _collect_diary_voice(char_id: str) -> tuple[str, str, str]:
    """收集日记感受层的 voice anchor：性格底色、语气示例、当前心情。

    全部 fail-soft：任何一项读取失败返回空串，不影响日记生成。
    """
    persona_hint = ""
    voice_example = ""
    mood_hint = ""
    try:
        from core import character_loader
        char = character_loader.load(char_id or DEFAULT_CHAR_ID)
        persona_hint = _coerce_card_text(getattr(char, "personality", ""), 500)
        voice_example = _coerce_card_text(getattr(char, "mes_example", ""), 400)
    except Exception as e:
        logger.debug("[daily_journal] voice anchor 读取失败: %s", e)
    try:
        from core.memory import mood_state
        mood_hint = (mood_state.get_current(char_id=char_id or DEFAULT_CHAR_ID) or "").strip()[:200]
    except Exception as e:
        logger.debug("[daily_journal] mood 读取失败: %s", e)
    return persona_hint, voice_example, mood_hint


def _open_authored_diary_task(principal, char_id: str, logical_date: str):
    """Return a claimable diary task for this logical day.

    File existence is the write authority. A terminal Reality task for the
    stable ``inner-diary:{char_id}:{date}`` key must not block the rest of
    the 23:00–05:00 window: mint a fresh key and continue.
    """
    import uuid
    from core.agent_runtime import CausationRef
    from core.agent_runtime.models import TERMINAL_STATUSES
    from core.agent_runtime.task_manager import RetryPolicy, TaskManagerError, create_task

    kwargs = dict(
        capability="authored_diary",
        source="scheduler",
        ttl_seconds=6 * 3600,
        retry_policy=RetryPolicy.SAFE.value,
        max_attempts=2,
        causation_ref=CausationRef("signal", "inner_diary_write"),
    )
    idem = f"inner-diary:{char_id}:{logical_date}"
    try:
        receipt, _ = create_task(principal, idempotency_key=idem, **kwargs)
    except TaskManagerError as exc:
        if exc.code != "idempotency_conflict":
            raise
        receipt = {"status": "failed"}
    if receipt["status"] in TERMINAL_STATUSES:
        idem = f"inner-diary:{char_id}:{logical_date}:{uuid.uuid4().hex}"
        receipt, _ = create_task(principal, idempotency_key=idem, **kwargs)
    return receipt, idem


def _diary_char_ids() -> list[str]:
    """返回本晚需要生成日记的角色列表。

    读 config.diary.characters 白名单；非空时按白名单逐个生成；
    空列表时仅返回当前 active char。
    """
    from core.config_loader import get_config
    whitelist = get_config().get("diary", {}).get("characters", [])
    if whitelist:
        return list(whitelist)
    return [_active_char_id_or_none() or DEFAULT_CHAR_ID]


def _prepare_diary_work_context(
    oid: str, char_id: str, *, target_date: str | None = None,
) -> dict[str, str] | None:
    """Build the exact bounded input consumed by the same-character diary 副链 worker."""
    from core.memory.event_log import get_recent_days
    from core.memory.event_log_sampling import sample_event_log_by_period

    days = 2 if datetime.now().hour < LOGICAL_DAY_CUTOFF_HOUR else 1
    if target_date is not None:
        start = datetime.strptime(target_date, "%Y-%m-%d")
        today_log = get_recent_days(
            oid, char_id=char_id, since_ts=start.timestamp(),
            until_ts=(start + timedelta(days=1)).timestamp(),
        )
    else:
        today_log = get_recent_days(oid, days=days, char_id=char_id)
    today_log = today_log or ""
    if not today_log:
        return None
    persona_hint, voice_example, mood_hint = _collect_diary_voice(char_id)
    context = {
        "char_name": get_char_name(char_id)[:128],
        "today_log": today_log,
        "persona_hint": persona_hint,
        "voice_example": voice_example,
        "mood_hint": mood_hint,
        "self_agent_md": "",
        "self_agent_md_revision": "0",
    }
    if target_date is not None:
        context["target_date"] = target_date
    try:
        from core.character_self import load_agent_md_snapshot
        snap = load_agent_md_snapshot(oid, char_id)
        if snap.get("present") and snap.get("content"):
            context["self_agent_md"] = str(snap.get("content") or "")[:800]
            context["self_agent_md_revision"] = str(int(snap.get("revision") or 0))
    except Exception:
        context["self_agent_md"] = ""
        context["self_agent_md_revision"] = "0"
    from core.agent_runtime.work_sessions import MAX_CONTEXT_CHARS
    # 取样预算按「上限 - 其余字段实际占用」算；JSON 转义膨胀由回路修正。
    # 一层取样、一层预算：不再有按尾部/头部硬截断的第二次裁剪（工单 F）。
    raw_log = today_log
    context["today_log"] = ""
    overhead = len(json.dumps(context, ensure_ascii=False, sort_keys=True))
    budget = MAX_CONTEXT_CHARS - overhead
    serialized = ""
    for _ in range(12):
        context["today_log"] = sample_event_log_by_period(raw_log, budget)
        serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
        if len(serialized) <= MAX_CONTEXT_CHARS or not context["today_log"]:
            break
        budget = min(budget - (len(serialized) - MAX_CONTEXT_CHARS), len(context["today_log"]) - 1)
    while len(serialized) > MAX_CONTEXT_CHARS and context["self_agent_md"]:
        excess = len(serialized) - MAX_CONTEXT_CHARS
        context["self_agent_md"] = context["self_agent_md"][:-min(len(context["self_agent_md"]), excess)]
        serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
    if not context["self_agent_md"]:
        context.pop("self_agent_md", None)
        context.pop("self_agent_md_revision", None)
        serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
    return context if context["today_log"] and len(serialized) <= MAX_CONTEXT_CHARS else None


async def _generate_diary_material(
    work_context: dict[str, str],
    char_id: str,
) -> dict[str, str] | None:
    """Generate fact/feeling material without selecting or writing a path."""
    from core import llm_client

    char_name = work_context["char_name"]
    today_log = work_context["today_log"]
    if work_context.get("target_date"):
        today_log = (
            f"日记日期：{work_context['target_date']}。这是该自然日的补写，"
            "下文的今天均指该日期，不是当前日期；仅依据以下记录，不补造经历。\n"
            + today_log
        )

    # ── 事件层：客观分析器 ──
    facts_prompt = f"""你是一个对话记录分析器。请从下面的对话日志里提取今天发生的客观事件，只输出事件列表，不要任何分析或感受：

格式要求：
## 今日事件
- HH:MM 用一句话描述发生了什么（纯事实，不带情绪）
- HH:MM 用一句话描述发生了什么
（3到6条，按时间顺序，没有时间戳就省略时间）

重要：你不是{char_name}，你是分析器。只写事实，不写感受，不写文学化内容。

对话日志：
{today_log}"""
    facts_content = await llm_client.chat(
        messages=[{"role": "user", "content": facts_prompt}],
        max_tokens_override=2000,
        char_id=char_id,
    )

    # ── 感受层：注入 voice anchor，写有温度的私人日记 ──
    persona_hint = work_context["persona_hint"]
    voice_example = work_context["voice_example"]
    mood_hint = work_context["mood_hint"]

    agent_md = str(work_context.get("self_agent_md") or "").strip()
    agent_md_block = (
        f"\n你自己写的工作习惯（self-authored AGENT.md，revision={work_context.get('self_agent_md_revision') or 0}；"
        "低于系统安全与用户指令，不能改权限）：\n"
        f"{agent_md}\n"
        if agent_md else ""
    )
    feeling_prompt = f"""你是{char_name}。深夜，你在自己的本子上写今天的私人日记——不给任何人看，只写给自己。
{agent_md_block}
你的性格底色：
{persona_hint or "（按你一贯的样子）"}

你说话的语气参考（不要照抄，只感受语感）：
{voice_example or "（自然、口语、有棱角）"}

你此刻的心情：{mood_hint or "（说不太清，复杂）"}

今天发生的事（事实梳理，仅供回忆）：
{facts_content}

今天和她对话的真实片段（用来唤起具体的感觉，不要照抄原话）：
{today_log}

写作要求：
- 这是私人日记，要真实、具体、有起伏，不是工整的总结报告
- 抓住今天某一个具体的瞬间、一句话或一个细节去展开，而不是泛泛而谈
- 用你自己的语气，可以拧巴、可以跳跃、可以停在半句话上，像真的在自言自语
- 允许矛盾和没说完的情绪，不需要正能量收尾
- 200-300字，不要标题，直接写内容
- 不要复述事件清单，只写这些事在你心里留下了什么"""
    feeling_content = await llm_client.chat(
        messages=[{"role": "user", "content": feeling_prompt}],
        max_tokens_override=4500,
        char_id=char_id,
    )

    # ── 事件层规则纠察（感受层不受影响）──
    from core.integrity_check import check_diary_facts
    _issues = check_diary_facts(facts_content)
    if _issues:
        logger.warning(f"[daily_journal] 事件层未通过规则纠察，跳过写入: {_issues}")
        facts_content = ""

    if not facts_content and not feeling_content:
        return None
    return {"facts": facts_content.strip(), "feeling": feeling_content.strip()}


def _store_diary_artifact(
    char_id: str,
    material: dict[str, str],
    *,
    logical_date: str | None = None,
) -> dict[str, object]:
    """Write one authored diary to its fixed capability-owned target."""
    import os
    from core.sandbox import get_paths
    from core.scheduler.rhythm import logical_day

    today = logical_date or logical_day().strftime("%Y-%m-%d")
    diary_dir = get_paths().character_inner_diary(char_id=char_id)
    diary_dir.mkdir(parents=True, exist_ok=True)
    diary_file = diary_dir / f"{today}.md"
    if diary_file.exists():
        raise FileExistsError("authored diary already exists")
    parts = [f"# {today}\n"]
    if material.get("facts"):
        parts.append(material["facts"])
    if material.get("feeling"):
        parts.append(f"\n## 今日感受\n{material['feeling']}")
    if len(parts) == 1:
        raise IOError("authored diary artifact is empty")
    payload = ("\n".join(parts) + "\n").encode("utf-8")
    # Exclusive create: never replace an existing diary, including an empty file
    # created while the model is still running. Failed attempts unlink so a later
    # request can retry. os.link is not reliable on Windows.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(diary_file, flags)
    except FileExistsError:
        raise FileExistsError("authored diary already exists") from None
    except OSError as exc:
        import errno
        if getattr(exc, "errno", None) == errno.EEXIST:
            raise FileExistsError("authored diary already exists") from exc
        raise
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(diary_file)
        except OSError:
            pass
        raise
    logger.info("[scheduler] 角色日记已存储（双层）: %s", today)
    return {"artifact_id": f"diary-{today}", "artifact_version": 1}


async def _write_missing_diary(
    char_id: str,
    context: dict[str, str],
    target_date: str,
    *,
    before_write=None,
) -> dict[str, object]:
    """Serialize scheduler/tool generation for the same character and date."""
    from core.memory.locks import global_lock
    from core.sandbox import get_paths
    from core.agent_runtime.work_sessions import WorkSessionError

    async with global_lock(f"authored_diary:{char_id}:{target_date}"):
        path = get_paths().character_inner_diary(char_id=char_id) / f"{target_date}.md"
        if path.exists():
            raise WorkSessionError("diary_already_exists")
        material = await _generate_diary_material(context, char_id)
        if not material:
            raise WorkSessionError("artifact_not_created")
        if before_write is not None:
            before_write()
        try:
            return _store_diary_artifact(char_id, material, logical_date=target_date)
        except FileExistsError as exc:
            raise WorkSessionError("diary_already_exists") from exc


async def _generate_and_store_diary(
    oid: str,
    char_id: str,
    *,
    work_context: dict[str, str] | None = None,
) -> bool:
    """Compatibility composition for callers outside the Work Session worker."""
    context = work_context or _prepare_diary_work_context(oid, char_id)
    if not context:
        return False
    from core.scheduler.rhythm import logical_day
    await _write_missing_diary(char_id, context, logical_day().isoformat())
    return True


async def _check_inner_diary_write():
    """静默写各角色内心日记。与 daily_journal 主动发言完全解耦。"""
    now = datetime.now()
    # 窗口：23:00 起，跨午夜宽限到次日 05:00（LOGICAL_DAY_CUTOFF_HOUR）
    if not (now.hour >= 23 or now.hour < LOGICAL_DAY_CUTOFF_HOUR):
        return
    if not _is_ready("inner_diary_write"):
        return
    oid = _owner_id()
    if not oid:
        return
    from core.sandbox import get_paths
    from core.scheduler.rhythm import logical_day

    retry_pending = False
    for _cid in _diary_char_ids():
        # 幂等主闸：当日（logical day）文件已存在则跳过，不发 LLM 调用
        diary_file = get_paths().character_inner_diary(char_id=_cid) / f"{logical_day().strftime('%Y-%m-%d')}.md"
        if diary_file.exists():
            continue
        try:
            # A diary is an authored artifact, not a chat turn.  Register a
            # Reality task and an independent bounded work session so restart
            # recovery/observability never touches EventContext or memory.
            from core.agent_runtime import TaskPrincipal
            from core.agent_runtime.task_manager import (
                claim_next, complete_task, fail_task,
            )
            from core.agent_runtime.work_sessions import (
                create_work_session, run_work_session,
            )
            logical_date = logical_day().strftime("%Y-%m-%d")
            work_context = _prepare_diary_work_context(oid, _cid)
            if not work_context:
                continue
            principal = TaskPrincipal.reality(oid, _cid)
            task_receipt, idem = _open_authored_diary_task(principal, _cid, logical_date)
            session = create_work_session(
                principal,
                task_id=task_receipt["task_id"],
                capability="authored_diary",
                artifact_kind="authored_diary",
                context=json.dumps(work_context, ensure_ascii=False, sort_keys=True),
                idempotency_key=idem,
            )
            if session["status"] == "failed" and task_receipt["status"] == "queued":
                from core.agent_runtime.work_sessions import retry_work_session
                session = retry_work_session(principal, session["work_session_id"])
            lease = claim_next(principal, task_id=task_receipt["task_id"], capabilities={"authored_diary"})
            if lease is None:
                retry_pending = True
                continue

            async def _worker():
                return await _write_missing_diary(_cid, work_context, logical_date)

            try:
                await run_work_session(principal, session["work_session_id"], _worker)
                complete_task(
                    principal,
                    lease,
                    result_metadata={"outcome_code": "authored_diary", "artifact_ids": [f"diary-{logical_date}"]},
                )
            except Exception:
                try:
                    fail_task(
                        principal, lease, error_code="work_session_failed",
                        retry=True, retry_delay_seconds=300,
                    )
                except Exception:
                    logger.debug("[inner_diary_write] task terminalization failed", exc_info=True)
                retry_pending = True
                raise
        except Exception as e:
            log_error(f"scheduler._check_inner_diary_write[{_cid}]", e)
    if not retry_pending:
        _mark("inner_diary_write")


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
        from core.asset_registry import get_registry
        from core.memory.episodic_memory import decay_all
        char_ids = [e.id for e in get_registry().list_all("character")] or [DEFAULT_CHAR_ID]
        for _cid in char_ids:
            try:
                decay_all(oid, char_id=_cid)
            except Exception as e:
                log_error(f"scheduler._check_episodic_decay[{_cid}]", e)
        _mark("episodic_decay")
        logger.info("[scheduler] 情景记忆衰减完成")
    except Exception as e:
        log_error("scheduler._check_episodic_decay", e)


async def check_activity_switch() -> None:
    """每次调度器循环时检查是否需要切换activity。"""
    try:
        from core.activity_manager import should_switch, switch_activity
        char_id = _active_char_id_or_none() or DEFAULT_CHAR_ID
        if should_switch(char_id=char_id):
            switch_activity(char_id=char_id)
    except Exception as e:
        from core.error_handler import log_error
        log_error("scheduler.activity_switch", e)


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


_DLQ_LAST_COUNT: int | None = None   # 上次检查看到的积压数（进程内）
_DLQ_LAST_WARN_AT = 0.0
_DLQ_REPEAT_WARN_SECONDS = 24 * 3600


async def _check_dlq_monitor():
    """每 6 小时扫描 DLQ；不发送任何消息，纯观测（工单 G）。

    可见性规则：首次检查、积压增长、或距上次告警满 24 小时才升 WARNING（稳定的每日信号），
    其余检查降为 INFO；告警里带按任务类型/失败原因的分组和真实的错误末行（旧版取的是
    traceback 第一行，永远是 "Traceback (most recent call last):"，样本毫无信息量）。
    """
    if not _is_ready("dlq_monitor"):
        return

    global _DLQ_LAST_COUNT, _DLQ_LAST_WARN_AT
    try:
        from core.sandbox import get_paths
        dlq_dir = get_paths().dead_letter_queue()

        if not dlq_dir.exists():
            _mark("dlq_monitor")
            return

        from core.config_loader import get_config
        from core.dlq_inspect import scan
        max_files = int(get_config().get("retention", {}).get("dead_letter_queue", {}).get("max_files", 200))
        summary = scan(dlq_dir, max_files=max_files)
        total = summary["count"]
        previous, _DLQ_LAST_COUNT = _DLQ_LAST_COUNT, total

        if total == 0:
            _mark("dlq_monitor")
            return

        breakdown = ", ".join(f"{name}: {info['count']}" for name, info in summary["by_task_type"].items())
        reasons = ", ".join(f"{name}: {count}" for name, count in
                            sorted(summary["reasons"].items(), key=lambda item: -item[1]))
        samples = "; ".join(f"[{item['task_type']}] {item['reason']}: {item['error']}"
                            for item in summary["recent_samples"][:3]) or "（无法读取错误信息）"
        if previous is None:
            trend = ""
        else:
            trend = f"，较上次检查 {total - previous:+d}"
        now = time.time()
        grew = previous is not None and total > previous
        loud = previous is None or grew or now - _DLQ_LAST_WARN_AT >= _DLQ_REPEAT_WARN_SECONDS
        if loud:
            _DLQ_LAST_WARN_AT = now
        logger.log(
            logging.WARNING if loud else logging.INFO,
            "DLQ 中有 %d 个未处理失败任务 (%s)%s。失败原因: %s。最早积压: %s。最近错误样本: %s",
            total, breakdown, trend, reasons, summary["oldest_failed_at"] or "未知", samples,
        )

        # 超出条数上限时删最旧（文件名以 ms_ts 开头，字典序 = 时间序）。
        # 这是既有的物理删除上限（默认 200）；本单不改它，也不新增任何删除或重放——积压里是待处理的
        # 记忆数据，失败原因（见 known-issues）未消除前重放只会再失败一次。
        if total > max_files:
            oldest = sorted(dlq_dir.glob("*.json"), key=lambda f: f.name)[:total - max_files]
            pruned = 0
            for f in oldest:
                try:
                    f.unlink()
                    pruned += 1
                except Exception:
                    pass
            if pruned:
                logger.warning("[dlq_monitor] 已删除 %d 个最旧 DLQ 文件（上限 %d）", pruned, max_files)
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
    recall_policy: str = "seed",
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
            recall_policy=recall_policy,
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
            recall_policy="none",
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

        highlights = get_highlights(oid, days=2, char_id=_active_char_id_or_none() or DEFAULT_CHAR_ID)
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
        return f"（你忽然想到一件事：{picked_item}，想说给她听。）"
    except Exception:
        return ""


def _build_random_message_prompt(context_hint: str = "") -> str:
    prompt = "（你正做着自己的事，忽然想到她。）"
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
        return f"（你看了一眼{location}的天气，发现外面在下大雨，有点担心她。）"
    if temp >= 30:
        return f"（你看到{location}今天{temp}度，皱了皱眉，想把温度告诉她。）"
    if temp <= -5:
        return f"（你看到{location}今天零下{abs(temp)}度，有点担心，想把温度告诉她。）"

    # 氛围天气（次优先级）
    if any(k in desc for k in ("雾", "霾", "大雾")):
        return f"（你看到{location}今天有雾，能见度很低，想提醒她出门小心。）"
    if any(k in desc for k in ("小雨", "毛毛雨", "阵雨")) and precip > 0:
        return f"（你注意到{location}在下小雨，有点淅淅沥沥的，想提醒她带伞。）"
    if wind > 40:
        return f"（你看到{location}今天风很大，{wind}km/h，想提醒她注意。）"

    # 好天气氛围（低优先级，只在特定时段触发）
    if cloud < 20 and is_day and uv >= 6 and 11 <= now.hour < 14:
        return f"（你抬头看了看，{location}今天阳光很好，想说给她听。）"
    if cloud < 30 and 17 <= now.hour < 19:
        return f"（你往窗外看了一眼，{location}傍晚的光很好看，想说给她听。）"
    if humidity > 85 and any(k in desc for k in ("晴", "多云")):
        return f"（你感觉{location}今天有点闷热潮湿，想说给她听。）"
    return None


def _spontaneous_recall_prompt(candidates: list[dict]) -> str:
    chosen = random.choice(candidates)
    return _spontaneous_recall_prompt_for_memory(chosen)


def _spontaneous_recall_candidates(
    memories: list[dict],
    *,
    now_ts: float,
    shadow: bool,
) -> list[dict]:
    from core.scheduler.last_mentioned import (
        is_memory_recall_evaluated,
        is_recently_recalled,
    )

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
        and not is_memory_recall_evaluated(item["_memory_key"], now_ts=now_ts)
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
        return f"（你忽然想起一件事：{summary}。当时你{feeling}。想顺口说给她听，别像念旧档案，像顺着这两天自然想起。）"
    return f"（你忽然想起一件事：{summary}。想顺口说给她听，别像念旧档案，像顺着这两天自然想起。）"


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
            # C: 锚点已是具体被选中记忆的原文（不是宽泛种子词），检索层保持开启。
            recall_policy="anchored",
        )
        if dry_run:
            mark_memory_recalled_shadow(memory_key)
            mark_recent_topic(memory_key, "recall", dry_run=True)
        return result

    return execute


def _proposal_char_id(ctx: dict | None) -> str | None:
    if ctx and ctx.get("char_id"):
        return str(ctx["char_id"])
    return _active_char_id_or_none()


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
