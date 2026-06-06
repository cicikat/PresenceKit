"""
tests/test_slow_queue_scope_payload.py

P1-3A: slow_queue payload MemoryScope 接入验收测试

Covers:
1.  新 enqueue payload 包含 scope 字段
2.  新 enqueue payload 同时保留 uid 和 char_id（向后兼容）
3.  handler 优先从 payload["scope"] 读取 uid / char_id
4.  scope=hongcha vs char_id=yexuan 冲突时，handler 以 scope 为准
5.  scope domain 非 reality → fail-loud ValueError
6.  scope 缺 character_id（如 global scope）→ fail-loud，不 fallback yexuan
7.  旧 payload 有 char_id、无 scope → 仍兼容
8.  旧 payload 无 char_id、无 scope → WARN + fallback yexuan
9.  handler_capture_turn_retry 使用 scope 中的 char_id
10. handler_summarize_to_midterm 使用 scope 中的 char_id
11. handler_reflect_to_episodic 使用 scope 中的 char_id
12. handler_user_profile_update 使用 scope 中的 char_id
13. handler_consolidate_to_identity 使用 scope 中的 char_id
14. 新 payload 不改变 slow_queue_char_scope 现有测试结果（regression）
15. MemoryScope roundtrip: to_payload() / from_payload() 结果一致
"""

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from core.memory.scope import MemoryScope


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / "yexuan.json").write_text(
        json.dumps({"name": "叶瑄", "description": "test", "world_book": []}),
        encoding="utf-8",
    )
    (chars / "hongcha.json").write_text(
        json.dumps({"name": "红茶", "description": "hongcha test", "world_book": []}),
        encoding="utf-8",
    )
    jb = chars / "reality" / "jailbreaks"
    jb.mkdir(parents=True)
    (jb / "base.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    import core.asset_registry as _reg_mod
    from core.asset_registry import AssetRegistry
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


def _make_pipeline(char_id: str, registry):
    from core.character_loader import load as _load
    from core.pipeline import Pipeline
    char = _load(char_id)
    lore = MagicMock()
    lore.match.return_value = []
    return Pipeline(char, lore_engine=lore, active_character_id=char_id)


def _write_active(sandbox, char_id: str):
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


# ── Test 1: 新 enqueue payload 包含 scope 字段 ───────────────────────────────

@pytest.mark.asyncio
async def test_new_payload_contains_scope_field(chars_tree, monkeypatch, sandbox, registry):
    """post_process 入队的每个携带 char_id 的 payload 都应有 scope 字段。"""
    pipeline = _make_pipeline("hongcha", registry)
    _write_active(sandbox, "hongcha")

    enqueued: list[tuple[str, dict]] = []
    import core.post_process.slow_queue as sq
    monkeypatch.setattr(sq, "enqueue", lambda tt, p: enqueued.append((tt, p)))

    from core.write_envelope import WriteEnvelope, SourceType
    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="turn_1"),
    ):
        await pipeline.post_process("u1", "hello", "hi", envelope=env)

    scoped_payloads = [(t, p) for t, p in enqueued if t != "consistency_check"]
    assert scoped_payloads, "应有至少一个非 consistency_check 入队任务"
    for task_type, payload in scoped_payloads:
        assert "scope" in payload, f"任务 {task_type!r} payload 缺少 scope 字段，实际: {payload!r}"


# ── Test 2: 新 enqueue payload 同时保留 uid 和 char_id ──────────────────────

