"""Persist the critical turn and run deferred effects at the existing sink boundaries."""

import asyncio
import logging

from core.memory import pending_perception as _pending_perception
from core.memory.scope import MemoryScope

logger = logging.getLogger("core.pipeline")


_AVATAR_DIRECTIVE_LAST: dict[str, tuple[str, float]] = {}
_AVATAR_DIRECTIVE_COOLDOWN_SEC = 5.0


def _detect_dream_echo(user_id: str, char_id: str, content: str, reply: str) -> bool:
    """Read-only check：本轮是否携带梦境印象回流（Brief 79）。
    只读本地印象文件 + tag 匹配，毫秒级、无 LLM，不消费 forced_impression_rounds_left
    ——计数器消费仍然只在 post_process_slow 的 consume_forced_impression_round 里发生一次，
    这里可以安全地在 post_process_critical（capture_turn 之前）提前调用一次判定来源标记。
    fail-open：读取异常按无回流处理。
    """
    try:
        from core.dream.dream_state import read_state as _read_dream_state
        from core.dream.impression_loader import load_impression_text as _load_imp_for_echo
        from core.config_loader import get_config as _get_config_for_echo
        from core.tag_rules import get_tags as _get_tags

        _echo_state = _read_dream_state(user_id)
        try:
            _echo_forced_left = max(0, int(_echo_state.get("forced_impression_rounds_left", 0)))
        except (TypeError, ValueError):
            _echo_forced_left = 0
        _echo_dream_cfg = _get_config_for_echo().get("dream") or {}
        _echo_imp_cfg = _echo_dream_cfg.get("impression") or {}
        return bool(_load_imp_for_echo(
            user_id,
            char_id=char_id,
            forced_rounds_left=_echo_forced_left,
            latest_dream_id=str(_echo_state.get("last_dream_id") or ""),
            user_text=f"{content}\n{reply}",
            tags=set(_get_tags(content)),
            recall_enabled=bool(_echo_imp_cfg.get("recall_enabled", True)),
        ))
    except Exception as _echo_error:
        logger.warning(
            "[pipeline._detect_dream_echo] dream impression echo check failed uid=%s: %s",
            user_id, _echo_error,
        )
        return False


async def _maybe_push_avatar_directive(mood_state: dict, char_id: str, *, is_active_character) -> None:
    """Phase A v8: push avatar_directive to desktop client when mood changes."""
    import time as _t
    try:
        from core.tools.devices_definitions import _push_desktop_action
        if not is_active_character(char_id):
            return
        emotion = mood_state.get("current", "")
        if not emotion or not mood_state.get("has_signal"):
            return
        intensity = float(mood_state.get("intensity", 0.0))
        now = _t.time()
        last_emotion, last_sent = _AVATAR_DIRECTIVE_LAST.get(char_id, (None, 0.0))
        if last_emotion == emotion and (now - last_sent) < _AVATAR_DIRECTIVE_COOLDOWN_SEC:
            return
        _AVATAR_DIRECTIVE_LAST[char_id] = (emotion, now)
        await _push_desktop_action({
            "type": "avatar_directive",
            "expression": emotion,
            "intensity": round(min(1.0, max(0.0, intensity)), 3),
            "ttl_ms": 6000,
            "char_id": char_id,
            "version": mood_state.get("version", 0),
            "source_turn_id": mood_state.get("source_turn_id", ""),
            "source_at": mood_state.get("source_at", 0.0),
            "expires_at": min(float(mood_state.get("expires_at") or 0), now + 6),
        })
    except Exception as e:
        logger.debug("[pipeline] avatar_directive push skipped: %s", e)


