"""
trait 统计系统
对最近对话内容做关键词命中统计，维护滑动窗口，输出 underrepresented trait 列表。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)

UNDERREPRESENTED_THRESHOLD = 2


def count_traits_in_history(history_lines: list[str], traits: list[dict]) -> dict[str, int]:
    """
    统计 traits 在 history_lines 中的命中次数。

    对每条 trait，遍历所有行，任一行包含任一 keyword 则该 trait 命中次数 +1。
    关键词匹配用简单的 `keyword in line`，不用正则。

    返回：{trait_id: count, ...}
    """
    counts: dict[str, int] = {}
    for trait in traits:
        trait_id = trait["id"]
        keywords = trait.get("keywords", [])
        hit = 0
        for line in history_lines:
            if any(kw in line for kw in keywords):
                hit += 1
        counts[trait_id] = hit
    return counts


def update_trait_state(
    counts: dict[str, int],
    state_path: Path,
    *,
    write_path: Path | None = None,
) -> None:
    """
    维护滑动窗口 state，计算 underrepresented traits，写回 state_path（或 write_path）。

    state_path: 读取源（调用方可传 for_read(new, old) 实现 S5 降级读）
    write_path: 写入目标；为 None 时写回 state_path（向后兼容）

    state 结构：
    {
      "windows": [{"timestamp": "...", "counts": {...}}, ...],
      "underrepresented": [...]
    }
    """
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {"windows": []}
    else:
        state = {"windows": []}

    if not isinstance(state, dict):
        logger.warning("[trait_tracker] invalid state mapping; rebuilding from current evidence")
        state = {}
    raw_windows = state.get("windows", [])
    if not isinstance(raw_windows, list):
        logger.warning("[trait_tracker] invalid window list; rebuilding from current evidence")
        raw_windows = []
    windows: list[dict] = [window for window in raw_windows
                          if isinstance(window, dict) and isinstance(window.get("counts"), dict)][:4]

    windows.insert(0, {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
    })
    windows = windows[:5]

    all_trait_ids = list(counts.keys())
    totals: dict[str, int] = {tid: 0 for tid in all_trait_ids}
    for window in windows:
        for tid, cnt in window.get("counts", {}).items():
            if tid in totals and isinstance(cnt, int) and not isinstance(cnt, bool) and cnt >= 0:
                totals[tid] += cnt

    underrepresented = [tid for tid, total in totals.items() if total <= UNDERREPRESENTED_THRESHOLD]

    state["windows"] = windows
    state["underrepresented"] = underrepresented

    if not isinstance(state, dict):
        logger.warning("[trait_tracker] state 不是 dict，跳过写入")
        return
    if not isinstance(state.get("windows"), list):
        logger.warning("[trait_tracker] state['windows'] 不是 list，跳过写入")
        return
    if not isinstance(state.get("underrepresented"), list):
        logger.warning("[trait_tracker] state['underrepresented'] 不是 list，跳过写入")
        return

    _write_to = write_path if write_path is not None else state_path
    safe_write_json(_write_to, state)
    logger.debug(f"[trait_tracker] underrepresented={underrepresented}")
