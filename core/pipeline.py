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
from core.data_paths import DEFAULT_CHAR_ID

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

# Phase A v8: avatar directive throttle — same emotion within cooldown window is skipped
_AVATAR_DIRECTIVE_LAST: dict[str, tuple[str, float]] = {}  # char_id → (emotion, sent_at)
_AVATAR_DIRECTIVE_COOLDOWN_SEC = 5.0


def _voice_reanchor(char_id: str) -> str:
    """Brief 28 · tool loop 收尾锚定：工具轮之后主生成容易滑进"报告腔"，
    用这条静态 system 提示把声音收回角色本身。char_name 走 get_char_name()，
    禁字面角色名（硬性规则8）。"""
    from core.character_name_provider import get_char_name
    char_name = get_char_name(char_id)
    return (
        f"工具用完了。接下来只以{char_name}的声音回复，"
        "把查到的东西揉进你自己的话里，不要报告腔、不要罗列。"
    )


def _check_yandere_trigger(user_message: str, reply: str, relation_priority: int) -> bool:
    if relation_priority < YANDERE_RELATION_THRESHOLD:
        return False
    text = user_message + " " + reply
    return any(kw in text for kw in YANDERE_KEYWORDS)


async def _maybe_push_avatar_directive(mood_state: dict, char_id: str) -> None:
    """Phase A v8: push avatar_directive to desktop client when mood changes."""
    import time as _t
    try:
        from core.tool_dispatcher import _push_desktop_action
        emotion = mood_state.get("current", "neutral")
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
        })
    except Exception as e:
        logger.debug("[pipeline] avatar_directive push skipped: %s", e)


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
        recall_policy: str = "seed",
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

        recall_policy（CC 任务 19 · C，召回锚点治理）：
          - "seed"（默认，兼容现状）：episodic/event_search/web_recall 正常检索，锚点为
            content（调用方传入的 search_query 或 prompt 全文）。
          - "anchored"：语义同 "seed"（检索层不跳过），仅表示调用方已用触发器自带的具体
            锚点（话题 key、被选中记忆原文）作为 content，而非宽泛种子词或 prompt 全文。
          - "none"：跳过 episodic/event_search/web_recall 三个检索层，只保留
            identity/mood/short_term/花园等状态层。主动触发的"由头"已经在种子 prompt 里
            写死（天气、想她了、看到她久坐），角色不需要再被 "今天" 这类宽泛词捞出的旧
            情景记忆带偏（RC6：乱召回导致"胡乱召回然后说一大堆废话"）。
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
        scoped_character = self.character
        scoped_lore_engine = self.lore_engine
        if char_id != self._active_character_id:
            from core.character_loader import load as load_character
            from core.lore_engine import LoreEngine

            scoped_character = load_character(char_id)
            scoped_lore_engine = LoreEngine()
            scoped_lore_engine.load()
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

        from core.recall_gate import is_low_information as _is_low_info
        _low_info = _is_low_info(content)
        # C: recall_policy="none" 跳过 episodic/event_search/web_recall 检索层（RC6）。
        _skip_recall = _low_info or recall_policy == "none"

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
        else:
            event_search_task = asyncio.create_task(
                event_log.search(uid, content, llm_client, char_id=char_id,
                                 return_trace=True, query_vec=_query_vec,
                                 since_ts=_since_ts, until_ts=_until_ts)
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
        from core.memory.episodic_memory import retrieve, format_for_prompt
        from core.memory.user_facts import get_user_pronoun as _get_pronoun
        _user_pronoun = _get_pronoun(uid)
        if recall_policy == "none":
            # C: 主动触发的"由头"已在种子 prompt 里写死，不需要被宽泛锚点词捞出的旧情景
            # 记忆带偏（RC6）。注意：只挂 recall_policy，不与 _low_info 合并——原有
            # low_info 场景下这条 retrieve() 一直是无条件执行的，这里不改变那部分行为。
            episodic_memories, _episodic_trace = [], []
        else:
            episodic_memories, _episodic_trace = retrieve(
                user_id=uid,
                topic=content,
                top_k=3,
                char_id=char_id,
                char_name=scoped_character.name,
                allow_strengthen=False,
                return_trace=True,
                query_vec=_query_vec,
                sem_hits=_episodic_sem_hits,
                since_ts=_since_ts,
                until_ts=_until_ts,
            )
        from core.memory.mood_state import get_current as _get_mood
        episodic_result = format_for_prompt(
            episodic_memories,
            char_name=scoped_character.name,
            current_emotion=_get_mood(char_id=char_id),
            user_pronoun=_user_pronoun,
        )

        # 兜底召回：tag 未命中时备用，存入 context 供 prompt_builder 判断
        from core.memory.episodic_memory import retrieve_fallback
        if _skip_recall:
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
        from core.memory.mood_state import get_current as _get_mood2
        episodic_fallback_result = format_for_prompt(
            episodic_fallback,
            char_name=scoped_character.name,
            current_emotion=_get_mood2(char_id=char_id),
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
        # uid-only global facts — no char_id, no fallback
        user_facts_text      = user_facts.format_for_prompt(uid)

        from core.tools.reminder import get_reminders
        reminders = get_reminders(uid)
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

        # N2-A: sleepy mood 已迁出 — 见 Pipeline.post_process 开头的
        #        maybe_mark_sleepy_from_time() 调用。fetch_context 是读路径，不写 mood。

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
            _write_recall_trace(uid, char_id, {
                "ts": _dt.now().isoformat(timespec="seconds"),
                "uid": uid,
                "char_id": char_id,
                "query": content,
                "episodic_hits": _episodic_trace,
                "episodic_fallback_used": bool(episodic_fallback),
                "episodic_fallback_hits": _episodic_fallback_trace,
                "event_log_hits": _event_log_trace,
                "lore_hits": _lore_trace,
                "semantic_hits": [(_sid, round(_dist, 4)) for _sid, _dist in _semantic_hits],
                "web_recall_hits": _web_recall_hits,
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
        consume_pending_perception: bool = True,
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
        _pending_paths: list[str] = []
        if consume_pending_perception:
            _pending, _pending_paths = _pending_perception.read_and_mark()
            if _pending:
                _perception = _pending.strip()

        # 跨通道接续感知
        if channel and self._last_channel and channel != self._last_channel:
            _switch_hint = "（你感觉她像是换了个地方继续跟你说话，但这还是同一段对话的延续。）"
            _perception = (_perception + "；" + _switch_hint) if _perception else _switch_hint
        if channel:
            self._last_channel = channel

        scoped_character = context.get("_scoped_character", self.character)
        scoped_author_note = self.author_note_extra if _char_id == self._active_character_id else ""
        messages, debug_info = prompt_builder.build(
            character=scoped_character,
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
            author_note_extra=scoped_author_note,
            current_time=_current_time,
            reminders=context.get("reminders", []),
            diary_context=context.get("diary_context", ""),
            episodic_result=context.get("episodic_result", ""),
            episodic_fallback_result=context.get("episodic_fallback_result", ""),
            mid_term_context=context.get("mid_term", ""),
            tags=_tags,
            dream_impression_text=context.get("dream_impression_text", ""),
            coplay_context_text=context.get("coplay_context_text", ""),
            coplay_residue_text=context.get("coplay_residue_text", ""),
            coplay_recall_text=context.get("coplay_recall_text", ""),
            char_id=_char_id,
            stage_presence=context.get("stage_presence", ""),
            stage_transcript=context.get("stage_transcript", ""),
            suppress_emotional_recall=context.get("suppress_emotional_recall", False),
            web_recall_result=context.get("web_recall_result", ""),
            web_recall_hits=context.get("web_recall_hits", []),
            action_trace_entries=context.get("action_trace_entries", []),
        )
        if _char_id == self._active_character_id:
            self.author_note_extra = ""
        debug_info["pending_paths"] = _pending_paths

        try:
            from core.observe.prompt_capture import capture as _capture_prompt
            _capture_prompt(user_id, messages, debug_info)
        except Exception:
            pass

        return messages, debug_info

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 3：调用 LLM（含重试）
    # ──────────────────────────────────────────────────────────────────────────

    async def run_llm(
        self, messages: list[dict], *, char_id: str | None = None, is_proactive: bool = False
    ) -> str:
        """调用 LLM 生成回复，失败自动重试。

        char_id: 显式指定"替谁说话"时传（Brief 30，如 Stage 群聊非活跃角色）；
          None（默认）按活跃角色解析，与现状一致。
        is_proactive: 本次是否 scheduler 主动消息（Brief 32 · thinking.apply_to_proactive 用）。
        """
        from core import llm_client
        from core.error_handler import with_retry

        @with_retry(module_name="pipeline.llm_call")
        async def _call():
            return await llm_client.chat(messages, char_id=char_id, is_proactive=is_proactive)

        reply = await _call()
        return await self._anti_collapse_prefix_retry(messages, reply)

    async def _anti_collapse_prefix_retry(self, messages: list[dict], reply: str) -> str:
        """
        问题7 (c) 输出端校验重试（硬止血）：
        软提示（层9历史投影去同质 + 层11 S2 提示）已经是"劝"，这里是"拦"——
        如果输出仍然复现了检测到的重复句首 P，追加一条强 system 指令重试 1 次；
        重试仍命中且 P 是填充词（嗯/啊/呃/哦/唔/哈等）→ 剥掉开头 P 后接受，
        非填充词前缀不做硬剥离，只接受重试结果。fail-open：任何异常都返回原始 reply。
        """
        try:
            from core.config_loader import get_config
            if not get_config().get("anti_collapse", {}).get("prefix_retry", True):
                return reply

            from core.memory.short_term import (
                detect_reply_homogeneity_prefix,
                is_filler_prefix,
            )
            # messages 里层9历史条目已可能被 build() 做过去同质投影（剥掉了重复前缀），
            # 命中的用 _raw_content 复原原文，才能拿到与 build() 内部同一份检测结果 P。
            hist_for_check = [
                {"role": "assistant", "content": m.get("_raw_content", m.get("content", ""))}
                for m in messages
                if m.get("_layer") == "9_history" and m.get("role") == "assistant"
            ]
            prefix = detect_reply_homogeneity_prefix(hist_for_check)
            if not prefix or not reply.strip().startswith(prefix):
                return reply

            logger.info("[anti_collapse] prefix retry")
            from core import llm_client
            retry_messages = messages + [{
                "role": "system",
                "content": f"你上一条回复又以「{prefix}」开头了，这次绝对不能再用这个开头，换一种方式开口。",
            }]
            retry_reply = await llm_client.chat(retry_messages)
            if retry_reply.strip().startswith(prefix) and is_filler_prefix(prefix):
                stripped = retry_reply.strip()[len(prefix):].lstrip()
                return stripped or retry_reply
            return retry_reply
        except Exception as e:
            from core.error_handler import log_error
            log_error("pipeline._anti_collapse_prefix_retry", e)
            return reply

    async def run_llm_stream(
        self, messages: list[dict], *, char_id: str | None = None, is_proactive: bool = False
    ):
        """流式生成，逐 token yield。失败（含零产出）时降级为非流式整段 yield 一次。

        降级语义：
        - 0 token + 异常 → 非流式 run_llm 兜底（完整输出）。
        - 部分 token + 异常 → 中止，不追加非流式文本（避免重复拼接）。

        char_id: 显式指定"替谁说话"时传（Brief 30）；None（默认）按活跃角色解析。
        is_proactive: 本次是否 scheduler 主动消息（Brief 32）。
        """
        from core import llm_client
        got_any = False
        try:
            async for piece in llm_client.chat_stream(messages, char_id=char_id, is_proactive=is_proactive):
                got_any = True
                yield piece
            # 流正常结束
            if got_any:
                return
            # 0 token（模型返回空流） → 降级
        except Exception as e:
            from core.error_handler import log_error
            log_error("pipeline.run_llm_stream", e)
            if got_any:
                # 已推出部分 token，中止而非追加以免重复
                return
        full = await self.run_llm(messages, char_id=char_id, is_proactive=is_proactive)
        if full:
            yield full

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤3B：多步工具执行器（Brief 28 · Path C，function_calling 模型专用）
    # ──────────────────────────────────────────────────────────────────────────

    async def run_agentic_loop(
        self,
        messages: list[dict],
        *,
        uid: str,
        char_id: str,
        session_state,
        is_group: bool = False,
        stream: bool = False,
        is_proactive: bool = False,
    ):
        """主生成多步调用工具再回答，只在 tool_dispatcher.tool_loop_active(uid) 为真时被调用。

        stream=False 时返回 str；stream=True 时返回 async generator（仅最终答案流式，
        工具决策步骤全程非流式——chat_turn 需要 tools 参数，与 chat_stream 的既有约束冲突）。

        origin 固定传 "assistant_loop"（tool_dispatcher._EXECUTE_ALLOWED_ORIGINS 白名单项）。
        is_proactive: 本次是否 scheduler 主动消息（Brief 32）。
        """
        from core import llm_client, thinking
        from core.config_loader import get_config
        from core.error_handler import log_error
        from core.tool_dispatcher import execute as _execute, get_tools_schema

        # Brief 32：monologue 注入只做一次（首步前），loop 内逐步复用同一份 loop_msgs，
        # 之后每步的 chat_turn() 不会重复触发（native 路线则每步都在 chat_turn 内部叠加）。
        messages = await thinking.maybe_apply(
            messages, call_category="chat", char_id=char_id, is_proactive=is_proactive,
        )

        cfg = get_config().get("tool_loop", {})
        max_steps = int(cfg.get("max_steps", 5))
        total_timeout_s = float(cfg.get("total_timeout_s", 90))
        # per-char 工具暴露面覆盖（Brief 29 · 3.4）：活跃角色卡 presence_ext.tool_categories
        # 存在则用它，否则回落全局 tool_loop.categories。exclude_tools 保持全局，不许 per-char 绕过。
        _active_char = getattr(self, "character", None)
        char_categories = (_active_char.presence_ext or {}).get("tool_categories") if _active_char else None
        categories = char_categories if char_categories is not None else cfg.get("categories", ["info", "desktop", "memory"])
        exclude_tools = set(cfg.get("exclude_tools", []))

        # Keep the registry helper's long-standing call shape for test/plugin
        # compatibility; proficiency is an exposure-layer filter applied here.
        from core.growth.mcp_proficiency import filter_schemas as _filter_growth_tools
        tools = [
            t for t in _filter_growth_tools(get_tools_schema(categories=categories), char_id=char_id)
            if (t.get("function") or t).get("name") not in exclude_tools
        ]

        loop_msgs = list(messages)
        # 工具意愿软提示（Brief 29 · 5，Brief 28 补丁）：利用 recency 位置，插在用户消息
        # 之前；只在 loop 首步注入一次，不进 short_term history（loop_msgs 本就是一次性副本）。
        if cfg.get("nudge_hint", True) and loop_msgs:
            loop_msgs.insert(len(loop_msgs) - 1, {
                "role": "system",
                "content": "需要外部信息或操作时，直接调用可用工具，不要凭记忆编造。",
                "_layer": "11.5_tool_nudge",
            })
        used_tool = False
        # ("natural"/"exhausted"/"confirm", text) — 收尾结果种类 + 文本
        outcome: tuple[str, str] | None = None

        async def _run_steps() -> None:
            nonlocal used_tool, outcome
            for _step in range(max_steps):
                turn = await llm_client.chat_turn(
                    loop_msgs, tools, char_id=char_id, is_proactive=is_proactive,
                )
                if not turn.tool_calls:
                    if turn.content.strip():
                        outcome = ("natural", turn.content)
                    else:
                        # Some OpenAI-compatible gateways occasionally return a
                        # successful tool-loop response whose assistant message
                        # contains neither content nor tool_calls.  Treating that
                        # as a natural stop leaks an empty reply into turn_sink,
                        # where USER_CHAT correctly rejects it and /desktop/chat
                        # becomes a local 500.  Reuse the existing tool-free
                        # closing path instead; it is also the safest fallback
                        # when the gateway loses a function-call translation.
                        logger.warning(
                            "[pipeline.run_agentic_loop] empty assistant completion "
                            "without tool_calls; falling back to tool-free final generation"
                        )
                        outcome = ("empty", "")
                    return
                loop_msgs.append(turn.assistant_message)
                used_tool = True
                for tc in turn.tool_calls:
                    try:
                        result, ask_confirm = await _execute(
                            tc["name"], tc["arguments"], uid, uid, is_group,
                            session_state, origin="assistant_loop", char_id=char_id,
                        )
                    except Exception as e:
                        log_error("pipeline.run_agentic_loop.execute", e)
                        result, ask_confirm = None, None
                    loop_msgs.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": ask_confirm or result or "（工具无结果或执行失败）",
                    })
                    if ask_confirm:
                        outcome = ("confirm", ask_confirm)
                        return
            outcome = ("exhausted", "")

        try:
            await asyncio.wait_for(_run_steps(), timeout=total_timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                "[pipeline.run_agentic_loop] 总预算 %.0fs 超时，按步数耗尽处理", total_timeout_s
            )
            outcome = ("exhausted", "")

        async def _single_chunk(text: str):
            if text:
                yield text

        kind, text = outcome
        if kind == "confirm":
            return _single_chunk(text) if stream else text

        if kind == "natural" and not used_tool:
            # 从未调用过工具：等价于原有单发生成，反坍缩检查照旧过一遍。
            final_text = await self._anti_collapse_prefix_retry(loop_msgs, text)
            return _single_chunk(final_text) if stream else final_text

        # natural（用过工具）或 exhausted：强制收尾，注入声音锚定，走不带 tools 的出口。
        loop_msgs.append({"role": "system", "content": _voice_reanchor(char_id)})
        if stream:
            return self.run_llm_stream(loop_msgs, is_proactive=is_proactive)
        return await self.run_llm(loop_msgs, is_proactive=is_proactive)

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 4：异步后处理
    # ──────────────────────────────────────────────────────────────────────────

    async def post_process_critical(
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
    ) -> dict:
        """
        Brief 37：send 前必须走完的关键路径——只做毫秒级本地落盘（capture_turn），
        不 await 任何 LLM/网络往返。detect_emotion / mood_state / avatar / profile /
        slow_queue 全部移到 post_process_slow，由调用方在 send 完成后异步调度
        （见 core/turn_sink.py record_assistant_turn）。

        emotion 占位：event_log 里这一轮的 emotion 字段写死 "neutral"（真实情绪
        要等 detect_emotion 完成才知道，那已经在 slow 段）。这只是 event_log 的
        标注字段，不影响任何下游判断——mid_term eager reflect 的情绪判断消费的是
        slow 段里 detect 出的真实 emotion，不读 event_log 这份占位值。

        返回值原样传给 post_process_slow(critical_result=...)。

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

        # N2-A/N2-B: sleepy mood — 从 fetch_context 迁出的显式写操作。
        # 放在关键段开头（uid_lock 外）：保留深夜 sleepy 语义，
        # 但不再污染读路径。覆盖所有调用链（QQ / admin / scheduler），
        # 因为 record_assistant_turn 最终都走 pipeline.post_process_critical。
        # N2-B: helper 已升级为 async + global_lock("mood_state")，需 await。
        # 本身不含 LLM/网络往返，留在关键段不影响 send 延迟。
        try:
            from core.mood_helpers import maybe_mark_sleepy_from_time as _mark_sleepy
            await _mark_sleepy(uid=user_id, char_id=char_id, envelope=envelope)
        except Exception as _sleepy_err:
            logger.warning("[pipeline.post_process_critical] sleepy mood 写入异常（已忽略）: %s", _sleepy_err)

        from core.memory import locks as _locks
        from core.error_handler import log_error
        from core.post_process import slow_queue

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

            # ── capture_turn：写 short_term + event_log（含 turn_id 血缘）───
            try:
                from core.memory.fixation_pipeline import capture_turn as _capture_turn
                _turn_id = _capture_turn(user_id, content, reply, "neutral", turn_id=_turn_id, trigger_name=trigger_name, envelope=envelope, char_id=char_id, audit_extras=audit_extras)
                _critical_written = True
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
                    })

        if pending_paths:
            _pending_perception.confirm_delivered(pending_paths)

        return {
            "turn_id": _turn_id,
            "critical_written": _critical_written,
            "emotion": "neutral",
            "char_id": char_id,
            "scope_payload": scope_payload,
            "should_update_profile": _should_update_profile,
            "profile_recent": _profile_recent,
        }

    async def post_process_slow(
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
    ) -> dict:
        """
        Brief 37：send 之后异步执行的慢段——detect_emotion → mood_state → avatar/
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

        # ── detect_emotion（带超时，只依赖 reply 文本）──
        try:
            _emotion = await asyncio.wait_for(
                llm_client.detect_emotion(reply), timeout=_DETECT_EMOTION_TIMEOUT
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[pipeline.post_process_slow] detect_emotion 降级 neutral: {e}")
            _emotion = "neutral"

        async with _locks.uid_lock(user_id):
            # ── mood_state 更新（全局锁，嵌套在 uid_lock 内；用刚 detect 出的 _emotion）
            _mood_state_after: dict | None = None
            if envelope.can_affect_mood:
                async with _locks.global_lock("mood_state"):
                    try:
                        from core.memory.mood_state import update as _update_mood
                        _mood_state_after = _update_mood(_emotion, source="detect", char_id=char_id)

                        try:
                            from core import user_relation as _user_relation
                            _relation = _user_relation.get_relation(user_id)
                            if _check_yandere_trigger(content, reply, _relation.get("priority", 1)):
                                from core.memory.mood_state import update as _update_mood_y
                                _mood_state_after = _update_mood_y("yandere", source="trigger", char_id=char_id)
                        except Exception as e:
                            log_error("post_process.yandere", e)
                    except Exception as e:
                        log_error("post_process.mood_state", e)

            # Phase A v8: push avatar directive (fail-open, outside locks)
            if _mood_state_after is not None:
                asyncio.create_task(_maybe_push_avatar_directive(_mood_state_after, char_id))
            # 爱意探针 → 板子爱心（非阻塞、锁外、fail-open）；梦境/沙盒等场景不触发实体爱心。
            if envelope.can_affect_mood:
                from core.embodiment.heart import maybe_draw_heart as _maybe_heart
                asyncio.create_task(_maybe_heart(reply, char_id))

        # ── 入慢队列 ───────────────────────────────────────────
        from core.tag_rules import get_tags as _get_tags
        _mt_tags = list(_get_tags(content))

        # Consolidation is silent exactly when this turn is a forced injection
        # or a topic-recall hit. Read before consuming so the third forced turn
        # still carries dream_echo=True.
        _dream_echo = False
        try:
            from core.dream.dream_state import (
                read_state as _read_dream_state,
                consume_forced_impression_round,
            )
            from core.dream.impression_loader import load_impression_text as _load_imp_for_echo
            from core.config_loader import get_config as _get_config_for_echo

            _echo_state = _read_dream_state(user_id)
            try:
                _echo_forced_left = max(
                    0, int(_echo_state.get("forced_impression_rounds_left", 0))
                )
            except (TypeError, ValueError):
                _echo_forced_left = 0
            _echo_dream_cfg = _get_config_for_echo().get("dream") or {}
            _echo_imp_cfg = _echo_dream_cfg.get("impression") or {}
            _dream_echo = bool(_load_imp_for_echo(
                user_id,
                char_id=char_id,
                forced_rounds_left=_echo_forced_left,
                latest_dream_id=str(_echo_state.get("last_dream_id") or ""),
                user_text=f"{content}\n{reply}",
                tags=set(_mt_tags),
                recall_enabled=bool(_echo_imp_cfg.get("recall_enabled", True)),
            ))
        except Exception as _echo_error:
            logger.warning(
                "[pipeline.post_process_slow] dream impression echo failed uid=%s: %s",
                user_id, _echo_error,
            )
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
            slow_queue.enqueue("summarize_to_midterm", _mt_payload)
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
                char_id=char_id,
                loop_executed=loop_executed,
            ))
        except Exception as e:
            log_error("pipeline.post_process.intent", e)

        return {
            "emotion": _emotion,
            "turn_id": _turn_id,
        }

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
        web_echo: bool = False,
        coplay_echo: bool = False,
        loop_executed: bool = False,
    ):
        """
        Brief 37 拆分前的组合入口：依次 await post_process_critical()（落盘）与
        post_process_slow()（detect_emotion / mood / slow_queue 等）。

        只给不需要"send 前只等落盘"的调用方用（如 admin/routers/chat.py 的
        fire-and-forget 桌宠聊天路径，整个 post_process 已经是 asyncio.create_task
        丢出去的，早于它的 HTTP 响应已经返回，等多久都不影响 send）。

        send 关键路径（core/turn_sink.py record_assistant_turn）请分别 await
        post_process_critical() 再在 send 完成后 asyncio.create_task()
        post_process_slow()，不要调用这个组合入口。
        """
        critical_result = await self.post_process_critical(
            user_id, content, reply,
            target_id=target_id, is_group=is_group, pending_paths=pending_paths,
            trigger_name=trigger_name, envelope=envelope, audit_extras=audit_extras,
            frozen_scope=frozen_scope,
        )
        slow_result = await self.post_process_slow(
            user_id, content, reply, critical_result,
            target_id=target_id, is_group=is_group, trigger_name=trigger_name,
            envelope=envelope, audit_extras=audit_extras, web_echo=web_echo,
            coplay_echo=coplay_echo, loop_executed=loop_executed,
        )
        return {
            "emotion": slow_result["emotion"],
            "turn_id": critical_result["turn_id"],
            "critical_written": critical_result["critical_written"],
        }

    # _compress_episode 已迁移为模块级 _do_compress_episode，由 slow_queue handler 调用

    async def _parse_and_execute_intent(
        self,
        reply: str,
        *,
        trigger_name: str = "",
        user_content: str = "",
        user_id: str = "",
        char_id: str | None = None,
        loop_executed: bool = False,
    ) -> None:
        """
        Path B: 解析角色回复里声称要执行的桌面操作，写入 agent_actions.json 队列。
        角色说'我去把游戏关掉'→真的执行 minimize_window。

        守卫（全部满足才执行）：
          (a) trigger_name 为空 → 真实 owner turn（非 scheduler/sensor/watch）
          (b) user_content 非空非纯空白 → 本轮有真实用户输入
          (c) 意图非 dangerous（device_shutdown/device_sleep 永不经此路径触发）
          (c2) per-uid 同动作幂等窗口 60s：窗口内已执行 → 跳过
          (d) Brief 28：本轮已走 tool loop（loop_executed=True）→ 跳过，模型在 loop 里
              已有完整行动机会，回复文本里的"我去帮你打开"不再需要反向解析执行一次。
        """
        import json as _json
        import re
        import time as _time
        from core import llm_client
        from core.config_loader import get_config as _cfg
        from core.error_handler import log_error

        # Brief 35 · Path B 两步降级第一步：config 默认关，关闭时直接 return。
        # 观察期一个月（见 config intent_reflex / docs/known-issues.md），无缺口后
        # 第二步整删本函数 + 守卫 + c2 幂等窗口。
        if not _cfg().get("intent_reflex", {}).get("enabled", False):
            logger.debug("[pipeline.intent] 跳过: intent_reflex.enabled=false（Path B 降级）")
            return

        # guard (d): this turn already went through the tool loop → skip Path B
        if loop_executed:
            logger.debug("[pipeline.intent] 跳过: loop_executed=True，本轮已走 tool loop")
            return

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

        _char = self.character.name

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
            f"params: {{\"title\": \"标题\", \"message\": \"内容\"}}\n"
            f"- dream_invite: 邀请用户进入梦境，仅当{_char}明确表达「一起去梦里/想和你做梦/来梦里找我」"
            f"等直接邀请语义时才触发，params: {{}}\n"
            f"- toy_invite: 进入玩耍模式，仅当{_char}明确表达「想和你玩玩具/一起玩/给你点小奖励/打开玩耍模式」"
            f"等当下、第一人称、主动邀请一起玩 toy 的语义时才触发，params: {{}}\n\n"
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

            # Brief 27 · 2.2: Path B 不经 tool_dispatcher.execute()，在此单独补记痕迹。
            try:
                from core.memory import action_trace
                _at_char_id = char_id or self._active_character_id
                _at_args = ", ".join(
                    f"{k}={v}" for k, v in params.items() if isinstance(v, (str, int, float))
                )[:60]
                action_trace.record(
                    user_id, _at_char_id,
                    tool=action, origin="assistant_intent",
                    status=("ok" if last_result == "ok" else "failed"),
                    args_digest=_at_args, result_digest=str(last_result)[:80],
                )
            except Exception as _at_err:
                log_error("pipeline.intent.action_trace", _at_err)

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
    user_id: str, user_content: str, reply: str, *, char_id: str = DEFAULT_CHAR_ID
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
    from core.character_name_provider import get_active_char_name

    char_name = get_active_char_name()

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


