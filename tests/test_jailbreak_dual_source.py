"""
tests/test_jailbreak_dual_source.py — CC 任务 24 · 1

破限条目双来源合并 bug 修复回归测试。

根因：`_load_jailbreak()` 只读 stems 源（`jailbreaks/{stem}.json`），从未读取
`characters/reality/jailbreak_entries.json`（前端 EntryManager CRUD 的存储），
导致只在 entries.json 里勾选的条目永远不注入。

覆盖：
1. entries.json 多条 enabled、不同 layer → 各自注入到对应 layer。
2. entries.json 内容与 stems 源重复 → 只注入一次（去重）。
3. disabled 条目不注入。
4. entries.json 缺失/损坏 → fail-open，不影响 stems 源正常注入。
"""

import json

import core.prompt_builder as _pb


def _write_stem(paths, stem: str, entries: list[dict]):
    d = paths.jailbreaks_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.json").write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8"
    )
    assets_path = paths.active_prompt_assets()
    if assets_path.exists():
        assets = json.loads(assets_path.read_text(encoding="utf-8"))
    else:
        assets = {}
    enabled = assets.get("enabled_jailbreaks", [])
    if stem not in enabled:
        enabled.append(stem)
    assets["enabled_jailbreaks"] = enabled
    assets_path.parent.mkdir(parents=True, exist_ok=True)
    assets_path.write_text(json.dumps(assets, ensure_ascii=False), encoding="utf-8")


def _write_entries(paths, entries: list[dict]):
    p = paths.jailbreak_entries()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")


def test_entries_json_multiple_layers_injected(sandbox):
    _write_entries(sandbox, [
        {"id": "a", "title": "性张力", "content": "entry layer0 content", "enabled": True, "layer": 0},
        {"id": "b", "title": "睡觉", "content": "entry layer2 content", "enabled": True, "layer": 2},
        {"id": "c", "title": "1", "content": "entry layer11 content", "enabled": True, "layer": 11},
    ])

    assert _pb._load_jailbreak(layer=0) == "entry layer0 content"
    assert _pb._load_jailbreak(layer=2) == "entry layer2 content"
    assert _pb._load_jailbreak(layer=11) == "entry layer11 content"


def test_entries_json_disabled_not_injected(sandbox):
    _write_entries(sandbox, [
        {"id": "a", "title": "off", "content": "should not appear", "enabled": False, "layer": 0},
    ])
    assert _pb._load_jailbreak(layer=0) == ""


def test_duplicate_content_across_sources_injected_once(sandbox):
    """情绪张力同时存在于 base.json（stems，layer 11）与 entries.json（layer 11）→ 只出现一次。"""
    _write_stem(sandbox, "base", [
        {"content": "情绪张力共享文案", "enabled": True, "layer": 11},
    ])
    _write_entries(sandbox, [
        {"id": "dup", "title": "情绪张力", "content": "情绪张力共享文案", "enabled": True, "layer": 11},
        {"id": "uniq", "title": "性张力", "content": "只在 entries 里的条目", "enabled": True, "layer": 11},
    ])

    result = _pb._load_jailbreak(layer=11)
    assert result.count("情绪张力共享文案") == 1
    assert "只在 entries 里的条目" in result


def test_entries_json_missing_fails_open(sandbox):
    """entries.json 不存在/损坏时 fail-open，stems 源仍正常注入。"""
    _write_stem(sandbox, "base", [
        {"content": "stems only content", "enabled": True, "layer": 0},
    ])
    # jailbreak_entries.json 从未创建
    assert _pb._load_jailbreak(layer=0) == "stems only content"


def test_entries_json_corrupted_fails_open(sandbox):
    p = sandbox.jailbreak_entries()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json", encoding="utf-8")
    _write_stem(sandbox, "base", [
        {"content": "stems still works", "enabled": True, "layer": 0},
    ])
    assert _pb._load_jailbreak(layer=0) == "stems still works"


def test_entries_json_layer_filter(sandbox):
    _write_entries(sandbox, [
        {"id": "a", "title": "t0", "content": "layer0 text", "enabled": True, "layer": 0},
        {"id": "b", "title": "t2", "content": "layer2 text", "enabled": True, "layer": 2},
    ])
    assert _pb._load_jailbreak(layer=0) == "layer0 text"
    assert "layer2 text" not in _pb._load_jailbreak(layer=0)
