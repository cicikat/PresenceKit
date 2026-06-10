"""
消息处理流水线
把 handle_message 的核心步骤封装成独立方法，main.py 只保留骨架调用。

Pipeline 实例持有角色卡和世界书引擎的引用，在 main.py 中初始化后全程复用。
"""

import asyncio
import logging

from core.llm_output_validator import record_failure, reset
from core.memory import pending_perception as _pending_perception
from core.memory.scope import MemoryScope

logger = logging.getLogger(__name__)

_DETECT_EMOTION_TIMEOUT = 8.0  # detect_emotion wait_for 超时阈值，测试时可 monkeypatch

# Path B 幂等窗口：同 uid + 同 action_type + 同关键参数在窗口内已执行 → 跳过。
# key = _intent_action_key(uid, action, params); value = last-executed timestamp。
_INTENT_LAST_ACTION: dict[str, float] = {}
_INTENT_COOLDOWN_SEC = 120.0  # 2 分钟内相同动作不重复执行（吐槽+复述链路可能跨 60s）

# Path B 危险动作黑名单：永不经 Path B 自动触发
_INTENT_DANGEROUS_ACTIONS: frozenset[str] = frozenset({"device_shutdown", "device_sleep"})

YANDERE_KEYWORDS = (
    "只属于我", "别看别人", "只能是我", "独占",
    "不许", "不准", "你是我的",
)
# priority 字段量纲：stranger=1，已认识的用户通常 >=2
YANDERE_RELATION_THRESHOLD = 2


def _check_yandere_trigger(user_message: str, reply: str, relation_priority: int) -> bool:
    if relation_priority < YANDERE_RELATION_THRESHOLD:
        return False
    text = user_message + " " + reply
    return any(kw in text for kw in YANDERE_KEYWORDS)


def _intent_action_key(user_id: str, action: str, params: dict) -> str:
    """c2 幂等 key：uid + action_type + 关键参数（区分「关了想再关一次」的不同目标）。"""
    if action == "minimize_window":
        return f"{user_id}:{action}:{params.get('window', '')}"
    if action == "play_song":
        return f"{user_id}:{action}:{params.get('song_name', '')}"
    if action == "open_url":
        return f"{user_id}:{action}:{params.get('url', '')}"
    return f"{user_id}:{action}"



def _validate_episode(data: dict) -> bool:
    for key in ("raw_facts", "topic_keywords", "emotion_peak", "strength"):
        if key not in data:
            return False
    if not isinstance(data["raw_facts"], list) or len(data["raw_facts"]) == 0:
        return False
    if not isinstance(data["topic_keywords"], list) or len(data["topic_keywords"]) == 0:
        return False
    if data["emotion_peak"] not in {"neutral", "happy", "sad", "gentle", "surprised", "angry", "thinking", "sleepy"}:
        return False
    try:
        s = float(data["strength"])
        if not (0.0 <= s <= 1.0):
            return False
    except (TypeError, ValueError):
        return False
    return True


