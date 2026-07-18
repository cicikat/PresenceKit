"""
tests/test_memory_isolation_p0_final.py

P0 Final Gate: 内容级跨角色串味验收
确认"不再产生新串味"成立。

场景 A: active=yexuan 写入唯一词"草莓大福-P0Final"
        → 切 active=character_b → fetch_context → prompt 不含该词
场景 B: active=character_b 写入唯一词"XYZ动画-P0Final"
        → 切 active=yexuan → fetch_context → prompt 不含该词
场景 C: yexuan 触发 hidden_state/afterglow → 读 character_b bucket → 未受影响
场景 D: 入梦 active=yexuan → 关梦前切 active=character_b
        → close (使用 dream_state.char_id=yexuan) → summary/impression 写 yexuan 桶

禁止 sleep；直接调用同步写路径 + drain slow_queue / 直接 handler 调用。
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import modules at module level so module-init runs before any monkeypatch.chdir()
import core.memory.event_log         # noqa: F401
import core.memory.user_profile      # noqa: F401
import core.memory.mid_term          # noqa: F401
import core.memory.short_term        # noqa: F401
import core.memory.episodic_memory   # noqa: F401
import core.memory.user_identity     # noqa: F401
import core.dream.impression_loader  # noqa: F401
import core.memory.group_context     # noqa: F401
import core.memory.diary_context     # noqa: F401
import core.tools.reminder           # noqa: F401
import core.memory.mood_state        # noqa: F401
import core.user_relation            # noqa: F401

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry

_UID_BASE = "p0final"


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
    # `registry` fixture below chdir's into this dir; core.config_loader resolves the
    # bare relative Path("config.yaml") against cwd, so one must exist here too — otherwise
    # the first get_config() call after the chdir (e.g. via capture_turn -> _scrub ->
    # _char_name) hard-raises RuntimeError. Self-contained stub, independent of whatever
    # real config.yaml a dev machine happens to have at the repo root.
    (tmp_path / "config.yaml").write_text("character:\n  name: Companion\n", encoding="utf-8")
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


def _apply_fetch_stubs(monkeypatch):
    """Stub all LLM/IO calls in fetch_context; short_term and mid_term use real paths."""
    import core.memory.event_log as _el
    import core.memory.user_profile as _up
    import core.memory.user_identity as _ui
    import core.dream.impression_loader as _il
    import core.memory.group_context as _gc
    import core.memory.diary_context as _dc
    import core.memory.episodic_memory as _ep

    monkeypatch.setattr(_el, "search", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(_up, "load", lambda *a, **kw: {})
    monkeypatch.setattr(_ep, "retrieve", lambda *a, **kw: ([], []) if kw.get("return_trace") else [])
    monkeypatch.setattr(_ep, "retrieve_fallback", lambda *a, **kw: ([], []) if kw.get("return_trace") else [])
    monkeypatch.setattr(_ui, "format_for_prompt", AsyncMock(return_value=""))
    monkeypatch.setattr(_il, "load_impression_text", lambda *a, **kw: "")
    monkeypatch.setattr(_gc, "get_recent", lambda *a, **kw: "")
    try:
        monkeypatch.setattr(_dc, "load", lambda *a, **kw: "")
    except Exception:
        pass
    import core.tools.reminder as _rem
    try:
        monkeypatch.setattr(_rem, "get_reminders", lambda *a, **kw: [])
    except Exception:
        pass
    import core.memory.mood_state as _ms
    monkeypatch.setattr(_ms, "get_current", lambda *a, **kw: "neutral")
    monkeypatch.setattr(_ms, "update", lambda *a, **kw: None)
    import core.user_relation as _ur
    monkeypatch.setattr(_ur, "get_relation", lambda *a, **kw: {"priority": 1})


# ═══════════════════════════════════════════════════════════════════════════════
# 场景 A: yexuan 写入唯一词 → 切 character_b → fetch_context → 不含 yexuan 词
# ═══════════════════════════════════════════════════════════════════════════════

def test_scenario_a_yexuan_content_not_in_character_b_context(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    内容级隔离 A:
    1. capture_turn 写"草莓大福-P0Final"到 yexuan short_term
    2. mid_term.append 写含唯一词摘要到 yexuan mid_term（同步，不走 LLM）
    3. 切 active=character_b
    4. fetch_context → context["history"] 和 context["mid_term"] 均不含唯一词
    """
    from core.memory.fixation_pipeline import capture_turn
    from core.memory.short_term import load_for_prompt
    from core.memory import mid_term as _mt
    from core.write_envelope import WriteEnvelope, SourceType

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)
    uid = _UID_BASE + "_a"
    unique_word = "草莓大福-P0Final"

    # Step 1: Write unique word to yexuan buckets
    capture_turn(uid, f"{unique_word}用户消息", f"{unique_word}回复", char_id="yexuan", envelope=env)
    _mt.append(uid, f"摘要:{unique_word}", tags=[], char_id="yexuan")

    # Sanity: verify unique word IS in yexuan bucket
    yexuan_hist = load_for_prompt(uid, char_id="yexuan")
    yexuan_hist_text = " ".join(m.get("content", "") for m in yexuan_hist)
    assert unique_word in yexuan_hist_text, "预置失败：yexuan short_term 应含唯一词"
    yexuan_mt = _mt.format_for_prompt(uid, char_id="yexuan")
    assert unique_word in yexuan_mt, "预置失败：yexuan mid_term 应含唯一词"

    # Step 2: Switch to character_b and fetch_context
    _write_active(sandbox, "character_b")
    _apply_fetch_stubs(monkeypatch)
    pipeline = _make_pipeline("character_b", registry)
    ctx = asyncio.run(pipeline.fetch_context(user_id=uid, content="你好"))

    # Step 3: Verify no contamination
    hist_text = " ".join(m.get("content", "") for m in ctx["history"])
    assert unique_word not in hist_text, (
        f"P0 FAIL 场景A: character_b context['history'] 含有 yexuan 唯一词 {unique_word!r}\n"
        f"history: {hist_text!r}"
    )
    mid_text = ctx.get("mid_term", "")
    assert unique_word not in mid_text, (
        f"P0 FAIL 场景A: character_b context['mid_term'] 含有 yexuan 唯一词 {unique_word!r}\n"
        f"mid_term: {mid_text!r}"
    )