async def _send_tts(text: str, target_id: str, is_group: bool, emotion: str = "neutral", char_id: str | None = None):
    """异步 TTS 合成并通过 NapCat 发送语音消息，失败只记日志。

    char_id 透传给 voice_adapter.synthesize()，按角色卡 presence_ext.tts_preset
    解析命名 TTS 预设（角色资产路由）；未传时回落全局 tts 配置（现状行为不变）。
    """
    from core.output.voice_adapter import clean_tts_text, synthesize, send_voice
    from core.error_handler import log_error
    import re
    # 清洗文本：去掉括号内的动作/环境描写，只保留说出口的话
    clean = clean_tts_text(text)
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
        audio_bytes = await synthesize(clean, emotion, char_id=char_id)
        if audio_bytes:
            await send_voice(target_id, audio_bytes, is_group)
            logger.info(f"[pipeline.tts] 语音已发送 -> {target_id} (emotion={emotion})")
        else:
            logger.debug("[pipeline.tts] synthesize 返回 None，跳过语音发送")
    except Exception as e:
        log_error("pipeline._send_tts", e)



class TurnEffects:
    def __init__(self, *, resolve_scope, is_active_character):
        if not callable(resolve_scope) or not callable(is_active_character):
            raise TypeError("TurnEffects requires scope and active-character capabilities")
        self._resolve_scope = resolve_scope
        self._is_active_character = is_active_character

    async def capture_critical(
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
        web_echo: bool = False,
        coplay_echo: bool = False,
        provenance_source: str = "",
        event_channel: str = "",
        event_source: str = "",
        visible_reply: str | None = None,
        raw_user_text: str | None = None,
        media_refs: list[dict] | None = None,
        event_context=None,
    ) -> dict:
        """
        Brief 37：send 前必须走完的关键路径——只做毫秒级本地落盘（capture_turn），
        不 await 任何 LLM/网络往返。expression / appraisal / avatar / profile /
        slow_queue 全部移到 post_process_slow，由调用方在 send 完成后异步调度
        （见 core/turn_sink.py record_assistant_turn）。

        event_log 的 emotion 保留 "neutral" 标注；慢段明确表达用于原有整合策略，
        内在事件评价只进入独立状态投影，不回填此标注，也不作为事实记忆证据。

        返回值原样传给 post_process_slow(critical_result=...)。

        frozen_scope: 如果传入，直接使用该 scope，跳过 _current_reality_scope()（N1）。

        web_echo / coplay_echo（Brief 79）：本轮是否携带 web 搜索召回 / coplay 演出内容，
        由调用方（turn_sink.record_assistant_turn）透传。连同本地重新判定的梦境印象回流
        一起换算成 event_log 的 source 标记，堵住 event_log_salvage 抢救链绕过固化隔离的
        通路。dream_echo 在这里独立重新判定（只读，不消费 forced_impression_rounds_left），
        与 post_process_slow 里那份用于 mid_term 跳过判断的 dream_echo 各自独立求值，
        两处判定逻辑一致（同一个 detect_dream_echo），只是求值时机不同。
        """
        from core.write_envelope import WriteEnvelope
        if envelope is None:
            envelope = WriteEnvelope()

        # Guard: if frozen_scope provided, use it directly (turn-level scope freeze);
        # otherwise validate active_character and construct scope.
        if frozen_scope is not None:
            scope = frozen_scope
        else:
            scope = self._resolve_scope(user_id)
        char_id = scope.character_id
        assert char_id is not None
        scope_payload = scope.to_payload()

        from core.memory import locks as _locks
        from core.error_handler import log_error
        from core.post_process import slow_queue

        _should_update_profile = False
        from core.tool_result_pins import projected as pin_projected
        _pin_echo = pin_projected(user_id,char_id)
        _profile_recent: list = []
        import time as _time
        _critical_started_at = _time.monotonic()
        _turn_id = (event_context.turn_id if event_context is not None and event_context.turn_id
                    else f"{user_id}_{int(_time.time() * 1000)}")
        _turn_context = event_context.with_turn(_turn_id) if event_context is not None else None
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

            # ── Brief 88：现实轮 gap 快照（读 short_term，早于 capture_turn 追加本轮）──
            # 供 post_process_slow 的 user_hidden_state SEEK_COMPANIONSHIP(a) 判定：
            # 本轮追加历史前，距上一条 owner 消息的秒数。读取失败 → None（判定端
            # 按"未知"处理，不触发）。
            _prior_gap_seconds: float | None = None
            try:
                from core.memory import short_term as _st_gap
                from core.presence import get_gap_from_history as _get_gap_from_history
                _prior_gap_seconds = _get_gap_from_history(_st_gap.load(user_id, char_id=char_id))
            except Exception:
                _prior_gap_seconds = None

            # ── capture_turn：写 short_term + event_log（含 turn_id 血缘）───
            try:
                from core.memory.fixation_pipeline import capture_turn as _capture_turn
                _dream_echo_for_source = _detect_dream_echo(user_id, char_id, content, reply)
                _source = 'tool_pin' if _pin_echo else provenance_source or (
                    "dream_echo" if _dream_echo_for_source
                    else "web" if web_echo
                    else "coplay" if coplay_echo
                    else ""
                )
                _capture_turn_kwargs = {
                    "turn_id": _turn_id,
                    "trigger_name": trigger_name,
                    "envelope": envelope,
                    "char_id": char_id,
                    "audit_extras": audit_extras,
                    "source": _source,
                    "event_channel": event_channel,
                    "event_source": event_source,
                    "visible_reply": visible_reply,
                    "raw_user_text": raw_user_text,
                    "media_refs": media_refs,
                }
                # Legacy callers have no ingress context. Do not add an
                # optional keyword argument when there is no context to carry.
                if _turn_context is not None:
                    _capture_turn_kwargs["event_context"] = _turn_context
                    _capture_turn_kwargs["observer_started_at"] = _critical_started_at
                _turn_id = _capture_turn(
                    user_id,
                    content,
                    reply,
                    "neutral",
                    **_capture_turn_kwargs,
                )
                _critical_written = True
                try:
                    from core.event_context_observer import record as _record_event_context
                    _record_event_context(
                        stage="critical", disposition="written", context=_turn_context,
                        orphan=_turn_context is None, started_at=_critical_started_at,
                    )
                except Exception:
                    pass
                logger.debug(f"[pipeline.post_process_critical] capture_turn: {_turn_id}")
                # 语义索引：event_log 条目异步写入向量库（fail-open）
                if envelope.can_write_memory:
                    try:
                        import time as _time
                        from core.memory import vector_store as _vs
                        _el_text = f"{content}\n{reply}".strip()
                        asyncio.create_task(
                            _vs.upsert(user_id, char_id, "event_log", _turn_id, _time.time(), _el_text)
                        )
                    except Exception as _vs_e:
                        logger.debug("[pipeline.post_process_critical] vector_store upsert schedule error: %s", _vs_e)
            except Exception as e:
                log_error("post_process.capture_turn", e)
                try:
                    from core.event_context_observer import record as _record_event_context
                    _record_event_context(
                        stage="critical", disposition="failed", context=_turn_context,
                        error_code="identity_mismatch" if isinstance(e, ValueError) else "capture_failed",
                        orphan=_turn_context is None,
                        scope_match=bool(_turn_context is None or _turn_context.scope == scope),
                        started_at=_critical_started_at,
                    )
                except Exception:
                    pass
                if envelope.can_write_memory:
                    slow_queue.enqueue("capture_turn_retry", {
                        "turn_id": _turn_id,
                        "uid": user_id,
                        "user_content": content,
                        "reply": reply,
                        "emotion": "neutral",
                        "trigger_name": trigger_name,
                        "char_id": char_id,
                        "scope": scope_payload,
                        "event_context": _turn_context.to_payload() if _turn_context is not None else None,
                        "audit_extras": audit_extras,
                        "source": _source,
                        "event_channel": event_channel,
                        "event_source": event_source,
                        "media_refs": media_refs,
                        "visible_reply": visible_reply,
                        "raw_user_text": raw_user_text,
                    })

        if pending_paths:
            _pending_perception.confirm_delivered(pending_paths)

        if _critical_written and envelope.can_write_memory and not trigger_name and not is_group:
            try:
                from core.tool_result_pins import consume
                consume(user_id,char_id,_turn_id)
            except Exception:
                logger.warning('[tool_result_pins] round commit unavailable')

        if _critical_written and envelope.can_write_memory and not trigger_name and not is_group and not web_echo and not coplay_echo and not _dream_echo_for_source and not _pin_echo:
            if not (audit_extras or {}).get("asr_low_confidence"):
                try:
                    from core.food_memory import enqueue as enqueue_food
                    enqueue_food(user_id, char_id, _turn_id, raw_user_text if raw_user_text is not None else content)
                except Exception as exc:
                    logger.warning("[food_memory] enqueue failed: %s", type(exc).__name__)

        from core.expression_signal import explicit_expression

        return {
            "turn_id": _turn_id,
            "critical_written": _critical_written,
            "emotion": "neutral",
            "char_id": char_id,
            "scope_payload": scope_payload,
            "should_update_profile": _should_update_profile and not _pin_echo,
            "tool_pin_echo": _pin_echo,
            "profile_recent": _profile_recent,
            "prior_gap_seconds": _prior_gap_seconds,
            "expression_signal": (audit_extras or {}).get("expression_signal", explicit_expression(reply)),
            "source_at": event_context.occurred_at if event_context is not None else _time.time(),
            "appraisal_input": raw_user_text if raw_user_text is not None else content,
            "source_refs": [str(ref["source_ref"]) for ref in (media_refs or [])
                            if isinstance(ref, dict) and ref.get("kind") == "activity_record" and ref.get("source_ref")],
        }


    async def run_deferred_effects(
        self,
        user_id: str,
        content: str,
        reply: str,
        critical_result: dict,
        target_id: str = "",
        is_group: bool = False,
        trigger_name: str = "",
        envelope=None,
        audit_extras: dict | None = None,
        web_echo: bool = False,
        coplay_echo: bool = False,
        loop_executed: bool = False,
        provenance_source: str = "",
    ) -> dict:
        """
        Brief 37：send 之后异步执行的慢段——明确表达 → mood_state → avatar/
        heart → profile → slow_queue 入队 → TTS/表情包 → 意图解析。调用方（
        turn_sink.record_assistant_turn）应在 channel fanout 完成后用
        asyncio.create_task() 调度本方法，不得 await（否则又把 send 堵回去）。

        critical_result: post_process_critical() 的返回值，携带 turn_id /
        char_id / scope_payload / should_update_profile / profile_recent。
        """
        from core.write_envelope import WriteEnvelope
        if envelope is None:
            envelope = WriteEnvelope()

        from core.memory import locks as _locks
        from core import llm_client
        from core.error_handler import log_error
        from core.post_process import slow_queue

        char_id = critical_result["char_id"]
        scope_payload = critical_result["scope_payload"]
        _turn_id = critical_result["turn_id"]
        _should_update_profile = critical_result["should_update_profile"]
        _profile_recent = critical_result["profile_recent"]
        web_echo = web_echo or bool(critical_result.get('tool_pin_echo'))

        if envelope.can_write_memory and not trigger_name and not is_group:
            slow_queue.enqueue("food_memory_update", {"uid": user_id, "char_id": char_id, "scope": scope_payload})

        if not trigger_name and envelope.can_write_memory:
            from core.memory.short_term import schedule_long_user_summaries
            schedule_long_user_summaries(user_id, char_id=char_id)

        if not trigger_name and not is_group:
            from core.document_reading import schedule_summaries
            schedule_summaries(user_id, char_id)

        _emotion = str(critical_result.get("expression_signal") or "")

        # ── Brief 88：user_hidden_state 现实侧信号映射（对话侧，emotion 已可用）──
        # 判定全用现成数据（tags / emotion / 现实轮 gap 快照 / 常量词表），零 LLM；
        # fail-open，任何异常不影响主链。trigger 轮零参与（函数内部判断）。
        from core.tag_rules import get_tags as _get_tags
        _reality_tags = set(_get_tags(content))
        try:
            from core.memory.user_hidden_state_reality_signals import process_reality_turn
            if not provenance_source.startswith("activity:"):
                process_reality_turn(
                    uid=user_id,
                    content=content,
                    tags=_reality_tags,
                    assistant_emotion="",
                    trigger_name=trigger_name,
                    envelope=envelope,
                    prior_gap_seconds=critical_result.get("prior_gap_seconds"),
                    char_id=char_id,
                    turn_id=str(_turn_id or ""),
                )
        except Exception as _hs_err:
            logger.warning(f"[pipeline.post_process_slow] hidden_state reality signal 映射失败: {_hs_err}")

        async with _locks.uid_lock(user_id):
            # Outward expression commits immediately, before semantic appraisal.
            _mood_state_after: dict | None = None
            if envelope.can_affect_mood:
                async with _locks.global_lock("mood_state"):
                    try:
                        from core.memory.mood_state import update as _update_mood
                        _mood_state_after = _update_mood(_emotion, source="explicit", char_id=char_id,
                            source_turn_id=str(_turn_id), source_at=critical_result.get("source_at"))
                    except Exception as e:
                        log_error("post_process.mood_state", e)

            # Phase A v8: push avatar directive (fail-open, outside locks)
            if (_mood_state_after is not None and _mood_state_after.get("has_signal")
                    and _mood_state_after.get("source_turn_id") == str(_turn_id)):
                asyncio.create_task(_maybe_push_avatar_directive(_mood_state_after, char_id, is_active_character=self._is_active_character))
            # 爱意探针 → 板子爱心（非阻塞、锁外、fail-open）；梦境/沙盒等场景不触发实体爱心。
            if envelope.can_affect_mood:
                from core.embodiment.heart import maybe_draw_heart as _maybe_heart
                asyncio.create_task(_maybe_heart(reply, char_id))

        # Real dialogue only. Trigger seeds, imports and isolated material are not
        # personal events. Network work is outside both UID and state locks.
        from core.write_envelope import SourceType
        if (envelope.can_affect_mood and not trigger_name and not is_group
                and envelope.source in (SourceType.USER_CHAT, SourceType.QQ, SourceType.WECHAT)
                and critical_result.get("critical_written")
                and not web_echo and not coplay_echo and not provenance_source
                and not _detect_dream_echo(user_id, char_id, content, reply)):
            try:
                from core import affective_appraisal
                from core.memory import mood_state as _mood
                _input = str(critical_result.get("appraisal_input") or "")
                _state = _mood.load(char_id=char_id)
                if (_mood.enabled() and _state.get("last_turn_id") == str(_turn_id)
                        and _state.get("last_appraisal_turn_id") != str(_turn_id)):
                    if affective_appraisal.source_available(user_id, char_id, str(_turn_id)):
                        _proposal, _status = await affective_appraisal.evaluate(_input, char_id=char_id)
                    else:
                        _proposal, _status = None, "source_unavailable"
                    if not affective_appraisal.source_available(user_id, char_id, str(_turn_id)):
                        _proposal, _status = None, "source_unavailable"
                    _mood.record_appraisal(_proposal, char_id=char_id, turn_id=str(_turn_id),
                                           occurred_at=critical_result["source_at"], evidence=_input, status=_status)
            except Exception as e:
                log_error("post_process.affective_appraisal", e)

        # ── 入慢队列 ───────────────────────────────────────────
        _mt_tags = list(_reality_tags)

        # Consolidation is silent exactly when this turn is a forced injection
        # or a topic-recall hit. Read before consuming so the third forced turn
        # still carries dream_echo=True.
        _dream_echo = _detect_dream_echo(user_id, char_id, content, reply)
        try:
            from core.dream.dream_state import consume_forced_impression_round
            consume_forced_impression_round(
                user_id, reality_owner_turn=not bool(trigger_name)
            )
        except Exception as _consume_error:
            logger.warning(
                "[pipeline.post_process_slow] forced impression counter failed uid=%s: %s",
                user_id, _consume_error,
            )

        # 若 emotion 显著，handler 内部会自动入队 reflect_to_episodic（eager）
        if envelope.can_write_memory:
            _mt_payload: dict = {
                "turn_id": _turn_id,
                "uid": user_id,
                "user_content": content,
                "reply": reply,
                "tags": _mt_tags,
                "emotion": _emotion,
                "char_id": char_id,
                "scope": scope_payload,
            }
            if trigger_name:
                _mt_payload["trigger_name"] = trigger_name
            if _dream_echo:
                _mt_payload["dream_echo"] = True
            if web_echo:
                _mt_payload["web_echo"] = True
            if coplay_echo:
                _mt_payload["coplay_echo"] = True
            if provenance_source:
                _mt_payload["source"] = provenance_source
            if critical_result.get("source_refs"):
                _mt_payload["source_refs"] = critical_result["source_refs"]
            if audit_extras and audit_extras.get("input_modality") == "voice":
                _mt_payload["input_modality"] = "voice"
                _mt_payload["asr_low_confidence"] = bool(audit_extras.get("asr_low_confidence"))
            slow_queue.enqueue("summarize_to_midterm", _mt_payload)
        slow_queue.enqueue("consistency_check", {
            "reply": reply,
            "char_id": char_id,
        })
        if envelope.can_write_memory and _should_update_profile:
            slow_queue.enqueue("user_profile_update", {
                "uid": user_id,
                "recent": _profile_recent,
                "char_id": char_id,
                "scope": scope_payload,
            })
            logger.info(f"[pipeline.post_process_slow] 用户画像更新已入队: {user_id}")
        if envelope.can_write_memory:
            slow_queue.enqueue("trait_tracker_update", {
                "uid": user_id,
                "char_id": char_id,
                "scope": scope_payload,
            })
        if envelope.can_write_memory:
            slow_queue.enqueue("toy_autogrow", {
                "uid": user_id,
                "char_id": char_id,
                "scope": scope_payload,
                "user_content": content,
                "reply": reply,
            })

        # ── side effects：保持 asyncio.create_task ────────────────────────────
        # QQ TTS needs a OneBot destination. Sticker delivery is different:
        # desktop/mobile have no QQ id but can receive its self-contained
        # payload through the channel registry, so do not share this gate.
        if target_id and _emotion != "neutral":
            try:
                from core.config_loader import get_config as _cfg
                import random
                _tts_enabled = _cfg().get("tts", {}).get("enabled", False)
                _tts_prob = _cfg().get("tts", {}).get("probability", 0.3)
                if _tts_enabled and random.random() < _tts_prob:
                    from core.qq_service import create_reply_media_task
                    create_reply_media_task(_send_tts(reply, target_id, is_group, emotion=_emotion, char_id=char_id))
            except Exception as e:
                log_error("pipeline.post_process.tts", e)

        # Stickers are only for a real owner-message reply.  `target_id` is
        # optional QQ routing data, not a delivery prerequisite for desktop or
        # mobile.  The sticker helper keeps QQ image delivery optional and
        # broadcasts the data-URL payload to active non-QQ channels.
        if envelope.can_affect_mood and not trigger_name and _emotion:
            try:
                from core.output.sticker import maybe_send_sticker
                from core.qq_service import create_reply_media_task
                create_reply_media_task(
                    maybe_send_sticker(
                        reply,
                        target_id or None,
                        is_group,
                        emotion=_emotion,
                        char_id=char_id,
                        recipient_id=user_id,
                    )
                )
            except Exception as e:
                log_error("pipeline.post_process.sticker", e)

        if target_id:
            try:
                from core.output.image_delivery import send_turn_images
                from core.qq_service import create_reply_media_task
                from core.tools.chat_artifacts import artifacts_for_turns
                _imgs = artifacts_for_turns([str(_turn_id)], uid=user_id, char_id=char_id).get(str(_turn_id), [])
                if _imgs:
                    create_reply_media_task(
                        send_turn_images(_imgs, target_id, is_group)
                    )
            except Exception as e:
                log_error("pipeline.post_process.image_delivery", e)

        return {
            "emotion": _emotion,
            "turn_id": _turn_id,
        }
