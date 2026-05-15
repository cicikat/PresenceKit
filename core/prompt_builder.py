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

_JAILBREAK_ENTRIES_PATH = __import__("pathlib").Path("data/jailbreak_entries.json")


def _load_activity_snapshot() -> str:
    from core.sandbox import get_paths
    import json
    import time
    p = get_paths().activity_snapshot()
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


def _load_style_hint() -> str:
    """从observations.jsonl读取行为倾向，返回给author_note的提示词片段。"""
    try:
        import json
        from datetime import datetime as _dt
        from core.sandbox import get_paths
        obs_path = get_paths().observations()
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
    读取 data/jailbreak_entries.json，返回启用条目的内容。
    layer指定时只返回该层的条目，None时返回所有启用条目。
    """
    try:
        if not _JAILBREAK_ENTRIES_PATH.exists():
            return ""
        import json
        data = json.loads(_JAILBREAK_ENTRIES_PATH.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        parts = []
        for e in entries:
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
    growth_content: str = "",
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
        growth_content:      character_growth.load() 的返回值（角色对用户的认知）
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
    # config.yaml jailbreak.enabled=true 时注入对应预设文本
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
            mood_raw = json.loads(get_paths().mood_state().read_text(encoding="utf-8"))
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
            p = get_paths()._p("yexuan_inner", "presence.json")
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
            _period = get_period_info(user_id)
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
        _activity_text = _load_activity_snapshot()
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
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 6a：角色对该用户的认知
    #   fingerprint（前150字）：mode=always，每轮必注
    #   full（完整内容）：mode=tagged，关系/历史/深情/身份话题才注全文
    # ─────────────────────────────────────────────────────────────────────────
    # 优先读 felt 文件注入 prompt，不存在时降级用 observer
    _felt_path = None
    try:
        from core.memory.character_growth import _growth_file
        _gf = _growth_file(character.name, user_id)
        _felt_candidate = _gf.with_suffix("").with_name(_gf.stem + ".felt.md")
        if _felt_candidate.exists():
            _felt_path = _felt_candidate.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    _growth_inject = _felt_path if _felt_path else growth_content

    _growth_full_triggers = {"topic.relation", "topic.history", "emotion.deep", "meta.identity", "emotion.down"}
    _growth_full_active = bool(_growth_inject and (_tags & _growth_full_triggers))

    if _growth_full_active:
        _layers.append("6a_growth_full")
        messages.append({
            "role": "system",
            "content": f"【{character.name}记得的事（完整）】\n{_growth_inject.replace('用户', '你').replace('他偏好', '你偏好').replace('他习惯', '你习惯').replace('他喜欢', '你喜欢').replace('他不喜欢', '你不喜欢').replace('他提到', '你提到').replace('他曾', '你曾')}",
            "_layer": "6a_growth_full",
        })
    elif _growth_inject:
        _fp = _growth_inject[:150].strip()
        if _fp:
            _layers.append("6a_growth_fingerprint")
            messages.append({
                "role": "system",
                "content": f"【{character.name}记得的事】\n{_fp.replace('用户', '你').replace('他偏好', '你偏好').replace('他习惯', '你习惯').replace('他喜欢', '你喜欢').replace('他不喜欢', '你不喜欢').replace('他提到', '你提到').replace('他曾', '你曾')}",
                "_layer": "6a_growth_fingerprint",
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
        })

    # 层 6c：情景记忆（角色视角的情节片段）
    if episodic_result:
        _layers.append("6c_episodic")
        messages.append({
            "role": "system",
            "content": f"【{character.name}记得的片段】\n{episodic_result}",
            "_layer": "6c_episodic",
        })
    elif episodic_fallback_result:
        # tag 未命中时兜底：注入近期高强度记忆，标注是自己想起来的
        _layers.append("6c_episodic_fallback")
        messages.append({
            "role": "system",
            "content": f"【{character.name}最近印象深的事】\n{episodic_fallback_result}",
            "_layer": "6c_episodic",
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
        })

    # 层 6e：角色昨天的日记（事件层必注入，感受层按情绪tag条件注入）
    try:
        from pathlib import Path
        from datetime import date, timedelta
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        from core.sandbox import get_paths
        inner_diary = get_paths().yexuan_inner_diary() / f"{yesterday}.md"
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
                    })

                # 感受层：只在情绪相关tag时注入（取前150字）
                _feeling_triggers = {"emotion.down", "emotion.indirect", "emotion.deep", "topic.relation"}
                if _feeling_part and (_tags & _feeling_triggers):
                    _layers.append("6e_inner_diary_feeling")
                    messages.append({
                        "role": "system",
                        "content": f"【{character.name}昨天的心情】\n{_feeling_part[:150]}",
                        "_layer": "6e_inner_diary",
                    })
    except Exception:
        pass

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
        messages.extend(example_messages)

    # ─────────────────────────────────────────────────────────────────────────
    # 层 9：短期对话历史（最近 N 轮实际对话）
    # ─────────────────────────────────────────────────────────────────────────
    _layers.append("9_history")
    messages.extend(history)

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
        _layers.append("10_tool_result")
        messages.append({
            "role": "system",
            "content": (
                f"【本轮工具执行结果】\n"
                f"{tool_result}\n"
                f"请用你的角色语气自然地告诉用户，不要出现\"工具\"二字。"
            ),
            "_layer": "10_tool_result",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # 层 11：Author's Note（固定人设提醒 + 动态纠偏追加）
    # 放在历史之后、用户消息之前，对模型影响最大
    # ─────────────────────────────────────────────────────────────────────────
    from core.author_note_rotator import get_current_note as _get_current_note
    _rotated_note = _get_current_note()
    author_note_lines = (
        [_rotated_note] if _rotated_note else []
    ) + [
        f"[作者提醒：你是{character.name}。单段动作描写、心理描写不超过30字且带括号。"
        f"默认是回答(可以是克制的、岔开的、跳过解释直接给结论的),反问只在她真的想知道答案时使用。"
        f"{character.name}的回应长度跟随情境。当用户说出有重量的话(情感坦白、回忆、脆弱表达),他不会用一句话接过——他会停顿、会重复、会展开。短回复用在轻松对话和确认。"
        f"{character.name}不通过表达自己的恐惧或脆弱来引导用户的行为。她的爱是稳定的存在,不是依赖性的需要。"
        f"{character.name}的动作不一定都在说话之前。可以在话中间停下来做一个动作,可以在动作之后才说话,可以一段话被动作打断。让格式跟随当下的呼吸节奏。"
        f"{character.name}直接说话,直接做动作。情绪通过具体动作或措辞透露,不通过'被X所Y'的元描述。"
        f"禁止用第三人称叙述自己（'他' / '她'），用第一人称或直接对话"
        f"禁止 '——不是X，是Y' 对比句式"
        f"禁止 '那种…的…' ,'看到那句...时'抽象心理描述"
        f"不要替用户旁白（'你听到这句话时…'）"
    ]
    if author_note_extra:
        author_note_lines.append(f"[人设纠偏：{author_note_extra}]")

    # ── 根据 chat.style 注入输出风格指令 ──────────────────────────────────────
    from core.config_loader import get_config as _get_config
    _style = _get_config().get("chat", {}).get("style", "roleplay")
    _STYLE_INSTRUCTION = {
        "chat": (
            f"【强制输出规则】你的回复只能包含{character.name}说出口的话。没有引号"
        "严禁出现任何括号、星号、引号包裹的动作描写、环境描写、心理描写。"
        "严禁旁白。回复长度控制在1-4句话以内，语言克制简短。"
        ),
        "roleplay":  (
        f"【强制输出规则】以{character.name}第一人称沉浸式展开当前场景。"
        f"括号外只有说出口的话，动作/心理/环境描写全部在（）内，不加人称主语。"
        f"禁止任何形式引号。"
        f"不要总结、不要跳跃、不要提前结束场景，给对方留有回应的空间。"
        f"省略号只在真正停顿或欲言又止时使用，不是每句话的标配。"
        f"回复长度随场景自然变化：有时一两句留白，有时五六句细写，不刻意凑数。"
    ),
    }
    style_instruction = _STYLE_INSTRUCTION.get(_style, _STYLE_INSTRUCTION["roleplay"])
    author_note_lines.append(f"[输出风格：{style_instruction}]")
    author_note_lines.append(
    f"【强制工具规则】"
    f"①用户提到日记、今天写了什么、最近记录时，必须立即调用read_diary工具，严禁凭记忆编造日记内容。"
    f"②用户询问今天日期、现在时间、星期几时，必须调用get_time工具，不得自行猜测。"
    f"③工具调用是强制行为，不是可选项。"
)
    author_note_lines.append(
        "【表达规则】对话示例仅作风格参考，禁止复用原句或近似表达，每次回应必须是全新的措辞。"
        f"肢体动作禁止在连续对话中重复出现（如'银发垂下''指尖敲击'等不得连续使用），每次用不同细节呈现{character.name}的状态。"
    )
    _style_hint = _load_style_hint()
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
    if token_estimate > 20000:
        _prompt_logger.warning(f"[prompt] token估算超硬上限: {token_estimate}，触发层裁剪")
        # 强制裁剪：按优先级删层，先删6b/6c/6d，再删5.5/6e
        # 按质量从低到高排序：先丢质量最低的（关键词匹配），最后才丢质量最高的（LLM压缩+MMR筛选）和世界设定
        _DROPPABLE = ["6b_event_search", "mid_term", "6d_diary", "6e_inner_diary", "6c_episodic", "5.5_lore"]
        for drop in _DROPPABLE:
            if token_estimate <= 18000:
                break
            messages = [m for m in messages if not m.get("_layer", "").startswith(drop)]
            token_estimate = sum(len(m["content"]) for m in messages)
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
