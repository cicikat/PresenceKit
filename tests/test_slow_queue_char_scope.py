"""
tests/test_slow_queue_char_scope.py

P0-T03: slow_queue payload + handler 透传 char_id 验收测试

Covers:
1.  post_process enqueue payload 携带 active char_id (active=character_b)
2.  角色切换后，旧 payload char_id 保持为入队时快照
3.  handler_summarize_to_midterm 透传 char_id 到 mid_term writer
4.  handler_reflect_to_episodic 透传 char_id 到 episodic writer
5.  handler_user_profile_update 透传 char_id 到 user_profile writer
6.  handler_consolidate_to_identity 透传 char_id 到 identity writer
7.  handler_capture_turn_retry 透传 char_id 到 capture_turn
8.  legacy DLQ payload 缺 char_id 不炸，WARN fallback=yexuan，不静默
9.  active 非法时不入队
10. 内容级端到端：yexuan/character_b 内容只落入对应桶
"""

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / "yexuan.json").write_text(
        json.dumps({"name": "Companion", "description": "test", "world_book": []}),
        encoding="utf-8",
    )
    (chars / "character_b.json").write_text(
        json.dumps({"name": "DemoUser", "description": "character_b test", "world_book": []}),
        encoding="utf-8",
    )
    jb = chars / "reality" / "jailbreaks"
    jb.mkdir(parents=True)
    (jb / "base.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


def _make_pipeline(char_id: str, registry):
    from core.character_loader import load as _load
    from core.pipeline import Pipeline
    char = _load(char_id)
    lore = MagicMock()
    lore.match.return_value = ([], [])
    return Pipeline(char, lore_engine=lore, active_character_id=char_id)


def _write_active(sandbox, char_id: str):
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


# ── 共用 post_process patch 层 ────────────────────────────────────────────────

def _common_post_process_patches():
    return [
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ]


# ── Test 1: post_process 入队 payload 携带 active char_id ─────────────────────

@pytest.mark.asyncio
async def test_post_process_payload_carries_active_char_id(
    chars_tree, monkeypatch, sandbox, registry
):
    """post_process() 所有 enqueue 调用都必须携带当前 active char_id。"""
    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    enqueued: list[tuple[str, dict]] = []

    import core.post_process.slow_queue as sq
    monkeypatch.setattr(sq, "enqueue", lambda task_type, payload: enqueued.append((task_type, payload)))

    from core.write_envelope import WriteEnvelope, SourceType
    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="turn_1"),
    ):
        await pipeline.post_process("u1", "你好", "在的", envelope=env)

    payloads_with_char = [
        (t, p) for t, p in enqueued
        if t not in ("consistency_check",)  # consistency_check 不需要 char_id
    ]
    assert payloads_with_char, "应有至少一个非 consistency_check 入队任务"
    for task_type, payload in payloads_with_char:
        assert payload.get("char_id") == "character_b", (
            f"任务 {task_type!r} payload 缺少 char_id='character_b'，实际: {payload!r}"
        )


# ── Test 2: 切换角色后，旧 payload 保留入队时 char_id ─────────────────────────

@pytest.mark.asyncio
async def test_payload_char_id_is_snapshot_not_current(
    chars_tree, monkeypatch, sandbox, registry
):
    """入队后切换角色不影响已入队 payload 的 char_id 快照。"""
    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")

    enqueued_a: list[dict] = []
    enqueued_b: list[dict] = []
    _enqueued = enqueued_a

    import core.post_process.slow_queue as sq

    def _capture(task_type, payload):
        _enqueued.append(payload)

    monkeypatch.setattr(sq, "enqueue", _capture)

    from core.write_envelope import WriteEnvelope, SourceType
    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="turn_a"),
    ):
        # 第一次入队：yexuan
        await pipeline.post_process("u1", "消息A", "回复A", envelope=env)

    payload_a_char_ids = {p.get("char_id") for p in enqueued_a if p.get("char_id")}
    assert "yexuan" in payload_a_char_ids, f"第一轮 payload char_id 应含 yexuan，实际: {payload_a_char_ids}"

    # 切换到 character_b
    _write_active(sandbox, "character_b")
    _enqueued = enqueued_b

    # 此时 enqueued_a 里的 payload 对象已固化，char_id 不会改变
    for p in enqueued_a:
        cid = p.get("char_id")
        if cid is not None:
            assert cid == "yexuan", (
                f"切换后旧 payload char_id 应仍是 'yexuan'，但变成了: {cid!r}"
            )

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="turn_b"),
    ):
        await pipeline.post_process("u1", "消息B", "回复B", envelope=env)

    payload_b_char_ids = {p.get("char_id") for p in enqueued_b if p.get("char_id")}
    assert "character_b" in payload_b_char_ids, (
        f"切换后第二轮 payload char_id 应含 character_b，实际: {payload_b_char_ids}"
    )


