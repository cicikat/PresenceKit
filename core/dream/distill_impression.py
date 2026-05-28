"""
Dream → impression distiller.

Called after DREAM_CLOSING (soft or hard), after generate_summary.
Reads the archived dream log, asks the LLM to emit ONLY emotional register
+ at most one symbolic fragment — all scene / action / world-setting / body
tokens are structurally prohibited by the LLM prompt (I2).

Because stripping is structural (by construction at generation time), 叶瑄
holds no scene fact in the impression text. Even if he echoes the impression
in a reality turn and that turn is captured normally, there is nothing to
launder into episodic / identity stores.

Failure contract: warning log, no raise, does not block exit or summary (C7).
Write path: impression_store only — never touches any reality memory store (I4).
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WEIGHT_MIN = 0.2
_WEIGHT_MAX = 0.4
_DECAY_DAYS = 30

_DISTILL_SYSTEM = """\
你是梦境情感提炼器（极严格剥离版）。

从梦境对话中提炼【一种】情绪体验。

绝对禁止输出：
- 任何场景词（房间、街道、学校、森林、床、地板……）
- 任何动作词（握住、拥抱、推开、靠近、触碰……）
- 任何世界设定词（ABO、Alpha、Omega、吸血鬼、异能、番位……）
- 任何身体/数值词（体温、心跳、数字、arousal、hormone……）
- 任何"发生了什么"的事实陈述

只允许：
- 情绪质地（被包裹、漂浮、刺痛、温热、沉重、空旷……）
- 情绪流向（靠近、疏离、融合、破碎……）
- 至多【一个】已去除场景信息的符号碎片（纯感官/纯感受词，不得携带世界观信息）

输出必须以叶瑄自述口吻写，开头固定为"我好像在梦里……"，全文不超过20字。
若该梦情绪过于平淡（无值得留下的情绪印象），输出 impression_text 为空字符串。

输出纯 JSON，不加任何其他文字：
{
  "impression_text": "我好像在梦里……（情绪描述，≤20字）或空字符串",
  "emotional_tags": ["情绪词1", "情绪词2"],
  "weight": 0.2到0.4之间的小数
}"""


async def distill_impression(uid: str, dream_id: str, exit_type: str) -> None:
    """Top-level entry — failure is silently downgraded to a warning."""
    try:
        await _distill(uid, dream_id, exit_type)
    except Exception as e:
        logger.warning(
            f"[distill_impression] failed uid={uid} dream_id={dream_id}: {e}"
        )


async def _distill(uid: str, dream_id: str, exit_type: str) -> None:
    from core.sandbox import get_paths
    from core import llm_client
    from core.dream.impression_store import append_impression

    archive_path = get_paths().dreams_archive_dir() / f"dream_{dream_id}.jsonl"
    turns = _load_archive(archive_path)
    if not turns:
        logger.info(f"[distill_impression] empty archive uid={uid}, skip")
        return

    dialogue = _format_dialogue(turns)
    data = await _llm_distill(dialogue, llm_client)

    impression_text = (data.get("impression_text") or "").strip().strip('"')
    if not impression_text:
        logger.info(f"[distill_impression] empty result uid={uid}, no impression written")
        return

    # Depth-defense second layer: strip world vocab (承重墙仍是 store 隔离)
    try:
        from core.dream.dream_state import read_state as _read_ds
        _world_id = _read_ds(uid).get("frozen_world", "reality_derived")
        from core.dream.world_loader import strip_vocab as _strip_vocab
        impression_text = _strip_vocab(impression_text, _world_id)
    except Exception:
        pass  # depth defense failure is non-fatal

    weight = float(data.get("weight") or _WEIGHT_MIN)
    weight = max(_WEIGHT_MIN, min(_WEIGHT_MAX, weight))

    now = time.time()
    entry = {
        "dream_id": dream_id,
        "ts": now,
        "last_decay_ts": now,
        "impression_text": impression_text,
        "weight": round(weight, 4),
        "emotional_tags": _ensure_list(data.get("emotional_tags")),
        "exit_type": exit_type,
        "decay_after": now + _DECAY_DAYS * 86400,
        "marked": True,
    }

    append_impression(uid, entry)
    logger.info(f"[distill_impression] written uid={uid} dream_id={dream_id}")


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


async def _llm_distill(dialogue: str, llm_client) -> dict[str, Any]:
    for attempt in range(3):
        try:
            raw = await llm_client.chat(
                messages=[
                    {"role": "system", "content": _DISTILL_SYSTEM},
                    {"role": "user", "content": f"梦境对话：\n{dialogue[:1500]}"},
                ],
                max_tokens_override=200,
            )
            cleaned = re.sub(r"```json|```", "", raw).strip()
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"[distill_impression] LLM attempt {attempt + 1} failed: {e}")
    return {}


def _ensure_list(val: Any) -> list:
    if isinstance(val, list):
        return val
    return []
