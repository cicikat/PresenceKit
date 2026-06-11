"""
S5/S6 布局路径往返验证（V9 post-soak，fallback 已退役）

覆盖：
  Reality chain (S6):
    - capture_turn 写 event_log 落新路径 memory/yexuan/{uid}/event_log/
    - capture_turn 写 short_term 落新路径 memory/yexuan/{uid}/history.json
    - 旧路径 event_log/{uid}/ 有数据时 get_recent_days union 可读到

  Inner state 读-改-写往返 (S5):
    mood / activity / trait / author_note / presence / pet / garden
    每项断言：写落新路径，重新 load 后数据恢复。
    旧路径 fallback 测试已在 V9 退役（soak 出口后删除）。
"""

import json
import time
from datetime import datetime
from pathlib import Path

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Reality chain — S6 新布局写入路径
# ═══════════════════════════════════════════════════════════════════════════════

def test_capture_turn_event_log_lands_in_new_layout(sandbox):
    """capture_turn 写 event_log 应落在 memory/yexuan/{uid}/event_log/YYYY-MM-DD.md。"""
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import stamp_ingest

    uid = "s6_uid_el"
    turn_id = capture_turn(uid, "测试消息", "测试回复", "neutral", envelope=stamp_ingest())

    today = datetime.now().strftime("%Y-%m-%d")
    new_dir = sandbox.user_memory_root(uid) / "event_log"
    day_file = new_dir / f"{today}.md"

    assert day_file.exists(), f"新路径日志文件不存在: {day_file}"
    text = day_file.read_text(encoding="utf-8")
    assert "测试消息" in text
    assert "测试回复" in text
    assert f"turn_id:{turn_id}" in text

    # 旧路径不应有写入
    old_file = sandbox._p("event_log") / uid / f"{today}.md"
    assert not old_file.exists(), "capture_turn 不应写旧路径"


def test_capture_turn_short_term_lands_in_new_layout(sandbox):
    """capture_turn 写 short_term 应落在 memory/yexuan/{uid}/history.json。"""
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import stamp_ingest

    uid = "s6_uid_st"
    capture_turn(uid, "用户说", "角色说", "gentle", envelope=stamp_ingest())

    new_path = sandbox.user_memory_root(uid) / "history.json"
    assert new_path.exists(), f"新路径 history.json 不存在: {new_path}"
    history = json.loads(new_path.read_text(encoding="utf-8"))
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


def test_old_event_log_fallback_readable_via_union(sandbox):
    """旧路径 event_log/{uid}/ 有数据时 get_recent_days union 能读到。"""
    from core.memory.event_log import get_recent_days

    uid = "s6_fallback_uid"
    today = datetime.now().strftime("%Y-%m-%d")

    # 只在旧路径写数据，新路径不存在
    old_dir = sandbox._p("event_log") / uid
    old_dir.mkdir(parents=True, exist_ok=True)
    (old_dir / f"{today}.md").write_text(
        "## 10:00\n**用户**：旧路径数据\n> turn_id:old-tid\n"
        "**叶瑄**：已读到\n> emotion:neutral intensity:0 turn_id:old-tid\n---\n",
        encoding="utf-8",
    )

    result = get_recent_days(uid, days=1)
    assert "旧路径数据" in result
    assert "已读到" in result


def test_reality_chain_full_turn_new_layout(sandbox):
    """
    capture_turn → fixation_state → event_log 全程命中新布局；
    旧路径数据（不同天）经 fallback 仍可经 get_recent_days 读到。
    """
    from core.memory.fixation_pipeline import capture_turn, _load_fixation_state
    from core.memory.event_log import get_recent_days
    from datetime import timedelta

    uid = "s6_full_chain"

    # 写入旧路径 2 天前的数据
    old_dir = sandbox._p("event_log") / uid
    old_dir.mkdir(parents=True, exist_ok=True)
    two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    (old_dir / f"{two_days_ago}.md").write_text(
        f"## 09:00\n**用户**：旧数据\n> turn_id:legacy-tid\n"
        f"**叶瑄**：旧回复\n> emotion:neutral intensity:0 turn_id:legacy-tid\n---\n",
        encoding="utf-8",
    )

    # 跑一轮真实 turn
    from core.write_envelope import stamp_ingest
    turn_id = capture_turn(uid, "新消息", "新回复", "happy", envelope=stamp_ingest())
    assert turn_id is not None

    # fixation_state 写新路径
    state = _load_fixation_state(uid)
    assert isinstance(state, dict)

    # get_recent_days 同时读到新轮和旧路径历史
    result = get_recent_days(uid, days=3)
    assert "新消息" in result, "新轮 capture_turn 应出现在 get_recent_days 结果中"
    assert "旧数据" in result, "旧路径历史应通过 fallback union 出现在结果中"


