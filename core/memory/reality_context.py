"""Collect scoped Reality evidence for prompt assembly, with no chat or delivery effects."""

import asyncio
import logging

from core.error_handler import log_error
from core.memory.scope import MemoryScope

logger = logging.getLogger("core.pipeline")


class RealityContextReader:
    def __init__(self, *, resolve_scope, read_snapshot):
        if not callable(resolve_scope) or not callable(read_snapshot):
            raise TypeError("RealityContextReader requires scope and snapshot capabilities")
        self._resolve_scope = resolve_scope
        self._read_snapshot = read_snapshot

    async def fetch(self, user_id: str, content: str, group_id: str | None = None,
                    frozen_scope: MemoryScope | None = None, recall_policy: str = "seed",
                    query_free_fallback: bool = False) -> dict:
        """Read evidence against the ingress-frozen character and recall policy."""
        scope = frozen_scope if frozen_scope is not None else self._resolve_scope(user_id)
        snapshot = self._read_snapshot()
        from core.character_session import CharacterContextSnapshot
        if not isinstance(snapshot, CharacterContextSnapshot):
            raise TypeError("context reader requires a CharacterContextSnapshot")
        character = snapshot.character
        lore_engine = snapshot.lore_engine
        active_character_id = snapshot.active_character_id
        uid = scope.uid
        char_id = scope.character_id
        assert char_id is not None
        scoped_character = character
        scoped_lore_engine = lore_engine
        if char_id != active_character_id:
            from core.character_loader import load as load_character
            from core.lore_engine import LoreEngine

            scoped_character = load_character(char_id)
            scoped_lore_engine = LoreEngine()
            scoped_lore_engine.load(char_id=char_id)
            if scoped_character.world_book:
                scoped_lore_engine.load_entries(scoped_character.world_book)

        from core.memory import short_term, user_profile, group_context, event_log, mid_term
        from core.memory import user_identity, user_facts
        from core import user_relation, llm_client

        # Brief 48: 查询侧时间意图，一轮只解析一次，非 None 时透传给 episodic /
        # event_log / 向量预取三处（fail-open：解析失败已在 parse_query_time_range
        # 内部兜底为 None，这里不用再包 try）。
        import time as _time
        from core.memory.temporal_query import parse_query_time_range
        _parsed_time_range = parse_query_time_range(content, _time.time())
        _since_ts, _until_ts = _parsed_time_range if _parsed_time_range else (None, None)

        # M5：长期/关系层问题（认识多久、怎么看我…）——不等模型自己想起来调工具，系统直接拉齐关系层记忆
        try:
            from core.tag_rules import get_tags as _lt_get_tags
            _long_term_query = "query.relationship_longterm" in _lt_get_tags(content)
        except Exception:
            _long_term_query = False

        from core.recall_gate import is_low_information as _is_low_info
        _low_info = _is_low_info(content)
        # C: recall_policy="none" 跳过 episodic/event_search/web_recall 检索层（RC6）。
        _skip_recall = _low_info or recall_policy == "none"
        _long_term_query = _long_term_query and not _skip_recall

        # Memory Event shadow runs once after legacy results are available, so
        # comparison never observes an artificial empty legacy result set.
        _shadow_recall_task = None

        # X2: compute query embedding once (fail-open), then get all semantic hits sync.
        # query_vec is passed down to event_log.search and episodic.retrieve so each
        # can do a source-filtered vs.query internally without re-embedding.
        _query_vec: list | None = None
        _semantic_hits: list = []
        # Brief 36: episodic-scoped top-10 hits, fetched here via query_async (single
        # worker executor) and handed to episodic.retrieve() as sem_hits — retrieve()
        # no longer calls the sync vector_store.query() itself (executor 化收尾).
        _episodic_sem_hits: list = []
        if not _skip_recall:
            try:
                from core.memory.embedding import embed
                _q_vecs = await embed([content])
                _query_vec = _q_vecs[0]
                from core.memory import vector_store as _vs
                _semantic_hits = await _vs.query_async(uid, char_id, _query_vec, k=8)
                if _semantic_hits:
                    logger.debug(
                        "[pipeline.semantic_recall] uid=%s top-%d: %s",
                        uid, len(_semantic_hits),
                        [(h[0], round(h[1], 4)) for h in _semantic_hits],
                    )
                _episodic_sem_hits = await _vs.query_async(
                    uid, char_id, _query_vec, k=10, sources=["episodic"], since_ts=_since_ts
                )
            except Exception as _ee:
                logger.debug("[pipeline.fetch_context] embedding/vs.query skip: %s", _ee)

        # 需要 IO 的任务并发进行
        loop = asyncio.get_event_loop()
        if _skip_recall:
            event_search_task = None
            shadow_search_task = None
        else:
            _search_days = 30
            if _long_term_query:
                try:
                    _all_days = event_log.list_days(uid, char_id=char_id)
                    if _all_days:
                        from datetime import datetime as _dtm
                        _span = (_dtm.now().date() - _dtm.strptime(min(_all_days), "%Y-%m-%d").date()).days + 1
                        _search_days = max(30, min(_span, 3650))
                except Exception as _de:
                    logger.debug("[pipeline.fetch_context] long-term day span skip: %s", _de)
            _search_extra = {"days": _search_days} if _search_days != 30 else {}
            event_search_task = asyncio.create_task(
                event_log.search(uid, content, llm_client, char_id=char_id,
                                 return_trace=True, query_vec=_query_vec,
                                 since_ts=_since_ts, until_ts=_until_ts, **_search_extra)
            )
            from core.memory.event_shadow_recall import run_shadow_query as _run_shadow_query
            shadow_search_task = asyncio.create_task(
                _run_shadow_query(
                    scope, content, occurred_after=_since_ts, occurred_before=_until_ts,
                )
            )
        profile_future = loop.run_in_executor(
            None, lambda: user_profile.load(uid, char_id=char_id)
        )
        # 同步读取（内存/小文件，不值得并发）
        history          = short_term.load_for_prompt(uid, char_id=char_id)
        mid_term_future = loop.run_in_executor(
            None, lambda: mid_term.format_for_prompt(uid, char_id=char_id, history=history)
        )
        # 旧的失败条目在本轮历史投影完成后重试；本轮使用裁剪兜底。
        short_term.schedule_long_user_summaries(uid, char_id=char_id)
        recent_group_ctx = group_context.get_recent(group_id, char_id=char_id)
        relation         = user_relation.get_relation(uid, char_id=char_id)
        lore_entries, _lore_trace = scoped_lore_engine.match(content, history, return_trace=True)

        # 关系事实（动态世界书）— 只注入 confirmed 条目，不含 pending
        try:
            from core.relationship_facts import match as _rf_match
            rf_entries = _rf_match(uid, content, history, char_id=char_id)
            if rf_entries:
                lore_entries = lore_entries + rf_entries
        except Exception as _rfe:
            logger.warning("[pipeline.fetch_context] relationship_facts match failed: %s", _rfe)

        # 情景记忆检索
        # N2-A: fetch_context 是读路径，传 allow_strengthen=False 禁止写回 strength，
        # 避免"召回→增强→更易召回"的永动机效应。写回仍由写路径触发（post_process 等）。
        from core.memory.episodic_memory import retrieve_mixed, format_for_prompt
        from core.memory.user_facts import get_user_pronoun as _get_pronoun
        _user_pronoun = _get_pronoun(uid, char_id=char_id)
        if recall_policy == "none":
            # C: 主动触发的"由头"已在种子 prompt 里写死，不需要被宽泛锚点词捞出的旧情景
            # 记忆带偏（RC6）。注意：只挂 recall_policy，不与 _low_info 合并——原有
            # low_info 场景下这条 retrieve() 一直是无条件执行的，这里不改变那部分行为。
            episodic_memories, _episodic_trace = [], []
        else:
            # M4：混合分桶召回（近期/中期/长期/修复点各取代表），history 用于剔除已在上下文里的近期条目
            episodic_memories, _episodic_trace = retrieve_mixed(
                user_id=uid,
                topic=content,
                char_id=char_id,
                char_name=scoped_character.name,
                history=[h.get("content", "") for h in history[-10:]],
                long_term=_long_term_query,
                return_trace=True,
                query_vec=_query_vec,
                sem_hits=_episodic_sem_hits,
                since_ts=_since_ts,
                until_ts=_until_ts,
            )
        from core.memory.mood_state import get_current as _get_mood
        recall_emotion = _get_mood(char_id=char_id)
        episodic_result = format_for_prompt(
            episodic_memories,
            char_name=scoped_character.name,
            current_emotion=recall_emotion,
            user_pronoun=_user_pronoun,
        )

        # 兜底召回：tag 未命中时备用，存入 context 供 prompt_builder 判断
        from core.memory.episodic_memory import retrieve_fallback
        # 工单 S1：fallback 不看 query，仅限主动回忆（调度器）；普通聊天默认关闭。
        if _skip_recall:
            _episodic_fallback_mode = "skipped_low_info"
        elif _long_term_query:
            _episodic_fallback_mode = "skipped_long_term"
        elif not query_free_fallback:
            _episodic_fallback_mode = "disabled_for_chat"
        else:
            _episodic_fallback_mode = "used"
        if _episodic_fallback_mode != "used":
            # 长期问题已由 retrieve_mixed(long_term=True) 拉齐 long/repair 桶，兜底不再跑
            episodic_fallback, _episodic_fallback_trace = [], []
        else:
            _recent_texts = [h.get("content", "") for h in history[-5:]]
            episodic_fallback, _episodic_fallback_trace = retrieve_fallback(
                user_id=uid,
                recent_history=_recent_texts,
                top_k=1,
                char_id=char_id,
                return_trace=True,
            )
        episodic_fallback_result = format_for_prompt(
            episodic_fallback,
            char_name=scoped_character.name,
            current_emotion=recall_emotion,
            user_pronoun=_user_pronoun,
        ) if episodic_fallback else ""

        # 等待异步任务
        if event_search_task is None:
            event_search_result, _event_log_trace = "", []
        else:
            event_search_result, _event_log_trace = await event_search_task
        profile              = await profile_future
        mid_term_text        = await mid_term_future
        user_identity_text   = await user_identity.format_for_prompt(uid, char_id=char_id)
        # Character-owned user facts; no cross-character fallback
        user_facts_text      = user_facts.format_for_prompt(uid, char_id=char_id)

        # Brief 258: an owned topic has one current-understanding output. The
        # dossier read is local and bounded; legacy retrieval still runs for
        # its independent trace, but overlapping prose is not injected.
        memory_dossier_context = ""
        _dossier_recall: dict = {}
        try:
            from core.config_loader import get_config as _get_cfg_dos
            _dos_cfg = _get_cfg_dos().get("memory_dossiers") or {}
        except Exception:
            _dos_cfg = {}
        # 工单 S3：prompt_injection=false 时不注入也不触发抑制
        _dos_inject = _dos_cfg.get("prompt_injection", True) is not False
        _dos_mode = str(_dos_cfg.get("suppression") or "global").strip().lower()
        if _dos_mode not in ("global", "overlap"):
            _dos_mode = "global"
        if not _skip_recall and _dos_inject:
            try:
                from core.memory.dossiers import build_recall_context
                # 长期问题：空查询取最近 3 份（功能默认关，这里只在启用时生效）
                _dossier_query = "" if _long_term_query else content
                _dossier_recall = await loop.run_in_executor(
                    None, lambda: build_recall_context(scope, _dossier_query),
                )
                memory_dossier_context = str(_dossier_recall.get("text") or "")
            except Exception as _de:
                logger.debug("[pipeline.fetch_context] dossier recall skip: %s", _de)

        # 工单 S3：dossier 抑制。global=整体清空旧召回（现状）；overlap=只移除与 dossier
        # 引用证据（event id）有交集的 episodic / event_search 项。
        _dossier_suppression: dict = {}
        _suppress_all = bool(memory_dossier_context)
        if memory_dossier_context and _dos_mode == "overlap":
            _suppress_all = False
            try:
                _dos_eids = {str(e) for e in (_dossier_recall.get("event_ids") or [])}

                def _split_overlap(items):
                    kept, removed = [], []
                    for it in items or []:
                        ids = set(str(e) for e in (it.get("source_event_ids") or [])) if isinstance(it, dict) else set()
                        (removed if ids & _dos_eids else kept).append(it)
                    return kept, removed

                _kept_ep, _removed_ep = _split_overlap(episodic_memories)
                _kept_fb, _removed_fb = _split_overlap(episodic_fallback)
                if (_removed_ep and _kept_ep) or (_removed_fb and _kept_fb):
                    recall_emotion = _get_mood(char_id=char_id)
                if _removed_ep:
                    episodic_memories = _kept_ep
                    episodic_result = format_for_prompt(
                        episodic_memories, char_name=scoped_character.name,
                        current_emotion=recall_emotion, user_pronoun=_user_pronoun,
                    ) if episodic_memories else ""
                if _removed_fb:
                    episodic_fallback = _kept_fb
                    episodic_fallback_result = format_for_prompt(
                        episodic_fallback, char_name=scoped_character.name,
                        current_emotion=recall_emotion, user_pronoun=_user_pronoun,
                    ) if episodic_fallback else ""
                _removed_ev: list = []
                _ev_fallback = False
                if event_search_result:
                    _ev_ids = [t.get("event_id") for t in _event_log_trace if isinstance(t, dict)]
                    if _ev_ids and all(_ev_ids):
                        # 拿得到 event id：有交集则整体移除（结果为渲染后的字符串，无法逐条拆）
                        _removed_ev = [str(e) for e in _ev_ids if str(e) in _dos_eids]
                        if _removed_ev:
                            event_search_result = ""
                    else:
                        _ev_fallback = True
                        event_search_result = ""
                _kept_count = len(episodic_memories) + len(episodic_fallback) + (1 if event_search_result else 0)
                _dossier_suppression = {
                    "mode": "overlap",
                    "removed_episode_ids": [m.get("id") for m in _removed_ep + _removed_fb if isinstance(m, dict)],
                    "removed_event_ids": _removed_ev,
                    "kept_count": _kept_count,
                    "suppression_fallback": _ev_fallback,
                }
            except Exception as _se:
                logger.debug("[pipeline.fetch_context] dossier overlap suppression failed: %s", _se)
                _suppress_all = True
        if memory_dossier_context and not _dossier_suppression:
            _dossier_suppression = {"mode": "global", "removed_episode_ids": [], "removed_event_ids": [],
                                    "kept_count": 0, "suppression_fallback": False}

        # M5：相识日期事实——只在长期问题时取，平时不常驻
        _relationship_span_text = ""
        _span_source = "none"
        if _long_term_query:
            try:
                from core.memory.relationship_span import first_interaction_info, format_span_fact
                _first_ts, _span_source = first_interaction_info(uid, char_id)
                _relationship_span_text = format_span_fact(_first_ts)
            except Exception as _rse:
                logger.debug("[pipeline.fetch_context] relationship span skip: %s", _rse)

        from core.tools.reminder import get_reminders
        reminders = get_reminders(uid, char_id=char_id)
        from core.memory.diary_context import load as _load_diary, load_meta as _load_diary_meta
        diary_context = _load_diary(uid)
        if diary_context:
            from datetime import date as _date
            _meta = _load_diary_meta(uid)
            _latest = _meta.get("latest_entry_date")
            _fresh = False
            if _latest:
                try:
                    from core.config_loader import get_config as _get_config
                    _max_age = _get_config().get("diary", {}).get("context_max_age_days", 4)
                    _age = (_date.today() - _date.fromisoformat(_latest)).days
                    _fresh = _age <= _max_age
                except ValueError:
                    _fresh = False
            if not _fresh:
                diary_context = ""
        if _low_info:
            diary_context = ""

        # Context fetching is read-only; clock time never invents a mood.

        # Dream impression — forced for the exact post-exit rounds, then topic recall.
        try:
            from core.dream.impression_loader import load_impression_text as _load_imp
            try:
                from core.dream.dream_state import read_state as _read_dream_state_for_imp
                _dream_state = _read_dream_state_for_imp(uid)
            except Exception:
                _dream_state = {}
            try:
                from core.config_loader import get_config as _get_config_for_imp
                _dream_cfg = _get_config_for_imp().get("dream") or {}
                _imp_cfg = _dream_cfg.get("impression") or {}
            except Exception:
                _imp_cfg = {}
            try:
                from core.tag_rules import get_tags as _get_tags_for_imp
                _imp_tags = set(_get_tags_for_imp(content))
            except Exception:
                _imp_tags = set()
            try:
                _forced_left = max(0, int(_dream_state.get("forced_impression_rounds_left", 0)))
            except (TypeError, ValueError):
                _forced_left = 0
            dream_impression_text = _load_imp(
                uid,
                char_id=char_id,
                char_name=scoped_character.name,
                forced_rounds_left=_forced_left,
                latest_dream_id=str(_dream_state.get("last_dream_id") or ""),
                user_text=content,
                tags=_imp_tags,
                recall_enabled=bool(_imp_cfg.get("recall_enabled", True)),
            )
        except Exception:
            dream_impression_text = ""

        # Coplay context — ambient, read-only, active-only (Brief 41). Already
        # fail-open inside build_coplay_context_text() itself; wrapped again here
        # for defense-in-depth (fetch_context must never raise on this).
        try:
            from core.coplay.game_state import build_coplay_context_text as _build_coplay_ctx
            coplay_context_text = _build_coplay_ctx(uid, char_id=char_id)
        except Exception:
            coplay_context_text = ""

        # Coplay afterglow + game_log recall (Brief 42) — only meaningful when
        # NOT currently in an active coplay session (coplay_context already
        # covers "the game you're playing right now").
        coplay_residue_text = ""
        coplay_recall_text = ""
        if not coplay_context_text:
            try:
                from core.coplay.afterglow import load_afterglow_text as _load_coplay_afterglow
                coplay_residue_text = _load_coplay_afterglow(uid, char_id=char_id)
            except Exception:
                coplay_residue_text = ""
            try:
                from core.coplay.game_state import build_game_log_recall_text as _build_coplay_recall
                coplay_recall_text = _build_coplay_recall(uid, content, char_id=char_id)
            except Exception:
                coplay_recall_text = ""

        logger.debug(
            f"[pipeline.fetch_context] uid={uid} "
            f"history={len(history)} lore={len(lore_entries)}"
        )

        # X3: web-sourced semantic recall — query vector_store for source="web" items
        _web_recall_text = ""
        _web_recall_hits: list = []
        if not _skip_recall and _query_vec:
            try:
                from core.memory import vector_store as _vs_web
                _web_hits = await _vs_web.query_with_preview_async(
                    uid, char_id, _query_vec, k=3, sources=["web"]
                )
                if _web_hits:
                    _web_parts = []
                    for _url, _preview, _dist in _web_hits:
                        if _preview:
                            _web_parts.append(
                                f"• {_preview.strip()}\n  来源：{_url}"
                            )
                    _web_recall_hits = [(_u, round(_d, 4)) for _u, _p, _d in _web_hits]
                    if _web_parts:
                        _web_recall_text = "\n\n".join(_web_parts)
                        logger.debug(
                            "[pipeline.web_recall] uid=%s hits=%d", uid, len(_web_hits)
                        )
            except Exception as _we:
                logger.debug("[pipeline.fetch_context] web_recall skip: %s", _we)

        # Recall trace — diagnostic only, never raises, not on hot path.
        # 放在 X3 之后写入，才能带上 web_recall_hits（A2：CC 任务 23）。
        try:
            from core.recall_trace import write_trace as _write_recall_trace
            from core.memory.mood_state import get_intensity as _get_intensity
            from datetime import datetime as _dt
            _shadow_recall = {
                "enabled": False,
                "status": "skipped_legacy_recall" if _skip_recall else "disabled",
                "seed_event_ids": [],
                "new_event_ids": [],
                "expand_count": 0,
                "related_count": 0,
                "candidate_count": 0,
                "chars": 0,
                "tokens": 0,
                "old_chars": 0,
                "old_tokens": 0,
                "overlap_rate": 0.0,
                "event_overlap_rate": 0.0,
                "turn_overlap_rate": 0.0,
                "event_coverage": 0.0,
                "old_mapped_count": 0,
                "old_unmapped_count": 0,
                "new_mapped_count": 0,
                "new_unmapped_count": 0,
                "extra_event_count": 0,
                "omitted_event_count": 0,
                "scope_rejections": 0,
                "truncation_reason": "",
                "timeout_reason": "",
                "elapsed_ms": 0,
            }
            if not _skip_recall:
                _old_chars = len(event_search_result or "") + len(episodic_result or "") + len(episodic_fallback_result or "")
                _legacy_shadow_results = [
                    {
                        "source_event_ids": item.get("source_event_ids") or [],
                        "source_turn_id": item.get("source_turn_id") or "",
                        "scope": {"uid": uid, "char_id": char_id, "realm": "reality"},
                    }
                    for item in (*episodic_memories, *episodic_fallback)
                    if isinstance(item, dict)
                ]
                _legacy_shadow_results.extend(
                    {
                        "turn_id": item.get("turn_id") or "",
                        "scope": {"uid": uid, "char_id": char_id, "realm": "reality"},
                    }
                    for item in _event_log_trace
                    if isinstance(item, dict)
                )
                from core.memory.event_shadow_recall import finalize_shadow_recall as _finalize_shadow_recall
                _shadow_recall = _finalize_shadow_recall(
                    await shadow_search_task, scope=scope,
                    old_results=_legacy_shadow_results, old_chars=_old_chars,
                )
            _write_recall_trace(uid, char_id, {
                "ts": _dt.now().isoformat(timespec="seconds"),
                "uid": uid,
                "char_id": char_id,
                "query": content,
                "episodic_hits": _episodic_trace,
                "long_term_query": _long_term_query,
                "relationship_span_source": _span_source,
                "episodic_fallback_used": bool(episodic_fallback),
                "episodic_fallback_mode": (
                    "empty" if _episodic_fallback_mode == "used" and not episodic_fallback
                    else _episodic_fallback_mode
                ),
                "episodic_fallback_hits": _episodic_fallback_trace,
                "event_log_hits": _event_log_trace,
                "lore_hits": _lore_trace,
                # h = (source_id, distance, ts) — vector_store.query_async() 三元组，
                # 用下标而非解包，兼容 core/memory/vector_store.py 之后再加字段（2026-07-25
                # 排查：ts 加入后这里仍按二元解包，每轮都触发
                # "recall trace write failed: too many values to unpack" 警告，见
                # docs/known-issues.md）。
                "semantic_hits": [(h[0], round(h[1], 4)) for h in _semantic_hits],
                "web_recall_hits": _web_recall_hits,
                "event_shadow_recall": _shadow_recall,
                "dossier_suppression": _dossier_suppression or None,
                "parsed_time_range": (
                    [round(_since_ts, 3) if _since_ts is not None else None,
                     round(_until_ts, 3) if _until_ts is not None else None]
                    if _parsed_time_range else None
                ),
                "mood": {
                    "current": _get_mood(char_id=char_id),
                    "intensity": round(_get_intensity(char_id=char_id), 3),
                },
            })
        except Exception as _te:
            logger.warning("[pipeline.fetch_context] recall trace write failed: %s", _te)

        # Brief 27: 最近工具动作痕迹（跨轮"你刚才做过什么"），fail-open
        _action_trace_entries: list = []
        try:
            from core.memory import action_trace
            from core.config_loader import get_config as _get_config_at
            _at_cfg = _get_config_at().get("action_trace", {})
            _action_trace_entries = action_trace.recent(
                uid, char_id,
                max_items=_at_cfg.get("inject_max_items", 5),
                window_hours=_at_cfg.get("inject_window_hours", 24),
            )
        except Exception as _ate:
            logger.debug("[pipeline.fetch_context] action_trace recent failed: %s", _ate)

        # Hardware jobs are persistent system state; only active jobs are
        # rendered and the remaining time is calculated by core.hardware.jobs.
        _hardware_jobs_text = ""
        try:
            from core.hardware import jobs as _hardware_jobs
            _hardware_jobs_text = _hardware_jobs.format_prompt()
        except Exception as _hje:
            logger.debug("[pipeline.fetch_context] hardware job state failed: %s", _hje)

        # 工单 S2：StatePacket 影子模式只需要 ID/指标，关闭时不计算。
        _state_trace: dict = {}
        try:
            from core.state_composer import enabled_for as _state_shadow_enabled
            if _state_shadow_enabled(uid, char_id):
                def _evt_ids(items) -> list:
                    out: list = []
                    for it in items or []:
                        if isinstance(it, dict):
                            out.extend(it.get("source_event_ids") or [])
                    return out
                _dossier_ids = list((_dossier_recall or {}).get("dossier_ids") or []) \
                    if memory_dossier_context else []
                _state_trace = {
                    "episodic_ids": [m.get("id") for m in episodic_memories if isinstance(m, dict)],
                    "episodic_event_ids": _evt_ids(episodic_memories),
                    "episodic_matches": [
                        t.get("match") for t in _episodic_trace
                        if isinstance(t, dict) and t.get("selected")
                    ],
                    "fallback_ids": [m.get("id") for m in episodic_fallback if isinstance(m, dict)],
                    "fallback_event_ids": _evt_ids(episodic_fallback),
                    "event_log_ids": [
                        t.get("turn_id") for t in _event_log_trace if isinstance(t, dict)
                    ],
                    "event_log_event_ids": [
                        t.get("event_id") for t in _event_log_trace
                        if isinstance(t, dict) and t.get("event_id")
                    ],
                    "dossier_ids": _dossier_ids,
                    "time_intent": bool(_parsed_time_range),
                    "long_term": bool(_long_term_query),
                }
        except Exception as _ste:
            logger.debug("[pipeline.fetch_context] state trace skip: %s", _ste)

        fetched_context = {
            "history":             history,
            "profile":             profile,
            "relation":            relation,
            "group_context":       recent_group_ctx,
            "user_identity_text":  user_identity_text,
            "user_facts_text":     user_facts_text,
            "event_search_result": "" if _suppress_all else event_search_result,
            "lore_entries":        lore_entries,
            "reminders":           reminders,
            "diary_context":       diary_context,
            "episodic_result":          "" if _suppress_all else episodic_result,
            "episodic_fallback_result": "" if _suppress_all else episodic_fallback_result,
            # M4：首条记忆来自哪个桶（recent/mid/long/repair），9.5 层据此决定是否再注入一遍
            "long_term_query":          _long_term_query,
            "relationship_span_text":   _relationship_span_text,
            "episodic_top_bucket":      (episodic_memories[0].get("_bucket", "") if episodic_memories else ""),
            "memory_dossier_context":   memory_dossier_context,
            "mid_term":                 mid_term_text,
            "dream_impression_text":    dream_impression_text,
            "coplay_context_text":      coplay_context_text,
            "coplay_residue_text":    coplay_residue_text,
            "coplay_recall_text":       coplay_recall_text,
            "_scoped_character":        scoped_character,
            "suppress_emotional_recall": _low_info,
            # 语义召回候选（X2 接管 score_recall；X3 复用 query_vec）
            "semantic_hits":    _semantic_hits,
            "query_vec":        _query_vec,
            # X3: web 资料召回（外部事实，已标注来源，不进 episodic/identity）
            "web_recall_result": _web_recall_text,
            "web_recall_hits":   _web_recall_hits,
            "action_trace_entries": _action_trace_entries,
            "_continuity_private": not group_id,
            "hardware_jobs_text": _hardware_jobs_text,
            "_state_trace": _state_trace,
        }
        if not group_id:
            try:
                from core.memory.temporal_facts import project
                fetched_context = await loop.run_in_executor(
                    None, lambda: project(MemoryScope.reality_scope(uid, char_id), profile, fetched_context),
                )
            except Exception as exc:
                log_error("pipeline.temporal_facts", exc)
        return fetched_context