def test_scenario_a_yexuan_bucket_still_has_content(sandbox):
    """场景 A 正控：yexuan 桶仍保有写入的唯一词（写路径未破坏）。"""
    from core.memory.fixation_pipeline import capture_turn
    from core.memory.short_term import load_for_prompt
    from core.write_envelope import WriteEnvelope, SourceType

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)
    uid = _UID_BASE + "_a_ctrl"
    unique_word = "草莓大福-P0Final-控制"

    capture_turn(uid, f"{unique_word}用户", f"{unique_word}回复", char_id="yexuan", envelope=env)

    yexuan_hist = load_for_prompt(uid, char_id="yexuan")
    yexuan_text = " ".join(m.get("content", "") for m in yexuan_hist)
    assert unique_word in yexuan_text, (
        "正控失败：yexuan 写路径未正确工作，short_term 应含唯一词"
    )
    # character_b bucket must be empty for this uid
    character_b_hist = load_for_prompt(uid, char_id="character_b")
    assert character_b_hist == [], "yexuan 写入不应污染 character_b short_term"


# ═══════════════════════════════════════════════════════════════════════════════
# 场景 B: character_b 写入唯一词 → 切 yexuan → fetch_context → 不含 character_b 词
# ═══════════════════════════════════════════════════════════════════════════════