# ═══════════════════════════════════════════════════════════════════════════════
# mood_state 读-改-写往返
# ═══════════════════════════════════════════════════════════════════════════════


def test_mood_write_new_path_and_reload(sandbox):
    """save() 写新路径 → load() 从新路径恢复正确状态。"""
    from core.memory import mood_state

    payload = {"current": "sad", "intensity": 0.5, "previous": "neutral", "updated_at": time.time()}
    mood_state.save(payload)

    # mood_state.save/load default to yexuan; roundtrip test checks yexuan path.
    assert sandbox.mood_state(char_id="yexuan").exists(), "save() 应写入新路径"

    loaded = mood_state.load()
    assert loaded["current"] == "sad"
    assert abs(loaded["intensity"] - 0.5) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# activity_state 读-改-写往返
# ═══════════════════════════════════════════════════════════════════════════════

def test_activity_write_new_path_and_reload(sandbox):
    """_save_state() 写新路径 → _load_state() 从新路径恢复。"""
    from core.activity_manager import _save_state, _load_state

    state = {"current_activity": "reading", "arc": "afternoon", "updated_at": time.time()}
    _save_state(state)

    assert sandbox.activity_state().exists(), "_save_state 应写入新路径"

    loaded = _load_state()
    assert loaded["current_activity"] == "reading"



# ═══════════════════════════════════════════════════════════════════════════════
# trait_state 对称链路往返
# ═══════════════════════════════════════════════════════════════════════════════

def test_trait_write_new_path_and_reload(sandbox):
    """update_trait_state 写新路径 → 从新路径读回正确状态（对称链路绿灯）。"""
    from core.memory.trait_tracker import update_trait_state
    from core.sandbox import for_read

    new_path = sandbox.trait_state()
    old_path = sandbox._p("yexuan_inner", "trait_state.json")
    new_path.parent.mkdir(parents=True, exist_ok=True)

    counts = {"trait_kindness": 3, "trait_humor": 0, "trait_cool": 1}
    # 新、旧路径均不存在 → for_read 返回 old (不存在也没事，update_trait_state 处理)
    read_path = for_read(new_path, old_path)
    update_trait_state(counts, read_path, write_path=new_path)

    assert new_path.exists(), "update_trait_state 应写入新路径"
    result = json.loads(new_path.read_text(encoding="utf-8"))
    assert result["windows"][0]["counts"]["trait_kindness"] == 3
    assert "underrepresented" in result

    # 重新 for_read：新路径已写入，应返回新路径
    assert for_read(new_path, old_path) == new_path


def test_trait_old_fallback_merged_into_new(sandbox):
    """旧路径有窗口历史 → update_trait_state 读旧、合并后写到新路径，旧路径不变。"""
    from core.memory.trait_tracker import update_trait_state
    from core.sandbox import for_read

    new_path = sandbox.trait_state()
    old_path = sandbox._p("yexuan_inner", "trait_state.json")
    old_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.parent.mkdir(parents=True, exist_ok=True)

    old_state = {
        "windows": [{"timestamp": "2026-01-01T00:00:00", "counts": {"trait_kindness": 2}}],
        "underrepresented": [],
    }
    old_path.write_text(json.dumps(old_state), encoding="utf-8")

    read_path = for_read(new_path, old_path)
    assert read_path == old_path  # 新路径不存在，应降级到旧路径

    update_trait_state({"trait_kindness": 1}, read_path, write_path=new_path)

    assert new_path.exists()
    result = json.loads(new_path.read_text(encoding="utf-8"))
    # 本轮窗口 + 旧窗口 = 2 个
    assert len(result["windows"]) == 2, f"应有2个窗口，实际: {result['windows']}"
    # 旧窗口数据保留
    old_counts = result["windows"][1]["counts"]
    assert old_counts.get("trait_kindness") == 2
    # 旧路径内容不变
    assert json.loads(old_path.read_text(encoding="utf-8")) == old_state


