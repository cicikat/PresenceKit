"""
tests/test_memory_list_endpoints.py — W4: memory/{uid} list 读接口

episodic / mid-term / user-facts / event-log 此前只有细粒度删除接口，没有对应的
list 读接口，管理面板无从浏览就无法定位要删哪条（孤儿写接口）。这组测试覆盖新增的
GET /memory/{uid}/episodic、/mid-term、/user-facts、/event-log。

user-facts 是 global scope（无 char_id），其余三个走 reality_scope，按 char_id 隔离。
"""

import asyncio
import json

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry


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
    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    import core.asset_registry as _ar
    monkeypatch.setattr(_ar, "_CHARACTERS_DIR", chars_tree / "characters")
    monkeypatch.setattr(_ar, "_LOREBOOKS_DIR", chars_tree / "characters" / "reality" / "lorebooks")
    monkeypatch.setattr(_ar, "_JAILBREAKS_DIR", chars_tree / "characters" / "reality" / "jailbreaks")
    monkeypatch.setattr(_ar, "_DREAM_PRESETS_DIR", chars_tree / "characters" / "dream_presets")
    monkeypatch.setattr(_ar, "_AVATARS_DIR", chars_tree / "characters" / "reality" / "avatars")
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


def _seed_active(sandbox, char_id: str):
    p = sandbox.active_prompt_assets()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"active_character": char_id, "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )


# ── episodic ──────────────────────────────────────────────────────────────────

def test_list_episodic_returns_entries_for_resolved_char(sandbox, registry):
    from core.memory import episodic_memory
    from admin.routers.memory import list_episodic

    uid = "u_ep_list"
    episodic_memory.write_episode(uid, {
        "id": "ep_1", "timestamp": 1000.0, "narrative_summary": "去公园散步",
        "topic_keywords": ["公园"], "emotion_peak": "happy",
    }, char_id="character_b")

    result = asyncio.run(list_episodic(uid, char_id="character_b", auth="dummy"))

    assert result["char_id"] == "character_b"
    assert result["count"] == 1
    assert result["entries"][0]["id"] == "ep_1"


def test_list_episodic_scoped_by_char_id(sandbox, registry):
    from core.memory import episodic_memory
    from admin.routers.memory import list_episodic

    uid = "u_ep_scope"
    episodic_memory.write_episode(uid, {
        "id": "ep_b", "timestamp": 1000.0, "narrative_summary": "character_b 专属",
    }, char_id="character_b")
    episodic_memory.write_episode(uid, {
        "id": "ep_y", "timestamp": 1000.0, "narrative_summary": "yexuan 专属",
    }, char_id="yexuan")

    result_b = asyncio.run(list_episodic(uid, char_id="character_b", auth="dummy"))
    result_y = asyncio.run(list_episodic(uid, char_id="yexuan", auth="dummy"))

    assert [e["id"] for e in result_b["entries"]] == ["ep_b"]
    assert [e["id"] for e in result_y["entries"]] == ["ep_y"]


def test_list_episodic_uses_active_char_when_omitted(sandbox, registry):
    from core.memory import episodic_memory
    from admin.routers.memory import list_episodic

    _seed_active(sandbox, "character_b")
    uid = "u_ep_active"
    episodic_memory.write_episode(uid, {
        "id": "ep_active", "timestamp": 1000.0, "narrative_summary": "x",
    }, char_id="character_b")

    result = asyncio.run(list_episodic(uid, char_id=None, auth="dummy"))
    assert result["char_id"] == "character_b"
    assert result["count"] == 1


# ── mid-term ──────────────────────────────────────────────────────────────────

def test_list_mid_term_returns_events(sandbox, registry):
    from core.memory import mid_term
    from admin.routers.memory import list_mid_term

    uid = "u_mid_list"
    mid_term.append(uid, "点了外卖", mid_id="mid_1", char_id="character_b")

    result = asyncio.run(list_mid_term(uid, char_id="character_b", auth="dummy"))

    assert result["char_id"] == "character_b"
    assert result["count"] == 1
    assert result["events"][0]["mid_id"] == "mid_1"


def test_list_mid_term_scoped_by_char_id(sandbox, registry):
    from core.memory import mid_term
    from admin.routers.memory import list_mid_term

    uid = "u_mid_scope"
    mid_term.append(uid, "character_b 的事", mid_id="mid_b", char_id="character_b")
    mid_term.append(uid, "yexuan 的事", mid_id="mid_y", char_id="yexuan")

    result_b = asyncio.run(list_mid_term(uid, char_id="character_b", auth="dummy"))
    result_y = asyncio.run(list_mid_term(uid, char_id="yexuan", auth="dummy"))

    assert [e["mid_id"] for e in result_b["events"]] == ["mid_b"]
    assert [e["mid_id"] for e in result_y["events"]] == ["mid_y"]


# ── user-facts (global scope) ──────────────────────────────────────────────────

def test_list_user_facts_returns_saved_facts(sandbox):
    from core.memory import user_facts
    from admin.routers.memory import list_user_facts

    uid = "u_facts_list"
    user_facts.save_user_facts(uid, {"timezone": "Asia/Shanghai"})

    result = asyncio.run(list_user_facts(uid, auth="dummy"))

    assert result["user_id"] == uid
    assert result["facts"]["timezone"] == "Asia/Shanghai"


def test_list_user_facts_empty_when_unset(sandbox):
    from admin.routers.memory import list_user_facts

    result = asyncio.run(list_user_facts("u_facts_empty", auth="dummy"))
    assert result["facts"] == {}


# ── event-log ─────────────────────────────────────────────────────────────────

def test_list_event_log_days_returns_dates(sandbox, registry):
    from core.memory import event_log
    from admin.routers.memory import list_event_log_days

    uid = "u_evlog_list"
    event_log.append(uid, "user", "今天天气不错", char_id="character_b")

    result = asyncio.run(list_event_log_days(uid, char_id="character_b", auth="dummy"))

    assert result["char_id"] == "character_b"
    assert result["count"] == 1
    import datetime
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    assert result["days"] == [today_str]


def test_list_event_log_days_scoped_by_char_id(sandbox, registry):
    from core.memory import event_log
    from admin.routers.memory import list_event_log_days

    uid = "u_evlog_scope"
    event_log.append(uid, "user", "character_b 的日志", char_id="character_b")

    result_b = asyncio.run(list_event_log_days(uid, char_id="character_b", auth="dummy"))
    result_y = asyncio.run(list_event_log_days(uid, char_id="yexuan", auth="dummy"))

    assert result_b["count"] == 1
    assert result_y["count"] == 0
