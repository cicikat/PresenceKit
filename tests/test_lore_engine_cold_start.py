"""
验证 main.py 冷启动路径中 LoreEngine 的加载顺序：
  正确：LoreEngine() → load() → load_entries(world_book)
  错误：LoreEngine(world_book) → load()  ← load() 会重置 entries，角色卡条目丢失
"""

import yaml
import pytest

from core.lore_engine import LoreEngine


_GLOBAL_ENTRY = {
    "keyword": ["全局关键词"],
    "content": "全局世界书内容",
    "enabled": True,
}

_CHAR_ENTRY = {
    "keywords": ["角色内嵌关键词"],
    "content": "角色卡内嵌世界书内容",
    "enabled": True,
}


def _write_lorebook(sandbox, entries):
    # LoreEngine.load() reads from lorebooks_dir/{stem}.yaml (enabled_lorebooks: ["base"])
    lorebooks_dir = sandbox.lorebooks_dir()
    lorebooks_dir.mkdir(parents=True, exist_ok=True)
    p = lorebooks_dir / "base.yaml"
    p.write_text(yaml.dump({"entries": entries}), encoding="utf-8")
    # Also set up active_prompt_assets.json so LoreEngine.load() can resolve enabled_lorebooks
    import json as _json
    apa = sandbox._base / "runtime" / "active_prompt_assets.json"
    apa.parent.mkdir(parents=True, exist_ok=True)
    apa.write_text(
        _json.dumps({"active_character": "yexuan", "enabled_lorebooks": ["base"], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


def test_cold_start_correct_order_loads_both(sandbox):
    """正确顺序：全局条目和角色卡条目都应存在"""
    _write_lorebook(sandbox, [_GLOBAL_ENTRY])
    engine = LoreEngine()
    engine.load()
    engine.load_entries([_CHAR_ENTRY])

    contents = [e["content"] for e in engine.entries]
    assert "全局世界书内容" in contents
    assert "角色卡内嵌世界书内容" in contents
    assert len(engine.entries) == 2


def test_cold_start_buggy_order_loses_world_book(sandbox):
    """回归对照：旧的错误顺序下角色卡条目被 load() 重置丢弃"""
    _write_lorebook(sandbox, [_GLOBAL_ENTRY])
    engine = LoreEngine([_CHAR_ENTRY])  # __init__ 写入 entries
    engine.load()                        # load() 把 entries 重置为 []

    # 只剩全局条目，角色卡条目已丢失
    assert len(engine.entries) == 1
    assert engine.entries[0]["content"] == "全局世界书内容"


def test_cold_start_empty_world_book_entry_count_unchanged(sandbox):
    """当角色卡 world_book=[] 时，修复后全局条目数与修复前完全一致"""
    _write_lorebook(sandbox, [_GLOBAL_ENTRY])

    engine_fixed = LoreEngine()
    engine_fixed.load()
    engine_fixed.load_entries([])

    engine_old = LoreEngine([])
    engine_old.load()

    assert len(engine_fixed.entries) == len(engine_old.entries) == 1