@pytest.mark.asyncio
async def test_new_payload_retains_uid_and_char_id(chars_tree, monkeypatch, sandbox, registry):
    """新 payload 在加入 scope 的同时，仍应保留 uid 和 char_id 字段（向后兼容）。"""
    pipeline = _make_pipeline("hongcha", registry)
    _write_active(sandbox, "hongcha")

    enqueued: list[tuple[str, dict]] = []
    import core.post_process.slow_queue as sq
    monkeypatch.setattr(sq, "enqueue", lambda tt, p: enqueued.append((tt, p)))

    from core.write_envelope import WriteEnvelope, SourceType
    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="turn_1"),
    ):
        await pipeline.post_process("u42", "hello", "hi", envelope=env)

    scoped_payloads = [(t, p) for t, p in enqueued if t != "consistency_check"]
    for task_type, payload in scoped_payloads:
        assert "uid" in payload, f"{task_type!r} payload 缺少 uid"
        assert "char_id" in payload, f"{task_type!r} payload 缺少 char_id（需向后兼容）"
        assert payload["uid"] == "u42", f"{task_type!r} uid 应为 'u42'"
        assert payload["char_id"] == "hongcha", f"{task_type!r} char_id 应为 'hongcha'"


# ── Test 3: handler 优先从 scope 读取 uid / char_id ─────────────────────────

@pytest.mark.asyncio
async def test_handler_reads_uid_char_id_from_scope(sandbox):
    """handler_summarize_to_midterm 优先从 payload['scope'] 取 uid/char_id。"""
    import core.memory.mid_term as _mt
    from core.memory.fixation_pipeline import handler_summarize_to_midterm

    captured: list[tuple[str, str]] = []

    def _spy_append(uid, summary, tags=None, mid_id=None, source_turn_id=None, *, char_id="yexuan"):
        captured.append((uid, char_id))

    scope = MemoryScope.reality_scope("u_scope", "hongcha").to_payload()
    payload = {
        "turn_id": "t1",
        "uid": "u_scope",
        "user_content": "msg",
        "reply": "rep",
        "char_id": "hongcha",
        "scope": scope,
    }

    with (
        patch.object(_mt, "append", side_effect=_spy_append),
        patch("core.memory.fixation_pipeline.summarize_to_midterm", new=AsyncMock()) as mock_sum,
    ):
        # directly check scope extraction without running full summarize_to_midterm
        from core.memory.fixation_pipeline import _get_scope_from_payload
        result_scope = _get_scope_from_payload(payload, "test")
        assert result_scope.uid == "u_scope"
        assert result_scope.character_id == "hongcha"
        assert result_scope.domain == "reality"


# ── Test 4: scope 与 char_id 冲突时 scope 优先 ──────────────────────────────

def test_scope_wins_over_char_id_field():
    """payload 同时有 scope=hongcha 和 char_id=yexuan 时，_get_scope_from_payload 应采用 scope。"""
    from core.memory.fixation_pipeline import _get_scope_from_payload

    scope_payload = MemoryScope.reality_scope("u99", "hongcha").to_payload()
    payload = {
        "uid": "u99",
        "char_id": "yexuan",    # 与 scope 冲突
        "scope": scope_payload,
    }
    result = _get_scope_from_payload(payload, "conflict_test")
    assert result.character_id == "hongcha", (
        f"scope 应优先于 char_id，期望 hongcha，实际: {result.character_id!r}"
    )
    assert result.uid == "u99"


# ── Test 5: scope domain 非 reality → fail-loud ValueError ──────────────────

def test_non_reality_scope_raises():
    """scope.domain != 'reality' 时 handler 应 fail-loud，不允许 fallback。"""
    from core.memory.fixation_pipeline import _get_scope_from_payload
    from core.memory.scope import MemoryScope

    # dream scope 需要 world_id
    dream_scope = MemoryScope.dream_scope("u1", "yexuan", "world_x").to_payload()
    payload = {"uid": "u1", "char_id": "yexuan", "scope": dream_scope}

    with pytest.raises(ValueError, match="reality"):
        _get_scope_from_payload(payload, "test_handler")


def test_non_reality_scope_raises_pipeline():
    """pipeline._get_scope_from_payload 对非 reality domain 也应 fail-loud。"""
    from core.pipeline import _get_scope_from_payload

    dream_scope = MemoryScope.dream_scope("u1", "yexuan", "world_x").to_payload()
    payload = {"uid": "u1", "char_id": "yexuan", "scope": dream_scope}

    with pytest.raises(ValueError, match="reality"):
        _get_scope_from_payload(payload, "test_handler")