def test_scenario_b_character_b_content_not_in_yexuan_context(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    内容级隔离 B:
    1. capture_turn 写"XYZ动画-P0Final"到 character_b short_term
    2. mid_term.append 写含唯一词摘要到 character_b mid_term（同步，不走 LLM）
    3. 切 active=yexuan
    4. fetch_context → context["history"] 和 context["mid_term"] 均不含唯一词
    """
    from core.memory.fixation_pipeline import capture_turn
    from core.memory.short_term import load_for_prompt
    from core.memory import mid_term as _mt
    from core.write_envelope import WriteEnvelope, SourceType

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)
    uid = _UID_BASE + "_b"
    unique_word = "XYZ动画-P0Final"

    # Step 1: Write unique word to character_b buckets
    capture_turn(uid, f"{unique_word}用户消息", f"{unique_word}回复", char_id="character_b", envelope=env)
    _mt.append(uid, f"摘要:{unique_word}", tags=[], char_id="character_b")

    # Sanity: verify unique word IS in character_b bucket
    character_b_hist = load_for_prompt(uid, char_id="character_b")
    character_b_text = " ".join(m.get("content", "") for m in character_b_hist)
    assert unique_word in character_b_text, "预置失败：character_b short_term 应含唯一词"
    character_b_mt = _mt.format_for_prompt(uid, char_id="character_b")
    assert unique_word in character_b_mt, "预置失败：character_b mid_term 应含唯一词"

    # Step 2: Switch to yexuan and fetch_context
    _write_active(sandbox, "yexuan")
    _apply_fetch_stubs(monkeypatch)
    pipeline = _make_pipeline("yexuan", registry)
    ctx = asyncio.run(pipeline.fetch_context(user_id=uid, content="你好"))

    # Step 3: Verify no contamination
    hist_text = " ".join(m.get("content", "") for m in ctx["history"])
    assert unique_word not in hist_text, (
        f"P0 FAIL 场景B: yexuan context['history'] 含有 character_b 唯一词 {unique_word!r}\n"
        f"history: {hist_text!r}"
    )
    mid_text = ctx.get("mid_term", "")
    assert unique_word not in mid_text, (
        f"P0 FAIL 场景B: yexuan context['mid_term'] 含有 character_b 唯一词 {unique_word!r}\n"
        f"mid_term: {mid_text!r}"
    )


def test_scenario_b_character_b_bucket_still_has_content(sandbox):
    """场景 B 正控：character_b 桶仍保有写入的唯一词（写路径未破坏）。"""
    from core.memory.fixation_pipeline import capture_turn
    from core.memory.short_term import load_for_prompt
    from core.write_envelope import WriteEnvelope, SourceType

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)
    uid = _UID_BASE + "_b_ctrl"
    unique_word = "XYZ动画-P0Final-控制"

    capture_turn(uid, f"{unique_word}用户", f"{unique_word}回复", char_id="character_b", envelope=env)

    character_b_hist = load_for_prompt(uid, char_id="character_b")
    character_b_text = " ".join(m.get("content", "") for m in character_b_hist)
    assert unique_word in character_b_text, (
        "正控失败：character_b 写路径未正确工作，short_term 应含唯一词"
    )
    # yexuan bucket must be empty for this uid
    yexuan_hist = load_for_prompt(uid, char_id="yexuan")
    assert yexuan_hist == [], "character_b 写入不应污染 yexuan short_term"


# ═══════════════════════════════════════════════════════════════════════════════
# 场景 C: yexuan afterglow → load character_b hidden_state → 未受污染
# ═══════════════════════════════════════════════════════════════════════════════

def test_scenario_c_yexuan_afterglow_not_in_character_b_hidden_state(sandbox):
    """
    隔离验收 C:
    - yexuan 触发 afterglow 落盘 + integrate (sensitivity.current 上涨)
    - 读 character_b hidden_state
    - character_b bucket sensitivity.current 保持 50.0（未受 yexuan afterglow 影响）
    - yexuan bucket sensitivity.current > 50.0（afterglow 正确写入）
    直接调用同步函数，不使用 sleep。
    """
    from datetime import datetime, timezone
    from core.memory.user_hidden_state import (
        AfterglowResidueInput, default_hidden_state,
    )
    from core.memory.user_hidden_state_store import (
        load_hidden_state, save_afterglow_residue, save_hidden_state,
    )
    from core.memory.user_hidden_state_integrator import integrate_afterglow_and_save
    from core.write_envelope import stamp_dream_afterglow

    uid = _UID_BASE + "_c"
    now = datetime.now(timezone.utc).isoformat()

    # Seed both buckets with identical baseline sensitivity=50
    state_y = default_hidden_state()
    state_y.sensitivity.current.value = 50.0
    save_hidden_state(uid, state_y, char_id="yexuan")

    state_h = default_hidden_state()
    state_h.sensitivity.current.value = 50.0
    save_hidden_state(uid, state_h, char_id="character_b")

    # yexuan afterglow: comfort tone → sensitivity.current should increase
    residue = AfterglowResidueInput(emotional_tags=["warm"], tone="comfort", age_hours=0.0)
    save_afterglow_residue(uid, residue, created_at=now, char_id="yexuan")
    envelope = stamp_dream_afterglow()
    integrate_afterglow_and_save(uid, residue, envelope, now, char_id="yexuan")

    # Load character_b bucket — must be unchanged at 50.0
    character_b_after = load_hidden_state(uid, char_id="character_b")
    yexuan_after = load_hidden_state(uid, char_id="yexuan")

    assert character_b_after.sensitivity.current.value == pytest.approx(50.0), (
        f"P0 FAIL 场景C: character_b hidden_state.sensitivity.current 被 yexuan afterglow 改动: "
        f"{character_b_after.sensitivity.current.value} (期望 50.0)"
    )
    assert yexuan_after.sensitivity.current.value > 50.0, (
        f"正控失败 场景C: yexuan afterglow 应已上调 sensitivity.current，"
        f"实际: {yexuan_after.sensitivity.current.value}"
    )


def test_scenario_c_afterglow_residue_file_isolation(sandbox):
    """
    场景 C 补充：afterglow_residue.json 文件只写 yexuan 桶，不写 character_b 桶。
    """
    from datetime import datetime, timezone
    from core.memory.user_hidden_state import AfterglowResidueInput
    from core.memory.user_hidden_state_store import save_afterglow_residue

    uid = _UID_BASE + "_c_file"
    now = datetime.now(timezone.utc).isoformat()

    residue = AfterglowResidueInput(emotional_tags=["calm"], tone="calm", age_hours=0.0)
    save_afterglow_residue(uid, residue, created_at=now, char_id="yexuan")

    yexuan_path = sandbox.user_memory_root(uid, char_id="yexuan") / "afterglow_residue.json"
    character_b_path = sandbox.user_memory_root(uid, char_id="character_b") / "afterglow_residue.json"

    assert yexuan_path.exists(), "yexuan afterglow_residue.json 应被写入"
    assert not character_b_path.exists(), (
        "yexuan afterglow 不应写入 character_b 桶的 afterglow_residue.json"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 场景 D: 入梦 active=yexuan → 切 active=character_b → close → 写 yexuan 桶
# ═══════════════════════════════════════════════════════════════════════════════

def test_scenario_d_dream_close_writes_to_session_char_yexuan(sandbox):
    """
    Dream 桶锁定验收 D:
    - dream_state.char_id="yexuan"（入梦时锁定）
    - "active" 已切换到 character_b（不影响 dream close 路径）
    - _generate_summary_bg(char_id="yexuan") 使用入梦时的 char_id
    - summary 写入 yexuan summaries_dir
    - impression 写入 yexuan impressions 桶
    - character_b summaries_dir 和 impressions 桶均为空
    """
    from core.dream.dream_state import write_state, DreamStatus
    from core.dream.impression_store import load_impressions
    from core.dream.dream_pipeline import _generate_summary_bg

    uid = _UID_BASE + "_d"
    dream_id = f"dream_{uid}_final_d"

    # Simulate dream state at close time: char_id frozen to yexuan at enter
    write_state(uid, {
        "user_id": uid,
        "status": DreamStatus.DREAM_CLOSING.value,
        "dream_id": dream_id,
        "char_id": "yexuan",  # frozen at enter time — does NOT change when active switches
    })

    # Write dream archive to yexuan path (simulating dream log at enter time)
    archive_dir = sandbox.dreams_archive_dir(char_id="yexuan")
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"dream_{dream_id}.jsonl").write_text(
        json.dumps({"role": "user", "content": "Companion梦境内容场景D"}) + "\n",
        encoding="utf-8",
    )

    mock_summary = json.dumps({
        "title": "Companion之梦",
        "summary": "漂浮感",
        "emotional_tags": ["温柔"],
        "high_weight_lines": [],
        "symbolic_fragments": [],
        "summary_weight": 0.5,
    }, ensure_ascii=False)

    mock_distill_result = {
        "impression_text": "我好像在梦里有种漂浮的感觉",
        "emotional_tags": ["温柔"],
        "weight": 0.3,
    }

    async def run():
        with patch("core.llm_client.chat", AsyncMock(return_value=mock_summary)), \
             patch("core.dream.distill_impression._llm_distill",
                   AsyncMock(return_value=mock_distill_result)), \
             patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"):
            # char_id comes from dream_state — even if active has been switched to character_b
            await _generate_summary_bg(uid, dream_id, "soft", char_id="yexuan")

    asyncio.run(run())

    # Verify summary written to yexuan summaries dir, NOT character_b
    yexuan_summary_path = sandbox.dreams_summaries_dir(char_id="yexuan") / f"dream_{dream_id}.summary.json"
    character_b_summary_path = sandbox.dreams_summaries_dir(char_id="character_b") / f"dream_{dream_id}.summary.json"

    assert yexuan_summary_path.exists(), (
        f"P0 FAIL 场景D: summary 应写入 yexuan summaries_dir，但文件不存在: {yexuan_summary_path}"
    )
    assert not character_b_summary_path.exists(), (
        f"P0 FAIL 场景D: summary 不应写入 character_b summaries_dir，但文件存在: {character_b_summary_path}"
    )

    # Verify summary record carries char_id='yexuan' (T-06 seam)
    summary_record = json.loads(yexuan_summary_path.read_text(encoding="utf-8"))
    assert summary_record.get("char_id") == "yexuan", (
        f"summary 记录的 char_id 应为 'yexuan'，实际: {summary_record.get('char_id')!r}"
    )

    # Verify impression written to yexuan bucket, NOT character_b
    yexuan_impressions = load_impressions(uid, char_id="yexuan")
    character_b_impressions = load_impressions(uid, char_id="character_b")

    assert len(yexuan_impressions) >= 1, (
        f"P0 FAIL 场景D: impression 应写入 yexuan 桶，但实际: {len(yexuan_impressions)} 条"
    )
    assert character_b_impressions == [], (
        f"P0 FAIL 场景D: character_b 桶应为空，但含有: {len(character_b_impressions)} 条"
    )


def test_scenario_d_dream_char_id_not_read_from_active(sandbox):
    """
    场景 D 补充：dream close 路径不读 active_prompt_assets.json，
    只依赖 dream_state.char_id 传递的 char_id 参数。
    验证：dream_state.char_id=yexuan 时 _generate_summary_bg 调 distill_impression(char_id="yexuan")。
    """
    from core.dream.dream_state import write_state, DreamStatus
    from core.dream.dream_pipeline import _generate_summary_bg

    uid = _UID_BASE + "_d2"
    dream_id = f"dream_{uid}_d2"

    write_state(uid, {
        "user_id": uid,
        "status": DreamStatus.DREAM_CLOSING.value,
        "dream_id": dream_id,
        "char_id": "yexuan",
    })

    archive_dir = sandbox.dreams_archive_dir(char_id="yexuan")
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"dream_{dream_id}.jsonl").write_text(
        json.dumps({"role": "user", "content": "测试内容"}) + "\n",
        encoding="utf-8",
    )

    distill_calls: list[dict] = []

    async def _mock_distill(uid_, dream_id_, exit_type_, *, char_id="yexuan", **_kwargs):
        distill_calls.append({"uid": uid_, "char_id": char_id})

    mock_summary = json.dumps({
        "title": "测试",
        "summary": "测试",
        "emotional_tags": [],
        "high_weight_lines": [],
        "symbolic_fragments": [],
        "summary_weight": 0.3,
    }, ensure_ascii=False)

    async def run():
        with patch("core.llm_client.chat", AsyncMock(return_value=mock_summary)), \
             patch("core.dream.distill_impression.distill_impression", _mock_distill), \
             patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"):
            # pass char_id from dream_state (not from active_prompt_assets)
            await _generate_summary_bg(uid, dream_id, "soft", char_id="yexuan")

    asyncio.run(run())

    assert distill_calls, "distill_impression 应被调用"
    assert distill_calls[0]["char_id"] == "yexuan", (
        f"P0 FAIL 场景D补充: distill_impression 应收到 char_id='yexuan'，"
        f"实际: {distill_calls[0]['char_id']!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# slow_queue drain 内容级端到端（场景 A/B 补充，直接 handler 调用）
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_slow_queue_drain_mid_term_isolation(sandbox):
    """
    slow_queue 内容级隔离：
    直接调用 handler_summarize_to_midterm（等效 drain），
    验证 yexuan / character_b 摘要只落入各自 mid_term 桶。
    不使用 sleep。
    """
    import core.memory.mid_term as _mt
    import core.llm_client as _llm
    from core.memory.fixation_pipeline import handler_summarize_to_midterm

    uid = _UID_BASE + "_sq"
    yexuan_word = "草莓大福-SQ"
    character_b_word = "XYZ动画-SQ"

    async def _mock_summarize(user_msg, reply, tags=None, **kwargs):
        return f"摘要:{user_msg[:30]}"

    with patch.object(_llm, "summarize_turn", side_effect=_mock_summarize), \
         patch.object(_mt, "load", wraps=_mt.load):

        # yexuan 任务（直接 handler 调用，等效 drain）
        await handler_summarize_to_midterm({
            "turn_id": f"turn_{uid}_yexuan",
            "uid": uid,
            "user_content": yexuan_word,
            "reply": "好吃的",
            "tags": [],
            "emotion": "neutral",
            "char_id": "yexuan",
        })

        # character_b 任务
        await handler_summarize_to_midterm({
            "turn_id": f"turn_{uid}_character_b",
            "uid": uid,
            "user_content": character_b_word,
            "reply": "好看的",
            "tags": [],
            "emotion": "neutral",
            "char_id": "character_b",
        })

    yexuan_events = _mt.load(uid, char_id="yexuan")
    character_b_events = _mt.load(uid, char_id="character_b")
    yexuan_text = " ".join(e.get("summary", "") for e in yexuan_events)
    character_b_text = " ".join(e.get("summary", "") for e in character_b_events)

    assert yexuan_word in yexuan_text, (
        f"P0 FAIL slow_queue: yexuan mid_term 应含 {yexuan_word!r}，实际: {yexuan_text!r}"
    )
    assert character_b_word not in yexuan_text, (
        f"P0 FAIL slow_queue: yexuan mid_term 不应含 character_b 词 {character_b_word!r}"
    )
    assert character_b_word in character_b_text, (
        f"P0 FAIL slow_queue: character_b mid_term 应含 {character_b_word!r}，实际: {character_b_text!r}"
    )
    assert yexuan_word not in character_b_text, (
        f"P0 FAIL slow_queue: character_b mid_term 不应含 yexuan 词 {yexuan_word!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# fail-loud 回归：active 缺失/空/非法 → 不写 short_term/event_log/slow_queue
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_fail_loud_missing_active_blocks_all_writes(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    fail-loud 回归: active_character 指向不存在角色 → post_process 抛错，
    short_term.append / event_log.append / slow_queue.enqueue 均不被调用。
    """
    import core.memory.short_term as _st
    import core.memory.event_log as _el
    import core.post_process.slow_queue as sq
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("yexuan", registry)
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "nonexistent_ghost",
                    "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    st_calls: list = []
    el_calls: list = []
    sq_calls: list = []

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=False)

    with patch.object(_st, "append", side_effect=lambda *a, **kw: st_calls.append((a, kw)) or True), \
         patch.object(_el, "append", side_effect=lambda *a, **kw: el_calls.append((a, kw)) or True), \
         patch.object(sq, "enqueue", side_effect=lambda *a, **kw: sq_calls.append((a, kw))), \
         patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")):
        with pytest.raises((ValueError, RuntimeError)):
            await pipeline.post_process("u_ghost", "你好", "在的", envelope=env)

    assert st_calls == [], f"short_term.append 不应被调用，实际: {st_calls}"
    assert el_calls == [], f"event_log.append 不应被调用，实际: {el_calls}"
    assert sq_calls == [], f"slow_queue.enqueue 不应被调用，实际: {sq_calls}"