class Pipeline:
    """
    消息处理流水线，四个核心步骤：

    1. fetch_context  — 并发拉取记忆数据 + 世界书匹配
    2. build_prompt   — 组装完整 prompt 消息列表
    3. run_llm        — 调用 LLM 生成回复（含重试）
    4. post_process   — 写记忆、更新画像、触发角色认知更新
    """

    def __init__(self, character, lore_engine, active_character_id: str = ""):
        self.character = character
        self.lore_engine = lore_engine
        # Track id of currently loaded character for hot-swap detection
        self._active_character_id: str = active_character_id
        # Author's Note 动态追加内容（consistency_check 结果），用完即清
        self.author_note_extra: str = ""
        self._last_channel: str | None = None

    def _refresh_character_if_needed(self) -> None:
        """Re-read active_prompt_assets.json; hot-swap self.character if active_character changed.

        Fail-loud contract (raises, does NOT silently continue):
        - File I/O error: raises RuntimeError.
        - active_character field missing or empty: raises ValueError + ERROR log.
        - active_character id unknown to registry: raises ValueError + ERROR log.
        pipeline.character is left unchanged in all error cases.
        """
        import json as _json
        from core.sandbox import get_paths as _get_paths

        # active_prompt_assets() raises RuntimeError if file is missing and config.default unset
        try:
            active_data = _json.loads(
                _get_paths().active_prompt_assets().read_text(encoding="utf-8")
            )
        except (OSError, IOError) as exc:
            raise RuntimeError(
                f"[pipeline] active_prompt_assets.json 读取失败: {exc}"
            ) from exc
        except _json.JSONDecodeError as exc:
            raise RuntimeError(
                f"[pipeline] active_prompt_assets.json JSON 解析失败: {exc}"
            ) from exc

        new_id = active_data.get("active_character", "")
        if not new_id:
            logger.error(
                "[pipeline] active_prompt_assets.json 中 active_character 字段缺失或为空，"
                f"当前角色: {self._active_character_id!r} — 本轮已中止"
            )
            raise ValueError(
                "[pipeline] active_prompt_assets.json missing or empty active_character"
            )

        if new_id == self._active_character_id:
            return  # no change, fast path

        from core import character_loader as _cl
        try:
            new_char = _cl.load(new_id)
        except (ValueError, FileNotFoundError) as exc:
            logger.error(
                f"[pipeline] active_character {new_id!r} 无法加载: {exc}  "
                f"— 保持原角色 {self._active_character_id!r}，本轮已中止"
            )
            raise ValueError(
                f"[pipeline] active_character {new_id!r} 无法加载"
            ) from exc

        self.character = new_char
        self._active_character_id = new_id
        if self.lore_engine is not None:
            self.lore_engine.load()
            if new_char.world_book:
                self.lore_engine.load_entries(new_char.world_book)
        logger.info(f"[pipeline] character hot-swapped to {new_id!r} ({new_char.name})")

    def _current_reality_scope(self, user_id: str) -> MemoryScope:
        """Construct a reality-domain MemoryScope for the active character.

        Calls _refresh_character_if_needed() — raises ValueError/RuntimeError on
        invalid active character.  Callers must not call _refresh_character_if_needed()
        again separately.
        """
        self._refresh_character_if_needed()
        return MemoryScope.reality_scope(str(user_id), self._active_character_id)

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 1：并发拉取记忆数据 + 世界书匹配
    # ──────────────────────────────────────────────────────────────────────────

    async def fetch_context(
        self,
        user_id: str,
        content: str,
        group_id: str | None = None,
        frozen_scope: "MemoryScope | None" = None,
    ) -> dict:
        """
        并发拉取所有记忆数据并进行世界书关键词匹配。

        返回 context 字典，供 build_prompt 使用：
        {
            "history":            list[dict],  # 短期对话历史
            "profile":            dict,        # 用户画像（scoped by char_id）
            "relation":           dict,        # 用户关系配置
            "group_context":      str,         # 群消息流（私聊为 ""）
            "user_identity_text": str,         # 用户稳定行为模式描述（scoped by char_id）
            "user_facts_text":    str,         # 跨角色全局用户事实（uid-only，无 char_id）
            "event_search_result": str,        # 事件日志语义搜索结果
            "lore_entries":       list[str],   # 命中的世界书条目
        }

        frozen_scope: 如果传入，直接使用该 scope，跳过 _current_reality_scope()。
          入口处已做一次性 scope freeze 时必须传入，保证全轮角色一致性（N1）。
        """
        # Guard: if frozen_scope provided, use it directly (turn-level scope freeze);
        # otherwise validate active_character and construct scope.
        if frozen_scope is not None:
            scope = frozen_scope
        else:
            scope = self._current_reality_scope(user_id)
        uid = scope.uid
        char_id = scope.character_id
        assert char_id is not None

        from core.memory import short_term, user_profile, group_context, event_log, mid_term
        from core.memory import user_identity, user_facts
        from core import user_relation, llm_client

        # 需要 IO 的任务并发进行
        loop = asyncio.get_event_loop()
        event_search_task = asyncio.create_task(
            event_log.search(uid, content, llm_client, char_id=char_id)
        )
        profile_future = loop.run_in_executor(
            None, lambda: user_profile.load(uid, char_id=char_id)
        )
        mid_term_future = loop.run_in_executor(
            None, lambda: mid_term.format_for_prompt(uid, char_id=char_id)
        )

        # 同步读取（内存/小文件，不值得并发）
        history          = short_term.load_for_prompt(uid, char_id=char_id)
        recent_group_ctx = group_context.get_recent(group_id)
        relation         = user_relation.get_relation(uid)
        lore_entries     = self.lore_engine.match(content, history)

        # 情景记忆检索
        # N2-A: fetch_context 是读路径，传 allow_strengthen=False 禁止写回 strength，
        # 避免"召回→增强→更易召回"的永动机效应。写回仍由写路径触发（post_process 等）。
        from core.memory.episodic_memory import retrieve, format_for_prompt
        episodic_memories = retrieve(
            user_id=uid,
            topic=content,
            top_k=3,
            char_id=char_id,
            allow_strengthen=False,
        )
        from core.memory.mood_state import get_current as _get_mood
        episodic_result = format_for_prompt(
            episodic_memories,
            char_name=self.character.name,
            current_emotion=_get_mood(char_id=char_id),
        )

        # 兜底召回：tag 未命中时备用，存入 context 供 prompt_builder 判断
        from core.memory.episodic_memory import retrieve_fallback
        _recent_texts = [h.get("content", "") for h in history[-5:]]
        episodic_fallback = retrieve_fallback(
            user_id=uid,
            recent_history=_recent_texts,
            top_k=1,
            char_id=char_id,
        )
        from core.memory.mood_state import get_current as _get_mood2
        episodic_fallback_result = format_for_prompt(
            episodic_fallback,
            char_name=self.character.name,
            current_emotion=_get_mood2(char_id=char_id),
        ) if episodic_fallback else ""

        # 等待异步任务
        event_search_result  = await event_search_task
        profile              = await profile_future
        mid_term_text        = await mid_term_future
        user_identity_text   = await user_identity.format_for_prompt(uid, char_id=char_id)
        # uid-only global facts — no char_id, no fallback
        user_facts_text      = user_facts.format_for_prompt(uid)

        from core.tools.reminder import get_reminders
        reminders = get_reminders(uid)
        from core.memory.diary_context import load as _load_diary
        diary_context = _load_diary(uid)

        # N2-A: sleepy mood 已迁出 — 见 Pipeline.post_process 开头的
        #        maybe_mark_sleepy_from_time() 调用。fetch_context 是读路径，不写 mood。

        # Dream impression — ambient, read-only, never written by reality chain
        try:
            from core.dream.impression_loader import load_impression_text as _load_imp
            dream_impression_text = _load_imp(uid, char_id=char_id)
        except Exception:
            dream_impression_text = ""

        logger.debug(
            f"[pipeline.fetch_context] uid={uid} "
            f"history={len(history)} lore={len(lore_entries)}"
        )
        return {
            "history":             history,
            "profile":             profile,
            "relation":            relation,
            "group_context":       recent_group_ctx,
            "user_identity_text":  user_identity_text,
            "user_facts_text":     user_facts_text,
            "event_search_result": event_search_result,
            "lore_entries":        lore_entries,
            "reminders":           reminders,
            "diary_context":       diary_context,
            "episodic_result":          episodic_result,
            "episodic_fallback_result": episodic_fallback_result,
            "mid_term":                 mid_term_text,
            "dream_impression_text":    dream_impression_text,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 2：组装 prompt
    # ──────────────────────────────────────────────────────────────────────────

    def build_prompt(
        self,
        user_id: str,
        content: str,
        context: dict,
        tool_result: str | None = None,
        tags: set[str] | None = None,
        channel: str | None = None,
        char_id: "str | None" = None,
    ) -> tuple[list[dict], dict]:
        """
        调用 prompt_builder 组装完整消息列表。
        根据 chat.mode 在 system prompt 末尾追加风格提示。
        author_note_extra 用完后立即清空（只影响本轮）。

        char_id: 如果传入，使用该值作为 _char_id，跳过 _refresh_character_if_needed()。
          入口处已做一次性 scope freeze 时必须传入（N1）。
        """
        if char_id is not None:
            _char_id = char_id
        else:
            self._refresh_character_if_needed()
            _char_id = self._active_character_id
        from core import prompt_builder
        from core.config_loader import get_config
        from datetime import datetime
        _now = datetime.now()
        _current_time = (
            _now.strftime("%Y年%m月%d日 %H:%M 星期")
            + ["一", "二", "三", "四", "五", "六", "日"][_now.weekday()]
        )

        from core.tag_rules import get_tags
        _tags = tags if tags is not None else get_tags(content)

        _perception = ""
        _pending, _pending_paths = _pending_perception.read_and_mark()
        if _pending:
            _perception = _pending.strip()

        # 跨通道接续感知
        if channel and self._last_channel and channel != self._last_channel:
            _channel_names = {"qq": "QQ", "desktop": "桌宠"}
            _from = _channel_names.get(self._last_channel, self._last_channel)
            _to = _channel_names.get(channel, channel)
            _switch_hint = f"（刚才还在{_from}那边说话，现在换到{_to}这里了。是同一个对话的延续。）"
            _perception = (_perception + "；" + _switch_hint) if _perception else _switch_hint
        if channel:
            self._last_channel = channel

        messages, debug_info = prompt_builder.build(
            character=self.character,
            user_id=user_id,
            user_message=content,
            history=context["history"],
            relation=context["relation"],
            profile=context["profile"],
            group_context=context["group_context"],
            user_identity_text=context["user_identity_text"],
            user_facts_text=context.get("user_facts_text", ""),
            event_search_result=context["event_search_result"],
            lore_entries=context["lore_entries"],
            tool_result=tool_result,
            perception_block=_perception,
            author_note_extra=self.author_note_extra,
            current_time=_current_time,
            reminders=context.get("reminders", []),
            diary_context=context.get("diary_context", ""),
            episodic_result=context.get("episodic_result", ""),
            episodic_fallback_result=context.get("episodic_fallback_result", ""),
            mid_term_context=context.get("mid_term", ""),
            tags=_tags,
            dream_impression_text=context.get("dream_impression_text", ""),
            char_id=_char_id,
        )
        self.author_note_extra = ""
        debug_info["pending_paths"] = _pending_paths
        return messages, debug_info

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 3：调用 LLM（含重试）
    # ──────────────────────────────────────────────────────────────────────────

    async def run_llm(self, messages: list[dict]) -> str:
        """调用 LLM 生成回复，失败自动重试。"""
        from core import llm_client
        from core.error_handler import with_retry

        @with_retry(module_name="pipeline.llm_call")
        async def _call():
            return await llm_client.chat(messages)

        return await _call()

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 4：异步后处理
    # ──────────────────────────────────────────────────────────────────────────

    async def post_process(
        self,
        user_id: str,
        content: str,
        reply: str,
        target_id: str = "",
        is_group: bool = False,
        pending_paths: list[str] | None = None,
        trigger_name: str = "",
        envelope=None,
        audit_extras: dict | None = None,
        frozen_scope: "MemoryScope | None" = None,
    ):
        """
        关键写入在 uid_lock 内同步完成，慢任务（LLM调用）入 slow_queue 异步执行。
        调用方应 await 此方法，不得用 asyncio.create_task() 丢弃引用（N10）。

        关键路径（uid_lock 内，按顺序）：
          short_term.append → event_log(user) → detect_emotion(timeout=8s)
          → global_lock(mood_state): mood_state.update + yandere
          → event_log(assistant, emotion)

        慢队列（uid_lock 释放后入队）：
          mid_term_append / episodic_compress / consistency_check
          user_profile_update（条件） / character_growth_update（条件）

        side effects（保持 asyncio.create_task）：TTS/表情包 / _parse_and_execute_intent

        frozen_scope: 如果传入，直接使用该 scope，跳过 _current_reality_scope()（N1）。
        """
        from core.write_envelope import WriteEnvelope
        if envelope is None:
            envelope = WriteEnvelope()

        # Guard: if frozen_scope provided, use it directly (turn-level scope freeze);
        # otherwise validate active_character and construct scope.
        if frozen_scope is not None:
            scope = frozen_scope
        else:
            scope = self._current_reality_scope(user_id)
        char_id = scope.character_id
        assert char_id is not None
        scope_payload = scope.to_payload()

        # N2-A: sleepy mood — 从 fetch_context 迁出的显式写操作。
        # 放在 post_process 开头（uid_lock 外）：保留深夜 sleepy 语义，
        # 但不再污染读路径。覆盖所有调用链（QQ / admin / scheduler），
        # 因为 record_assistant_turn 最终都走 pipeline.post_process。
        try:
            from core.mood_helpers import maybe_mark_sleepy_from_time as _mark_sleepy
            _mark_sleepy(uid=user_id, char_id=char_id, envelope=envelope)
        except Exception as _sleepy_err:
            logger.warning("[pipeline.post_process] sleepy mood 写入异常（已忽略）: %s", _sleepy_err)

        from core.memory import locks as _locks
        from core import llm_client
        from core.error_handler import log_error
        from core.post_process import slow_queue

        _emotion = "neutral"
        _should_update_profile = False
        _profile_recent: list = []
        import time as _time
        _turn_id = f"{user_id}_{int(_time.time() * 1000)}"
        _critical_written = False

        async with _locks.uid_lock(user_id):
            # ── 检查用户画像更新条件（在写入前，读取当前历史长度 +2 估算）────
            try:
                from core.memory import short_term as _st
                from core.config_loader import get_config
                cfg = get_config()
                every_n = cfg.get("memory", {}).get("summary_every_n_rounds", 20)
                _hist_len_after = len(_st.load(user_id, char_id=char_id)) + 2
                if _hist_len_after > 0 and _hist_len_after % every_n == 0:
                    _should_update_profile = True
                    _profile_recent = _st.load(user_id, char_id=char_id)[-(every_n * 2):]
            except Exception as e:
                log_error("post_process.check_conditions", e)

            # ── detect_emotion（带超时，绝不拖死 uid_lock）───────────────────
            try:
                _emotion = await asyncio.wait_for(
                    llm_client.detect_emotion(reply), timeout=_DETECT_EMOTION_TIMEOUT
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"[pipeline.post_process] detect_emotion 降级 neutral: {e}")
                _emotion = "neutral"

            # ── mood_state 更新（全局锁，嵌套在 uid_lock 内）────────────────
            if envelope.can_affect_mood:
                async with _locks.global_lock("mood_state"):
                    try:
                        from core.memory.mood_state import update as _update_mood
                        _update_mood(_emotion, source="detect", char_id=char_id)

                        try:
                            from core import user_relation as _user_relation
                            _relation = _user_relation.get_relation(user_id)
                            if _check_yandere_trigger(content, reply, _relation.get("priority", 1)):
                                from core.memory.mood_state import update as _update_mood_y
                                _update_mood_y("yandere", source="trigger", char_id=char_id)
                        except Exception as e:
                            log_error("post_process.yandere", e)
                    except Exception as e:
                        log_error("post_process.mood_state", e)

            # ── capture_turn：写 short_term + event_log（含 turn_id 血缘）───
            try:
                from core.memory.fixation_pipeline import capture_turn as _capture_turn
                _turn_id = _capture_turn(user_id, content, reply, _emotion, turn_id=_turn_id, trigger_name=trigger_name, envelope=envelope, char_id=char_id, audit_extras=audit_extras)
                _critical_written = True
                logger.debug(f"[pipeline.post_process] capture_turn: {_turn_id}")
            except Exception as e:
                log_error("post_process.capture_turn", e)
                if envelope.can_write_memory:
                    slow_queue.enqueue("capture_turn_retry", {
                        "turn_id": _turn_id,
                        "uid": user_id,
                        "user_content": content,
                        "reply": reply,
                        "emotion": _emotion,
                        "trigger_name": trigger_name,
                        "char_id": char_id,
                        "scope": scope_payload,
                    })

        # ── uid_lock 释放，入慢队列 ───────────────────────────────────────────
        from core.tag_rules import get_tags as _get_tags
        _mt_tags = list(_get_tags(content))

        # summarize_to_midterm 替代旧的 mid_term_append；
        # 若 emotion 显著，handler 内部会自动入队 reflect_to_episodic（eager）
        if envelope.can_write_memory:
            slow_queue.enqueue("summarize_to_midterm", {
                "turn_id": _turn_id,
                "uid": user_id,
                "user_content": content,
                "reply": reply,
                "tags": _mt_tags,
                "emotion": _emotion,
                "char_id": char_id,
                "scope": scope_payload,
            })
        slow_queue.enqueue("consistency_check", {
            "reply": reply,
        })
        if envelope.can_write_memory and _should_update_profile:
            slow_queue.enqueue("user_profile_update", {
                "uid": user_id,
                "recent": _profile_recent,
                "char_id": char_id,
                "scope": scope_payload,
            })
            logger.info(f"[pipeline.post_process] 用户画像更新已入队: {user_id}")

        # ── side effects：保持 asyncio.create_task ────────────────────────────
        if target_id and _emotion != "neutral":
            try:
                from core.config_loader import get_config as _cfg
                import random
                _tts_enabled = _cfg().get("tts", {}).get("enabled", False)
                _tts_prob = _cfg().get("tts", {}).get("probability", 0.3)
                _sticker_prob = 0.06
                _roll = random.random()
                if _tts_enabled and _roll < _tts_prob:
                    asyncio.create_task(self._send_tts(reply, target_id, is_group, emotion=_emotion))
                elif _roll < _tts_prob + _sticker_prob:
                    from core.output.sticker import maybe_send_sticker
                    asyncio.create_task(
                        maybe_send_sticker(reply, target_id, is_group, emotion=_emotion)
                    )
            except Exception as e:
                log_error("pipeline.post_process.tts_sticker", e)

        try:
            logger.info(f"[pipeline.intent] 开始解析，reply前30字={reply[:30]!r}")
            asyncio.create_task(self._parse_and_execute_intent(
                reply,
                trigger_name=trigger_name,
                user_content=content,
                user_id=user_id,
            ))
        except Exception as e:
            log_error("pipeline.post_process.intent", e)

        if pending_paths:
            _pending_perception.confirm_delivered(pending_paths)

        return {
            "emotion": _emotion,
            "turn_id": _turn_id,
            "critical_written": _critical_written,
        }

    # _compress_episode 已迁移为模块级 _do_compress_episode，由 slow_queue handler 调用

    async def _parse_and_execute_intent(
        self,
        reply: str,
        *,
        trigger_name: str = "",
        user_content: str = "",
        user_id: str = "",
    ) -> None:
        """
        Path B: 解析角色回复里声称要执行的桌面操作，写入 agent_actions.json 队列。
        角色说'我去把游戏关掉'→真的执行 minimize_window。

        守卫（全部满足才执行）：
          (a) trigger_name 为空 → 真实 owner turn（非 scheduler/sensor/watch）
          (b) user_content 非空非纯空白 → 本轮有真实用户输入
          (c) 意图非 dangerous（device_shutdown/device_sleep 永不经此路径触发）
          (c2) per-uid 同动作幂等窗口 60s：窗口内已执行 → 跳过
        """
        import json as _json
        import re
        import time as _time
        from core import llm_client
        from core.error_handler import log_error

        # guard (a): non-empty trigger_name → scheduler/sensor/watch turn → skip
        if trigger_name:
            logger.debug(
                "[pipeline.intent] 跳过: trigger_name=%r 非真实 owner turn", trigger_name
            )
            return

        # guard (b): no real user content this turn → skip
        if not user_content or not user_content.strip():
            logger.debug("[pipeline.intent] 跳过: user_content 为空")
            return

        if len(reply) < 10:
            return

        from core.config_loader import _char_name
        _char = _char_name()

        # c1: 收紧意图解析 prompt，只在「第一人称、当下要做」时命中；
        #     承认/复述/过去式/回应吐槽/睡眠关机语义一律不命中。
        intent_prompt = (
            f"判断以下回复里{_char}是否【当下、第一人称、主动声称要做】某个桌面操作。\n\n"
            f"严格规则：\n"
            f"- 只在{_char}表达「我现在/马上/去做X」等第一人称当下主动意图时命中\n"
            f"- 以下情形一律不命中：\n"
            f"  · 承认/解释：「{_char}说自己刚才做了X」\n"
            f"  · 复述用户的话：用户说了什么，{_char}重述或回应\n"
            f"  · 过去式：「{_char}已经做了/之前做过」\n"
            f"  · 回应用户吐槽或抱怨：用户抱怨某动作，{_char}道歉或解释\n"
            f"  · 睡眠/关机/让屏幕休眠语义：永不命中为任何操作类型\n\n"
            f"回复：{reply[:200]}\n\n"
            f"如果满足严格规则，输出JSON（只输出JSON，不要其他内容）：\n"
            f'{{\"action\": \"操作类型\", \"params\": {{}}}}\n\n'
            f"操作类型只能是以下之一（不含关机/睡眠）：\n"
            f"- minimize_window: 最小化窗口（不得匹配睡眠/关机语义），params: {{\"window\": \"窗口关键词\"}}\n"
            f"- play_song: 播放歌曲，params: {{\"song_name\": \"歌名\", \"artist\": \"歌手（可选）\"}}\n"
            f"- open_url: 打开网址，params: {{\"url\": \"网址\"}}\n"
            f"- play_pause: 播放暂停媒体，params: {{}}\n"
            f"- send_notification: 发通知，仅当{_char}明确说「提醒你/通知你/告诉你记得」等字样时才触发，"
            f"params: {{\"title\": \"标题\", \"message\": \"内容\"}}\n\n"
            f"如果不满足严格规则，输出空字符串。"
        )

        try:
            raw = await llm_client.chat(
                messages=[{"role": "user", "content": intent_prompt}],
                max_tokens_override=120,
                call_category="intent",
            )
            if not raw or not raw.strip():
                return

            raw = re.sub(r'```json|```', '', raw).strip()
            if not raw or raw == '""' or raw == "''":
                return

            data = _json.loads(raw)
            action = data.get("action", "")
            params = data.get("params", {})

            if not action:
                return

            # guard (c): dangerous actions never via Path B
            if action in _INTENT_DANGEROUS_ACTIONS:
                logger.warning(
                    "[pipeline.intent] 拒绝: 危险动作 %r 不得经 Path B 自动触发", action
                )
                return

            _NOTIFY_TIME_WORDS = [
                "等下", "待会", "一会", "等一下", "明天", "后天",
                "点", "分钟后", "小时后", "到时", "之后", "时候"
            ]
            _NOTIFY_ACTION_WORDS = [
                "提醒你", "通知", "告诉你", "帮你记", "记着", "别忘", "不要忘"
            ]
            if action == "send_notification":
                has_time = any(kw in reply for kw in _NOTIFY_TIME_WORDS)
                has_action = any(kw in reply for kw in _NOTIFY_ACTION_WORDS)
                if not (has_time and has_action):
                    logger.info(
                        "[pipeline.intent] send_notification 组合校验未通过"
                        "（time=%s, action=%s），跳过", has_time, has_action
                    )
                    return

            # c2: per-uid 同动作幂等窗口
            _ck = _intent_action_key(user_id, action, params)
            _now = _time.time()
            if _now - _INTENT_LAST_ACTION.get(_ck, 0.0) < _INTENT_COOLDOWN_SEC:
                logger.info(
                    "[pipeline.intent] 幂等跳过: action=%r 在 %.0fs 窗口内已执行",
                    action, _INTENT_COOLDOWN_SEC,
                )
                return

            from core.tool_dispatcher import _push_desktop_action
            action_payload = {"type": action, **params}
            last_result = "未执行"
            for _ in range(2):
                last_result = await _push_desktop_action(action_payload)
                if last_result == "ok":
                    break
                await asyncio.sleep(0.5)
            else:
                _pending_perception.write(
                    text=f"{action} 执行失败（重试2次）: {last_result}",
                    action=action, result=last_result,
                )

            if last_result == "ok":
                _INTENT_LAST_ACTION[_ck] = _now

            logger.info("[pipeline.intent] 检测到意图: %s(%s), result=%s", action, params, last_result)

        except _json.JSONDecodeError:
            pass
        except Exception as e:
            log_error("pipeline._parse_and_execute_intent", e)

    async def _send_tts(self, text: str, target_id: str, is_group: bool, emotion: str = "neutral"):
        """异步 TTS 合成并通过 NapCat 发送语音消息，失败只记日志"""
        from core.output.voice_adapter import synthesize, send_voice
        from core.error_handler import log_error
        import re
        # 清洗文本：去掉括号内的动作/环境描写，只保留说出口的话
        clean = re.sub(r'（[^）]*）', '', text)  # 中文括号
        clean = re.sub(r'\([^)]*\)', '', clean)   # 英文括号
        clean = clean.strip()
        if not clean:
            logger.debug("[pipeline.tts] 清洗后文本为空，跳过语音")
            return
        # 按标点切分，随机抽一句，优先抽10-30字的句子
        import random
        _sentences = re.split(r'[。！？…\n]', clean)
        _sentences = [s.strip() for s in _sentences if 5 <= len(s.strip()) <= 40]
        if _sentences:
            clean = random.choice(_sentences)
        else:
            clean = clean[:40]
        try:
            audio_bytes = await synthesize(clean, emotion)
            if audio_bytes:
                await send_voice(target_id, audio_bytes, is_group)
                logger.info(f"[pipeline.tts] 语音已发送 -> {target_id} (emotion={emotion})")
            else:
                logger.debug("[pipeline.tts] synthesize 返回 None，跳过语音发送")
        except Exception as e:
            log_error("pipeline._send_tts", e)


# ═══════════════════════════════════════════════════════════════════════════════
# 慢队列独立函数 + handlers（由 main.py 注册到 slow_queue）
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
                f"[pipeline.{handler_name}] scope domain must be 'reality', got {scope.domain!r}"
            )
        return scope

    # legacy fallback：旧 payload 无 scope 字段
    uid = payload.get("uid", "unknown")
    char_id = payload.get("char_id")
    if char_id:
        return MemoryScope.reality_scope(str(uid), char_id)

    logger.warning(
        "[pipeline.%s] payload 缺少 scope/char_id，使用 legacy DLQ fallback char_id=yexuan "
        "(uid=%s)",
        handler_name, uid,
    )
    return MemoryScope.reality_scope(str(uid), "yexuan")


