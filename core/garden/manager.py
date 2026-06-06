"""
core/garden/manager — 五株并行花园系统核心逻辑。
数据目录: data/runtime/characters/{char_id}/garden/（via get_paths().garden(char_id=)）

公开函数：
  water(slot_key, *, reason, char_id) -> dict
  auto_water_tick(*, char_id) -> dict | None
  force_water(mood=None, *, char_id) -> dict
  daily_check(*, char_id) -> list
  get_state(*, char_id) -> dict
"""

import json
import logging
import random
import threading
import time
from pathlib import Path

from core.garden.constants import (
    FLOWERS,
    GROWTH_PER_WATER,
    STAGE_THRESHOLDS,
    AUTO_WATER_PROBABILITY,
    HARVEST_EXPIRE_SECONDS,
    HARVEST_HANDLE_SECONDS,
    VASE_WILT_SECONDS,
    HANDLE_ASK_THRESHOLD,
    HANDLE_SELF_THRESHOLD,
    HANDLE_GIFT_THRESHOLD,
)
from core.safe_write import safe_write_json
from core.sandbox import get_paths, _TRANSITION_CHARACTER_INNER

logger = logging.getLogger(__name__)
_garden_lock = threading.RLock()


# ── 路径 helpers ───────────────────────────────────────────────────────────────

def _plants_path(char_id: str = "yexuan") -> Path:
    return get_paths().garden(char_id=char_id) / "plants.json"


def _storage_path(char_id: str = "yexuan") -> Path:
    return get_paths().garden(char_id=char_id) / "storage.json"


def _read_plants_path(char_id: str = "yexuan") -> Path:
    return _plants_path(char_id)


def _read_storage_path(char_id: str = "yexuan") -> Path:
    return _storage_path(char_id)


# ── JSON I/O ──────────────────────────────────────────────────────────────────

def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path: Path, data, *, char_id: str = "yexuan") -> None:
    if not safe_write_json(path, data):
        raise OSError(f"failed to write garden state: {path}")
    if _TRANSITION_CHARACTER_INNER:
        old_dir = get_paths()._p("garden")
        old_path = old_dir / path.name
        safe_write_json(old_path, data)


# ── 内部工具函数 ───────────────────────────────────────────────────────────────

def _flower_meta(flower_id: str) -> dict:
    """Return {name, language} for a flower_id, fallback to defaults."""
    for flower in FLOWERS:
        if flower["id"] == flower_id:
            return {"name": flower["name"], "language": flower["language"]}
    return {"name": flower_id, "language": ""}


def _stage_for_growth(growth: int) -> str:
    """遍历 STAGE_THRESHOLDS，取 growth 能到达的最高 stage。"""
    result = "seed"
    for stage, threshold in STAGE_THRESHOLDS:
        if growth >= threshold:
            result = stage
    return result


def _resolve_slot_for_mood(mood: str) -> str | None:
    """找 mood 所属的槽位 slot_key，找不到返回 None。"""
    for flower in FLOWERS:
        if mood in flower["mood_keys"]:
            return flower["slot_key"]
    return None


def _bootstrap(*, char_id: str = "yexuan") -> dict:
    """plants.json 不存在则初始化五槽位；storage.json 不存在则初始化空仓库。返回 plants 数据。"""
    garden_dir = get_paths().garden(char_id=char_id)
    garden_dir.mkdir(parents=True, exist_ok=True)

    read_plants = garden_dir / "plants.json"
    read_storage = garden_dir / "storage.json"

    if not read_plants.exists():
        now = time.time()
        slots = {}
        for flower in FLOWERS:
            slots[flower["slot_key"]] = {
                "slot_key": flower["slot_key"],
                "flower_id": flower["id"],
                "stage": "seed",
                "growth": 0,
                "planted_at": now,
                "last_watered": None,
                "bloomed_at": None,
            }
        data = {"slots": slots}
        _save(_plants_path(char_id), data, char_id=char_id)
    else:
        data = _load(read_plants, {"slots": {}})

    if not read_storage.exists():
        _save(_storage_path(char_id), {"harvest": [], "vase": [], "history": []}, char_id=char_id)

    return data