async def _handler_trait_tracker_update(payload: dict) -> None:
    # R8-B: independent trait refresh — detached from character_growth.update()
    import yaml
    from core.memory import locks as _locks, short_term as _st
    from core.memory.trait_tracker import count_traits_in_history, update_trait_state
    from core.sandbox import get_paths

    scope = _get_scope_from_payload(payload, "_handler_trait_tracker_update")
    uid = scope.uid
    char_id = scope.character_id

    traits_path = get_paths().yexuan_traits(char_id=char_id)
    try:
        with open(traits_path, encoding="utf-8") as _f:
            data = yaml.safe_load(_f)
        trait_key = f"{char_id}_traits"
        traits: list = data.get(trait_key) or data.get("yexuan_traits") or []
    except Exception as _e:
        logger.warning("[pipeline.trait_tracker] traits 定义加载失败，跳过: %s", _e)
        return
    if not traits:
        logger.debug("[pipeline.trait_tracker] traits 为空，跳过: char_id=%s", char_id)
        return

    async with _locks.uid_lock(uid):
        recent = _st.load(uid, char_id=char_id)[-40:]
        # 只统计 assistant 行：user 行包含关键词不代表角色表达过该特质
        history_lines = [msg["content"] for msg in recent if msg.get("role") == "assistant"]
        counts = count_traits_in_history(history_lines, traits)
        trait_path = get_paths().trait_state(char_id=char_id)
        update_trait_state(counts, trait_path, write_path=trait_path)
    logger.info("[pipeline.trait_tracker] 更新完成: uid=%s char_id=%s", uid, char_id)