async def _do_compress_episode(
    user_id: str, user_content: str, reply: str, *, char_id: str = "yexuan"
) -> None:
    """
    用 LLM 把一轮对话压缩成情景记忆并写入。
    handler 通过 slow_queue 调用，异常向上抛供 worker 重试。
    """
    import re
    import json
    import time
    from core import llm_client
    from core.memory import locks as _locks
    from core.memory.episodic_memory import write_episode
    from core.config_loader import _char_name

    char_name = _char_name()

    base_prompt = f"""你是一个对话记录分析器。请分析下面这段对话，输出结构化记忆，只输出JSON，不要有任何多余文字：
{{
  "raw_facts": ["用户说了什么（一句话事实）", "用了什么词或表达", "表达了什么状态"],
  "topic_keywords": ["3到5个话题关键词，用于未来召回"],
  "emotion_peak": "neutral/happy/sad/gentle/surprised/angry 中选一个",
  "emotion_texture": "用一句话描述对话中最有重量的情绪质感，20字以内，可留空",
  "emotion_arc": "情绪流动方向，10字以内，可留空",
  "user_state": "用一个短语描述用户当时的状态，如 stressed_about_work / excited / tired",
  "narrative_summary": "用一句自然语言描述这段对话发生了什么，15字以内，供{char_name}回忆时用",
  "strength": 0到1之间的浮点数（情绪越强、事件越重要越高）
}}

重要：你不是{char_name}，你是分析器。用第三人称客观陈述，不要使用文学化语言，不要写动作描写。

用户说：{user_content}
{char_name}回：{reply}"""

    _fail_key = f"compress_episode_{user_id}"
    data = None
    _last_result = ""

    for attempt in range(3):
        _prompt = base_prompt
        if attempt > 0:
            _prompt += "\n\n上次输出不符合格式要求，请严格只输出JSON，不要有任何多余文字。"
        _last_result = await llm_client.chat(
            messages=[{"role": "user", "content": _prompt}],
            max_tokens_override=400,
        )
        try:
            cleaned = re.sub(r"```json|```", "", _last_result).strip()
            candidate = json.loads(cleaned)
            if _validate_episode(candidate):
                data = candidate
                break
        except (json.JSONDecodeError, Exception):
            pass

    if data is None:
        record_failure(_fail_key, _last_result, user_id)
        return

    if data.get("emotion_peak") == "neutral" and data.get("strength", 0) < 0.4:
        return

    episode = {
        "id": f"ep_{int(time.time())}",
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
    }
    async with _locks.uid_lock(user_id):
        write_episode(user_id, episode, char_id=char_id)
    reset(_fail_key)