# ── Test 6: scope 缺 character_id（global scope）→ fail-loud ────────────────

def test_global_scope_raises_on_from_payload():
    """global scope 在 MemoryScope.__post_init__ 就会拒绝 character_id，
    from_payload({domain:'global', uid:'u1', character_id:None}) 会构造出一个无 char 的 scope；
    handler 发现 domain != reality → ValueError。"""
    from core.memory.fixation_pipeline import _get_scope_from_payload

    global_scope = MemoryScope.global_scope("u1").to_payload()
    payload = {"uid": "u1", "scope": global_scope}

    with pytest.raises(ValueError, match="reality"):
        _get_scope_from_payload(payload, "test_global")


# ── Test 7: 旧 payload 有 char_id、无 scope → 兼容 ─────────────────────────

def test_legacy_payload_with_char_id_no_scope():
    """旧 payload 没有 scope 字段但有 char_id，_get_scope_from_payload 应正常返回对应 scope。"""
    from core.memory.fixation_pipeline import _get_scope_from_payload

    payload = {"uid": "u_legacy", "char_id": "hongcha"}
    scope = _get_scope_from_payload(payload, "legacy_test")
    assert scope.uid == "u_legacy"
    assert scope.character_id == "hongcha"
    assert scope.domain == "reality"


def test_legacy_payload_pipeline_with_char_id_no_scope():
    """pipeline._get_scope_from_payload 对旧格式同样兼容。"""
    from core.pipeline import _get_scope_from_payload

    payload = {"uid": "u_old", "char_id": "yexuan"}
    scope = _get_scope_from_payload(payload, "legacy_test")
    assert scope.uid == "u_old"
    assert scope.character_id == "yexuan"
    assert scope.domain == "reality"


# ── Test 8: 旧 payload 无 char_id、无 scope → WARN + fallback yexuan ────────

def test_legacy_payload_no_char_id_no_scope_warns_and_fallback(caplog):
    """旧 DLQ payload 缺 char_id 和 scope 时应 WARN 并 fallback yexuan。"""
    from core.memory.fixation_pipeline import _get_scope_from_payload

    payload = {"uid": "u_dlq"}
    with caplog.at_level(logging.WARNING):
        scope = _get_scope_from_payload(payload, "dlq_handler")

    assert scope.character_id == "yexuan", "fallback 应为 yexuan"
    assert scope.uid == "u_dlq"
    assert any("yexuan" in r.message for r in caplog.records), "应有 WARN 日志"


def test_legacy_payload_pipeline_no_char_id_warns(caplog):
    """pipeline._get_scope_from_payload 同样在无 scope/char_id 时 WARN + fallback yexuan。"""
    from core.pipeline import _get_scope_from_payload

    payload = {"uid": "u_dlq2"}
    with caplog.at_level(logging.WARNING):
        scope = _get_scope_from_payload(payload, "dlq_handler2")

    assert scope.character_id == "yexuan"
    assert any("yexuan" in r.message for r in caplog.records)


# ── Test 9: handler_capture_turn_retry 使用 scope 中的 char_id ──────────────

