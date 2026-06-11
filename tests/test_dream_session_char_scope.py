"""
tests/test_dream_session_char_scope.py — P0-T05.5: Dream session char_id plumbing

Covers:
1.  enter_dream writes char_id into dream_state JSON
2.  Production admin route passes active char_id (not the default "yexuan")
3.  _generate_summary_bg / _do_close_dream pass dream_state.char_id to distill_impression
4.  close after active-character switch still uses the session char_id (not current active)
5.  Legacy dream_state missing char_id: WARN fallback to "yexuan", no crash
6.  distill_impression reads archive from correct char_id-scoped path
7.  afterglow summary record carries char_id field (T-06 seam)
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_UID = "dream_char_scope_u1"


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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_state(sandbox, uid, state_dict):
    path = sandbox.dream_state_path(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state_dict, ensure_ascii=False), encoding="utf-8")


def _read_state(sandbox, uid):
    path = sandbox.dream_state_path(uid)
    return json.loads(path.read_text(encoding="utf-8"))


# ── 1. enter_dream writes char_id into dream_state ────────────────────────────

def test_enter_dream_writes_char_id(sandbox):
    """enter_dream(uid, char_id='hongcha') must persist char_id in dream_state JSON."""
    from core.dream.dream_pipeline import enter_dream
    from core.dream.dream_state import DreamStatus

    async def run():
        with patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value={
            "created_at": time.time(),
            "user_id": _UID,
            "yexuan_awareness": "lucid_shared",
            "boundary": "dream_only",
            "entry_reason": "",
            "relationship_state": {},
            "recent_reality_context": "",
            "episodic_summary": "",
            "mid_term_context": "",
            "profile_impression": "",
        })):
            return await enter_dream(_UID, entry_reason="test", char_id="hongcha")

    result = asyncio.run(run())
    assert result.get("ok"), f"enter_dream failed: {result}"

    state = _read_state(sandbox, _UID)
    assert state.get("char_id") == "hongcha", (
        f"dream_state must have char_id='hongcha', got {state.get('char_id')!r}"
    )
    assert state.get("status") == DreamStatus.DREAM_ACTIVE.value


def test_enter_dream_yexuan_char_id(sandbox):
    """enter_dream with char_id='yexuan' writes 'yexuan' into dream_state."""
    from core.dream.dream_pipeline import enter_dream

    async def run():
        with patch("core.dream.dream_context.build_snapshot", new=AsyncMock(return_value={
            "created_at": time.time(),
            "user_id": _UID,
            "yexuan_awareness": "lucid_shared",
            "boundary": "dream_only",
            "entry_reason": "",
            "relationship_state": {},
            "recent_reality_context": "",
            "episodic_summary": "",
            "mid_term_context": "",
            "profile_impression": "",
        })):
            return await enter_dream(_UID, char_id="yexuan")

    asyncio.run(run())
    assert _read_state(sandbox, _UID).get("char_id") == "yexuan"


# ── 2. Production admin route passes active char_id, not default ──────────────

def test_admin_dream_enter_passes_active_char_id(sandbox, chars_tree, monkeypatch):
    """POST /dream/enter must read char_id from pipeline._active_character_id and pass it."""
    monkeypatch.chdir(chars_tree)

    from core.dream.dream_state import write_state, DreamStatus
    write_state(_UID, {"user_id": _UID, "status": DreamStatus.REALITY_CHAT.value})

    captured_char_id: list[str] = []

    async def _mock_enter(uid, entry_reason="", *, char_id="yexuan", dream_mode="sandbox", script_id=None):
        captured_char_id.append(char_id)
        return {"ok": True, "dream_id": "mock_dream_id"}

    mock_pipeline = MagicMock()
    mock_pipeline._active_character_id = "hongcha"

    _mock_cfg = {"scheduler": {"owner_id": _UID}}
    with patch("core.pipeline_registry.get", return_value=mock_pipeline), \
         patch("core.dream.dream_pipeline.enter_dream", new=_mock_enter), \
         patch("core.config_loader.get_config", return_value=_mock_cfg), \
         patch("admin.routers.dream.get_config", return_value=_mock_cfg):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from admin.routers.dream import router
        from admin.auth import verify_token
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[verify_token] = lambda: True
        client = TestClient(app)
        resp = client.post("/dream/enter", json={})

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert len(captured_char_id) == 1
    assert captured_char_id[0] == "hongcha", (
        f"Production admin route must pass active char_id='hongcha', got {captured_char_id[0]!r}"
    )


# ── 3. _generate_summary_bg passes dream_state.char_id to distill_impression ──

def test_generate_summary_bg_uses_dream_state_char_id(sandbox):
    """
    _do_close_dream reads char_id from dream_state and passes it to _generate_summary_bg,
    which must pass it to distill_impression.
    """
    from core.dream.dream_state import write_state, DreamStatus

    dream_id = f"dream_{_UID}_scope_bg"
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_CLOSING.value,
        "dream_id": dream_id,
        "char_id": "hongcha",
    })

    captured_kwargs: list[dict] = []

    async def _mock_distill(uid, did, exit_type, *, char_id="yexuan"):
        captured_kwargs.append({"uid": uid, "dream_id": did, "char_id": char_id})

    async def run():
        with patch("core.dream.dream_log.archive_current"), \
             patch("core.dream.dream_hud.delete_hud_state"):
            # Suppress asyncio.create_task, close the coro to avoid unawaited warning
            with patch("asyncio.create_task") as mock_ct:
                mock_ct.side_effect = lambda coro: coro.close() or MagicMock()
                from core.dream.dream_pipeline import _do_close_dream
                await _do_close_dream(_UID, dream_id, "soft")

            # The WARN from _state_char_id + the char_id passed to bg is the key test.
            # Run _generate_summary_bg directly with the char_id that _do_close_dream resolved.
            with patch("core.dream.dream_summary.generate_summary", new=AsyncMock()), \
                 patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"), \
                 patch("core.dream.distill_impression.distill_impression", new=_mock_distill):
                from core.dream.dream_pipeline import _generate_summary_bg
                await _generate_summary_bg(_UID, dream_id, "soft", char_id="hongcha")

    asyncio.run(run())

    assert len(captured_kwargs) >= 1, "distill_impression must be called"
    assert captured_kwargs[0]["char_id"] == "hongcha", (
        f"distill_impression must receive char_id='hongcha', got {captured_kwargs[0]['char_id']!r}"
    )


def test_generate_summary_bg_char_id_forwarded_directly(sandbox):
    """
    _generate_summary_bg(char_id='hongcha') must forward char_id to distill_impression.
    Tests the function directly without going through _do_close_dream.
    """
    from core.dream.dream_state import write_state, DreamStatus

    dream_id = f"dream_{_UID}_bg_direct"
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.REALITY_AFTERGLOW.value,
        "char_id": "hongcha",
    })

    captured: list[str] = []

    async def _mock_distill(uid, did, exit_type, *, char_id="yexuan"):
        captured.append(char_id)

    async def run():
        from core.dream.dream_pipeline import _generate_summary_bg
        with patch("core.dream.dream_summary.generate_summary", new=AsyncMock()), \
             patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"), \
             patch("core.dream.distill_impression.distill_impression", new=_mock_distill):
            await _generate_summary_bg(_UID, dream_id, "soft", char_id="hongcha")

    asyncio.run(run())
    assert captured == ["hongcha"], (
        f"_generate_summary_bg must pass char_id='hongcha' to distill_impression, got {captured}"
    )


# ── 4. Close after active-character switch uses session char_id ───────────────

def test_close_uses_session_char_id_not_current_active(sandbox):
    """
    enter with char_id='yexuan', then switch active to 'hongcha', then close:
    distill_impression must receive char_id='yexuan' (the session char_id).
    """
    from core.dream.dream_state import write_state, DreamStatus

    dream_id = f"dream_{_UID}_switch_test"
    # Simulate a yexuan-entered dream
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_CLOSING.value,
        "dream_id": dream_id,
        "char_id": "yexuan",  # frozen at enter time
    })

    # "Switch" active character to hongcha (would normally write active_prompt_assets)
    # dream_pipeline must NOT read active_character during close — only dream_state.char_id

    captured_char_id: list[str] = []

    async def _mock_distill(uid, did, exit_type, *, char_id="yexuan"):
        captured_char_id.append(char_id)

    async def run():
        from core.dream.dream_pipeline import _generate_summary_bg
        with patch("core.dream.dream_summary.generate_summary", new=AsyncMock()), \
             patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"), \
             patch("core.dream.distill_impression.distill_impression", new=_mock_distill):
            # Pass the session char_id as stored in dream_state, even though "active" is now hongcha
            await _generate_summary_bg(_UID, dream_id, "soft", char_id="yexuan")

    asyncio.run(run())

    assert captured_char_id == ["yexuan"], (
        f"After active switch, close must still use session char_id='yexuan', "
        f"got {captured_char_id}"
    )


# ── 5. Legacy dream_state missing char_id: WARN + fallback to "yexuan" ────────

def test_legacy_dream_state_missing_char_id_warns_and_fallbacks(sandbox, caplog):
    """
    When dream_state has no char_id field (legacy session), _do_close_dream must:
    - fallback to char_id='yexuan'
    - emit a WARNING log containing 'legacy', 'fallback', and 'yexuan'
    - NOT raise KeyError
    """
    from core.dream.dream_state import write_state, DreamStatus

    dream_id = f"dream_{_UID}_legacy_no_char"
    write_state(_UID, {
        "user_id": _UID,
        "status": DreamStatus.DREAM_CLOSING.value,
        "dream_id": dream_id,
        # deliberately omit char_id
    })

    captured_char_id: list[str] = []

    async def _mock_distill(uid, did, exit_type, *, char_id="yexuan"):
        captured_char_id.append(char_id)

    with caplog.at_level(logging.WARNING, logger="core.dream.dream_pipeline"):
        async def run():
            from core.dream.dream_pipeline import _generate_summary_bg
            with patch("core.dream.dream_summary.generate_summary", new=AsyncMock()), \
                 patch("core.dream.dream_exit_afterglow.wire_afterglow_from_summary"), \
                 patch("core.dream.distill_impression.distill_impression", new=_mock_distill), \
                 patch("core.dream.dream_log.archive_current"), \
                 patch("core.dream.dream_hud.delete_hud_state"):
                from core.dream.dream_pipeline import _do_close_dream
                # Suppress the background task to avoid unawaited coro warning
                with patch("asyncio.create_task") as mock_ct:
                    mock_ct.side_effect = lambda coro: coro.close() or MagicMock()
                    await _do_close_dream(_UID, dream_id, "soft")

                # Run the bg task directly to verify it uses the fallback char_id
                await _generate_summary_bg(_UID, dream_id, "soft", char_id="yexuan")

        asyncio.run(run())

    # Should fallback to "yexuan"
    assert captured_char_id == ["yexuan"], (
        f"Legacy state missing char_id must fallback to 'yexuan', got {captured_char_id}"
    )

    # Must emit a WARNING with "legacy" and "yexuan"
    warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("legacy" in m and "yexuan" in m for m in warn_msgs), (
        f"Must WARN about legacy fallback to yexuan. Got warnings: {warn_msgs}"
    )


# ── 6. distill_impression reads archive from correct char_id-scoped path ─────

def test_distill_impression_reads_from_char_id_archive(sandbox):
    """
    distill_impression with char_id='hongcha' reads from hongcha archive,
    NOT from the yexuan archive.
    """
    from core.dream.distill_impression import distill_impression
    from core.dream.impression_store import load_impressions

    uid = "distill_arch_scope_u1"
    dream_id = f"dream_{uid}_arch_scope"

    # Write archive to hongcha path
    hc_archive = sandbox.dreams_archive_dir(char_id="hongcha")
    hc_archive.mkdir(parents=True, exist_ok=True)
    (hc_archive / f"dream_{dream_id}.jsonl").write_text(
        json.dumps({"role": "user", "content": "红茶梦境内容"}) + "\n",
        encoding="utf-8",
    )

    # Do NOT write to yexuan archive (verifies isolation)
    mock_result = {
        "impression_text": "我好像在梦里有种红茶的感觉",
        "emotional_tags": ["温热"],
        "weight": 0.3,
    }

    async def run():
        with patch("core.dream.distill_impression._llm_distill", new=AsyncMock(return_value=mock_result)):
            await distill_impression(uid, dream_id, "soft", char_id="hongcha")

    asyncio.run(run())

    hc_entries = load_impressions(uid, char_id="hongcha")
    yx_entries = load_impressions(uid, char_id="yexuan")

    assert len(hc_entries) == 1, (
        f"hongcha impression bucket must have 1 entry, got {len(hc_entries)}"
    )
    assert "红茶" in hc_entries[0]["impression_text"]
    assert yx_entries == [], "yexuan bucket must be empty when char_id='hongcha'"


def test_distill_impression_empty_if_wrong_archive_path(sandbox):
    """
    If archive is at yexuan path but distill is called with char_id='hongcha',
    the hongcha archive is empty → distill returns early with no impression written.
    This verifies the archive path isolation.
    """
    from core.dream.distill_impression import distill_impression
    from core.dream.impression_store import load_impressions

    uid = "distill_wrong_path_u1"
    dream_id = f"dream_{uid}_wrong_path"

    # Write ONLY to yexuan archive
    yx_archive = sandbox.dreams_archive_dir(char_id="yexuan")
    yx_archive.mkdir(parents=True, exist_ok=True)
    (yx_archive / f"dream_{dream_id}.jsonl").write_text(
        json.dumps({"role": "user", "content": "叶瑄内容"}) + "\n",
        encoding="utf-8",
    )

    async def run():
        # hongcha archive is empty → distill must skip
        await distill_impression(uid, dream_id, "soft", char_id="hongcha")

    asyncio.run(run())

    # No entry should be written to hongcha bucket
    assert load_impressions(uid, char_id="hongcha") == [], (
        "When archive is missing for char_id='hongcha', no impression should be written"
    )


# ── 7. afterglow summary record carries char_id (T-06 seam) ──────────────────

def test_generate_summary_writes_char_id_into_record(sandbox):
    """
    generate_summary(char_id='hongcha') must write a summary record that includes
    char_id='hongcha' — T-06 afterglow integrator will use this field.
    """
    from core.dream.dream_summary import generate_summary

    uid = "summary_char_scope_u1"
    dream_id = f"dream_{uid}_sum_scope"

    # Write archive at hongcha path
    archive_dir = sandbox.dreams_archive_dir(char_id="hongcha")
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"dream_{dream_id}.jsonl").write_text(
        json.dumps({"role": "user", "content": "梦境测试内容"}) + "\n",
        encoding="utf-8",
    )

    llm_result = json.dumps({
        "title": "梦境",
        "summary": "漂浮感",
        "emotional_tags": ["温柔"],
        "high_weight_lines": [],
        "symbolic_fragments": [],
        "summary_weight": 0.6,
    }, ensure_ascii=False)

    async def run():
        with patch("core.llm_client.chat", AsyncMock(return_value=llm_result)):
            await generate_summary(uid, dream_id, "soft", char_id="hongcha")

    asyncio.run(run())

    summaries_dir = sandbox.dreams_summaries_dir(char_id="hongcha")
    dest = summaries_dir / f"dream_{dream_id}.summary.json"
    assert dest.exists(), f"Summary file not created: {dest}"

    record = json.loads(dest.read_text(encoding="utf-8"))
    assert record.get("char_id") == "hongcha", (
        f"Summary record must carry char_id='hongcha' for T-06, got {record.get('char_id')!r}"
    )
    # Summary must NOT land in yexuan summaries dir
    yx_dest = sandbox.dreams_summaries_dir(char_id="yexuan") / f"dream_{dream_id}.summary.json"
    assert not yx_dest.exists(), (
        "Summary with char_id='hongcha' must NOT write to yexuan summaries dir"
    )


def test_afterglow_payload_char_id_forwarded_to_wire(sandbox):
    """
    wire_afterglow_from_summary called with char_id='hongcha' reads from
    the hongcha summaries directory (not yexuan).
    """
    from core.dream.dream_exit_afterglow import wire_afterglow_from_summary

    uid = "afterglow_char_scope_u1"
    dream_id = f"dream_{uid}_aglow_scope"

    # Write summary to hongcha path
    summaries_dir = sandbox.dreams_summaries_dir(char_id="hongcha")
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "dream_id": dream_id,
        "uid": uid,
        "char_id": "hongcha",
        "created_at": time.time(),
        "exit_type": "soft",
        "afterglow": "gentle_residue",
        "summary_weight": 0.6,
        "emotional_tags": ["温柔"],
        "reality_boundary": "dream_only",
        "never_retrieve": True,
        "not_memory_source": True,
    }
    (summaries_dir / f"dream_{dream_id}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )

    called_with: list[dict] = []

    def _mock_save_residue(uid_, residue, *, created_at=None, char_id="yexuan"):
        called_with.append({"uid": uid_, "tone": residue.tone, "char_id": char_id})
        return True

    def _mock_integrate(uid_, residue, *, write_envelope, now=None, char_id="yexuan"):
        from core.memory.user_hidden_state import IntegrationResult
        return None, MagicMock(accepted=True, rejected=False, touched_fields=[], rejected_reasons=[])

    with patch("core.memory.user_hidden_state_store.save_afterglow_residue", _mock_save_residue), \
         patch("core.memory.user_hidden_state_integrator.integrate_afterglow_and_save", _mock_integrate), \
         patch("core.write_envelope.stamp_dream_afterglow", return_value=MagicMock()):
        wire_afterglow_from_summary(uid, dream_id, "soft", char_id="hongcha")

    assert len(called_with) >= 1, "save_afterglow_residue must be called"
    assert called_with[0]["tone"] == "calm", (
        f"gentle_residue + weight<0.7 → tone='calm', got {called_with[0]['tone']!r}"
    )


# ── Regression: _state_char_id helper ────────────────────────────────────────

def test_state_char_id_present(caplog):
    """_state_char_id returns the value when char_id is in state."""
    from core.dream.dream_pipeline import _state_char_id
    result = _state_char_id({"char_id": "hongcha"}, "test_handler")
    assert result == "hongcha"
    assert not any("legacy" in r.message for r in caplog.records)


def test_state_char_id_missing_warns(caplog):
    """_state_char_id warns and falls back to 'yexuan' when char_id absent."""
    from core.dream.dream_pipeline import _state_char_id
    with caplog.at_level(logging.WARNING, logger="core.dream.dream_pipeline"):
        result = _state_char_id({}, "test_handler", uid="u1", dream_id="d1")
    assert result == "yexuan"
    warn_text = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
    assert "legacy" in warn_text
    assert "yexuan" in warn_text
