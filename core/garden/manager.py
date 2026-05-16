"""
core/garden/manager — 五株并行花园系统核心逻辑。
数据目录: data/garden/（via get_paths().garden()）

公开函数：
  water(slot_key, *, reason) -> dict
  auto_water_tick() -> dict | None
  force_water(mood=None) -> dict
  get_state() -> dict
"""

import json
import logging
import random
import time
from pathlib import Path

from core.sandbox import get_paths
from core.garden.constants import (
    FLOWERS,
    GROWTH_PER_WATER,
    STAGE_THRESHOLDS,
    AUTO_WATER_PROBABILITY,
    HARVEST_EXPIRE_SECONDS,
)

logger = logging.getLogger(__name__)


# ── 路径 helpers ───────────────────────────────────────────────────────────────

def _plants_path() -> Path:
    return get_paths().garden() / "plants.json"


def _storage_path() -> Path:
    return get_paths().garden() / "storage.json"


# ── JSON I/O ──────────────────────────────────────────────────────────────────

def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 内部工具函数 ───────────────────────────────────────────────────────────────

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


def _bootstrap() -> dict:
    """plants.json 不存在则初始化五槽位；storage.json 不存在则初始化空仓库。返回 plants 数据。"""
    plants_path = _plants_path()
    storage_path = _storage_path()

    if not plants_path.exists():
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
        _save(plants_path, data)
    else:
        data = _load(plants_path, {"slots": {}})

    if not storage_path.exists():
        _save(storage_path, {"harvest": [], "vase": [], "history": []})

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

def water(slot_key: str, *, reason: str) -> dict:
    """给指定槽位浇一次水，推进 growth / stage，bloom 时自动重播种。"""
    data = _bootstrap()
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

    if bloomed:
        plant["bloomed_at"] = time.time()
        storage = _load(_storage_path(), {"harvest": [], "vase": [], "history": []})
        _on_bloom(plant, storage)
        _save(_storage_path(), storage)
    else:
        plant["stage"] = new_stage

    _save(_plants_path(), data)

    return {
        "ok": True,
        "slot_key": slot_key,
        "flower_id": plant["flower_id"],
        "stage": plant["stage"],
        "growth": plant["growth"],
        "bloomed": bloomed,
        "reason": reason,
    }


def auto_water_tick() -> dict | None:
    """scheduler 调用入口。按概率 roll，命中后读 mood 并浇对应槽位。"""
    if random.random() >= AUTO_WATER_PROBABILITY:
        return None

    from core.memory.mood_state import get_current
    mood = get_current()

    slot_key = _resolve_slot_for_mood(mood)
    if slot_key is None:
        return None

    result = water(slot_key, reason="auto")
    logger.info(
        "[garden] auto water: mood=%s slot=%s stage=%s growth=%s bloomed=%s",
        mood, slot_key, result.get("stage"), result.get("growth"), result.get("bloomed"),
    )
    return result


def force_water(mood: str | None = None) -> dict:
    """被动浇水入口（工具调用）。mood 为 None 时自动读当前情绪。"""
    if mood is None:
        from core.memory.mood_state import get_current
        mood = get_current()

    slot_key = _resolve_slot_for_mood(mood)
    if slot_key is None:
        return {"ok": False, "reason": "no_slot_for_mood", "mood": mood}

    return water(slot_key, reason="force")


def get_state() -> dict:
    """给 admin 路由返回完整花园状态。"""
    data = _bootstrap()
    slots = data["slots"]
    storage = _load(_storage_path(), {"harvest": [], "vase": [], "history": []})

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