def register_slow_handlers() -> None:
    """main.py 启动时调用一次，注册所有慢任务 handler。"""
    from core.post_process import slow_queue
    from core.memory.fixation_pipeline import (
        handler_capture_turn_retry,
        handler_summarize_to_midterm,
        handler_reflect_to_episodic,
        handler_consolidate_to_identity,
        handler_digest_evicted_episodes,
    )
    # 新 pipeline handler
    slow_queue.register_handler("capture_turn_retry",       handler_capture_turn_retry)
    slow_queue.register_handler("summarize_to_midterm",     handler_summarize_to_midterm)
    slow_queue.register_handler("reflect_to_episodic",      handler_reflect_to_episodic)
    slow_queue.register_handler("consolidate_to_identity",  handler_consolidate_to_identity)
    slow_queue.register_handler("digest_evicted_episodes",  handler_digest_evicted_episodes)
    slow_queue.register_handler("consistency_check",       _handler_consistency_check)
    slow_queue.register_handler("user_profile_update",     _handler_user_profile_update)
    slow_queue.register_handler("trait_tracker_update",    _handler_trait_tracker_update)
    from core.post_process.toy_autogrow import handler_toy_autogrow
    slow_queue.register_handler("toy_autogrow",            handler_toy_autogrow)
    from core.stage.char_relations import handler_update_char_relations
    slow_queue.register_handler("update_char_relations",   handler_update_char_relations)
    from core.growth.practice_session import handler_practice_session
    slow_queue.register_handler("practice_session",        handler_practice_session)