# ── Test 3: handler_summarize_to_midterm 透传 char_id ─────────────────────────

@pytest.mark.asyncio
async def test_handler_summarize_to_midterm_passes_char_id(sandbox):
    """handler_summarize_to_midterm 必须把 payload['char_id'] 传给 mid_term.append。"""
    import core.memory.mid_term as _mt
    import core.llm_client as _llm
    from core.memory.fixation_pipeline import handler_summarize_to_midterm

    captured: list[str] = []

    def _spy_append(uid, summary, tags=None, mid_id=None, source_turn_id=None, *, char_id="yexuan", occurred_at=None, **kw):
        captured.append(char_id)

    monkeypatch_mt = patch.object(_mt, "append", side_effect=_spy_append)
    monkeypatch_load = patch.object(_mt, "load", return_value=[])
    monkeypatch_llm = patch.object(_llm, "summarize_turn", new=AsyncMock(return_value="摘要内容"))

    with monkeypatch_mt, monkeypatch_load, monkeypatch_llm:
        await handler_summarize_to_midterm({
            "turn_id": "turn_x",
            "uid": "u_test",
            "user_content": "内容",
            "reply": "回复",
            "char_id": "character_b",
        })

    assert captured, "mid_term.append 应该被调用"
    assert captured[0] == "character_b", (
        f"handler_summarize_to_midterm 应透传 char_id='character_b'，实际: {captured[0]!r}"
    )


# ── Test 4: handler_reflect_to_episodic 透传 char_id ─────────────────────────

@pytest.mark.asyncio
async def test_handler_reflect_to_episodic_passes_char_id(sandbox):
    """handler_reflect_to_episodic 必须把 payload['char_id'] 传给 write_episode。"""
    import core.memory.episodic_memory as _ep
    import core.memory.mid_term as _mt
    import core.llm_client as _llm
    from core.memory.fixation_pipeline import handler_reflect_to_episodic

    captured: list[str] = []

    def _spy_write_episode(user_id, episode, *, char_id="yexuan"):
        captured.append(char_id)

    fake_mid_entry = {
        "mid_id": "mt_u_x_1",
        "summary": "测试摘要",
        "promoted_to_episodic_id": None,
    }

    llm_response = json.dumps({
        "raw_facts": ["用户说了X"],
        "topic_keywords": ["X"],
        "emotion_peak": "happy",
        "strength": 0.7,
    })

    with (
        patch.object(_mt, "load", return_value=[fake_mid_entry]),
        patch.object(_ep, "_load_memories", return_value=[]),
        patch.object(_ep, "write_episode", side_effect=_spy_write_episode),
        patch.object(_mt, "mark_promoted", return_value=None),
        patch("core.memory.fixation_pipeline._load_fixation_state", return_value={
            "last_consolidated_at": 0.0, "episodic_since_last": 0,
            "high_strength_since_last": 0, "strength_accumulated": 0.0, "last_sweep_at": 0.0,
        }),
        patch("core.memory.fixation_pipeline._save_fixation_state", return_value=None),
        patch("core.llm_client.chat", new=AsyncMock(return_value=llm_response)),
    ):
        await handler_reflect_to_episodic({
            "uid": "u_test",
            "mid_ids": ["mt_u_x_1"],
            "trigger": "eager",
            "char_id": "character_b",
        })

    assert captured, "write_episode 应该被调用"
    assert captured[0] == "character_b", (
        f"handler_reflect_to_episodic 应透传 char_id='character_b'，实际: {captured[0]!r}"
    )


# ── Test 5: user_profile_update handler 透传 char_id ─────────────────────────

@pytest.mark.asyncio
async def test_handler_user_profile_update_passes_char_id(sandbox):
    """_handler_user_profile_update 必须把 payload['char_id'] 传给 extract_and_update。"""
    import core.memory.user_profile as _up
    from core.pipeline import _handler_user_profile_update

    captured: list[str] = []

    async def _spy_extract(uid, recent_messages, *, char_id="yexuan"):
        captured.append(char_id)

    with patch.object(_up, "extract_and_update", side_effect=_spy_extract):
        await _handler_user_profile_update({
            "uid": "u_test",
            "recent": [{"role": "user", "content": "hi"}],
            "char_id": "character_b",
        })

    assert captured, "extract_and_update 应该被调用"
    assert captured[0] == "character_b", (
        f"handler_user_profile_update 应透传 char_id='character_b'，实际: {captured[0]!r}"
    )


