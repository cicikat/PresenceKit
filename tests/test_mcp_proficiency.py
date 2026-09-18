from __future__ import annotations
import pytest

def _cfg(level_tools=None):
    return {"mcp_proficiency":{"art":{"domain":"drawing","tiers":{1:["basic"],3:["basic","inpaint"],5:["*"]}}}}

def test_organ_server_unchanged(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config",lambda:_cfg())
    from core.growth.mcp_proficiency import is_tool_allowed
    assert is_tool_allowed("mcp__weather__forecast",char_id="c")

def test_tier_visibility(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config",lambda:_cfg())
    from core.growth import mcp_proficiency as mp
    monkeypatch.setattr("core.growth.interest_state.highest_level_for_domain",lambda c,d:None)
    assert not mp.is_tool_allowed("mcp__art__basic",char_id="c")
    monkeypatch.setattr("core.growth.interest_state.highest_level_for_domain",lambda c,d:1)
    assert mp.is_tool_allowed("mcp__art__basic",char_id="c") and not mp.is_tool_allowed("mcp__art__inpaint",char_id="c")
    monkeypatch.setattr("core.growth.interest_state.highest_level_for_domain",lambda c,d:3)
    assert mp.is_tool_allowed("mcp__art__inpaint",char_id="c")
    monkeypatch.setattr("core.growth.interest_state.highest_level_for_domain",lambda c,d:5)
    assert mp.is_tool_allowed("mcp__art__anything",char_id="c")

@pytest.mark.asyncio
async def test_execute_defensive_gate(monkeypatch):
    from core import tool_dispatcher as td
    monkeypatch.setitem(td._TOOL_REGISTRY,"mcp__art__inpaint",{"func":lambda:None,"description":"x","dangerous":False,"category":"mcp","parameters":{}})
    monkeypatch.setattr("core.growth.mcp_proficiency.is_tool_allowed",lambda *a,**k:False)
    class S: WAITING_CONFIRM="w"; status="x"
    result=await td.execute_structured("mcp__art__inpaint",{},"u","u",False,S(),origin="assistant_loop",char_id="c")
    assert result.result=="这项操作现在还做不了。" and result.confirmation_request is None
