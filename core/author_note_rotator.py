"""
author_note 轮换系统
每隔 30 分钟从 characters/yexuan_author_notes.json 加权随机选一条注入层 11
"""

import json
import logging
import random
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SWITCH_INTERVAL_MINUTES = 30


def _load_pool(pool_path: Path) -> list[dict]:
    if not pool_path.exists():
        raise FileNotFoundError(
            f"[author_note_rotator] notes pool 文件不存在: {pool_path}"
        )
    data = json.loads(pool_path.read_text(encoding="utf-8"))
    return data.get("notes", [])


def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {"current_id": None, "last_switched_at": None, "history": []}
    return json.loads(state_path.read_text(encoding="utf-8"))


def _save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _should_switch(state: dict) -> bool:
    if state.get("current_id") is None or state.get("last_switched_at") is None:
        return True
    try:
        last = datetime.fromisoformat(state["last_switched_at"])
        elapsed = (datetime.now() - last).total_seconds() / 60
        return elapsed >= _SWITCH_INTERVAL_MINUTES
    except Exception:
        return True


def _days_since(note_id: str, history: list[dict]) -> int:
    today = date.today()
    for entry in history:
        if entry.get("id") == note_id:
            try:
                return (today - date.fromisoformat(entry["date"])).days
            except Exception:
                return 999
    return 999


def _pick_note(pool: list[dict], state: dict, underrepresented: list[str]) -> dict:
    history = state.get("history", [])
    days_map = {n["id"]: _days_since(n["id"], history) for n in pool}

    forbidden = {n["id"] for n in pool if days_map[n["id"]] <= 1}
    forced = [n for n in pool if days_map[n["id"]] >= 15]

    if forced:
        candidates = [n for n in forced if n["id"] not in forbidden]
        if not candidates:
            candidates = forced
    else:
        candidates = [n for n in pool if n["id"] not in forbidden]

    if not candidates:
        candidates = pool

    def _weight(note: dict) -> float:
        base = max(days_map[note["id"]], 1) ** 1.5
        trait_ids = note.get("trait_ids", [])
        if any(t in underrepresented for t in trait_ids):
            return base * 2
        return base

    weights = [_weight(n) for n in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


def get_current_note(paths=None, char_id: str | None = None) -> str:
    """
    返回当前轮次应注入层 11 的 author_note 文本。
    满足任一条件时切换：从未切换过，或距上次切换超过 30 分钟。
    paths 可选；不传时自动调用 get_paths()。
    char_id 可选；不传时保持旧行为（路径方法使用各自的默认值）。
    """
    if paths is None:
        from core.sandbox import get_paths
        paths = get_paths()

    from core.sandbox import _TRANSITION_CHARACTER_INNER

    _kw = {"char_id": char_id} if char_id is not None else {}

    pool_path = paths.author_notes_pool(**_kw)
    write_state_path = paths.author_note_state(**_kw)

    try:
        pool = _load_pool(pool_path)
        state = _load_state(write_state_path)

        if not _should_switch(state):
            current_id = state.get("current_id")
            if current_id is not None:
                for note in pool:
                    if note["id"] == current_id:
                        return note["content"]

        underrepresented: list[str] = []
        try:
            trait_state = json.loads(paths.trait_state(**_kw).read_text(encoding="utf-8"))
            underrepresented = trait_state.get("underrepresented", [])
        except Exception:
            pass

        chosen = _pick_note(pool, state, underrepresented)
        history = state.get("history", [])
        history.insert(0, {"id": chosen["id"], "date": date.today().isoformat()})

        state["current_id"] = chosen["id"]
        state["last_switched_at"] = datetime.now().isoformat(timespec="seconds")
        state["history"] = history[:30]

        _save_state(write_state_path, state)
        if _TRANSITION_CHARACTER_INNER and (char_id is None or char_id == "yexuan"):
            _save_state(paths._p("yexuan_inner", "author_note_state.json"), state)
        logger.info(f"[author_note_rotator] 切换到 note id={chosen['id']}")
        return chosen["content"]

    except FileNotFoundError:
        raise
    except Exception as e:
        logger.warning(f"[author_note_rotator] 获取 note 失败，跳过: {e}")
        return ""
