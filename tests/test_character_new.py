"""
tests/test_character_new.py — Brief 94 §1: POST /characters/new（从模板新建角色卡）

覆盖：
  ① 合法 id → 落盘 characters/{id}.json，内容取自 examples/character_template.json，
     name 字段被替换为传入的 name（缺省用 id）
  ② 不传 name → name 字段回落为 id
  ③ 非法 id（空 / 路径穿越 / 以 . 开头）→ 422，不落盘
  ④ id 冲突（文件已存在）→ 409，不覆盖原文件
  ⑤ 新建的卡不写 config.yaml、不切换活跃角色（正控：active_id 不受影响）
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException


@pytest.fixture
def chars_tree(tmp_path):
    """最小工作目录：characters/（含既有 yexuan 卡）+ examples/character_template.json。"""
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / "yexuan.json").write_text(
        json.dumps({"name": "叶瑄", "world_book": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        "character:\n  default: yexuan\n", encoding="utf-8",
    )
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "character_template.json").write_text(
        json.dumps({
            "name": "角色",
            "system_prompt": ["模板 system_prompt"],
            "description": ["模板 description"],
            "world_book": [],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    import core.asset_registry as _reg_mod
    monkeypatch.chdir(chars_tree)
    reg = _reg_mod.AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


def _new_character(body):
    from admin.routers.character import new_character
    return asyncio.run(new_character(body, auth="dummy"))


# ═══════════════════════════════════════════════════════════════════════════════
# ① 合法 id → 落盘，name 取传入值
# ═══════════════════════════════════════════════════════════════════════════════

def test_new_character_creates_file_from_template(registry, chars_tree):
    result = _new_character({"id": "newbie", "name": "新角色"})

    assert result["id"] == "newbie"
    assert result["filename"] == "newbie.json"
    assert result["label"] == "新角色"

    dest = chars_tree / "characters" / "newbie.json"
    assert dest.exists()
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["name"] == "新角色"
    assert data["system_prompt"] == ["模板 system_prompt"]  # 模板其余字段原样带过来


# ═══════════════════════════════════════════════════════════════════════════════
# ② 不传 name → 回落为 id
# ═══════════════════════════════════════════════════════════════════════════════

def test_new_character_name_defaults_to_id(registry, chars_tree):
    result = _new_character({"id": "bare_id"})
    assert result["label"] == "bare_id"
    data = json.loads((chars_tree / "characters" / "bare_id.json").read_text(encoding="utf-8"))
    assert data["name"] == "bare_id"


# ═══════════════════════════════════════════════════════════════════════════════
# ③ 非法 id 被拒，且不落盘
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad_id", ["", "   ", "../evil", "a/b", "a\\b", ".hidden"])
def test_new_character_rejects_illegal_id(registry, chars_tree, bad_id):
    with pytest.raises(HTTPException) as exc:
        _new_character({"id": bad_id})
    assert exc.value.status_code == 422
    # 正控：目录下除了预置的 yexuan.json，没有任何新文件被创建
    assert sorted(p.name for p in (chars_tree / "characters").glob("*.json")) == ["yexuan.json"]


# ═══════════════════════════════════════════════════════════════════════════════
# ④ id 冲突 → 409，不覆盖
# ═══════════════════════════════════════════════════════════════════════════════

def test_new_character_conflict_returns_409_and_does_not_overwrite(registry, chars_tree):
    with pytest.raises(HTTPException) as exc:
        _new_character({"id": "yexuan"})
    assert exc.value.status_code == 409
    # 正控：原有 yexuan.json 内容未被模板覆盖
    data = json.loads((chars_tree / "characters" / "yexuan.json").read_text(encoding="utf-8"))
    assert data["name"] == "叶瑄"


# ═══════════════════════════════════════════════════════════════════════════════
# ⑤ 新建不激活
# ═══════════════════════════════════════════════════════════════════════════════

def test_new_character_does_not_change_active_character(registry, chars_tree):
    _new_character({"id": "newbie2"})
    cfg = (chars_tree / "config.yaml").read_text(encoding="utf-8")
    assert "default: yexuan" in cfg, "新建角色卡不应改写活跃角色配置"
