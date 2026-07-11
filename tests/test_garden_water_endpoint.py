"""
tests/test_garden_water_endpoint.py — W5: POST /garden/water

管理面板此前只有 GET /garden/state（只读），浇水靠聊天触发 LLM probe，手机/桌面
花园页只能看不能点。这组测试覆盖新增的 POST /garden/water，复用
core.garden.manager.force_water 的逻辑（与被动浇水工具 core/tools/garden_tools.py
共用同一底层函数）。
"""

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from core.garden import manager as garden_manager


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


@pytest.mark.asyncio
async def test_water_endpoint_passes_active_char_id(sandbox, character_b_registered):
    """POST /garden/water resolves active_character and forwards it to force_water."""
    _write_active(sandbox, "character_b")

    called = []

    def spy_force(**kw):
        called.append(kw.get("char_id"))
        return {"ok": True, "flower_id": "rose", "stage": "budding", "bloomed": False}

    with patch.object(garden_manager, "force_water", side_effect=spy_force):
        from admin.routers.garden import water_garden_endpoint
        result = await water_garden_endpoint(auth="dummy")

    assert called == ["character_b"]
    assert result == {"ok": True, "flower_id": "rose", "stage": "budding", "bloomed": False}


@pytest.mark.asyncio
async def test_water_endpoint_empty_active_raises_503(sandbox):
    """POST /garden/water with empty active_character must raise 503, not call force_water."""
    _write_active_empty(sandbox)

    called = []
    with patch.object(garden_manager, "force_water", side_effect=lambda **kw: called.append(kw)):
        from admin.routers.garden import water_garden_endpoint
        with pytest.raises(HTTPException) as exc_info:
            await water_garden_endpoint(auth="dummy")

    assert exc_info.value.status_code == 503
    assert called == []


@pytest.mark.asyncio
async def test_water_endpoint_invalid_active_raises_422(sandbox):
    """POST /garden/water with unknown active_character must raise 422, not call force_water."""
    _write_active(sandbox, "ghost_char_xyz")

    called = []
    with patch.object(garden_manager, "force_water", side_effect=lambda **kw: called.append(kw)):
        from admin.routers.garden import water_garden_endpoint
        with pytest.raises(HTTPException) as exc_info:
            await water_garden_endpoint(auth="dummy")

    assert exc_info.value.status_code == 422
    assert called == []


@pytest.mark.asyncio
async def test_water_endpoint_propagates_no_slot_reason(sandbox, character_b_registered):
    """When force_water reports no_slot_for_mood, the endpoint returns that verbatim (no swallow)."""
    _write_active(sandbox, "character_b")

    with patch.object(
        garden_manager, "force_water",
        return_value={"ok": False, "reason": "no_slot_for_mood", "mood": "furious"},
    ):
        from admin.routers.garden import water_garden_endpoint
        result = await water_garden_endpoint(auth="dummy")

    assert result == {"ok": False, "reason": "no_slot_for_mood", "mood": "furious"}