def _on_bloom(plant: dict, storage: dict) -> None:
    # TODO 2d.5e: harvest 过期 / handle / vase 检查
    now = time.time()
    storage["harvest"].append({
        "flower_id": plant["flower_id"],
        "bloomed_at": plant["bloomed_at"],
        "expires_at": now + HARVEST_EXPIRE_SECONDS,
        "status": "fresh",
        "gifted_note": None,
        "handle_notified": False,
    })
    # 就地重新播种
    plant["stage"] = "seed"
    plant["growth"] = 0
    plant["planted_at"] = now
    plant["bloomed_at"] = None
    plant["last_watered"] = None


# ── 公开函数 ───────────────────────────────────────────────────────────────────

def water(slot_key: str, *, reason: str, char_id: str) -> dict:
    """给指定槽位浇一次水，推进 growth / stage，bloom 时自动重播种。"""
    with _garden_lock:
        data = _bootstrap(char_id=char_id)
        slots = data["slots"]

        if slot_key not in slots:
            return {"ok": False, "reason": "slot_not_found", "slot_key": slot_key}

        plant = slots[slot_key]

        if plant["stage"] == "bloom":
            return {"ok": False, "reason": "already_bloomed", "slot_key": slot_key}

        old_stage = plant["stage"]
        plant["growth"] = plant.get("growth", 0) + GROWTH_PER_WATER
        plant["last_watered"] = time.time()

        new_stage = _stage_for_growth(plant["growth"])
        bloomed = new_stage == "bloom" and old_stage != "bloom"

        events = []
        if bloomed:
            plant["bloomed_at"] = time.time()
            storage = _load(_read_storage_path(char_id), {"harvest": [], "vase": [], "history": []})
            _on_bloom(plant, storage)
            _save(_storage_path(char_id), storage, char_id=char_id)
            meta = _flower_meta(plant["flower_id"])
            events.append({"type": "bloom", "flower_id": plant["flower_id"], "name": meta["name"]})
        else:
            plant["stage"] = new_stage

        _save(_plants_path(char_id), data, char_id=char_id)

        return {
            "ok": True,
            "slot_key": slot_key,
            "flower_id": plant["flower_id"],
            "stage": plant["stage"],
            "growth": plant["growth"],
            "bloomed": bloomed,
            "events": events,
            "reason": reason,
        }


def auto_water_tick(*, char_id: str) -> dict | None:
    """scheduler 调用入口。按概率 roll，命中后读 mood 并浇对应槽位。"""
    if random.random() >= AUTO_WATER_PROBABILITY:
        return None

    from core.memory.mood_state import get_current
    mood = get_current(char_id=char_id)

    slot_key = _resolve_slot_for_mood(mood)
    if slot_key is None:
        return None

    result = water(slot_key, reason="auto", char_id=char_id)
    logger.info(
        "[garden] auto water: char=%s mood=%s slot=%s stage=%s growth=%s bloomed=%s",
        char_id, mood, slot_key, result.get("stage"), result.get("growth"), result.get("bloomed"),
    )
    return result


def force_water(mood: str | None = None, *, char_id: str) -> dict:
    """被动浇水入口（工具调用）。mood 为 None 时自动读当前情绪。"""
    if mood is None:
        from core.memory.mood_state import get_current
        mood = get_current(char_id=char_id)

    slot_key = _resolve_slot_for_mood(mood)
    if slot_key is None:
        return {"ok": False, "reason": "no_slot_for_mood", "mood": mood}

    return water(slot_key, reason="force", char_id=char_id)


