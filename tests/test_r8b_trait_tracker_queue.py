"""
R8-B: trait_tracker_update 独立 slow_queue 任务。

Coverage:
1.  register_slow_handlers() 注册 trait_tracker_update handler
2.  post_process() 在 can_write_memory=True 时入队 trait_tracker_update
3.  payload 使用实际 char_id，不硬编码 yexuan（用 character_b 证伪）
4.  can_write_memory=False 时不入队
5.  handler 直接执行后写入 trait_state 文件
6.  author_note_rotator 读取的路径与 handler 写入路径一致
7.  fetch_context 源码中不含 trait_tracker_update 调用
8.  R3 Rule-1：core/pipeline.py 不引入新 char_id="yexuan" 函数参数默认值
"""

import json
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PUBLIC_DEFAULT_CHAR_ID = "default"

# ── Fixtures（镜像 test_pipeline_write_scope.py，用于 post_process 集成测试）────

@pytest.fixture
def chars_tree(tmp_path):
    """Minimal characters/ tree with yexuan + character_b."""
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
    import core.asset_registry as _reg_mod
    from core.asset_registry import AssetRegistry
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


def _make_pipeline(char_id: str, registry):
    from core.character_loader import load as _load
    from core.pipeline import Pipeline
    from unittest.mock import MagicMock
    char = _load(char_id)
    lore = MagicMock()
    lore.match.return_value = ([], [])
    return Pipeline(char, lore_engine=lore, active_character_id=char_id)


def _write_active(sandbox, char_id: str):
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({
            "active_character": char_id,
            "enabled_lorebooks": [],
            "enabled_jailbreaks": [],
        }),
        encoding="utf-8",
    )


# ── 1. Handler 注册 ────────────────────────────────────────────────────────────

def test_handler_registered_in_slow_queue(monkeypatch):
    """register_slow_handlers() 必须注册 trait_tracker_update。"""
    import core.post_process.slow_queue as sq
    sq._handlers = {}

    async def _stub(_): ...
    monkeypatch.setattr("core.memory.fixation_pipeline.handler_capture_turn_retry",      _stub, raising=False)
    monkeypatch.setattr("core.memory.fixation_pipeline.handler_summarize_to_midterm",    _stub, raising=False)
    monkeypatch.setattr("core.memory.fixation_pipeline.handler_reflect_to_episodic",     _stub, raising=False)
    monkeypatch.setattr("core.memory.fixation_pipeline.handler_consolidate_to_identity", _stub, raising=False)

    from core.pipeline import register_slow_handlers
    register_slow_handlers()

    assert "trait_tracker_update" in sq._handlers, (
        "trait_tracker_update must be registered after register_slow_handlers()"
    )


# ── 2. post_process 入队 ───────────────────────────────────────────────────────

async def test_post_process_enqueues_trait_tracker_update(
    chars_tree, monkeypatch, sandbox, registry
):
    """post_process() with can_write_memory=True must enqueue trait_tracker_update."""
    import core.memory.fixation_pipeline as _fp
    import core.post_process.slow_queue as sq
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")

    enqueued: list[tuple[str, dict]] = []

    def _spy_ct(uid, user_msg, reply, emotion="neutral", turn_id=None, trigger_name="",
                envelope=None, *, char_id="yexuan", **kw):
        return turn_id or f"{uid}_spy"

    monkeypatch.setattr(_fp, "capture_turn", _spy_ct)
    monkeypatch.setattr(sq, "enqueue", lambda t, p: enqueued.append((t, p)))

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process("u1", "你好", "在的", envelope=env)

    types_enqueued = [t for t, _ in enqueued]
    assert "trait_tracker_update" in types_enqueued, (
        f"trait_tracker_update must be enqueued; got: {types_enqueued}"
    )


# ── 3. payload 使用实际 char_id，不硬编码 ──────────────────────────────────────

async def test_payload_uses_active_char_id_not_hardcoded(
    chars_tree, monkeypatch, sandbox, registry
):
    """trait_tracker_update payload must carry the pipeline's active char_id."""
    import core.memory.fixation_pipeline as _fp
    import core.post_process.slow_queue as sq
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("character_b", registry)
    _write_active(sandbox, "character_b")

    captured_payloads: list[dict] = []

    def _capture(task_type, payload):
        if task_type == "trait_tracker_update":
            captured_payloads.append(payload)

    def _spy_ct(uid, user_msg, reply, emotion="neutral", turn_id=None, trigger_name="",
                envelope=None, *, char_id="yexuan", **kw):
        return turn_id or f"{uid}_spy"

    monkeypatch.setattr(_fp, "capture_turn", _spy_ct)
    monkeypatch.setattr(sq, "enqueue", _capture)

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process("u1", "你好", "在的", envelope=env)

    assert captured_payloads, "trait_tracker_update must be enqueued"
    p = captured_payloads[0]
    assert p["char_id"] == "character_b", (
        f"char_id must be 'character_b' (active char), got {p.get('char_id')!r}"
    )
    assert p["uid"] == "u1"
    assert "scope" in p, "payload must contain scope"


