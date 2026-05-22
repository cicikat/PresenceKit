"""
验证 main.py 冷启动路径中 LoreEngine 的加载顺序：
  正确：LoreEngine() → load() → load_entries(world_book)
  错误：LoreEngine(world_book) → load()  ← load() 会重置 entries，角色卡条目丢失
"""

import yaml
import pytest
from pathlib import Path
from unittest.mock import patch

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


@pytest.fixture
def lorebook_yaml(tmp_path):
    p = tmp_path / "lorebook.yaml"
    p.write_text(yaml.dump({"entries": [_GLOBAL_ENTRY]}), encoding="utf-8")
    return p


def test_cold_start_correct_order_loads_both(lorebook_yaml):
    """正确顺序：全局条目和角色卡条目都应存在"""
    with patch("core.lore_engine.LOREBOOK_PATH", lorebook_yaml):
        engine = LoreEngine()
        engine.load()
        engine.load_entries([_CHAR_ENTRY])

    contents = [e["content"] for e in engine.entries]
    assert "全局世界书内容" in contents
    assert "角色卡内嵌世界书内容" in contents
    assert len(engine.entries) == 2


def test_cold_start_buggy_order_loses_world_book(lorebook_yaml):
    """回归对照：旧的错误顺序下角色卡条目被 load() 重置丢弃"""
    with patch("core.lore_engine.LOREBOOK_PATH", lorebook_yaml):
        engine = LoreEngine([_CHAR_ENTRY])  # __init__ 写入 entries
        engine.load()                        # load() 把 entries 重置为 []

    # 只剩全局条目，角色卡条目已丢失
    assert len(engine.entries) == 1
    assert engine.entries[0]["content"] == "全局世界书内容"


def test_cold_start_empty_world_book_entry_count_unchanged(lorebook_yaml):
    """当角色卡 world_book=[] 时，修复后全局条目数与修复前完全一致"""
    with patch("core.lore_engine.LOREBOOK_PATH", lorebook_yaml):
        engine_fixed = LoreEngine()
        engine_fixed.load()
        engine_fixed.load_entries([])  # 空列表，不追加任何条目

        engine_old = LoreEngine([])
        engine_old.load()

    assert len(engine_fixed.entries) == len(engine_old.entries) == 1