def daily_check(*, char_id: str) -> list:
    """
    每天扫一次：harvest 过期 / harvest 触发 handle / vase 枯萎。
    状态变更必执行；返回事件列表给上层 trigger 决定是否让叶瑄说话。
    """
    with _garden_lock:
        _bootstrap(char_id=char_id)
        storage = _load(_read_storage_path(char_id), {"harvest": [], "vase": [], "history": []})
        now = time.time()
        events = []

        # A. harvest 过期（now > expires_at）
        expired = [
            item for item in storage.get("harvest", [])
            if now > item.get("expires_at", float("inf"))
        ]
        for item in expired:
            storage["harvest"].remove(item)
            item["status"] = "expired"
            storage.setdefault("history", []).append(item)
            meta = _flower_meta(item["flower_id"])
            events.append({"type": "harvest_expired", "flower_id": item["flower_id"], "name": meta["name"]})

        # B. harvest 触发 handle（bloomed_at 超过 HARVEST_HANDLE_SECONDS 且未触发过）
        harvest_to_remove = []
        for item in list(storage.get("harvest", [])):
            if item.get("handle_triggered"):
                continue
            if now - item.get("bloomed_at", now) <= HARVEST_HANDLE_SECONDS:
                continue
            item["handle_triggered"] = True
            r = random.random()
            meta = _flower_meta(item["flower_id"])
            if r < HANDLE_ASK_THRESHOLD:
                action = "ask"
            elif r < HANDLE_SELF_THRESHOLD:
                if random.random() < 0.5:
                    action = "dry"
                    item["status"] = "dried"
                else:
                    action = "vase"
                    item["status"] = "vased"
                    storage.setdefault("vase", []).append({
                        "flower_id": item["flower_id"],
                        "placed_at": now,
                        "wilts_at": now + VASE_WILT_SECONDS,
                    })
                    harvest_to_remove.append(item)
            elif r < HANDLE_GIFT_THRESHOLD:
                action = "gift"
                item["gifted_note"] = meta["language"]
            else:
                action = "silent"
            event = {
                "type": "harvest_handle",
                "handle_action": action,
                "flower_id": item["flower_id"],
                "name": meta["name"],
            }
            if action == "gift":
                event["language"] = meta["language"]
            events.append(event)

        for item in harvest_to_remove:
            try:
                storage["harvest"].remove(item)
            except ValueError:
                pass

        # C. vase 枯萎（now > wilts_at）
        wilted = [v for v in storage.get("vase", []) if now > v.get("wilts_at", float("inf"))]
        for item in wilted:
            storage["vase"].remove(item)
            item["status"] = "wilted"
            storage.setdefault("history", []).append(item)
            meta = _flower_meta(item["flower_id"])
            events.append({"type": "vase_wilted", "flower_id": item["flower_id"], "name": meta["name"]})

        _save(_storage_path(char_id), storage, char_id=char_id)
        return events


def get_state(*, char_id: str) -> dict:
    """给 admin 路由返回完整花园状态。"""
    with _garden_lock:
        data = _bootstrap(char_id=char_id)
        slots = data["slots"]
        storage = _load(_read_storage_path(char_id), {"harvest": [], "vase": [], "history": []})

        result_slots = []
        for flower in FLOWERS:
            sk = flower["slot_key"]
            plant = slots.get(sk, {})
            growth = plant.get("growth", 0)
            stage = plant.get("stage", "seed")

            # 计算当前 stage 的 min/max 边界
            stage_min = 0
            stage_max = None
            for i, (s, threshold) in enumerate(STAGE_THRESHOLDS):
                if s == stage:
                    stage_min = threshold
                    if i + 1 < len(STAGE_THRESHOLDS):
                        stage_max = STAGE_THRESHOLDS[i + 1][1]
                    else:
                        stage_max = threshold  # bloom：无下一阶段
                    break

            if stage == "bloom" or stage_max is None or stage_max <= stage_min:
                stage_progress = 1.0
            else:
                stage_progress = min(1.0, (growth - stage_min) / (stage_max - stage_min))

            result_slots.append({
                "slot_key": sk,
                "flower_id": plant.get("flower_id", flower["id"]),
                "name": flower["name"],
                "en_name": flower["en_name"],
                "stage": stage,
                "growth": growth,
                "stage_min": stage_min,
                "stage_max": stage_max,
                "stage_progress": round(stage_progress, 4),
                "mood_keys": flower["mood_keys"],
                "last_watered": plant.get("last_watered"),
            })

        return {
            "slots": result_slots,
            "harvest_count": len(storage.get("harvest", [])),
            "vase_count": len(storage.get("vase", [])),
        }