async def _handler_mid_term_append(payload: dict) -> None:
    # 保留旧 handler 供 DLQ 里残留任务重试用，新入队任务已改走 summarize_to_midterm
    from core.memory import locks as _locks, mid_term as _mid_term
    from core import llm_client
    scope = _get_scope_from_payload(payload, "_handler_mid_term_append")
    uid = scope.uid
    char_id = scope.character_id
    async with _locks.uid_lock(uid):
        summary = await llm_client.summarize_turn(
            payload["user_content"], payload["reply"], tags=payload.get("tags")
        )
        _mid_term.append(uid, summary, tags=payload.get("tags"), char_id=char_id)


async def _handler_episodic_compress(payload: dict) -> None:
    # 保留旧 handler 供 DLQ 里残留任务重试用，新入队任务已改走 reflect_to_episodic
    scope = _get_scope_from_payload(payload, "_handler_episodic_compress")
    await _do_compress_episode(
        user_id=scope.uid,
        user_content=payload["user_content"],
        reply=payload["reply"],
        char_id=scope.character_id,
    )


async def _handler_consistency_check(payload: dict) -> None:
    from core import character_loader
    from core.pipeline_registry import get as _get_pipeline
    pipeline = _get_pipeline()
    if pipeline is None:
        return
    check_result = await character_loader.consistency_check(pipeline.character, payload["reply"])
    if not check_result.get("ok"):
        issue = check_result.get("issue", "")
        if issue:
            pipeline.author_note_extra = issue
            logger.info(f"[pipeline.consistency] 一致性问题，下轮追加纠偏: {issue}")