# ── Test 6: handler_consolidate_to_identity 透传 char_id ─────────────────────

@pytest.mark.asyncio
async def test_handler_consolidate_to_identity_passes_char_id(sandbox):
    """handler_consolidate_to_identity 必须把 payload['char_id'] 传给 consolidate_to_identity。"""
    import core.memory.fixation_pipeline as _fp

    captured: list[str] = []

    async def _spy_consolidate(uid, llm_client, *, char_id="yexuan"):
        captured.append(char_id)
        return True

    with patch.object(_fp, "consolidate_to_identity", side_effect=_spy_consolidate):
        await _fp.handler_consolidate_to_identity({
            "uid": "u_test",
            "char_id": "character_b",
        })

    assert captured, "consolidate_to_identity 应该被调用"
    assert captured[0] == "character_b", (
        f"handler_consolidate_to_identity 应透传 char_id='character_b'，实际: {captured[0]!r}"
    )


# ── Test 7: handler_capture_turn_retry 透传 char_id ─────────────────────────

@pytest.mark.asyncio
async def test_handler_capture_turn_retry_passes_char_id(sandbox):
    """handler_capture_turn_retry 必须把 payload['char_id'] 传给 capture_turn。"""
    import core.memory.fixation_pipeline as _fp

    captured: list[str] = []

    def _spy_capture_turn(uid, user_msg, reply, emotion="neutral", turn_id=None,
                          trigger_name="", envelope=None, *, char_id="yexuan"):
        captured.append(char_id)
        return turn_id or f"{uid}_spy"

    with patch.object(_fp, "capture_turn", side_effect=_spy_capture_turn):
        await _fp.handler_capture_turn_retry({
            "uid": "u_test",
            "turn_id": "turn_retry",
            "user_content": "内容",
            "reply": "回复",
            "emotion": "neutral",
            "char_id": "character_b",
        })

    assert captured, "capture_turn 应该被调用"
    assert captured[0] == "character_b", (
        f"handler_capture_turn_retry 应透传 char_id='character_b'，实际: {captured[0]!r}"
    )


# ── Test 8: legacy DLQ payload 缺 char_id 不炸，WARN fallback ─────────────────

@pytest.mark.asyncio
async def test_legacy_payload_missing_char_id_warns_and_falls_back(sandbox, caplog):
    """
    对所有 handler，不带 char_id 的 legacy payload 必须：
    - 不因 KeyError 崩溃
    - 透传到 writer 的 char_id 为 'yexuan'
    - caplog 中有 WARN 且含 'yexuan'
    """
    import core.memory.fixation_pipeline as _fp
    import core.memory.mid_term as _mt
    import core.memory.user_profile as _up
    import core.llm_client as _llm

    # --- handler_summarize_to_midterm ---
    st_captured: list[str] = []

    def _spy_mt_append(uid, summary, tags=None, mid_id=None, source_turn_id=None, *, char_id="yexuan", occurred_at=None, **kw):
        st_captured.append(char_id)

    with (
        patch.object(_mt, "append", side_effect=_spy_mt_append),
        patch.object(_mt, "load", return_value=[]),
        patch.object(_llm, "summarize_turn", new=AsyncMock(return_value="摘要")),
        caplog.at_level(logging.WARNING, logger="core.memory.fixation_pipeline"),
    ):
        await _fp.handler_summarize_to_midterm({
            "turn_id": "t_legacy",
            "uid": "u_legacy",
            "user_content": "内容",
            "reply": "回复",
            # 故意不带 char_id
        })

    assert st_captured, "mid_term.append 应被调用（legacy fallback 路径）"
    assert st_captured[0] == "yexuan", (
        f"legacy fallback char_id 必须是 'yexuan'，实际: {st_captured[0]!r}"
    )
    assert any("yexuan" in r.message for r in caplog.records if r.levelno >= logging.WARNING), (
        "caplog 应含有包含 'yexuan' 的 WARNING"
    )

    # --- handler_capture_turn_retry ---
    caplog.clear()
    retry_captured: list[str] = []

    def _spy_ct(uid, user_msg, reply, emotion="neutral", turn_id=None,
                trigger_name="", envelope=None, *, char_id="yexuan"):
        retry_captured.append(char_id)
        return turn_id or f"{uid}_spy"

    with (
        patch.object(_fp, "capture_turn", side_effect=_spy_ct),
        caplog.at_level(logging.WARNING, logger="core.memory.fixation_pipeline"),
    ):
        await _fp.handler_capture_turn_retry({
            "uid": "u_legacy",
            "turn_id": "t_retry",
            "user_content": "内容",
            "reply": "回复",
            # 故意不带 char_id
        })

    assert retry_captured, "capture_turn 应被调用（legacy fallback 路径）"
    assert retry_captured[0] == "yexuan", (
        f"legacy fallback char_id 必须是 'yexuan'，实际: {retry_captured[0]!r}"
    )
    assert any("yexuan" in r.message for r in caplog.records if r.levelno >= logging.WARNING), (
        "capture_turn_retry legacy caplog 应含 'yexuan' WARN"
    )

    # --- _handler_user_profile_update (pipeline.py handler) ---
    caplog.clear()
    from core.pipeline import _handler_user_profile_update

    up_captured: list[str] = []

    async def _spy_extract(uid, recent_messages, *, char_id="yexuan"):
        up_captured.append(char_id)

    with (
        patch.object(_up, "extract_and_update", side_effect=_spy_extract),
        caplog.at_level(logging.WARNING, logger="core.pipeline"),
    ):
        await _handler_user_profile_update({
            "uid": "u_legacy",
            "recent": [],
            # 故意不带 char_id
        })

    assert up_captured, "extract_and_update 应被调用（legacy fallback 路径）"
    assert up_captured[0] == "yexuan", (
        f"legacy fallback char_id 必须是 'yexuan'，实际: {up_captured[0]!r}"
    )
    assert any("yexuan" in r.message for r in caplog.records if r.levelno >= logging.WARNING), (
        "user_profile_update legacy caplog 应含 'yexuan' WARN"
    )