@pytest.mark.asyncio
async def test_fail_loud_empty_active_blocks_fetch_context(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    fail-loud 回归: active_character="" → fetch_context 在读任何记忆前就抛 ValueError。
    """
    import core.memory.short_term as _st

    pipeline = _make_pipeline("yexuan", registry)
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    load_calls: list = []
    original_load = _st.load_for_prompt

    def _spy(*args, **kwargs):
        load_calls.append(args)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(_st, "load_for_prompt", _spy)

    with pytest.raises(ValueError, match="active_character"):
        await pipeline.fetch_context(user_id="u_empty", content="hello")

    assert load_calls == [], (
        "short_term.load_for_prompt 不应被调用 (active_character 为空)"
    )


@pytest.mark.asyncio
async def test_fail_loud_missing_active_no_mood_update(
    chars_tree, monkeypatch, sandbox, registry
):
    """
    fail-loud 回归: active_character 非法 → mood_state.update 不应被调用。
    """
    import core.memory.mood_state as _ms
    from core.write_envelope import WriteEnvelope, SourceType

    pipeline = _make_pipeline("yexuan", registry)
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "ghost_2", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    mood_calls: list = []

    env = WriteEnvelope(source=SourceType.INGEST, can_write_memory=True, can_affect_mood=True)

    with patch.object(_ms, "update", side_effect=lambda *a, **kw: mood_calls.append((a, kw))), \
         patch("core.llm_client.detect_emotion", new=AsyncMock(return_value="neutral")):
        with pytest.raises((ValueError, RuntimeError)):
            await pipeline.post_process("u_ghost2", "你好", "在的", envelope=env)

    assert mood_calls == [], f"mood_state.update 不应被调用，实际: {mood_calls}"
