from __future__ import annotations

import json
from pathlib import Path


def test_assistant_example_declares_human_direct_tool_loop_profile():
    card = json.loads(Path("bundled/examples/assistant.example.json").read_text(encoding="utf-8"))
    ext = card["presence_ext"]

    assert ext["tool_loop"] == "on"
    assert ext["model_routing"] == "claude-main"
    assert ext["tool_categories"] == ["info", "desktop", "memory", "fs"]
    assert ext["proactive"] == "off"
    assert {"0_jailbreak", "2_jailbreak", "11_jailbreak", "11_author_note"} <= set(
        ext["disabled_layers"]
    )
    # 事实边界与系统身份是 ALWAYS_ON，不能被示例卡错误地关闭。
    assert "1_system_prompt" not in ext["disabled_layers"]