async def _handler_user_profile_update(payload: dict) -> None:
    from core.memory import locks as _locks, user_profile
    scope = _get_scope_from_payload(payload, "_handler_user_profile_update")
    uid = scope.uid
    char_id = scope.character_id
    async with _locks.uid_lock(uid):
        await user_profile.extract_and_update(uid, payload["recent"], char_id=char_id)
    logger.info(f"[pipeline.user_profile] 画像更新完成: {uid}")


def register_slow_handlers() -> None:
    """main.py 启动时调用一次，注册所有慢任务 handler。"""
    from core.post_process import slow_queue
    from core.memory.fixation_pipeline import (
        handler_capture_turn_retry,
        handler_summarize_to_midterm,
        handler_reflect_to_episodic,
        handler_consolidate_to_identity,
    )
    # 新 pipeline handler
    slow_queue.register_handler("capture_turn_retry",       handler_capture_turn_retry)
    slow_queue.register_handler("summarize_to_midterm",     handler_summarize_to_midterm)
    slow_queue.register_handler("reflect_to_episodic",      handler_reflect_to_episodic)
    slow_queue.register_handler("consolidate_to_identity",  handler_consolidate_to_identity)
    slow_queue.register_handler("mid_term_append",         _handler_mid_term_append)
    slow_queue.register_handler("episodic_compress",       _handler_episodic_compress)
    slow_queue.register_handler("consistency_check",       _handler_consistency_check)
    slow_queue.register_handler("user_profile_update",     _handler_user_profile_update)
