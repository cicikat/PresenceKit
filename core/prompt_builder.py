"""
Prompt 构建模块
分层顺序组装完整的消息列表
每一层都有清晰的注释说明其来源和作用
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from core.character_loader import Character
from core.error_handler import log_error
from core.prompt_ablation import ALWAYS_ON
from core.data_paths import DEFAULT_CHAR_ID


@dataclass
class LayerSpec:
    name: str
    mode: Literal["always", "tagged", "scored"]
    triggers: list[str] = field(default_factory=list)
    token_budget: int = 0

logger = logging.getLogger(__name__)
_prompt_logger = logging.getLogger("prompt_builder.token")

_WATCH_FRESH_DAYS = 3
_WATCH_TRIGGERS = {
    "topic.energy", "topic.health", "topic.activity", "query.body_state",
}
_GROWTH_SELF_TRIGGERS = {
    "topic.writing", "topic.drawing", "topic.music", "topic.learning",
    "query.growth_self",
}


def _watch_segment_is_fresh(segment_time: str, *, today=None) -> bool:
    """Return whether a watch sleep segment is recent enough for layer 3.6."""
    try:
        from datetime import date
        from core.config_loader import get_config

        if today is None:
            today = date.today()
        raw_days = (get_config().get("watch") or {}).get(
            "fresh_days", _WATCH_FRESH_DAYS
        )
        fresh_days = max(0, int(raw_days))
        segment_date = date.fromisoformat(str(segment_time)[:10])
        age_days = (today - segment_date).days
        return 0 <= age_days <= fresh_days
    except Exception:
        return False


def _format_growth_self_hint(char_id: str = DEFAULT_CHAR_ID) -> str:
    """Render up to two active interests as a soft character-self hint."""
    try:
        from core.growth.interest_state import active_interests
        from core.growth.notes import load as load_notes

        parts: list[str] = []
        for interest in active_interests(char_id)[:2]:
            name = str(interest.get("name") or "").strip()
            if not name:
                continue
            level = max(1, min(5, int(interest.get("level", 1) or 1)))
            text = f"你最近在学{name}，到 level {level} 了"
            notes = load_notes(str(interest.get("id") or ""), char_id=char_id)
            if notes:
                latest = str(notes[-1].get("text") or "").strip()
                if latest:
                    text += f"；上次琢磨出：{latest}"
            parts.append(text)
        if not parts:
            return ""
        return "（" + "；".join(parts) + "。这是你自己的近况，可自然提起，别像报数据。）"
    except Exception:
        return ""

# tone → soft description for afterglow hint (see _format_afterglow_soft_hint)
_AG_TONE_DESC: dict[str, str] = {
    "comfort":  "warm, calm",
    "warm":     "warm, calm",
    "safe":     "warm, calm",
    "trusted":  "warm, calm",
    "calm":     "calm",
    "stress":   "uneasy",
    "fear":     "uneasy",
    "threat":   "uneasy",
}


def _format_dream_afterglow_detail(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    """Return the active clear/fading dream summary, failing closed."""
    try:
        from core.dream.dream_afterglow import load_afterglow
        return load_afterglow(uid, char_id=char_id)
    except Exception as exc:
        logger.warning("[prompt_builder] dream afterglow detail read failed: %s", exc)
        return ""


def _format_afterglow_soft_hint(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    """Return a short soft-hint string if a fresh afterglow residue exists, else ''.

    Read-only.  Never raises.  Never writes memory / mood / profile / hidden state.
    Injects only when tone or at least one emotional_tag is in _AG_TONE_DESC (whitelist).
    Returns '' when: residue absent, TTL expired, tone not whitelisted and no whitelisted
    tag found, or any read error.
    """
    try:
        from core.memory.user_hidden_state import read_afterglow_residue
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        residue = read_afterglow_residue(uid, now, char_id=char_id)
        if residue is None:
            return ""
        tone_desc = _AG_TONE_DESC.get(residue.tone, "")
        if not tone_desc:
            # Tone not whitelisted — check tags for any whitelisted entry
            for tag in residue.emotional_tags:
                tone_desc = _AG_TONE_DESC.get(tag, "")
                if tone_desc:
                    break
        if not tone_desc:
            return ""
        return (
            "[recent_dream_afterglow]\n"
            f"近期梦境余韵可能轻微影响用户此刻的语气：{tone_desc}。"
        )
    except Exception as exc:
        logger.warning("[prompt_builder] afterglow soft hint read failed: %s", exc)
        return ""

def _load_activity_snapshot(*, char_id: str) -> str:
    from core.sandbox import get_paths
    import json
    import time
    p = get_paths().activity_snapshot(char_id=char_id)
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("received_at", 0) > 300:
            return ""
        current = data.get("current", {})
        summary = data.get("today_summary", "")
        cat = current.get("category", "")
        dur = current.get("duration_min", 0)
        parts = []
        if cat and cat != "idle":
            parts.append(f"用户现在在{cat}，已经{dur}分钟了")
        if summary:
            parts.append(f"今天：{summary}")
        return "；".join(parts)
    except Exception:
        return ""


_REALTIME_ACTIVITY_LABELS: dict[str, str] = {
    "coding": "写代码",
    "code": "写代码",
    "browsing": "浏览网页",
    "browser": "浏览网页",
    "gaming": "打游戏",
    "game": "打游戏",
    "video": "看视频",
    "social": "使用社交软件",
    "document": "写文档",
    "work": "处理工作",
    "music": "听音乐",
    "creative": "进行创作",
    "idle": "暂时停下来了",
}


def _format_realtime_awareness(tags: set[str], *, now: float | None = None) -> str:
    """Build a short ephemeral sensor hint summarizing app/input activity (no window title)."""
    try:
        import time
        from core.memory import realtime_state

        snap = realtime_state.get()
        if snap is None:
            return ""

        current_time = time.time() if now is None else now
        stale_seconds = current_time - float(snap.get("received_at", 0))
        tag_hit = bool(tags & {"query.what_doing", "topic.activity"})
        input_data = snap.get("input", {})
        idle_seconds = int(input_data.get("idle_seconds", 9999))
        if not ((tag_hit and stale_seconds < 300) or (stale_seconds < 180 and idle_seconds < 120)):
            return ""

        parts: list[str] = []
        screen = snap.get("screen") or {}
        activity = _REALTIME_ACTIVITY_LABELS.get(
            str(screen.get("app_label", "")).casefold()
        )
        if activity:
            parts.append(f"大致在{activity}")
        else:
            app = str(snap.get("focus", {}).get("app", "")).strip()
            # Process name is a coarse app summary. Strip path/control-like characters.
            app = re.sub(r"[^0-9A-Za-z._ +()-]", "", app)[:40]
            if app and app.casefold() != "unknown":
                parts.append(f"在用 {app}")

        edit_hint = input_data.get("edit_hint")
        if edit_hint in ("typing_long", "editing"):
            parts.append("正在认真输入")
        elif edit_hint == "deleting":
            parts.append("像是在反复修改")
        elif idle_seconds >= 120:
            parts.append("暂时停下来了")

        return "，".join(parts)
    except Exception as exc:
        logger.warning("[prompt_builder] realtime awareness read failed: %s", exc)
        return ""


def _load_style_hint(*, char_id: str) -> str:
    """从observations.jsonl读取行为倾向，返回给author_note的提示词片段。"""
    try:
        import json
        from datetime import datetime as _dt
        from core.sandbox import get_paths
        obs_path = get_paths().observations(char_id=char_id)
        if not obs_path.exists():
            return ""
        lines = obs_path.read_text(encoding="utf-8").strip().splitlines()
        hour = _dt.now().hour
        is_night = hour >= 22 or hour <= 5
        hints = []
        for line in lines:
            item = json.loads(line)
            text = item.get("text", "")
            if is_night and any(w in text for w in ["晚上", "深夜", "脆弱", "情绪"]):
                hints.append("温柔克制")
            if any(w in text for w in ["压力", "忙", "累", "焦虑"]):
                hints.append("轻柔")
        if not hints:
            return ""
        hint = list(dict.fromkeys(hints))[0]
        return f"此刻回话倾向：{hint}"
    except Exception:
        return ""


def _normalize_injection(text: str, *, char_name: str) -> str:
    """注入前文本规范化的唯一入口。只清洗 system 层正文；不碰 <...> 标签名，不碰真实对话。"""
    # Split on angle-bracket tokens; rewrite only the text segments between tags.
    parts = re.split(r'(<[^>]+>)', text)
    out: list[str] = []
    for part in parts:
        if part.startswith('<') and part.endswith('>'):
            out.append(part)
        else:
            # Specific compound phrase first — preserves "非角色记忆" semantic marker.
            part = part.replace('用户客观信息', '她的客观信息')
            part = part.replace('该用户', '她')
            part = part.replace('这个用户', '她')
            part = part.replace('用户的', '她的')
            part = part.replace('用户', '她')
            part = re.sub(r'\buser\b', '她', part, flags=re.IGNORECASE)
            out.append(part)
    return ''.join(out)


def _load_jailbreak(layer: int | None = None) -> str:
    """
    两套破限存储合并注入，按内容去重：
    1) stems 源：active_prompt_assets.json 的 enabled_jailbreaks 列表，
       按顺序加载 characters/reality/jailbreaks/{stem}.json。
    2) entries 源：characters/reality/jailbreak_entries.json（前端「偏好→世界→破限条目」
       EntryManager 管理的条目）。
    layer 指定时只返回该层的条目，None 时返回所有启用条目。
    保持 layer 0 / 2 / 11 注入顺序不变（由调用方控制）。
    两源内容重复（`content.strip()` 相同）时只注入一次。
    """
    parts: list[str] = []
    seen: set[str] = set()

    def _add(content: str) -> None:
        content = content.strip()
        if content and content not in seen:
            seen.add(content)
            parts.append(content)

    try:
        import json
        from core.sandbox import get_paths
        paths = get_paths()

        assets_path = paths.active_prompt_assets()
        assets = json.loads(assets_path.read_text(encoding="utf-8"))
        enabled_jailbreaks: list = assets.get("enabled_jailbreaks", [])

        jailbreaks_dir = paths.jailbreaks_dir()

        for stem in enabled_jailbreaks:
            file_path = jailbreaks_dir / f"{stem}.json"
            if not file_path.exists():
                logger.warning(f"[prompt_builder] jailbreak 文件不存在，跳过: {file_path}")
                continue
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception as e:
                from core.error_handler import log_error
                log_error(f"prompt_builder._load_jailbreak.{stem}", e)
                continue

            for e in data.get("entries", []):
                if not e.get("enabled", True):
                    continue
                if layer is not None and e.get("layer", 0) != layer:
                    continue
                _add(e.get("content", ""))
    except Exception as e:
        from core.error_handler import log_error
        log_error("prompt_builder._load_jailbreak.stems", e)

    try:
        import json
        from core.sandbox import get_paths
        entries_path = get_paths().jailbreak_entries()
        entries_data = json.loads(entries_path.read_text(encoding="utf-8"))
        for e in entries_data.get("entries", []):
            if not e.get("enabled", True):
                continue
            if layer is not None and e.get("layer", 0) != layer:
                continue
            _add(e.get("content", ""))
    except Exception as e:
        from core.error_handler import log_error
        log_error("prompt_builder._load_jailbreak.entries", e)

    return "\n".join(parts)

def _recent_openings(history: list[dict], n: int = 3, k: int = 8) -> list[str]:
    """Return the first k chars of the last n assistant turns (fail-open)."""
    outs: list[str] = []
    for turn in reversed(history):
        if turn.get("role") != "assistant":
            continue
        head = str(turn.get("content") or "").lstrip().replace("\n", "")[:k]
        if head:
            outs.append(head)
        if len(outs) >= n:
            break
    return outs


def _dedupe_filler_prefix_history(history: list[dict], prefix: str) -> list[dict]:
    """
    历史投影去同质（问题7 (a)）：只改注入 prompt 的历史副本，绝不写回 short_term 存储。

    近场 assistant 回复连续以填充词前缀 P 开头时，模型会从上下文里"学到"每句都要 P 开头——
    一条软提示对抗不了整段历史的示范效应。这里保留最早一条完整的 P，其余各条剥掉开头的 P，
    使投影给模型看的历史里不再是"每句都嗯。"。

    被剥掉前缀的消息额外带 `_raw_content` 记录原始文本（未去同质），供 pipeline 侧输出端
    校验重试（问题7 (c)）复原同一份检测所需的原始 assistant 历史；`_raw_content` 与 `_layer` 等
    内部字段一样在 `sanitize_messages()` 出口统一剥离，不会发给供应商，也不写回磁盘。
    """
    projected: list[dict] = []
    kept_one = False
    for msg in history:
        content = msg.get("content")
        if (
            msg.get("role") == "assistant"
            and isinstance(content, str)
            and content.lstrip().startswith(prefix)
        ):
            if not kept_one:
                kept_one = True
                projected.append(msg)
            else:
                lstripped = content.lstrip()
                leading_ws = content[: len(content) - len(lstripped)]
                new_msg = dict(msg)
                new_msg["content"] = leading_ws + lstripped[len(prefix):]
                new_msg["_raw_content"] = content
                projected.append(new_msg)
        else:
            projected.append(msg)
    return projected


def build(
    character: Character,
    user_id: str,
    user_message: str,
    history: list[dict],
    relation: dict,
    profile: dict,
    group_context: list[dict],
    user_identity_text: str = "",
    event_search_result: str = "",
    lore_entries: list[str] = None,
    tool_result: str | None = None,
    perception_block: str = "",
    author_note_extra: str = "",
    affection_info: str = "",
    pet_info: str = "",
    current_time: str = "",
    reminders: list = None,
    diary_context: str = "",
    episodic_result: str = "",
    episodic_fallback_result: str = "",
    mid_term_context: str = "",
    tags: set[str] | None = None,
    dream_impression_text: str = "",
    char_id: str = DEFAULT_CHAR_ID,
    user_facts_text: str = "",
    stage_presence: str = "",
    stage_transcript: str = "",
    suppress_emotional_recall: bool = False,
    web_recall_result: str = "",
    web_recall_hits: list | None = None,
    action_trace_entries: list[dict] | None = None,
    coplay_context_text: str = "",
    coplay_residue_text: str = "",
    coplay_recall_text: str = "",
) -> tuple[list[dict], dict]:
    """
    组装完整的 prompt 消息列表

    返回 OpenAI 格式的消息列表，直接传给 llm_client.chat()

    参数说明：
        character:           当前角色卡
        user_id:             用户QQ号
        user_message:        本轮用户消息内容
        history:             短期对话历史 [{role, content}, ...]
        relation:            user_relation.get_relation() 的返回值
        profile:             user_profile.load() 的返回值
        group_context:       group_context.get_recent() 的返回值
        user_identity_text:  user_identity.format_for_prompt() 的返回值（用户稳定行为模式）
        event_search_result: event_log.search() 的返回值（相关往事摘要）
        lore_entries:        lore_engine.match() 的返回值
        tool_result:         本轮工具执行结果（有则注入）
        action_trace_entries: action_trace.recent() 返回的最近工具动作痕迹（层 10.5，跨轮回忆）
        author_note_extra:   consistency_check 发现问题时的纠偏提示
    """
    if lore_entries is None:
        lore_entries = []
    _tags: set[str] = tags or set()
    messages: list[dict] = []

    # 层级消融开关（CC 任务 23 · B）：一次性读取，B3 统一过滤点复用同一结果。
    from core.prompt_ablation import get_state as _ablation_state
    _ab = _ablation_state()

    # 预计算本轮消息间隔（基于 short_term history timestamp，问题3用）
    from core.presence import (
        get_gap_from_history as _get_gap,
        format_gap_text as _fmt_gap,
        _LAST_SEEN_MIN_SECS,
        _GAP_HINT_MIN_SECS,
    )
    _msg_gap_secs: float | None = _get_gap(history)

    # S2 检测：近场 assistant 回复是否连续以同一前缀开头（问题7）。
    # 只检测一次，供层9历史投影去同质 (a) 与层11软提示文案 (b) 复用同一份结果。
    _s2_prefix: str | None = None
    try:
        from core.memory.short_term import detect_reply_homogeneity_prefix as _detect_s2_prefix
        _s2_prefix = _detect_s2_prefix(history)
    except Exception:
        _s2_prefix = None

    # ─────────────────────────────────────────────────────────────────────────
    # 层 0：破限预设（jailbreak，最高优先级，放在最前面）
    # 来自 stems（jailbreaks/{stem}.json，受 enabled_jailbreaks 控制）与
    # characters/reality/jailbreak_entries.json 两套存储中启用且 layer=0 的条目，按内容去重合并
    # ─────────────────────────────────────────────────────────────────────────
    jailbreak_text = _load_jailbreak(layer=0)
    if jailbreak_text:
        messages.append({"role": "system", "content": jailbreak_text, "_layer": "0_jailbreak"})


    # ─────────────────────────────────────────────────────────────────────────
    # 层 1：全局 system prompt（来自角色卡的 system_prompt 字段）
    # ─────────────────────────────────────────────────────────────────────────
    if character.system_prompt:
        perception = perception_block.strip() if perception_block else ""
        if _ab["perception_block_disabled"]:
            perception = ""

        from core.mood_text import get_mood_text
        import json
        from core.sandbox import get_paths
        try:
            mood_raw = json.loads(get_paths().mood_state(char_id=char_id).read_text(encoding="utf-8"))
        except Exception:
            mood_raw = {}
        mood_line = get_mood_text(mood_raw)

        sp = character.system_prompt
        _perception_section = "## 当前感知（实时，非记忆）\n{perception_block}"
        if _perception_section in sp:
            sp = sp.replace(
                _perception_section,
                f"{mood_line}\n\n## 当前感知（实时，非记忆）\n{perception}",
            )
        else:
            sp = sp.replace("{perception_block}", perception)

        messages.append({
            "role": "system",
            "content": sp,
            "_layer": "1_system_prompt",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 1.5：事实边界（数据驱动，条件注入）
    # 空感知 → 单句禁令；有感知 → 给实际数据 + 边界句。
    # _realtime_awareness 在此提前计算，3.9 层复用该变量。
    # ─────────────────────────────────────────────────────────────────────────
    _realtime_awareness = _format_realtime_awareness(_tags)
    if _realtime_awareness:
        _fact_boundary_text = (
            f"【现实信息】{_realtime_awareness}。仅以上为已确认，其余未知。"
            "屏幕上的桌宠形象是你自己在屏幕上的存在，不是用户的角色。"
        )
    else:
        _fact_boundary_text = (
            "【现实信息】当前没有任何已确认的现实细节，"
            "凡未列出的现实物品/食物/天气/她的身体状态一律未知，不补充、不暗示。"
            "没有真实屏幕感知时，不得虚构屏幕画面、界面状态或用户正在做的事。"
            "屏幕上的桌宠形象是你自己在屏幕上的存在，不是用户的角色。"
        )
    messages.append({
        "role": "system",
        "content": _fact_boundary_text,
        "_layer": "1.5_fact_boundary",
    })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 2：角色描述 + 性格 + 情境
    # ─────────────────────────────────────────────────────────────────────────
    char_desc_parts = []
    if character.description:
        char_desc_parts.append(f"【角色描述】\n{character.description}")
    if character.personality:
        char_desc_parts.append(f"【性格】\n{character.personality}")
    scenario_text = character.scenario or ""
    if scenario_text:
        char_desc_parts.append(f"【当前情境】\n{scenario_text}")

    if char_desc_parts:
        messages.append({
            "role": "system",
            "content": "\n\n".join(char_desc_parts),
            "_layer": "2_char_desc",
        })

    # ── Layer 2.2: Stage presence ────────────────────────────────────────────
    if stage_presence:
        messages.append({
            "role": "system",
            "content": stage_presence,
            "_layer": "2.2_stage_presence",
        })

    jb_layer2 = _load_jailbreak(layer=2)
    if jb_layer2:
        messages.append({"role": "system", "content": jb_layer2, "_layer": "2_jailbreak"})

    # ─────────────────────────────────────────────────────────────────────────
    # 层 2.5：当前时间（让角色知道现在几点、星期几）
    # ─────────────────────────────────────────────────────────────────────────
    if current_time:
        messages.append({
            "role": "system",
            "content": f"【当前时间】{current_time}",
            "_layer": "2.5_time",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 2.55：上次说话时间（精确化：基于 short_term history timestamp）
    # 静默时段不显示；gap < 6h 不显示
    # ─────────────────────────────────────────────────────────────────────────
    from core.scheduler.rhythm import is_quiet_sleep_time as _is_quiet
    if (
        not _is_quiet()
        and _msg_gap_secs is not None
        and _msg_gap_secs >= _LAST_SEEN_MIN_SECS
    ):
        messages.append({
            "role": "system",
            "content": f"用户上一条消息距现在{_fmt_gap(_msg_gap_secs)}",
            "_layer": "2.55_last_seen",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 2.6：角色此刻（activity）
    # 对话开头或沉默超10分钟时注入，进行中不干扰连续性
    # ─────────────────────────────────────────────────────────────────────────
    def _is_silent_10min(user_id: str) -> bool:
        try:
            import json, time
            from core.sandbox import get_paths
            p = get_paths().presence()
            if not p.exists():
                return False
            data = json.loads(p.read_text(encoding="utf-8"))
            last_at = data.get(user_id, {}).get("last_message_at", 0)
            return (time.time() - last_at) > 600
        except Exception:
            return False

    logger.info(f"[activity_inject] should_inject={not bool(history)}, history_len={len(history)}")
    if not history or _is_silent_10min(user_id):
        try:
            from core.activity_manager import get_prompt_fragment
            _activity_fragment = get_prompt_fragment(
                char_id=char_id,
                suppress_growth=bool(_tags & _GROWTH_SELF_TRIGGERS),
            )
            logger.info(f"[activity_inject] fragment={_activity_fragment!r}")
            if _activity_fragment:
                messages.append({
                    "role": "system",
                    "content": f"## {character.name}此刻\n{_activity_fragment}",
                    "_layer": "2.6_presence",
                })
        except Exception as _e:
            logger.warning(f"[activity_inject] 异常: {_e}")

    # ─────────────────────────────────────────────────────────────────────────
    # 层 3：与该用户的关系
    # 来自 UserRelation，说明 bot 该用什么态度对待这个用户
    # ─────────────────────────────────────────────────────────────────────────
    # 关系数据不存在/仍是硬编码兜底默认值时，该层整体不注入——没有信息就别说，
    # 胜过注入"陌生人"误导角色对 owner 冷淡（Brief 97 §5，冷启动新用户场景）。
    from core.user_relation import has_configured_relation
    if has_configured_relation(user_id):
        role = relation.get("role", "stranger")
        nickname = relation.get("nickname")
        extra_prompt = relation.get("extra_prompt", "")

        if nickname:
            relation_text = f"该用户是你的{role}，你叫他\"{nickname}\"。"
        else:
            relation_text = f"该用户是你的{role}。"
        if extra_prompt:
            relation_text += extra_prompt

        messages.append({
            "role": "system",
            "content": f"<与用户关系>\n【与该用户的关系】\n{relation_text}\n</与用户关系>",
            "_layer": "3_relation",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 3.5：生理期感知（mode=tagged，经期相关话题才注入）
    # ─────────────────────────────────────────────────────────────────────────
    _period_triggers = {"topic.body", "emotion.physical_discomfort", "query.body_state", "emotion.down", "emotion.indirect"}
    if _tags & _period_triggers:
        try:
            from core.memory.user_profile import get_period_info
            from datetime import date as _date, datetime as _datetime
            _period = get_period_info(user_id, char_id=char_id)
            _last = _period.get("last_period_date")
            if _last:
                _days = (_date.today() - _datetime.strptime(_last, "%Y-%m-%d").date()).days
                if 0 <= _days <= 7:
                    messages.append({
                        "role": "system",
                        "content": f"（她生理期第{_days + 1}天，态度更温柔些，不提冰/冷饮/剧烈运动。）",
                        "_layer": "3.5_period",
                        "_provenance": {
                            "mode": "tagged",
                            "triggers_checked": sorted(_period_triggers),
                            "matched_tags": sorted(_tags & _period_triggers),
                        },
                    })
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # 层 3.6：watch数据摘要（mode=tagged，体能/健康/睡眠相关话题才注入）
    # ─────────────────────────────────────────────────────────────────────────
    if _tags & _WATCH_TRIGGERS:
        try:
            from core.memory.user_profile import load as _load_up
            _up = _load_up(user_id)
            _segs = [s for s in _up.get("sleep_segments", []) if s.get("duration_minutes", 0) > 0]
            if _segs:
                _last_seg = _segs[-1]
                if not _watch_segment_is_fresh(_last_seg.get("time", "")):
                    raise ValueError("stale or invalid watch sleep segment")
                _dur = int(_last_seg.get("duration_minutes", 0))
                _h, _m = _dur // 60, _dur % 60
                _seg_date = _last_seg["time"][:10]
                _start = _last_seg.get("sleep_start", "")
                _end = _last_seg.get("sleep_end_time", "")
                messages.append({
                    "role": "system",
                    "content": f"（她最近一次睡眠：{_seg_date} {_start}–{_end}，共{_h}时{_m}分。可自然提起。）",
                    "_layer": "3.6_watch",
                    "_provenance": {
                        "mode": "tagged",
                        "triggers_checked": sorted(_WATCH_TRIGGERS),
                        "matched_tags": sorted(_tags & _WATCH_TRIGGERS),
                    },
                })
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # 层 3.7：手机传感器摘要（口袋角色回传）
    # ─────────────────────────────────────────────────────────────────────────
    try:
        from core.memory.user_profile import load as _load_up2
        _up2 = _load_up2(user_id)
        _sensor = _up2.get("phone_sensor_today", {})
        _sensor_date = _sensor.get("date", "")
        _today_str = __import__("datetime").date.today().isoformat()
        if _sensor and _sensor_date == _today_str:
            _s_parts = []
            if _sensor.get("steps") is not None:
                _s_parts.append(f"{_sensor['steps']}步")
            if _sensor.get("battery") is not None:
                _s_parts.append(f"电量{_sensor['battery']}%")
            if _sensor.get("location"):
                _s_parts.append(_sensor["location"])
            if _s_parts:
                messages.append({
                    "role": "system",
                    "content": f"（她今天：{'、'.join(_s_parts)}。自然提，别罗列。）",
                    "_layer": "3.7_sensor",
                })
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # 层 3.8：桌宠屏幕活动快照（mode=tagged，活动/询问在做什么时注入）
    # ─────────────────────────────────────────────────────────────────────────
    _activity_triggers = {"topic.activity", "query.what_doing", "emotion.positive"}
    if _tags & _activity_triggers:
        _activity_text = _load_activity_snapshot(char_id=char_id)
        if _activity_text:
            messages.append({
                "role": "system",
                "content": f"（她在{_activity_text}。可自然提起。）",
                "_layer": "3.8_activity",
                "_provenance": {
                    "mode": "tagged",
                    "triggers_checked": sorted(_activity_triggers),
                    "matched_tags": sorted(_tags & _activity_triggers),
                },
            })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 3.8_growth_self：角色自身成长近况（mode=tagged）
    # ─────────────────────────────────────────────────────────────────────────
    if _tags & _GROWTH_SELF_TRIGGERS:
        _growth_self_text = _format_growth_self_hint(char_id)
        if _growth_self_text:
            messages.append({
                "role": "system",
                "content": _growth_self_text,
                "_layer": "3.8_growth_self",
                "_drop_priority": 15,
                "_provenance": {
                    "mode": "tagged",
                    "triggers_checked": sorted(_GROWTH_SELF_TRIGGERS),
                    "matched_tags": sorted(_tags & _GROWTH_SELF_TRIGGERS),
                },
            })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 3.9：桌面实时感知（sidecar /sensor/realtime 快照）
    # mode=tagged/fresh：询问在做什么 / 活动相关话题时注入；或快照极新时主动注入。
    # 只注摘要（app / 输入行为），永不注入 visible_text / clickable_text 原文。
    # TTL：tag 触发允许 5 分钟内快照；无 tag 触发要求 3 分钟内且用户活跃。
    # _realtime_awareness 已在层 1.5 前计算，此处直接复用。
    # ─────────────────────────────────────────────────────────────────────────
    if _realtime_awareness:
        messages.append({
            "role": "system",
            "content": f"（她此刻{_realtime_awareness}，短时线索，别当长期事实。）",
            "_layer": "3.9_screen_awareness",
            "_drop_priority": 25,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 4：群聊上下文（仅群聊时注入，私聊时 group_context 为空列表）
    # 格式："[时间] 发言人：内容"，已按相关性过滤 + 末尾 N 条保底 + 时间升序
    # ─────────────────────────────────────────────────────────────────────────
    if group_context:
        from core.memory.group_context import relevance_score as _gc_relevance
        from core.config_loader import get_config as _gc_get_config
        _cfg_mem = _gc_get_config().get("memory", {})
        _GC_KEEP_LATEST: int = int(_cfg_mem.get("group_context_keep_latest", 3))
        _GC_TOP_K: int = int(_cfg_mem.get("group_context_top_k", 5))
        _GC_MIN_SCORE: float = float(_cfg_mem.get("group_context_min_score", 0.3))

        _char_name: str = getattr(character, "name", "") or ""
        _gc_all = group_context  # already sorted ts-asc by get_recent()
        _gc_tail = _gc_all[-_GC_KEEP_LATEST:] if len(_gc_all) > _GC_KEEP_LATEST else _gc_all
        _gc_head = _gc_all[:-_GC_KEEP_LATEST] if len(_gc_all) > _GC_KEEP_LATEST else []

        _gc_scored = sorted(
            _gc_head,
            key=lambda m: _gc_relevance(m, query_text=user_message, tags=_tags, char_name=_char_name),
            reverse=True,
        )
        _gc_relevant = [
            m for m in _gc_scored
            if _gc_relevance(m, query_text=user_message, tags=_tags, char_name=_char_name) > _GC_MIN_SCORE
        ][:_GC_TOP_K]

        # 合并后按 ts 升序，保持时间顺序
        _gc_merged = {id(m): m for m in (_gc_relevant + list(_gc_tail))}
        _gc_chosen = sorted(_gc_merged.values(), key=lambda m: float(m.get("ts") or 0))

        ctx_lines = []
        for msg in _gc_chosen:
            sender = msg.get("sender_name", "群友")
            ts_label = msg.get("timestamp", "")
            content = msg.get("content", "")
            ctx_lines.append(f"[{ts_label}] {sender}：{content}" if ts_label else f"{sender}：{content}")

        messages.append({
            "role": "system",
            "content": (
                "<群聊上下文>\n"
                "【群聊上下文（以下是群里与当前对话较相关的最近消息，已按相关性挑选、按时间排列，"
                "仅供理解语境，不是对你的直接提问；只在被 @ 或明显需要回应时才接话）】\n"
                + "\n".join(ctx_lines) + "\n</群聊上下文>"
            ),
            "_layer": "4_group_context",
        })

    # ── Layer 4.2: shared Stage transcript ──────────────────────────────────
    if stage_transcript:
        messages.append({
            "role": "system",
            "content": "<群聊对话>\n【当前群聊共享对话】\n" + stage_transcript + "\n</群聊对话>",
            "_layer": "4.2_stage_transcript",
            "_drop_priority": 90,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 5：关于这个用户（用户画像）
    # 稳定字段（name/location/pets/occupation）+ stable/misc 标签事实：100% 注入
    # 易变事实（pref.*/habit/health/status.project）：recency 门控或 tag 命中时召回
    # ─────────────────────────────────────────────────────────────────────────
    import time as _time_mod
    from core.memory.user_profile import _normalize_fact, _is_recency_tag, _recency_window_for

    profile_parts = []
    if profile.get("name"):
        profile_parts.append(f"名字：{profile['name']}")
    if profile.get("location"):
        profile_parts.append(f"地点：{profile['location']}")
    if profile.get("pets"):
        profile_parts.append(f"宠物：{profile['pets']}")
    if profile.get("interests"):
        profile_parts.append(f"兴趣：{profile['interests']}")
    if profile.get("occupation"):
        profile_parts.append(f"职业：{profile['occupation']}")

    # 分拣 important_facts：稳定段直接平铺，易变段按 recency/tag 召回
    _current_ts = _time_mod.time()
    _current_tags: set[str] = tags if tags else set()
    _stable_facts: list[str] = []
    _recency_facts: list[tuple] = []  # (ts, text, tag)

    for raw_fact in profile.get("important_facts") or []:
        norm = _normalize_fact(raw_fact)
        text = norm["text"]
        if not text:
            continue
        fact_tag = norm["tag"]
        if _is_recency_tag(fact_tag):
            _recency_facts.append((norm["ts"], text, fact_tag))
        else:
            _stable_facts.append(text)

    if _stable_facts:
        profile_parts.append("其他：" + "；".join(_stable_facts))

    # 易变段：recency 窗口内 OR 当前话题 tag 命中时注入（tag 前缀匹配）
    _recalled_tagged: list[str] = []   # tag 命中召回
    _recalled_recency: list[str] = []  # 仅 recency 窗口召回
    for ts, text, fact_tag in sorted(_recency_facts, key=lambda x: -x[0]):
        in_window = (_current_ts - ts) < _recency_window_for(fact_tag)
        # tag 命中：pref.music → 查 "music" 是否在当前 tags；habit → 查 "habit"
        tag_key = fact_tag.removeprefix("pref.") if fact_tag.startswith("pref.") else fact_tag
        tag_hit = any(tag_key in t or t in tag_key for t in _current_tags)
        if tag_hit:
            _recalled_tagged.append(text)
        elif in_window:
            _recalled_recency.append(text)
    _recalled_facts = _recalled_tagged + _recalled_recency

    if profile_parts:
        messages.append({
            "role": "system",
            "content": "<用户概况>\n【关于这个用户】\n" + "，".join(profile_parts) + "\n</用户概况>",
            "_layer": "5_profile",
        })

    if _recalled_facts:
        messages.append({
            "role": "system",
            "content": (
                "<用户偏好>\n【用户近期偏好与习惯】\n"
                + "\n".join(f"- {f}" for f in _recalled_facts)
                + "\n</用户偏好>"
            ),
            "_layer": "5_profile_pref",
            "_provenance": {
                "mode": "tagged" if _recalled_tagged else "recency",
                "tagged_count": len(_recalled_tagged),
                "recency_count": len(_recalled_recency),
            },
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 5.1：全局用户事实（跨角色客观信息，与角色主观记忆无关）
    # 来自 user_facts.py；uid-only，不含角色关系史或主观印象。
    # 标题明确区分：角色不应把此处内容当作自己的记忆或感受。
    # ─────────────────────────────────────────────────────────────────────────
    if user_facts_text:
        messages.append({
            "role": "system",
            "content": (
                "<用户客观信息>\n"
                "【用户客观信息（跨角色通用，非角色记忆）】\n"
                + user_facts_text
                + "\n</用户客观信息>"
            ),
            "_layer": "5.1_user_facts",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 5.2：待办备忘录（让角色随时知道用户记了什么）
    # ─────────────────────────────────────────────────────────────────────────
    if reminders:
        reminder_lines = [
            f"- {r['content']}（{r['remind_at']}）" for r in reminders
        ]
        messages.append({
            "role": "system",
            "content": "<待办备忘>\n【待办备忘录】\n" + "\n".join(reminder_lines) + "\n</待办备忘>",
            "_layer": "5.2_reminders",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 5.5：世界书条目（LoreEngine 命中时注入，放在记忆层之前）
    # 世界观背景信息先于角色个人记忆，让记忆有世界观基础
    # ─────────────────────────────────────────────────────────────────────────
    if lore_entries:
        lore_text = "\n\n".join(lore_entries)
        messages.append({
            "role": "system",
            "content": f"<世界书>\n【世界书】\n{lore_text}\n</世界书>",
            "_layer": "5.5_lore",
            "_drop_priority": 80,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 6a：用户稳定行为模式（user_identity，不可裁，与 5_profile 同级）
    # ─────────────────────────────────────────────────────────────────────────
    if user_identity_text:
        _identity_block = (
            "关于用户的长期观察（优先级低于当前对话，如有冲突以当下为准）：\n"
            + user_identity_text
        )
        messages.append({
            "role": "system",
            "content": _identity_block,
            "_layer": "6a_user_identity",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 6b：相关往事（来自 event_log.search() 的摘要，无结果时跳过）
    # TODO: event_log.search() 当前返回拼接字符串，无分数字段；
    #       等 search() 改为返回 (text, score) 后，接入 score < 0.5 不注入的阈值逻辑
    # ─────────────────────────────────────────────────────────────────────────
    # 6b：相关往事。search() 内部已过滤 score < 0.5 的低相关结果，此处直接注入。
    if event_search_result:
        messages.append({
            "role": "system",
            "content": f"<相关往事>\n【相关往事】\n{event_search_result}\n</相关往事>",
            "_layer": "6b_event_search",
            "_drop_priority": 30,
            "_provenance": {
                "mode": "scored",
                "rag_query": user_message[:200],
            },
        })

    # 层 6c：情景记忆（角色视角的情节片段）
    if episodic_result:
        messages.append({
            "role": "system",
            "content": f"<情景记忆>\n【{character.name}记得的片段】\n{episodic_result}\n</情景记忆>",
            "_layer": "6c_episodic",
            "_drop_priority": 70,
            "_provenance": {
                "mode": "scored",
                "rag_query": user_message[:200],
            },
        })
    elif episodic_fallback_result:
        # tag 未命中时兜底：注入近期高强度记忆，标注是自己想起来的
        # _report_layer 与 _layer 有意不同：_layer 保持 "6c_episodic" 以复用同一条
        # 消融/裁剪规则；_report_layer 用于 layers_activated 观测，区分"命中检索"
        # 与"兜底注入"——run_eval/memeval 的 layers_absent 断言依赖这个区分。
        messages.append({
            "role": "system",
            "content": f"<情景记忆>\n【{character.name}最近印象深的事】\n{episodic_fallback_result}\n</情景记忆>",
            "_layer": "6c_episodic",
            "_report_layer": "6c_episodic_fallback",
            "_drop_priority": 70,
            "_provenance": {
                "mode": "scored",
                "rag_query": "(fallback: recent high-strength)",
            },
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 mid_term：过去 12 小时事件压缩视图（介于 episodic 和 diary 之间）
    # format_for_prompt() 已渲染好，空时跳过整个 section
    # ─────────────────────────────────────────────────────────────────────────
    if mid_term_context:
        messages.append({
            "role": "system",
            "content": f"<近12小时摘要>\n# 最近 12 小时\n{mid_term_context}\n</近12小时摘要>",
            "_layer": "mid_term",
            "_drop_priority": 40,
        })

    # ──────────────────────────────────────────────────────────────────────────
    # 层 6d：日记上下文（独立存储，不参与检索，单独注入）
    # ──────────────────────────────────────────────────────────────────────────
    _diary_triggers = {"emotion.down", "emotion.indirect"}
    if diary_context and (_tags & _diary_triggers):
        messages.append({
            "role": "system",
            "content": f"<近期日记>\n【用户的近期日记】\n{diary_context}\n</近期日记>",
            "_layer": "6d_diary_context",
            "_drop_priority": 50,
            "_provenance": {
                "mode": "tagged",
                "triggers_checked": sorted(_diary_triggers),
                "matched_tags": sorted(_tags & _diary_triggers),
            },
        })

    # 层 6e：角色昨天的日记（事件层必注入，感受层按情绪tag条件注入）
    try:
        from pathlib import Path
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        from core.sandbox import get_paths
        _new_diary = get_paths().yexuan_inner_diary(char_id=char_id)
        _diary_dir = _new_diary if _new_diary.is_dir() else get_paths()._p("yexuan_inner", "diary")
        inner_diary = _diary_dir / f"{yesterday}.md"
        if inner_diary.exists():
            diary_text = inner_diary.read_text(encoding="utf-8").strip()
            if diary_text:
                # 拆分事件层和感受层
                _facts_part = ""
                _feeling_part = ""
                if "## 今日感受" in diary_text:
                    _split = diary_text.split("## 今日感受", 1)
                    _facts_part = _split[0].strip()
                    _feeling_part = _split[1].strip()
                else:
                    # 旧格式日记，整体作为感受层兼容
                    _feeling_part = diary_text

                # 事件层：必注入（取前200字）
                if _facts_part:
                    messages.append({
                        "role": "system",
                        "content": f"<昨日记录>\n【{character.name}昨天的记录】\n{_facts_part[:200]}\n</昨日记录>",
                        "_layer": "6e_inner_diary",
                        "_drop_priority": 60,
                    })

                # 感受层：只在情绪相关tag时注入（取前150字）
                _feeling_triggers = {"emotion.down", "emotion.indirect", "emotion.deep", "topic.relation"}
                if _feeling_part and (_tags & _feeling_triggers) and not suppress_emotional_recall:
                    messages.append({
                        "role": "system",
                        "content": f"<昨日心情>\n【{character.name}昨天的心情】\n{_feeling_part[:150]}\n</昨日心情>",
                        "_layer": "6e_inner_diary",
                        "_drop_priority": 60,
                        "_provenance": {
                            "mode": "tagged",
                            "triggers_checked": sorted(_feeling_triggers),
                            "matched_tags": sorted(_tags & _feeling_triggers),
                        },
                    })
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # 层 web_recall（X3）：角色查过的相关网络资料（外部事实，非记忆/经历）
    # 标注来源，提示 LLM 这是外部信息，不应固化为自身记忆。最先裁剪（优先级 35）。
    # ─────────────────────────────────────────────────────────────────────────
    if web_recall_result:
        messages.append({
            "role": "system",
            "content": (
                f"<查到的资料>\n"
                f"【你之前查到的相关网络资料（外部事实，不是你的记忆或亲身经历，注明来源）】\n"
                f"{web_recall_result}\n"
                f"</查到的资料>"
            ),
            "_layer": "web_recall",
            "_drop_priority": 35,
            "_provenance": {
                "mode": "scored",
                "rag_query": user_message[:200],
                "source": "vector_store:web",
                "hits": web_recall_hits or [],
            },
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 6f：梦境余韵（只读，非事实）
    # 0~5h 优先注入逐渐模糊的梦境摘要；摘要层不活跃后才由 residue 软提示接管，
    # 避免同一轮同时注入两种深度的 afterglow。
    # ─────────────────────────────────────────────────────────────────────────
    _afterglow_detail = _format_dream_afterglow_detail(user_id, char_id=char_id)
    if _afterglow_detail:
        messages.append({
            "role": "system",
            "content": _afterglow_detail,
            "_layer": "6f_dream_afterglow",
            "_drop_priority": 10,
        })
    else:
        _afterglow_hint = _format_afterglow_soft_hint(user_id, char_id=char_id)
        if _afterglow_hint:
            messages.append({
                "role": "system",
                "content": _afterglow_hint,
                "_layer": "dream_afterglow_soft_hint",
                "_drop_priority": 10,
            })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 6g：梦境印象回流（ambient，非事实框定，最先裁剪）
    # 来自 impression_loader；仅当有未过期印象时注入。
    # ─────────────────────────────────────────────────────────────────────────
    if dream_impression_text:
        messages.append({
            "role": "system",
            "content": dream_impression_text,
            "_layer": "6g_dream_impression",
            "_drop_priority": 20,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 6h：storyline 叙事弧（mode=tagged，relevance 门控，非常态注入，Brief 80 §4）。
    # identity 回答"他是个什么样的人"，storyline 回答"他在经历什么弧线"——只在本轮话题
    # 触及某条弧线时召回那一条，同 dream 印象/watch/growth_self 的 tagged 惯例，不逐轮刷。
    # backchannel 低信息轮跳过（同 diary 闸的 recall_gate 用法）。
    # _drop_priority=65：介于 mid_term(40) 与 episodic(70) 之间——叙事质量高于中期压缩，
    # 但低于精筛情景记忆。
    # ─────────────────────────────────────────────────────────────────────────
    try:
        from core.recall_gate import is_low_information as _storyline_low_info
        if not _storyline_low_info(user_message):
            from core.memory import storyline as _storyline_store
            _recallable_arcs = _storyline_store.list_recallable_arcs(user_id, char_id=char_id)
            _best_arc = None
            _best_overlap: set[str] = set()
            for _arc in _recallable_arcs:
                _overlap = _tags & set(_arc.get("tags") or [])
                if _overlap and len(_overlap) > len(_best_overlap):
                    _best_arc, _best_overlap = _arc, _overlap
            if _best_arc is not None:
                _recent_nodes = _best_arc["nodes"][-3:]
                _node_text = "；".join(n["summary"] for n in _recent_nodes)
                _arc_text = f"《{_best_arc['title']}》：{_node_text}"[:300]
                messages.append({
                    "role": "system",
                    "content": (
                        f"（这是你记得的一段持续经历，不是此刻发生的事：{_arc_text}）"
                    ),
                    "_layer": "6h_storyline",
                    "_drop_priority": 65,
                    "_provenance": {
                        "mode": "tagged",
                        "triggers_checked": sorted(_best_arc.get("tags") or []),
                        "matched_tags": sorted(_best_overlap),
                    },
                })
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # 层 coplay_context：陪玩模式 active 时的游戏进度 + 最近动态 + 剧透压制硬约束
    # （Brief 41）。已由 core.coplay.game_state.build_coplay_context_text() 整段
    # 拼好（含 <陪玩状态> 定界标签），active 状态才非空。
    # 内容很小（进度行 + 最近3条动态 + 一句硬约束），_drop_priority 放在 lore 之
    # 后（数字更大→更晚丢），token 预算真正吃紧前基本不会被裁到。
    # ─────────────────────────────────────────────────────────────────────────
    if coplay_context_text:
        messages.append({
            "role": "system",
            "content": coplay_context_text,
            "_layer": "coplay_context",
            "_drop_priority": 85,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 coplay_residue_soft_hint：陪玩结束后 4 小时内的软提示（Brief 42）。
    # 与 coplay_context 互斥（调用方保证：只在非 active 时才可能非空）。
    # ─────────────────────────────────────────────────────────────────────────
    if coplay_residue_text:
        messages.append({
            "role": "system",
            "content": coplay_residue_text,
            "_layer": "coplay_residue_soft_hint",
            "_drop_priority": 12,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 coplay_recall：聊天里提到玩过的游戏名/别名时，回忆上次游玩摘要
    # （Brief 42，game_log tag 门控注入）。与 coplay_context 互斥。
    # ─────────────────────────────────────────────────────────────────────────
    if coplay_recall_text:
        messages.append({
            "role": "system",
            "content": coplay_recall_text,
            "_layer": "coplay_recall",
            "_drop_priority": 45,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 7：对话示例（few-shot，来自角色卡的 mes_example 字段）
    # mes_example 格式："{{user}}: xxx\n{{char}}: xxx\n<START>..."
    # ─────────────────────────────────────────────────────────────────────────
    if character.mes_example:
        mes_example_str = character.mes_example
        if isinstance(mes_example_str, list):
            mes_example_str = "\n<START>\n".join(mes_example_str)
        example_messages = _parse_mes_example(mes_example_str, character.name)
        if example_messages:
            messages.append({
                "role": "system",
                "content": (
                    f'<语气示例 note="以下仅为{character.name}的说话风格/语气参考，'
                    f'不是真实发生过的对话，不要当作记忆或事实">'
                ),
                "_layer": "7_mes_example_item",
            })
            for _em in example_messages:
                _em.setdefault("_layer", "7_mes_example_item")
                messages.append(_em)
            messages.append({
                "role": "system",
                "content": "</语气示例>",
                "_layer": "7_mes_example_item",
            })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 9：短期对话历史（最近 N 轮实际对话）
    # ─────────────────────────────────────────────────────────────────────────
    messages.append({
        "role": "system",
        "content": '<对话记录 note="以下是与用户真实发生的对话">',
        "_layer": "9_history",
    })

    # 历史投影去同质（问题7 (a)）：仅当命中前缀属于填充词白名单时才改投影副本，
    # 非填充词前缀（如"现在，"）不做投影改写，只走层11软提示。
    _history_for_prompt = history
    if _s2_prefix:
        try:
            from core.memory.short_term import is_filler_prefix as _is_filler_prefix
            if _is_filler_prefix(_s2_prefix):
                _history_for_prompt = _dedupe_filler_prefix_history(history, _s2_prefix)
        except Exception:
            _history_for_prompt = history

    for _hm in _history_for_prompt:
        # 防御性：trigger_stub（系统触发锚点，含内部 trigger_name 明文）绝不投影进 prompt。
        # 主过滤在 short_term.load_for_prompt，此处为第二道闸，挡住其他历史来源。
        if _hm.get("_source") == "trigger_stub":
            continue
        # short_term 持久化 speaker_id/timestamp/turn_id；当前单聊 prompt 只投影
        # OpenAI 标准字段。Stage 会在 P2 用独立 transcript renderer 展示发言人。
        # _raw_content（若有）只供 pipeline 侧输出校验重试复原原始文本，同为内部字段一并投影。
        _hm_out = {
            "role": _hm.get("role", "user"),
            "content": _hm.get("content", ""),
            "_layer": "9_history",
        }
        if "_raw_content" in _hm:
            _hm_out["_raw_content"] = _hm["_raw_content"]
        messages.append(_hm_out)
    messages.append({
        "role": "system",
        "content": "</对话记录>",
        "_layer": "9_history",
    })

    # 层 9_anti_repeat：跨轮开头去同质（fail-open，只读软约束，drop_priority=15 低优先可裁）
    # 取最近 2–3 条 assistant 回复的起手，告知模型避免复读相同开头，对症「连着好几句『现在，』」
    try:
        _ar_ops = _recent_openings(history)
        if _ar_ops:
            messages.append({
                "role": "system",
                "content": (
                    "<避免复读>\n你最近几句的开头分别是："
                    + "、".join(f"「{o}…」" for o in _ar_ops)
                    + "。这次换一个完全不同的起手，别再用重复开头，"
                    "也别用上面任何一句的句式。自然地说，像真人不会连用同一种开场。\n</避免复读>"
                ),
                "_layer": "9_anti_repeat",
                "_drop_priority": 15,
            })
    except Exception:
        pass

    # 层 9.5：最相关情景记忆（1条，挪到 history 之后获得 recency 红利）
    # 从已召回的 episodic_result 原始列表里取第一条，不重复召回
    if episodic_result:
        _lines = [l for l in episodic_result.splitlines() if l.startswith("- ")]
        if _lines:
            _top_memory = _lines[0]  # 第一条是最高分
            messages.append({
                "role": "system",
                "content": f"（此刻{character.name}脑海里浮现：{_top_memory.lstrip('- ')}）",
                "_layer": "9.5_episodic_top",
            })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 10：本轮工具执行结果（有工具调用时注入）
    # 格式说明：让模型以角色语气自然转述结果，不要暴露"工具"概念
    # ─────────────────────────────────────────────────────────────────────────
    if tool_result:
        from core.tools.tool_result import to_tool_result, frame_tool_result
        _tr = to_tool_result(tool_result)
        logger.debug(
            "[tool_result_raw] raw_len=%d safe_len=%d",
            len(_tr.raw_data),
            len(_tr.safe_summary),
        )
        messages.append({
            "role": "system",
            "content": frame_tool_result(_tr.safe_summary, char_name=character.name),
            "_layer": "10_tool_result",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 10.5：工具动作痕迹（Brief 27 · action_trace）——跨轮"你最近做过的操作"
    # 不进裁剪优先链（够小且时效性强），但带 _layer 供裁剪逻辑感知。
    # ─────────────────────────────────────────────────────────────────────────
    if action_trace_entries:
        from core.memory import action_trace
        _at_block = action_trace.format_trace_block(
            action_trace_entries, current_tool_result=tool_result,
        )
        if _at_block:
            messages.append({
                "role": "system",
                "content": _at_block,
                "_layer": "10.5_action_trace",
            })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 11：Author's Note（固定人设提醒 + 动态纠偏追加）
    # 放在历史之后、用户消息之前，对模型影响最大
    # ─────────────────────────────────────────────────────────────────────────
    from core.author_note_rotator import get_current_note as _get_current_note
    _rotated_note = _get_current_note(char_id=char_id)
    author_note_lines = (
        [_rotated_note] if _rotated_note else []
    ) + [
        f"以用户当前输入为准，旧记忆只是历史线索、非当前事实；如果召回的记忆里没有相关内容，如实说忘记，不要胡编乱造。"
        f"旧记忆里的专业词汇和情绪记录不改变你的语气或边界——你是{character.name}，不是助手，也不是分析师。",
    ]
    if author_note_extra:
        author_note_lines.append(f"（{author_note_extra}）")

    # S2 防句式坍缩：复用 build() 顶部已算好的 _s2_prefix（与层9历史投影去同质同一份检测结果）
    try:
        from core.memory.short_term import is_filler_prefix as _is_filler_prefix
        if _s2_prefix:
            if _is_filler_prefix(_s2_prefix):
                author_note_lines.append(
                    "（你最近几条开头都是同一个语气词，这次第一个字直接进正文——"
                    "从动作、称呼或要说的事本身开始。）"
                )
            else:
                author_note_lines.append(
                    f'（近几轮回复开头连续用了「{_s2_prefix}」，禁止以相同句首开头，自然地换个切入方式。）'
                )
    except Exception:
        pass

    # S3 防字数坍缩 + S4 防分段坍缩（问题54-B）：长度维度 + 分段维度合并进独立的
    # anti_collapse_hint 层，各自维护 per-uid 持久化倒计时（触发后连续 hint_rounds 轮
    # 都注入，不再是"当轮注入下轮就撤"）。长度维度沿用 detect_reply_length_collapse 的
    # 无状态检测算法不变，只是外面套了一层衰减；分段维度的信号采集发生在
    # capture_turn()（用 scrub 前的原始文本，见 core/memory/fixation_pipeline.py），
    # 这里只读取+衰减，两个维度的衰减都在 get_anti_collapse_hint() 里统一处理。
    # 不叠加情感浓度权重——情感浓度高时 LLM 本就会无视纠偏提示，与本提示自然中和，无需额外系数
    try:
        from core.config_loader import get_config as _get_config_ac
        from core.memory.short_term import get_anti_collapse_hint as _get_ac_hint
        _ac_cfg = _get_config_ac().get("anti_collapse", {})
        if "thresholds" in _ac_cfg or "recent_n" in _ac_cfg:
            logger.warning(
                "[anti_collapse] 配置键 thresholds/recent_n 已废弃并被忽略，"
                "请改用 short_max/recent_n_long/recent_n_short"
            )
        if _ac_cfg.get("enabled", True):
            _ac_hint = _get_ac_hint(
                user_id,
                history,
                char_id=char_id,
                short_max=_ac_cfg.get("short_max", 60),
                recent_n_long=_ac_cfg.get("recent_n_long", 4),
                recent_n_short=_ac_cfg.get("recent_n_short", 7),
                hint_rounds=_ac_cfg.get("hint_rounds", 3),
            )
            if _ac_hint:
                logger.debug("[anti_collapse] hint_injected uid=%s char_id=%s", user_id, char_id)
                messages.append({
                    "role": "system",
                    "content": _ac_hint,
                    "_layer": "anti_collapse_hint",
                })
    except Exception:
        pass

    # ── 根据 chat.style 注入输出风格指令（合并为一处，按模式分叉）─────────────
    from core.config_loader import get_config as _get_config
    _style = _get_config().get("chat", {}).get("style", "roleplay")

    _STYLE_INSTRUCTION = {
        "chat": (
            f"【输出格式】回复以对白为主，直接写说出口的话，不加任何标记。"
            "禁止动作描写行、感受描写行、环境描写行，不要用铺垫段落引出对白。"
            "话语有长有短。回复正文至少分为两段，段落之间必须保留一个空行，"
            "也就是输出两个真实换行符；不要把字面量 `\\n\\n` 输出给用户。"
            "分段不依赖句号，有第二句或第二个意思时直接另起一段。"
        ),
        "roleplay": (
            f"【输出格式】以{character.name}第一人称沉浸式展开当前场景。"
            "说出口的话直接写，动作/心理/环境全部在（）括号内，不加人称主语。"
            "话语有长有短，句号后换行。不要总结、不要跳跃，给对方留回应空间。"
            "回复正文至少分为两段，段落之间必须保留一个空行。且必须使用 `\\n\\n` 作为段落分隔。"
        ),
    }
    style_instruction = _STYLE_INSTRUCTION.get(_style, _STYLE_INSTRUCTION["roleplay"])
    author_note_lines.append(style_instruction)
    author_note_lines.append(
        "【词级强调】每条回复在情绪或语义焦点处用一次 <hl>词</hl>（重音）；"
        "需要时再用 <big>词</big>（放大）/ <sm>词</sm>（缩小）。每条 1–3 处，自然不堆砌。"
    )
    if tool_result:
        author_note_lines.append(
            "【工具结果已提供】"
            "本轮层10已注入工具执行结果，直接依据该结果回答；"
            "禁止声称'我去查一下'或暗示将再次调用工具——结果本轮已在上下文中。"
        )
    else:
        author_note_lines.append(
            "【无工具结果】"
            "本轮没有任何工具执行结果。"
            "禁止声称调用了任何工具；禁止编造日记内容；"
            "禁止引用任何未经工具返回的日记文字或实时数据。"
            "如果用户提到日记，可以询问是否希望你读取，或基于用户当前发来的内容回应。"
        )
    author_note_lines.append(
        "【表达规则】对话示例仅作风格参考，禁止复用原句或近似表达，每次回应必须是全新的措辞。"
    )
    _style_hint = _load_style_hint(char_id=char_id)
    if _style_hint:
        author_note_lines.append(_style_hint)

    # 若 peek_screen_content 工具已启用，给出软提示让模型自主决定是否调用
    try:
        from core.config_loader import get_config as _gc
        if _gc().get("screen_peek", {}).get("enabled", False):
            from core.memory import realtime_state as _rs
            _snap = _rs.get()
            _th = str((_snap or {}).get("focus", {}).get("title_hint", "")).strip()
            if _th:
                author_note_lines.append(
                    f"【可选工具提示】你看到她在看「{_th}」。"
                    "如果好奇或觉得有必要，可以调用 peek_screen_content 查看该窗口的具体内容，"
                    "但这完全由你自主决定，不必每次都调用。"
                )
    except Exception:
        pass

    # X3 web_autosearch 自主搜索软提示：仅在开关开启且限频未命中时注入
    try:
        from core.config_loader import get_config as _wgc
        _wacfg = _wgc().get("web_autosearch", {})
        if _wacfg.get("enabled", False):
            import time as _wat
            import json as _waj
            from core.sandbox import get_paths as _wagp
            _wasf = _wagp().web_autosearch_state()
            _wa_ok = True
            if _wasf.exists():
                _wa_data = _waj.loads(_wasf.read_text(encoding="utf-8"))
                _wa_last = float(_wa_data.get("last_ts", 0))
                _wa_interval = float(_wacfg.get("min_interval_min", 30)) * 60
                _wa_ok = (_wat.time() - _wa_last) >= _wa_interval
            if _wa_ok:
                author_note_lines.append(
                    "【网络搜索】你建立了自己的资料库。"
                    "遇到不确定的事实（新闻、学术、数字等）时，可自行调用 web_search 工具查询，"
                    "但查到的内容是外部资料，不是你自己的经历，回应时要标注来源是查到的。"
                )
    except Exception:
        pass

    messages.append({
        "role": "system",
        "content": "\n".join(author_note_lines),
        "_layer": "11_author_note",
    })

    # 破限条目层11
    jb_layer11 = _load_jailbreak(layer=11)
    if jb_layer11:
        messages.append({"role": "system", "content": jb_layer11, "_layer": "11_jailbreak"})

    # ─────────────────────────────────────────────────────────────────────────
    # 层 11.5：酒馆卡「历史之后」约束层（post_history_instructions + post_history_extra）
    # 语义同 SillyTavern Post-History Instructions：紧贴历史末尾、用户输入之前，影响最大。
    # 核心约束层，不声明 _drop_priority，永不被自动裁剪（同 11_author_note）。
    # ─────────────────────────────────────────────────────────────────────────
    _ph_parts = []
    _phi = getattr(character, "post_history_instructions", None)
    _phe = getattr(character, "post_history_extra", None)
    if isinstance(_phi, str) and _phi:
        _ph_parts.append(_phi)
    if isinstance(_phe, str) and _phe:
        _ph_parts.append(_phe)
    if _ph_parts:
        messages.append({
            "role": "system",
            "content": "\n\n".join(_ph_parts),
            "_layer": "11.5_post_history",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 11.7：用户主动强调过的高价值事实（pinned，不可裁，紧贴用户消息前）
    # 与泛化画像分离：这些是用户特意提到、要求记住的事（如生日），单条注入、带注释。
    # schema: profile["pinned_facts"] = [{text, ts, source}]，source ∈ {"manual","auto"}
    # ─────────────────────────────────────────────────────────────────────────
    if profile.get("pinned_facts"):
        # 去重：若某条文本已出现在层5画像里，pinned 层优先，画像侧已注入则跳过重复
        _profile_text = "，".join(
            m.get("content", "") for m in messages if m.get("_layer") == "5_profile"
        )
        _pinned_lines = [
            f"- {f['text']}" for f in profile["pinned_facts"]
            if f.get("text") and f["text"] not in _profile_text
        ]
        if _pinned_lines:
            messages.append({
                "role": "system",
                "content": "<重点记得>\n【用户特意提过、要你记住的事】\n" + "\n".join(_pinned_lines) + "\n</重点记得>",
                "_layer": "11.7_pinned_facts",
                "_provenance": {"mode": "pinned", "count": len(_pinned_lines)},
                # 故意不设 _drop_priority —— 永不被 token 裁剪
            })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 12：用户当前消息（最后一层）
    # 大间隔（>10分钟）时先注入时间提示，帮助模型感知消息时效性
    # ─────────────────────────────────────────────────────────────────────────
    if _msg_gap_secs is not None and _msg_gap_secs >= _GAP_HINT_MIN_SECS:
        messages.append({
            "role": "system",
            "content": f"<时间提示>距上一条消息已过去{_fmt_gap(_msg_gap_secs)}</时间提示>",
            "_layer": "12_time_hint",
        })
    messages.append({
        "role": "user",
        "content": user_message,
        "_layer": "12_user_message",
    })

    # ─────────────────────────────────────────────────────────────────────────
    # 层级消融开关（CC 任务 23 · B3）：组装完成后、token 估算与裁剪之前统一过滤。
    # 只过滤注入，检索层（fetch_context）不受影响——已在任务决策中确认。
    # ─────────────────────────────────────────────────────────────────────────
    _ablated_layers: list[str] = []
    if _ab["disabled_layers"]:
        _keep = []
        for _m in messages:
            _lyr = _m.get("_layer", "")
            if _lyr in _ab["disabled_layers"] and _lyr not in ALWAYS_ON:
                _ablated_layers.append(_lyr)
            else:
                _keep.append(_m)
        messages = _keep

    # ─────────────────────────────────────────────────────────────────────────
    # 注入前集中规范化（seam）——只清洗 system 层，绝不触碰真实对话
    # ─────────────────────────────────────────────────────────────────────────
    for _m in messages:
        if _m.get("role") == "system" and isinstance(_m.get("content"), str):
            _m["content"] = _normalize_injection(_m["content"], char_name=character.name)

    # ─────────────────────────────────────────────────────────────────────────
    # 定界标签配平检查（轻量 integrity，不配平打 WARNING）
    # 仅检查 content 以 < 非斜线开头的层（已包裹的外部内容层），防止漏闭合。
    # ─────────────────────────────────────────────────────────────────────────
    import re as _re_ic
    for _ic_m in messages:
        _ic_c = _ic_m.get("content", "")
        # 只检查 content 里同时含有开标签和对应闭标签的消息（单独 <tag> 占一条消息的框架标记跳过）
        if "</" not in _ic_c:
            continue
        _ic_mo = _re_ic.match(r'^<([^/>\s"]+)', _ic_c)
        if _ic_mo:
            _ic_tag = _ic_mo.group(1)
            if not _ic_c.rstrip().endswith(f"</{_ic_tag}>"):
                _prompt_logger.warning(
                    "[prompt_integrity] layer=%s tag=<%s> not closed",
                    _ic_m.get("_layer", "?"), _ic_tag,
                )

    # ─────────────────────────────────────────────────────────────────────────
    # token 估算警戒 + 强制裁剪
    # 估算基于字符数（1 token ≈ 1.5~2 汉字，此处保守用字符数做硬上限）
    # ─────────────────────────────────────────────────────────────────────────
    _layers_before_trim = [
        m.get("_report_layer") or m.get("_layer", "unknown") for m in messages
    ]
    token_estimate = sum(len(m["content"]) for m in messages)
    _removed_layers: list[str] = []
    if token_estimate > 20000:
        _prompt_logger.warning(f"[prompt] token估算超硬上限: {token_estimate}，触发层裁剪")
        # R4-B: 动态裁剪——收集所有带 _drop_priority 的消息，按 priority 升序排列（数字越小越先丢），
        # 同 priority 整批一次性丢弃，直到估算 ≤18000。无 _drop_priority 的层永不被自动丢弃。
        _droppable = [
            (i, m) for i, m in enumerate(messages)
            if m.get("_drop_priority") is not None
        ]
        _droppable.sort(key=lambda x: (x[1]["_drop_priority"], x[0]))
        _drop_indices: set[int] = set()
        _di = 0
        while _di < len(_droppable) and token_estimate > 18000:
            _cur_prio = _droppable[_di][1]["_drop_priority"]
            # 整批同 priority 的消息一次性丢弃
            while _di < len(_droppable) and _droppable[_di][1]["_drop_priority"] == _cur_prio:
                _idx, _msg = _droppable[_di]
                _drop_indices.add(_idx)
                _removed_layers.append(_msg.get("_layer", "?"))
                token_estimate -= len(_msg["content"])
                _di += 1
        if _drop_indices:
            messages = [m for j, m in enumerate(messages) if j not in _drop_indices]
        if _removed_layers:
            _prompt_logger.info(f"[prompt] 裁剪层：{_removed_layers}，裁剪后估算：{token_estimate}")
        if token_estimate > 18000:
            _prompt_logger.warning(
                "[prompt] 裁完仍超预算: %d，全部可裁层已丢弃，无法继续压缩",
                token_estimate,
            )
    elif token_estimate > 15000:
        _prompt_logger.warning(f"[prompt] token估算超软警戒: {token_estimate}")

    import logging
    _tlog = logging.getLogger("prompt_builder.debug")
    for m in messages:
        layer = m.get("_layer", "unknown")
        size = len(m.get("content", ""))
        if size > 200:
            _tlog.info(f"[layer_size] {layer}: {size} chars")
            
    return messages, {
        "layers_activated": [
            m.get("_report_layer") or m.get("_layer", "unknown") for m in messages
        ],
        "layers_before_trim": _layers_before_trim,
        "token_estimate": sum(len(m["content"]) for m in messages),
        "tags": list(_tags),
        "removed_layers": _removed_layers,
        "ablated_layers": _ablated_layers,
    }


def _parse_mes_example(mes_example: str, char_name: str) -> list[dict]:
    """
    解析  mes_example 格式为 OpenAI 消息列表。

    支持多段续行：{{user}}:/{{char}}: 开头的行开启新消息，其余行（含空行）
    追加到上一条 buffer，空行保留为 \\n\\n 段落分隔信号供 LLM 识别停顿。

    输入示例：
        <START>
        {{user}}: 你好
        {{char}}: 你好啊！
        第二段回复。
    """
    messages = []
    parts = re.split(r"<START>", mes_example, flags=re.IGNORECASE)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        current_role = None
        current_buf: list[str] = []

        def _flush():
            if current_role and current_buf:
                content = "\n".join(current_buf).strip()
                if content:
                    messages.append({
                        "role": current_role,
                        "content": content,
                        "_layer": "7_mes_example_item",
                    })

        for line in part.split("\n"):
            stripped = line.strip()
            if stripped.startswith("{{user}}:"):
                _flush()
                current_role = "user"
                current_buf = [stripped[len("{{user}}:"):].strip()]
            elif stripped.startswith("{{char}}:"):
                _flush()
                current_role = "assistant"
                current_buf = [stripped[len("{{char}}:"):].strip()]
            else:
                # 续行（含空行）合并到上一条，保留原始 line 使空行成为段落分隔信号
                if current_role is not None:
                    current_buf.append(line)
        _flush()
    return messages


# ─────────────────────────────────────────────────────────────────────────────
# 层级消融开关（CC 任务 23 · B6）：覆盖全部 build() 中出现的 _layer 字面量。
# perception_block 不是独立 _layer（嵌在 1_system_prompt 槽位），由 API 独立字段
# perception_block_disabled 表达，不在此表内。
#
# 放在文件末尾（而非顶部）：避免与 tests/test_r4b_prompt_drop_priority.py 等按
# `src.find('"layer_name"')` 定位 messages.append() 代码块的测试发生字符串误命中。
# ─────────────────────────────────────────────────────────────────────────────
KNOWN_LAYERS: list[tuple[str, str]] = [
    ("0_jailbreak", "破限预设 layer=0"),
    ("1_system_prompt", "角色存在性定义 + 情绪软提示 + 感知槽位（不可消融）"),
    ("1.5_fact_boundary", "现实信息事实边界句"),
    ("2_char_desc", "角色描述 + 性格 + 情境"),
    ("2.2_stage_presence", "群聊在场成员提醒"),
    ("2_jailbreak", "破限预设 layer=2"),
    ("2.5_time", "当前时间"),
    ("2.55_last_seen", "上次说话时间差"),
    ("2.6_presence", "角色此刻在做什么（ambient presence）"),
    ("3_relation", "与该用户的关系"),
    ("3.5_period", "生理期感知（tagged）"),
    ("3.6_watch", "watch 睡眠数据摘要（tagged）"),
    ("3.7_sensor", "手机传感器摘要"),
    ("3.8_activity", "桌宠屏幕活动快照（tagged）"),
    ("3.8_growth_self", "角色自身成长近况（tagged）"),
    ("3.9_screen_awareness", "桌面实时感知摘要"),
    ("4_group_context", "群聊上下文"),
    ("4.2_stage_transcript", "Stage 共享对话 transcript"),
    ("5_profile", "用户画像"),
    ("5_profile_pref", "用户偏好/习惯类事实"),
    ("5.1_user_facts", "跨角色全局用户事实"),
    ("5.2_reminders", "待办备忘录"),
    ("5.5_lore", "世界书条目"),
    ("6a_user_identity", "用户稳定行为模式"),
    ("6b_event_search", "相关往事（event_log 语义搜索）"),
    ("6c_episodic", "情景记忆片段（含 fallback，两者共用此层名）"),
    ("mid_term", "过去 12 小时事件压缩视图"),
    ("6d_diary_context", "用户近期日记（tagged）"),
    ("6e_inner_diary", "角色昨天的日记（事件层 + 感受层）"),
    ("web_recall", "向量库 X3 web 资料召回"),
    ("6f_dream_afterglow", "梦境余韵详细层"),
    ("dream_afterglow_soft_hint", "梦境余韵软提示"),
    ("6g_dream_impression", "梦境印象回流"),
    ("coplay_context", "陪玩模式 active 时的游戏进度/动态 + 剧透压制约束"),
    ("coplay_residue_soft_hint", "陪玩结束后 4 小时内的软提示"),
    ("coplay_recall", "聊天提到玩过的游戏时回忆上次游玩摘要"),
    ("7_mes_example_item", "对话示例（few-shot）"),
    ("9_history", "短期对话历史（关闭将严重改变行为）"),
    ("9_anti_repeat", "跨轮开头去同质软约束"),
    ("9.5_episodic_top", "最相关情景记忆置顶一条"),
    ("10_tool_result", "本轮工具执行结果"),
    ("10.5_action_trace", "工具动作痕迹：你最近做过的操作（跨轮回忆，不进裁剪链）"),
    ("anti_collapse_hint", "反坍缩提示：长度/分段维度合并，各自持久化倒计时 hint_rounds 轮"),
    ("11_author_note", "Author's Note 人设核心提醒"),
    ("11_jailbreak", "破限预设 layer=11"),
    ("11.5_post_history", "酒馆卡历史之后约束层"),
    ("11.7_pinned_facts", "用户特意提过要记住的事"),
    ("12_time_hint", "时间提示（gap≥10分钟）"),
    ("12_user_message", "用户当前消息（不可消融）"),
]