# ═══════════════════════════════════════════════════════════════════════════════
# author_note_state 读-改-写往返
# ═══════════════════════════════════════════════════════════════════════════════

def test_author_note_state_write_new_path_and_reload(sandbox):
    """_save_state / _load_state 往返：写新路径，reload 恢复。"""
    from core.author_note_rotator import _save_state, _load_state

    state_path = sandbox.author_note_state()
    state_path.parent.mkdir(parents=True, exist_ok=True)

    state = {
        "current_id": "note_calm",
        "last_switched_at": datetime.now().isoformat(),
        "history": [{"id": "note_calm", "date": datetime.now().date().isoformat()}],
    }
    _save_state(state_path, state)

    assert state_path.exists()
    loaded = _load_state(state_path)
    assert loaded["current_id"] == "note_calm"
    assert len(loaded["history"]) == 1


def test_author_note_state_old_fallback(sandbox):
    """旧路径有 author_note_state.json → for_read 降级后 _load_state 读到旧数据。"""
    from core.author_note_rotator import _load_state
    from core.sandbox import for_read

    new_path = sandbox.author_note_state()
    old_path = sandbox._p("yexuan_inner", "author_note_state.json")
    old_path.parent.mkdir(parents=True, exist_ok=True)

    old_state = {
        "current_id": "note_old",
        "last_switched_at": "2026-01-01T00:00:00",
        "history": [],
    }
    old_path.write_text(json.dumps(old_state), encoding="utf-8")

    read_path = for_read(new_path, old_path)
    assert read_path == old_path

    loaded = _load_state(read_path)
    assert loaded["current_id"] == "note_old"


# ═══════════════════════════════════════════════════════════════════════════════
# presence 读-改-写往返
# ═══════════════════════════════════════════════════════════════════════════════

def test_presence_write_new_path(sandbox):
    """update_last_message → 写新路径；< 6h 内 get_last_seen_text 返回空字符串。"""
    from core.presence import update_last_message, get_last_seen_text

    uid = "pres_uid"
    update_last_message(uid)

    new_path = sandbox.presence()
    assert new_path.exists(), "update_last_message 应写入新路径"

    data = json.loads(new_path.read_text(encoding="utf-8"))
    assert uid in data
    assert data[uid]["last_message_at"] > 0

    # < 6 小时内，不显示时间
    assert get_last_seen_text(uid) == ""



# ═══════════════════════════════════════════════════════════════════════════════
# pet 读-改-写往返
# ═══════════════════════════════════════════════════════════════════════════════

def test_pet_create_writes_new_path_and_reload(sandbox):
    """create_pet() → 写新路径 → get_pet() 从新路径读回。"""
    from core.pet import create_pet, get_pet

    create_pet("Kitty", "猫")

    assert sandbox.pet_file().exists(), "create_pet 应写入新路径"

    loaded = get_pet()
    assert loaded is not None
    assert loaded["name"] == "Kitty"
    assert loaded["species"] == "猫"



# ═══════════════════════════════════════════════════════════════════════════════
# garden 读-改-写往返
# ═══════════════════════════════════════════════════════════════════════════════

def test_garden_water_writes_new_path(sandbox):
    """water(slot_key) → 写新路径 plants.json → get_state 包含该槽位。"""
    from core.garden.manager import water, get_state

    result = water("calm", reason="smoke_test", char_id="yexuan")
    assert result["ok"], f"water 返回失败: {result}"

    new_plants = sandbox.garden(char_id="yexuan") / "plants.json"
    assert new_plants.exists(), "water 应写入新路径 plants.json"

    state = get_state(char_id="yexuan")
    slot_keys = {s["slot_key"] for s in state["slots"]}
    assert "calm" in slot_keys


