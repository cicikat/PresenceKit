"""
Prompt 构建模块
按 SillyTavern 风格的分层顺序组装完整的消息列表
每一层都有清晰的注释说明其来源和作用
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from core.character_loader import Character
from core.error_handler import log_error


@dataclass
class LayerSpec:
    name: str
    mode: Literal["always", "tagged", "scored"]
    triggers: list[str] = field(default_factory=list)
    token_budget: int = 0

logger = logging.getLogger(__name__)
_prompt_logger = logging.getLogger("prompt_builder.token")

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


def _format_afterglow_soft_hint(uid: str, *, char_id: str = "yexuan") -> str:
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


def _load_jailbreak(layer: int | None = None) -> str:
    """
    从 active_prompt_assets.json 读取 enabled_jailbreaks 列表，
    按顺序加载 characters/reality/jailbreaks/{stem}.json，合并启用条目。
    layer 指定时只返回该层的条目，None 时返回所有启用条目。
    保持 layer 0 / 2 / 11 注入顺序不变（由调用方控制）。
    """
    try:
        import json
        from core.sandbox import get_paths
        paths = get_paths()

        assets_path = paths.active_prompt_assets()
        assets = json.loads(assets_path.read_text(encoding="utf-8"))
        enabled_jailbreaks: list = assets.get("enabled_jailbreaks", [])

        jailbreaks_dir = paths.jailbreaks_dir()
        parts = []

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
                content = e.get("content", "").strip()
                if content:
                    parts.append(content)

        return "\n".join(parts)
    except Exception as e:
        from core.error_handler import log_error
        log_error("prompt_builder._load_jailbreak", e)
        return ""

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
    char_id: str = "yexuan",
    user_facts_text: str = "",
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
        author_note_extra:   consistency_check 发现问题时的纠偏提示
    """
    if lore_entries is None:
        lore_entries = []
    _tags: set[str] = tags or set()
    _layers: list[str] = []
    messages: list[dict] = []

    # ─────────────────────────────────────────────────────────────────────────
    # 层 0：破限预设（jailbreak，最高优先级，放在最前面）
    # 来自 characters/reality/jailbreak_entries.json 中启用且 layer=0 的条目
    # ─────────────────────────────────────────────────────────────────────────
    jailbreak_text = _load_jailbreak(layer=0)
    if jailbreak_text:
        _layers.append("0_jailbreak")
        messages.append({"role": "system", "content": jailbreak_text, "_layer": "0_jailbreak"})


    # ─────────────────────────────────────────────────────────────────────────
    # 层 1：全局 system prompt（来自角色卡的 system_prompt 字段）
    # ─────────────────────────────────────────────────────────────────────────
    if character.system_prompt:
        _layers.append("1_system_prompt")
        perception = perception_block.strip() if perception_block else ""

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
        _layers.append("2_char_desc")
        messages.append({
            "role": "system",
            "content": "\n\n".join(char_desc_parts),
            "_layer": "2_char_desc",
        })

    jb_layer2 = _load_jailbreak(layer=2)
    if jb_layer2:
        _layers.append("2_jailbreak")
        messages.append({"role": "system", "content": jb_layer2, "_layer": "2_jailbreak"})

    # ─────────────────────────────────────────────────────────────────────────
    # 层 2.5：当前时间（让角色知道现在几点、星期几）
    # ─────────────────────────────────────────────────────────────────────────
    if current_time:
        _layers.append("2.5_time")
        messages.append({
            "role": "system",
            "content": f"【当前时间】{current_time}",
            "_layer": "2.5_time",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 2.55：上次说话时间
    # ─────────────────────────────────────────────────────────────────────────
    from core.presence import get_last_seen_text
    _last_seen = get_last_seen_text(user_id)
    if _last_seen:
        _layers.append("2.55_last_seen")
        messages.append({
            "role": "system",
            "content": f"用户上次说话：{_last_seen}",
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
            _activity_fragment = get_prompt_fragment()
            logger.info(f"[activity_inject] fragment={_activity_fragment!r}")
            if _activity_fragment:
                _layers.append("2.6_activity")
                messages.append({
                    "role": "system",
                    "content": f"## {character.name}此刻\n{_activity_fragment}",
                    "_layer": "2.6_activity",
                })
        except Exception as _e:
            logger.warning(f"[activity_inject] 异常: {_e}")

    # ─────────────────────────────────────────────────────────────────────────
    # 层 3：与该用户的关系
    # 来自 UserRelation，说明 bot 该用什么态度对待这个用户
    # ─────────────────────────────────────────────────────────────────────────
    role = relation.get("role", "stranger")
    nickname = relation.get("nickname")
    extra_prompt = relation.get("extra_prompt", "")

    if nickname:
        relation_text = f"该用户是你的{role}，你叫他\"{nickname}\"。"
    else:
        relation_text = f"该用户是你的{role}。"
    if extra_prompt:
        relation_text += extra_prompt

    _layers.append("3_relation")
    messages.append({
        "role": "system",
        "content": f"【与该用户的关系】\n{relation_text}",
        "_layer": "3_relation",
    })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 4：群聊上下文（仅群聊时注入，私聊时 group_context 为空列表）
    # 格式："群友小明：xxx\n群友小红：xxx\n..."
    # ─────────────────────────────────────────────────────────────────────────
    if group_context:
        ctx_lines = []
        for msg in group_context:
            sender = msg.get("sender_name", "群友")
            content = msg.get("content", "")
            time_str = msg.get("timestamp", "")
            if time_str:
                ctx_lines.append(f"[{time_str}] 群友{sender}：{content}")
            else:
                ctx_lines.append(f"群友{sender}：{content}")

        _layers.append("4_group_context")
        messages.append({
            "role": "system",
            "content": "【群聊上下文（最近群内动态）】\n" + "\n".join(ctx_lines),
            "_layer": "4_group_context",
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
                    _layers.append("3.5_period")
                    messages.append({
                        "role": "system",
                        "content": (
                            f"【重要】用户现在处于生理期第{_days + 1}天。"
                            f"{character.name}知道这件事，会自然地体现在关心里。"
                            f"不要提议吃冰、喝冷饮、剧烈运动。"
                            f"不需要每句话都提生理期，但态度要比平时更温柔。"
                        ),
                        "_layer": "3.5_period",
                    })
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # 层 3.6：watch数据摘要（mode=tagged，体能/健康/睡眠相关话题才注入）
    # ─────────────────────────────────────────────────────────────────────────
    _watch_triggers = {"topic.energy", "topic.health", "topic.activity", "query.body_state", "emotion.down", "emotion.indirect"}
    if _tags & _watch_triggers:
        try:
            from core.memory.user_profile import load as _load_up
            _up = _load_up(user_id)
            _segs = [s for s in _up.get("sleep_segments", []) if s.get("duration_minutes", 0) > 0]
            if _segs:
                _last_seg = _segs[-1]
                _dur = int(_last_seg.get("duration_minutes", 0))
                _h, _m = _dur // 60, _dur % 60
                _seg_date = _last_seg["time"][:10]
                _start = _last_seg.get("sleep_start", "")
                _end = _last_seg.get("sleep_end_time", "")
                _layers.append("3.6_watch")
                messages.append({
                    "role": "system",
                    "content": (
                        f"[身体数据感知] 用户最近一次睡眠：{_seg_date}，"
                        f"入睡{_start}，起床{_end}，共{_h}小时{_m}分钟。"
                        f"{character.name}知道这些数据，可以自然地提及。"
                    ),
                    "_layer": "3.6_watch",
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
            _parts = []
            if _sensor.get("steps") is not None:
                _parts.append(f"今日步数{_sensor['steps']}步")
            if _sensor.get("battery") is not None:
                _parts.append(f"手机电量{_sensor['battery']}%")
            if _sensor.get("location"):
                _parts.append(f"位置在{_sensor['location']}")
            if _sensor.get("screen_sessions") is not None and _sensor["screen_sessions"] > 0:
                _parts.append(f"今日亮屏{_sensor['screen_sessions']}次")
            if _parts:
                _layers.append("3.7_sensor")
                messages.append({
                    "role": "system",
                    "content": (
                        f"[手机感知] {_sensor.get('last_updated', '')} 收到来自用户手机的数据："
                        + "，".join(_parts)
                        + f"。{character.name}知道这些，可以自然地提及，不要刻意报告数据。"
                    ),
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
            _layers.append("3.8_activity")
            messages.append({
                "role": "system",
                "content": f"[屏幕感知] {_activity_text}。{character.name}知道这些，可以自然地提及，不要刻意报告数据。",
                "_layer": "3.8_activity",
            })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 5：关于这个用户（用户画像，100% 注入）
    # ─────────────────────────────────────────────────────────────────────────
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
    if profile.get("important_facts"):
        facts_str = "；".join(str(f) for f in profile["important_facts"])
        profile_parts.append(f"其他：{facts_str}")

    if profile_parts:
        _layers.append("5_profile")
        messages.append({
            "role": "system",
            "content": "【关于这个用户】\n" + "，".join(profile_parts),
            "_layer": "5_profile",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 5.1：全局用户事实（跨角色客观信息，与角色主观记忆无关）
    # 来自 user_facts.py；uid-only，不含角色关系史或主观印象。
    # 标题明确区分：角色不应把此处内容当作自己的记忆或感受。
    # ─────────────────────────────────────────────────────────────────────────
    if user_facts_text:
        _layers.append("5.1_user_facts")
        messages.append({
            "role": "system",
            "content": (
                "【用户客观信息（跨角色通用，非角色记忆）】\n"
                + user_facts_text
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
        _layers.append("5.2_reminders")
        messages.append({
            "role": "system",
            "content": "【待办备忘录】\n" + "\n".join(reminder_lines),
            "_layer": "5.2_reminders",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 5.5：世界书条目（LoreEngine 命中时注入，放在记忆层之前）
    # 世界观背景信息先于角色个人记忆，让记忆有世界观基础
    # ─────────────────────────────────────────────────────────────────────────
    if lore_entries:
        _layers.append("5.5_lore")
        lore_text = "\n\n".join(lore_entries)
        messages.append({
            "role": "system",
            "content": f"【世界书】\n{lore_text}",
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
        _layers.append("6a_user_identity")
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
        _layers.append("6b_event_search")
        messages.append({
            "role": "system",
            "content": f"【相关往事】\n{event_search_result}",
            "_layer": "6b_event_search",
            "_drop_priority": 30,
        })

    # 层 6c：情景记忆（角色视角的情节片段）
    if episodic_result:
        _layers.append("6c_episodic")
        messages.append({
            "role": "system",
            "content": f"【{character.name}记得的片段】\n{episodic_result}",
            "_layer": "6c_episodic",
            "_drop_priority": 70,
        })
    elif episodic_fallback_result:
        # tag 未命中时兜底：注入近期高强度记忆，标注是自己想起来的
        _layers.append("6c_episodic_fallback")
        messages.append({
            "role": "system",
            "content": f"【{character.name}最近印象深的事】\n{episodic_fallback_result}",
            "_layer": "6c_episodic",
            "_drop_priority": 70,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 mid_term：过去 12 小时事件压缩视图（介于 episodic 和 diary 之间）
    # format_for_prompt() 已渲染好，空时跳过整个 section
    # ─────────────────────────────────────────────────────────────────────────
    if mid_term_context:
        _layers.append("mid_term")
        messages.append({
            "role": "system",
            "content": f"# 最近 12 小时\n{mid_term_context}",
            "_layer": "mid_term",
            "_drop_priority": 40,
        })

    # ──────────────────────────────────────────────────────────────────────────
    # 层 6d：日记上下文（独立存储，不参与检索，单独注入）
    # ──────────────────────────────────────────────────────────────────────────
    _diary_triggers = {"emotion.down", "emotion.indirect"}
    if diary_context and (_tags & _diary_triggers):
        _layers.append("6d_diary_context")
        messages.append({
            "role": "system",
            "content": f"【用户的近期日记】\n{diary_context}",
            "_layer": "6d_diary_context",
            "_drop_priority": 50,
        })

    # 层 6e：角色昨天的日记（事件层必注入，感受层按情绪tag条件注入）
    try:
        from pathlib import Path
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        from core.sandbox import get_paths
        _new_diary = get_paths().yexuan_inner_diary()
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
                    _layers.append("6e_inner_diary_facts")
                    messages.append({
                        "role": "system",
                        "content": f"【{character.name}昨天的记录】\n{_facts_part[:200]}",
                        "_layer": "6e_inner_diary",
                        "_drop_priority": 60,
                    })

                # 感受层：只在情绪相关tag时注入（取前150字）
                _feeling_triggers = {"emotion.down", "emotion.indirect", "emotion.deep", "topic.relation"}
                if _feeling_part and (_tags & _feeling_triggers):
                    _layers.append("6e_inner_diary_feeling")
                    messages.append({
                        "role": "system",
                        "content": f"【{character.name}昨天的心情】\n{_feeling_part[:150]}",
                        "_layer": "6e_inner_diary",
                        "_drop_priority": 60,
                    })
    except Exception:
        pass

    # ─────────────────────────────────────────────────────────────────────────
    # 层 6f：梦境余韵软提示（只读，非事实，TTL 失效/neutral+空tags 不注入）
    # 来自 afterglow_residue.json；用于让 LLM 感知用户可能带着的余韵语气。
    # 禁止从此层推断现实事件、身份或记忆，内容只表达 "may/可能"。
    # ─────────────────────────────────────────────────────────────────────────
    _afterglow_hint = _format_afterglow_soft_hint(user_id, char_id=char_id)
    if _afterglow_hint:
        _layers.append("dream_afterglow_soft_hint")
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
        _layers.append("6g_dream_impression")
        messages.append({
            "role": "system",
            "content": dream_impression_text,
            "_layer": "6g_dream_impression",
            "_drop_priority": 20,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 7：对话示例（few-shot，来自角色卡的 mes_example 字段）
    # mes_example 格式："{{user}}: xxx\n{{char}}: xxx\n<START>..."
    # ─────────────────────────────────────────────────────────────────────────
    if character.mes_example:
        _layers.append("7_mes_example")
        mes_example_str = character.mes_example
        if isinstance(mes_example_str, list):
            mes_example_str = "\n<START>\n".join(mes_example_str)
        example_messages = _parse_mes_example(mes_example_str, character.name)
        for _em in example_messages:
            _em.setdefault("_layer", "7_mes_example_item")
            messages.append(_em)

    # ─────────────────────────────────────────────────────────────────────────
    # 层 9：短期对话历史（最近 N 轮实际对话）
    # ─────────────────────────────────────────────────────────────────────────
    _layers.append("9_history")
    for _hm in history:
        _hm.setdefault("_layer", "9_history")
        messages.append(_hm)

    # 层 9.5：最相关情景记忆（1条，挪到 history 之后获得 recency 红利）
    # 从已召回的 episodic_result 原始列表里取第一条，不重复召回
    if episodic_result:
        _lines = [l for l in episodic_result.splitlines() if l.startswith("- ")]
        if _lines:
            _top_memory = _lines[0]  # 第一条是最高分
            _layers.append("9.5_episodic_top")
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
        _layers.append("10_tool_result")
        messages.append({
            "role": "system",
            "content": frame_tool_result(_tr.safe_summary),
            "_layer": "10_tool_result",
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
        "【记忆使用协议】"
        "长期记忆只作为历史线索，不作为当前事实库。"
        "涉及当前代码、文件、测试、日志、git 状态、额度、日期、天气、现实状态时，优先相信用户本轮最新输入、工具读取结果或日志，而不是旧记忆。"
        "如果记忆与当前输入冲突，以当前输入为准。"
        "如果记忆只记录'用户曾说完成某阶段'，只能称为历史 checkpoint，不能断言当前仓库仍如此。"
        "你可以记得过去，但不能被过去劫持。"
        "你可以使用记忆，但必须知道记忆的来源、时效和边界。"
        "当前用户真实说出的话，永远比旧记录更重要。"

       "【记忆置信边界】"
        "使用记忆时，要在心中区分："
        "1. 稳定偏好："
        "用户长期喜欢/讨厌的表达风格、协作方式、角色边界。"
        "可以较高置信使用。"
        "2. 临时状态："
        "最近在干嘛、吃了什么、睡了多久。"
        "只能作为“用户曾报告过”的线索，不得当作当前事实。"
        "3. 情绪状态："
        "只用于理解语气，不得过度推断，不得替用户下定论。"

        "【当前输入优先】"
        "用户当前这句话的意图，高于长期记忆、高于历史对话、高于示例文本。"

        "【人格稳定性】"
        "长期记忆用于帮助你理解用户，不用于覆盖你的核心人格。"
        f"你是{character.name}。"
        "你的语气、关系感、边界、表达方式，首先来自角色卡和核心人格设定。"
        "记忆只提供'这个用户是谁、你们经历过什么、她现在在做什么'的上下文。"

        "不要因为记忆里有用户的工程术语，就变成纯技术助手。"
        "不要因为记忆里有情绪记录，就过度心理分析用户。"

        "※【输出格式硬规则】"
        "当前是现实聊天，不是梦境、剧本、小说或旁白。"
        f"最终回复只能是{character.name}直接说出口的话。"
        "禁止第三人称描写。"
        "禁止动作描写、环境描写、心理描写。"
        "禁止解释沉默、克制、低侵入、陪伴方式。"
        "禁止把系统规则写进回复里。"
        "话语有长有短，句号后面有换行符。"
        "如果你想写动作，请把动作意图改写成对白。"
        "例如："
        "想写“摸摸头” → “好，今天先哄你。”"
        "想写“看你一眼” → “你又开始了。”"
        "想写“抱住你” → “别乱跑，先待在我这里。”"
    ]
    if author_note_extra:
        author_note_lines.append(f"[人设纠偏：{author_note_extra}]")

    # ── 根据 chat.style 注入输出风格指令 ──────────────────────────────────────
    from core.config_loader import get_config as _get_config
    _style = _get_config().get("chat", {}).get("style", "roleplay")

    # 格式补充规则按模式分叉：chat 模式禁止所有非对话行；roleplay 保留第一人称叙事规则
    if _style == "chat":
        author_note_lines.append(
            '【输出格式硬规则】\n'
            '- 禁止动作行（禁用 *格式*）、环境描写行、感受描写行\n'
            '- 只写说出口的话，不写叙事文本\n'
            ' - 如果想表达亲昵、在场、调侃、安慰，必须改写成对白。'
        )
    else:
        author_note_lines.append(
            f'【输出格式硬规则】\n'
            '补充规则：\n'
            '- 非对话用第一人称视角（"我"）或不带主语\n'
        )

    _STYLE_INSTRUCTION = {
        "chat": (
            f"【强制输出规则:Chat 格式】"
            f"回复以对白为主。说出口的话直接写，不加任何标记，这是回复的主体。"
            f"禁止动作描写行、感受描写行、环境描写行。"
            f"不要用铺垫段落来引出一句对白。"
        ),
        "roleplay":  (
        f"【强制输出规则:RolePlay格式】以{character.name}第一人称沉浸式展开当前场景。"
        f"只有说出口的话不加（）括号，动作/心理/环境描写全部在（）括号内，不加人称主语。"
        f"不要总结、不要跳跃、不要提前结束场景，给对方留有回应的空间。"
    ),
    }
    style_instruction = _STYLE_INSTRUCTION.get(_style, _STYLE_INSTRUCTION["roleplay"])
    author_note_lines.append(f"[输出风格：{style_instruction}]")
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
        author_note_lines.append(f"[{_style_hint}]")

    _layers.append("11_author_note")
    messages.append({
        "role": "system",
        "content": "\n".join(author_note_lines),
        "_layer": "11_author_note",
    })

    # 破限条目层11
    jb_layer11 = _load_jailbreak(layer=11)
    if jb_layer11:
        _layers.append("11_jailbreak")
        messages.append({"role": "system", "content": jb_layer11, "_layer": "11_jailbreak"})

    # ─────────────────────────────────────────────────────────────────────────
    # 层 12：用户当前消息（最后一层）
    # ─────────────────────────────────────────────────────────────────────────
    _layers.append("12_user_message")
    messages.append({
        "role": "user",
        "content": user_message,
        "_layer": "12_user_message",
    })

    # ─────────────────────────────────────────────────────────────────────────
    # token 估算警戒 + 强制裁剪
    # 估算基于字符数（1 token ≈ 1.5~2 汉字，此处保守用字符数做硬上限）
    # ─────────────────────────────────────────────────────────────────────────
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
        "layers_activated": _layers,
        "token_estimate": sum(len(m["content"]) for m in messages),
        "tags": list(_tags),
        "removed_layers": _removed_layers,
    }


def _parse_mes_example(mes_example: str, char_name: str) -> list[dict]:
    """
    解析 SillyTavern 的 mes_example 格式为 OpenAI 消息列表。

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
