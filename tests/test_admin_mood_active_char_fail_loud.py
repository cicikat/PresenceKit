"""
tests/test_admin_mood_active_char_fail_loud.py

P1-0F.2: admin/routers/mood.py active char_id fail-loud 验收测试

Covers:
1.  active 缺失时 admin mood route 返回 HTTP 503，不调用 mood_state.load
2.  active 读取失败时返回 HTTP 503，不调用 mood_state.load
3.  active 非法时返回 HTTP 422，不调用 mood_state.load
4.  active=hongcha 时 _active_char_id 返回 "hongcha"
5.  GET /state active=hongcha → mood_state.load 收到 char_id="hongcha"
6.  active=yexuan 时 _active_char_id 返回 "yexuan"（合法路径，非 fallback）
7.  失败路径不 fallback yexuan（empty + invalid 各自验证）
8.  源文件中不再存在 active 失败 fallback "yexuan" 字符串
"""

import json
import pathlib
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from core.memory import mood_state as _mood_state_mod


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_active(sandbox, char_id: str) -> None:
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


def _write_active_empty(sandbox) -> None:
    p = sandbox.active_prompt_assets()
    p.write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


# ── 1. active 缺失 → HTTP 503 ─────────────────────────────────────────────────

def test_mood_empty_active_raises_503(sandbox):
    """_active_char_id with empty active_character must raise HTTP 503."""
    _write_active_empty(sandbox)

    called = []
    with patch.object(_mood_state_mod, "load", side_effect=lambda **kw: called.append(kw)):
        from admin.routers.mood import _active_char_id
        with pytest.raises(HTTPException) as exc_info:
            _active_char_id()

    assert exc_info.value.status_code == 503
    assert called == [], "mood_state.load must not be called when active_character is empty"


# ── 2. active 读取失败 → HTTP 503 ────────────────────────────────────────────

def test_mood_read_failure_raises_503(sandbox, monkeypatch):
    """_active_char_id when read_text raises must return HTTP 503.

    _active_char_id() 实现已抽到 admin/routers/_common.py（CC 任务 24 · 3），
    mood.py 只是重导出，所以要 monkeypatch 的是 _common 模块里的 _get_paths。
    """
    import admin.routers.mood as _mood_router
    import admin.routers._common as _common

    mock_path_obj = type("_P", (), {
        "read_text": lambda *a, **k: (_ for _ in ()).throw(OSError("disk failure")),
    })()

    def _bad_get_paths():
        return type("_G", (), {"active_prompt_assets": lambda self: mock_path_obj})()

    monkeypatch.setattr(_common, "_get_paths", _bad_get_paths)

    called = []
    with patch.object(_mood_state_mod, "load", side_effect=lambda **kw: called.append(kw)):
        with pytest.raises(HTTPException) as exc_info:
            _mood_router._active_char_id()

    assert exc_info.value.status_code == 503
    assert called == [], "mood_state.load must not be called when read fails"


# ── 3. active 非法 → HTTP 422 ────────────────────────────────────────────────

def test_mood_invalid_active_raises_422(sandbox):
    """_active_char_id with unknown char_id must raise HTTP 422."""
    _write_active(sandbox, "ghost_char_xyz")

    called = []
    with patch.object(_mood_state_mod, "load", side_effect=lambda **kw: called.append(kw)):
        from admin.routers.mood import _active_char_id
        with pytest.raises(HTTPException) as exc_info:
            _active_char_id()

    assert exc_info.value.status_code == 422
    assert called == [], "mood_state.load must not be called when active_character is unknown"


# ── 4. active=hongcha → _active_char_id returns "hongcha" ────────────────────

def test_mood_active_hongcha_returns_hongcha(sandbox):
    """_active_char_id with active=hongcha must return 'hongcha'."""
    _write_active(sandbox, "hongcha")

    from admin.routers.mood import _active_char_id
    result = _active_char_id()
    assert result == "hongcha", f"expected 'hongcha', got {result!r}"


# ── 5. GET /state active=hongcha → mood_state.load(char_id="hongcha") ────────

@pytest.mark.asyncio
async def test_mood_get_state_passes_hongcha_to_load(sandbox):
    """GET /state with active=hongcha must call mood_state.load(char_id='hongcha')."""
    _write_active(sandbox, "hongcha")

    captured = []

    def _spy_load(**kw):
        captured.append(kw.get("char_id"))
        return {"current": "neutral", "intensity": 0.0, "previous": "neutral", "updated_at": 0.0}

    with patch.object(_mood_state_mod, "load", side_effect=_spy_load):
        from admin.routers.mood import get_mood_state
        await get_mood_state()

    assert captured == ["hongcha"], f"expected char_id='hongcha', got {captured}"


# ── 6. active=yexuan → _active_char_id returns "yexuan" (valid path, not fallback) ──

def test_mood_active_yexuan_returns_yexuan(sandbox):
    """_active_char_id with active=yexuan must return 'yexuan' (valid active, not hardcoded fallback)."""
    _write_active(sandbox, "yexuan")

    from admin.routers.mood import _active_char_id
    result = _active_char_id()
    assert result == "yexuan", f"expected 'yexuan', got {result!r}"


# ── 7a. empty active → never fallback to yexuan ──────────────────────────────

def test_no_yexuan_fallback_on_empty_active(sandbox):
    """Empty active_character must raise, never call load(char_id='yexuan')."""
    _write_active_empty(sandbox)

    yexuan_calls = []

    def spy_load(**kw):
        if kw.get("char_id") == "yexuan":
            yexuan_calls.append(kw)
        return {}

    with patch.object(_mood_state_mod, "load", side_effect=spy_load):
        from admin.routers.mood import _active_char_id
        with pytest.raises(HTTPException):
            _active_char_id()

    assert yexuan_calls == [], "load must never be called with fallback char_id='yexuan' on empty active"


# ── 7b. invalid active → never fallback to yexuan ────────────────────────────

def test_no_yexuan_fallback_on_invalid_active(sandbox):
    """Invalid active_character must raise 422, never call load(char_id='yexuan')."""
    _write_active(sandbox, "ghost_char_xyz")

    yexuan_calls = []

    def spy_load(**kw):
        if kw.get("char_id") == "yexuan":
            yexuan_calls.append(kw)
        return {}

    with patch.object(_mood_state_mod, "load", side_effect=spy_load):
        from admin.routers.mood import _active_char_id
        with pytest.raises(HTTPException):
            _active_char_id()

    assert yexuan_calls == [], "load must never be called with fallback char_id='yexuan' on invalid active"


# ── 8. 源文件中不含 active 失败 fallback "yexuan" ─────────────────────────────

def test_mood_router_source_no_fallback_yexuan():
    """admin/routers/mood.py must not contain the active-failure fallback to 'yexuan'."""
    src = (
        pathlib.Path(__file__).parent.parent / "admin" / "routers" / "mood.py"
    ).read_text(encoding="utf-8")

    assert '"yexuan"' not in src, (
        'admin/routers/mood.py must not contain a hardcoded "yexuan" fallback string'
    )