@pytest.mark.asyncio
async def test_handler_capture_turn_retry_uses_scope_char_id(sandbox):
    """handler_capture_turn_retry 应从 payload['scope'] 提取 char_id。"""
    from core.memory.fixation_pipeline import handler_capture_turn_retry

    captured_char_ids: list[str] = []

    def _spy_capture_turn(uid, user_content, reply, emotion, *, turn_id, trigger_name,
                          envelope, char_id):
        captured_char_ids.append(char_id)

    scope = MemoryScope.reality_scope("u1", "hongcha").to_payload()
    payload = {
        "uid": "u1",
        "char_id": "yexuan",   # 旧字段，scope 应覆盖
        "scope": scope,
        "user_content": "msg",
        "reply": "rep",
        "emotion": "neutral",
        "turn_id": "t99",
        "trigger_name": "test",
    }

    with (
        patch("core.memory.fixation_pipeline.capture_turn", side_effect=_spy_capture_turn),
        patch("core.memory.locks.uid_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)
        await handler_capture_turn_retry(payload)

    assert captured_char_ids == ["hongcha"], (
        f"capture_turn_retry 应用 scope.character_id='hongcha'，实际: {captured_char_ids}"
    )


# ── Test 10: handler_summarize_to_midterm 使用 scope 中的 char_id ────────────

@pytest.mark.asyncio
async def test_handler_summarize_to_midterm_uses_scope_char_id(sandbox):
    """handler_summarize_to_midterm 应从 payload['scope'] 提取 char_id。"""
    from core.memory.fixation_pipeline import handler_summarize_to_midterm

    captured: list[tuple[str, str]] = []

    async def _spy_summarize(turn_id, uid, user_msg, reply, tags, emotion, *, char_id):
        captured.append((uid, char_id))

    scope = MemoryScope.reality_scope("u2", "hongcha").to_payload()
    payload = {
        "turn_id": "t2",
        "uid": "u2",
        "user_content": "msg",
        "reply": "rep",
        "char_id": "yexuan",   # scope 应覆盖
        "scope": scope,
    }

    with patch("core.memory.fixation_pipeline.summarize_to_midterm", side_effect=_spy_summarize):
        await handler_summarize_to_midterm(payload)

    assert captured == [("u2", "hongcha")], f"期望 [('u2','hongcha')]，实际: {captured}"


# ── Test 11: handler_reflect_to_episodic 使用 scope 中的 char_id ─────────────

@pytest.mark.asyncio
async def test_handler_reflect_to_episodic_uses_scope_char_id(sandbox):
    """handler_reflect_to_episodic 应从 payload['scope'] 提取 char_id。"""
    from core.memory.fixation_pipeline import handler_reflect_to_episodic

    captured: list[tuple[str, str]] = []

    async def _spy_reflect(uid, mid_ids, trigger, *, char_id):
        captured.append((uid, char_id))

    scope = MemoryScope.reality_scope("u3", "hongcha").to_payload()
    payload = {
        "uid": "u3",
        "mid_ids": ["m1"],
        "trigger": "eager",
        "char_id": "yexuan",
        "scope": scope,
    }

    with patch("core.memory.fixation_pipeline.reflect_to_episodic", side_effect=_spy_reflect):
        await handler_reflect_to_episodic(payload)

    assert captured == [("u3", "hongcha")], f"期望 [('u3','hongcha')]，实际: {captured}"


# ── Test 12: handler_user_profile_update 使用 scope 中的 char_id ─────────────

@pytest.mark.asyncio
async def test_handler_user_profile_update_uses_scope_char_id(sandbox):
    """pipeline._handler_user_profile_update 应从 payload['scope'] 提取 char_id。"""
    from core.pipeline import _handler_user_profile_update

    captured: list[tuple[str, str]] = []

    async def _spy_extract(uid, recent, *, char_id):
        captured.append((uid, char_id))

    scope = MemoryScope.reality_scope("u4", "hongcha").to_payload()
    payload = {
        "uid": "u4",
        "recent": "some text",
        "char_id": "yexuan",
        "scope": scope,
    }

    with (
        patch("core.memory.user_profile.extract_and_update", side_effect=_spy_extract),
        patch("core.memory.locks.uid_lock") as mock_lock,
    ):
        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)
        await _handler_user_profile_update(payload)

    assert captured == [("u4", "hongcha")], f"期望 [('u4','hongcha')]，实际: {captured}"


# ── Test 13: handler_consolidate_to_identity 使用 scope 中的 char_id ──────────

@pytest.mark.asyncio
async def test_handler_consolidate_to_identity_uses_scope_char_id(sandbox):
    """handler_consolidate_to_identity 应从 payload['scope'] 提取 char_id。"""
    from core.memory.fixation_pipeline import handler_consolidate_to_identity

    captured: list[tuple[str, str]] = []

    async def _spy_consolidate(uid, llm_client, *, char_id):
        captured.append((uid, char_id))

    scope = MemoryScope.reality_scope("u5", "hongcha").to_payload()
    payload = {
        "uid": "u5",
        "char_id": "yexuan",
        "scope": scope,
    }

    with patch("core.memory.fixation_pipeline.consolidate_to_identity", side_effect=_spy_consolidate):
        await handler_consolidate_to_identity(payload)

    assert captured == [("u5", "hongcha")], f"期望 [('u5','hongcha')]，实际: {captured}"


# ── Test 14: 新 payload 不改变 char_id 字段（regression：char_scope 仍全绿）──

@pytest.mark.asyncio
async def test_new_payload_char_id_field_unchanged(chars_tree, monkeypatch, sandbox, registry):
    """新 payload 中 char_id 字段值与 scope.character_id 一致，不影响原有读取逻辑。"""
    pipeline = _make_pipeline("hongcha", registry)
    _write_active(sandbox, "hongcha")

    enqueued: list[tuple[str, dict]] = []
    import core.post_process.slow_queue as sq
    monkeypatch.setattr(sq, "enqueue", lambda tt, p: enqueued.append((tt, p)))

    from core.write_envelope import WriteEnvelope, SourceType
    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
        patch("core.memory.fixation_pipeline.capture_turn", return_value="turn_1"),
    ):
        await pipeline.post_process("u_reg", "msg", "rep", envelope=env)

    for task_type, payload in enqueued:
        if task_type == "consistency_check":
            continue
        # scope.character_id == char_id 字段
        if "scope" in payload and "char_id" in payload:
            scope = MemoryScope.from_payload(payload["scope"])
            assert scope.character_id == payload["char_id"], (
                f"{task_type!r}: scope.character_id={scope.character_id!r} 与 "
                f"char_id={payload['char_id']!r} 不一致"
            )
        if "scope" in payload and "uid" in payload:
            scope = MemoryScope.from_payload(payload["scope"])
            assert scope.uid == payload["uid"], (
                f"{task_type!r}: scope.uid={scope.uid!r} 与 uid={payload['uid']!r} 不一致"
            )