# ── Test 9: active 非法时不入队 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_process_invalid_active_does_not_enqueue(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    当 active_character 非法时，post_process 应抛错且完全不调用 slow_queue.enqueue。
    """
    import core.post_process.slow_queue as sq
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("yexuan", registry)
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "missing_id", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    enqueue_called = []

    def _fail_enqueue(task_type, payload):
        enqueue_called.append((task_type, payload))
        pytest.fail(f"slow_queue.enqueue 不应被调用，但调用了: {task_type}")

    monkeypatch.setattr(sq, "enqueue", _fail_enqueue)

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with pytest.raises((ValueError, RuntimeError)):
        await pipeline.post_process("u1", "你好", "在的", envelope=env)

    assert enqueue_called == [], "active 非法时 slow_queue.enqueue 不应被调用"


# ── Test 10: 内容级端到端 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_char_scoped_mid_term_isolation(sandbox):
    """
    端到端：yexuan 和 character_b 的 summarize_to_midterm 写入各自桶，互不污染。
    使用 slow_queue.drain() 等待 worker 处理完毕，LLM 已 monkeypatch。
    """
    import core.post_process.slow_queue as sq
    import core.memory.mid_term as _mt
    import core.llm_client as _llm
    from core.memory.fixation_pipeline import handler_summarize_to_midterm

    uid = "e2e_uid_t03"

    # 注册 handler
    sq.register_handler("summarize_to_midterm", handler_summarize_to_midterm)

    # monkeypatch LLM，返回包含 char_id 特征字符串的固定摘要
    async def _mock_summarize(user_msg, reply, tags=None, **kwargs):
        return f"摘要:{user_msg[:20]}"

    with patch.object(_llm, "summarize_turn", side_effect=_mock_summarize):
        sq.start_worker()

        # 入队 yexuan 任务
        sq.enqueue("summarize_to_midterm", {
            "turn_id": f"turn_{uid}_yexuan",
            "uid": uid,
            "user_content": "草莓大福-T03",
            "reply": "好吃的",
            "tags": [],
            "emotion": "neutral",
            "char_id": "yexuan",
        })

        # 入队 character_b 任务
        sq.enqueue("summarize_to_midterm", {
            "turn_id": f"turn_{uid}_character_b",
            "uid": uid,
            "user_content": "XYZ动画-T03",
            "reply": "好看的",
            "tags": [],
            "emotion": "neutral",
            "char_id": "character_b",
        })

        await sq.drain()

    # 读两个桶
    yexuan_events = _mt.load(uid, char_id="yexuan")
    character_b_events = _mt.load(uid, char_id="character_b")

    yexuan_text = " ".join(e.get("summary", "") for e in yexuan_events)
    character_b_text = " ".join(e.get("summary", "") for e in character_b_events)

    assert "草莓大福-T03" in yexuan_text, (
        f"yexuan 桶应含 '草莓大福-T03'，实际: {yexuan_text!r}"
    )
    assert "XYZ动画-T03" not in yexuan_text, (
        f"yexuan 桶不应含 'XYZ动画-T03'（character_b 内容），实际: {yexuan_text!r}"
    )
    assert "XYZ动画-T03" in character_b_text, (
        f"character_b 桶应含 'XYZ动画-T03'，实际: {character_b_text!r}"
    )
    assert "草莓大福-T03" not in character_b_text, (
        f"character_b 桶不应含 '草莓大福-T03'（yexuan 内容），实际: {character_b_text!r}"
    )


# ── T-01/T-02 回归：快速冒烟 ─────────────────────────────────────────────────

# Import modules eagerly so module-level init runs before any chdir
import core.memory.short_term      # noqa: F401
import core.memory.event_log       # noqa: F401
import core.memory.user_profile    # noqa: F401
import core.memory.mid_term        # noqa: F401
import core.memory.episodic_memory # noqa: F401
import core.memory.user_identity   # noqa: F401
import core.dream.impression_loader# noqa: F401
import core.memory.group_context   # noqa: F401
import core.memory.diary_context   # noqa: F401
import core.tools.reminder         # noqa: F401
import core.memory.mood_state      # noqa: F401
import core.user_relation          # noqa: F401


def test_t01_regression_fetch_context_char_id(chars_tree, monkeypatch, sandbox, registry):
    """T-01 回归：fetch_context 仍向 short_term.load_for_prompt 传 char_id。"""
    import core.memory.short_term as _st
    import core.memory.event_log as _el
    import core.memory.user_profile as _up
    import core.memory.mid_term as _mt
    import core.memory.episodic_memory as _ep
    import core.memory.user_identity as _ui
    import core.dream.impression_loader as _il
    import core.memory.group_context as _gc
    import core.memory.diary_context as _dc
    import core.memory.mood_state as _ms
    import core.user_relation as _ur

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    monkeypatch.setattr(_el, "search", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(_up, "load", lambda *a, **kw: {})
    monkeypatch.setattr(_mt, "format_for_prompt", lambda *a, **kw: "")
    monkeypatch.setattr(_ep, "retrieve", lambda *a, **kw: ([], []) if kw.get("return_trace") else [])
    monkeypatch.setattr(_ep, "retrieve_fallback", lambda *a, **kw: ([], []) if kw.get("return_trace") else [])
    monkeypatch.setattr(_ui, "format_for_prompt", AsyncMock(return_value=""))
    monkeypatch.setattr(_il, "load_impression_text", lambda *a, **kw: "")
    monkeypatch.setattr(_gc, "get_recent", lambda *a, **kw: "")
    monkeypatch.setattr(_ms, "get_current", lambda *a, **kw: "neutral")
    monkeypatch.setattr(_ms, "update", lambda *a, **kw: None)
    monkeypatch.setattr(_ur, "get_relation", lambda *a, **kw: {"priority": 1})
    try:
        monkeypatch.setattr(_dc, "load", lambda *a, **kw: "")
    except Exception:
        pass
    import core.tools.reminder as _rem
    try:
        monkeypatch.setattr(_rem, "get_reminders", lambda *a, **kw: [])
    except Exception:
        pass

    captured: list[str] = []

    def _spy_load(user_id, *, budget_rounds=None, near_k=5, char_id="yexuan"):
        captured.append(char_id)
        return []

    monkeypatch.setattr(_st, "load_for_prompt", _spy_load)

    asyncio.run(pipeline.fetch_context(user_id="u1", content="hello"))

    assert captured, "short_term.load_for_prompt 应被调用"
    assert captured[0] == "character_b", (
        f"T-01 回归: short_term.load_for_prompt 应收到 char_id='character_b'，实际: {captured[0]!r}"
    )


def test_t02_regression_capture_turn_char_id(sandbox):
    """T-02 回归：capture_turn 仍向 short_term.append 传 char_id。"""
    import core.memory.short_term as _st
    import core.memory.event_log as _el
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import WriteEnvelope, SourceType

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)
    captured: list[str] = []

    def _spy_st(user_id, role, content, turn_id=None, *, char_id="yexuan"):
        captured.append(char_id)
        return True

    with (
        patch.object(_st, "append", side_effect=_spy_st),
        patch.object(_el, "append", return_value=True),
    ):
        capture_turn("u1", "你好", "在的", char_id="character_b", envelope=env)

    assert captured, "short_term.append 应被调用"
    assert all(c == "character_b" for c in captured), (
        f"T-02 回归: short_term.append 应收到 char_id='character_b'，实际: {captured}"
    )
