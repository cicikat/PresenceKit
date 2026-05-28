"""
Dream summary generator — post-dream afterglow material.

Called in background after dream close. Reads the archived dream log,
strips scene/action/setting, keeps emotional register + symbolic fragments.
Output stored in dreams/summaries/dream_{id}.summary.json.

Contract:
- Never read by reflect_to_episodic / consolidate_to_identity / any reality loader
- high_weight_lines are archive-only (never injected into prompts)
- emotional_trace_weight field reserved but not consumed in MVP1
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STRIP_SCENE_SYSTEM = """你是梦境情感分析器。
从以下梦境对话中提取情绪 register 和象征碎片，剥除所有场景、动作、设定描写。
只保留：情绪质地、情绪流动方向、象征性词语/意象。

输出 JSON，不要其他文字：
{
  "title": "5字以内的梦境标题",
  "summary": "20字以内的情绪摘要（不含场景/动作，不含具体行为描写）",
  "emotional_tags": ["情绪词1", "情绪词2", "情绪词3"],
  "high_weight_lines": ["最有情绪分量的原文句子1", "原文句子2"],
  "symbolic_fragments": ["象征意象1", "象征意象2"],
  "summary_weight": 0.0到1.0之间的浮点数（情绪越浓/越重要越高）
}"""


async def generate_summary(uid: str, dream_id: str, exit_type: str) -> None:
    """Generate and persist afterglow summary for a completed dream."""
    from core.sandbox import get_paths
    from core.safe_write import safe_write_json
    from core import llm_client

    archive_path = get_paths().dreams_archive_dir() / f"dream_{dream_id}.jsonl"
    turns = _load_archive(archive_path)
    if not turns:
        logger.info(f"[dream_summary] empty archive uid={uid} dream_id={dream_id}, skip")
        return

    dialogue = _format_dialogue(turns)
    data = await _llm_strip_scene(dialogue, llm_client)

    # Read frozen_world for depth-defense vocab strip
    try:
        from core.dream.dream_state import read_state as _read_ds
        _ds = _read_ds(uid)
        _frozen_world = _ds.get("frozen_world", "reality_derived")
    except Exception:
        _frozen_world = "reality_derived"

    summary_record: dict[str, Any] = {
        # Identification
        "dream_id": dream_id,
        "uid": uid,
        "created_at": time.time(),
        "exit_type": exit_type,
        "world_id": _frozen_world,
        # Core afterglow content
        "title": str(data.get("title", "")),
        "summary": str(data.get("summary", "")),
        "emotional_tags": _ensure_list(data.get("emotional_tags")),
        # high_weight_lines: archive-only, never injected into any prompt
        "high_weight_lines": _ensure_list(data.get("high_weight_lines")),
        "symbolic_fragments": _ensure_list(data.get("symbolic_fragments")),
        "summary_weight": float(data.get("summary_weight") or 0.5),
        "afterglow": _decide_afterglow_type(exit_type),
        # Boundary
        "reality_boundary": "dream_only",
        # Sentinel: never retrieved by reality loaders
        "never_retrieve": True,
        "not_memory_source": True,
        # Reserved seam — not consumed in MVP1
        "emotional_trace_weight": None,
    }

    summaries_dir = get_paths().dreams_summaries_dir()
    summaries_dir.mkdir(parents=True, exist_ok=True)
    dest = summaries_dir / f"dream_{dream_id}.summary.json"
    safe_write_json(dest, summary_record)
    logger.info(f"[dream_summary] saved uid={uid} -> {dest.name}")


def _load_archive(archive_path: Path) -> list[dict[str, Any]]:
    if not archive_path.exists():
        return []
    turns: list[dict[str, Any]] = []
    for line in archive_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            turns.append(json.loads(line))
        except Exception:
            pass
    return turns


def _format_dialogue(turns: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for t in turns:
        role = t.get("role", "?")
        content = (t.get("content") or "")[:200]
        if content:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def _llm_strip_scene(dialogue: str, llm_client) -> dict[str, Any]:
    """Call LLM to strip scene/action, keep emotional register. Up to 3 attempts."""
    for attempt in range(3):
        try:
            raw = await llm_client.chat(
                messages=[
                    {"role": "system", "content": _STRIP_SCENE_SYSTEM},
                    {"role": "user", "content": f"梦境对话：\n{dialogue[:2000]}"},
                ],
                max_tokens_override=400,
            )
            cleaned = re.sub(r"```json|```", "", raw).strip()
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"[dream_summary] LLM attempt {attempt + 1} failed: {e}")
    return {}


def _decide_afterglow_type(exit_type: str) -> str:
    """Narrative difference only — no system penalty."""
    if exit_type == "hard_exit":
        return "hurt_reluctance"
    return "gentle_residue"


def _ensure_list(val: Any) -> list:
    if isinstance(val, list):
        return val
    return []