# ── Test 15: MemoryScope roundtrip ───────────────────────────────────────────

def test_memory_scope_roundtrip_reality():
    """MemoryScope.reality_scope().to_payload() → from_payload() 应完整还原。"""
    original = MemoryScope.reality_scope("u_rt", "hongcha")
    raw = original.to_payload()
    restored = MemoryScope.from_payload(raw)
    assert restored == original


def test_memory_scope_roundtrip_dream():
    """dream scope roundtrip 保持一致。"""
    original = MemoryScope.dream_scope("u_d", "yexuan", "world_z")
    raw = original.to_payload()
    restored = MemoryScope.from_payload(raw)
    assert restored == original


def test_memory_scope_roundtrip_global():
    """global scope roundtrip 保持一致。"""
    original = MemoryScope.global_scope("u_g")
    raw = original.to_payload()
    restored = MemoryScope.from_payload(raw)
    assert restored == original


def test_memory_scope_from_payload_missing_field_raises():
    """from_payload 缺少 uid 或 domain 时应 fail-loud。"""
    with pytest.raises((ValueError, TypeError)):
        MemoryScope.from_payload({"domain": "reality", "character_id": "yexuan"})  # 缺 uid

    with pytest.raises((ValueError, TypeError)):
        MemoryScope.from_payload({"uid": "u1"})  # 缺 domain


def test_memory_scope_from_payload_not_dict_raises():
    """from_payload 传入非 dict 应 fail-loud。"""
    with pytest.raises(TypeError):
        MemoryScope.from_payload("not_a_dict")