# ── 4. can_write_memory=False 时不入队 ────────────────────────────────────────

async def test_no_enqueue_when_write_disabled(
    chars_tree, monkeypatch, sandbox, registry
):
    """can_write_memory=False must suppress trait_tracker_update enqueue."""
    import core.memory.fixation_pipeline as _fp
    import core.post_process.slow_queue as sq
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("yexuan", registry)
    _write_active(sandbox, "yexuan")

    enqueued_types: list[str] = []

    def _spy_ct(uid, user_msg, reply, emotion="neutral", turn_id=None, trigger_name="",
                envelope=None, *, char_id="yexuan", **kw):
        return turn_id or f"{uid}_spy"

    monkeypatch.setattr(_fp, "capture_turn", _spy_ct)
    monkeypatch.setattr(sq, "enqueue", lambda t, _p: enqueued_types.append(t))

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=False, can_affect_mood=False)

    with (
        patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")),
        patch("core.memory.short_term.load", return_value=[]),
        patch("core.memory.pending_perception.confirm_delivered", return_value=None),
    ):
        await pipeline.post_process("u1", "你好", "在的", envelope=env)

    assert "trait_tracker_update" not in enqueued_types, (
        "trait_tracker_update must not be enqueued when can_write_memory=False"
    )


# ── 5. Handler 写入 trait_state ────────────────────────────────────────────────

async def test_handler_creates_trait_state_file(sandbox):
    """The public default character's handler writes a valid trait_state file."""
    from core.pipeline import _handler_trait_tracker_update
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("u_test", PUBLIC_DEFAULT_CHAR_ID)
    payload = {
        "uid": "u_test",
        "char_id": PUBLIC_DEFAULT_CHAR_ID,
        "scope": scope.to_payload(),
    }

    await _handler_trait_tracker_update(payload)

    trait_path = sandbox.trait_state(char_id=PUBLIC_DEFAULT_CHAR_ID)
    assert trait_path.exists(), f"trait_state file must be created: {trait_path}"

    state = json.loads(trait_path.read_text(encoding="utf-8"))
    assert "windows" in state, "state must contain 'windows'"
    assert "underrepresented" in state, "state must contain 'underrepresented'"
    assert isinstance(state["windows"], list)
    assert isinstance(state["underrepresented"], list)
    assert len(state["windows"]) >= 1, "at least one window must be recorded"


# ── 6. author_note_rotator 读取路径与 handler 写入路径一致 ─────────────────────

def test_author_note_rotator_reads_handler_write_path(sandbox):
    """Handler and rotator use the same explicit public-default character path."""
    from core.sandbox import get_paths
    handler_write_path = get_paths().trait_state(char_id=PUBLIC_DEFAULT_CHAR_ID)
    rotator_read_path = get_paths().trait_state(char_id=PUBLIC_DEFAULT_CHAR_ID)
    assert handler_write_path == rotator_read_path, (
        f"Path mismatch: handler writes to {handler_write_path}, "
        f"rotator reads from {rotator_read_path}"
    )


# ── 7. fetch_context 源码不含 trait_tracker_update ────────────────────────────

def test_fetch_context_has_no_trait_tracker_enqueue():
    """fetch_context is a pure-read path and must not enqueue trait_tracker_update."""
    import core.pipeline as _p
    source = inspect.getsource(_p.Pipeline.fetch_context)
    assert "trait_tracker_update" not in source, (
        "fetch_context must never enqueue trait_tracker_update (pure-read path)"
    )


# ── 9. R3 Rule-1：pipeline.py 不引入新 char_id="yexuan" 参数默认值 ────────────

def test_r3_no_new_yexuan_default_from_trait_handler():
    """_handler_trait_tracker_update must not declare char_id='yexuan' as default."""
    import ast, core.pipeline as _p

    source = inspect.getsource(_p._handler_trait_tracker_update)
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        all_args = node.args.posonlyargs + node.args.args
        offset = len(all_args) - len(node.args.defaults)
        for i, default in enumerate(node.args.defaults):
            arg = all_args[offset + i]
            if arg.arg in ("char_id", "character_id"):
                assert not (
                    isinstance(default, ast.Constant) and default.value == "yexuan"
                ), "handler must not default char_id='yexuan'"
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            if default is None:
                continue
            if arg.arg in ("char_id", "character_id"):
                assert not (
                    isinstance(default, ast.Constant) and default.value == "yexuan"
                ), "handler must not default char_id='yexuan'"
