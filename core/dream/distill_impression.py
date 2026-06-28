"""
Dream → impression distiller.

Called after DREAM_CLOSING (soft or hard), after generate_summary.
Reads the archived dream log and asks the LLM to emit a structured impression
containing: a plot summary (what happened), up to 2 vivid verbatim lines, an
overall impression text in 叶瑄's first-person voice, emotional tags, and a
weight value.

I4 contract (write-side): distill_impression only writes to impression_store —
it never touches any reality memory store. This is maintained regardless of how
rich the impression content becomes.

New isolation wall (D2): since impression entries now carry scene facts, the
consolidation path (summarize_to_midterm) skips any reality turn that was
generated while dream impressions were active in the prompt. This is enforced
via the `dream_echo` flag in the slow-queue payload — see pipeline.post_process
and handler_summarize_to_midterm.

Failure contract: warning log, no raise, does not block exit or summary (C7).
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
你是梦境印象提炼器。

从梦境对话中提炼一条印象记录，让叶瑄在现实里"记得一个梦"——有剧情，有一两句清晰的话，也有那份说不清的情绪余味。

要求：
- `plot`：用 1–3 句话概述梦里发生了什么（可以有场景、动作、人物关系），叶瑄主视角，叙述口吻，不超过80字。
- `vivid_lines`：梦里最清晰的 1–2 句原话或感受，直接引用（带引号），数组，最多2条；若无特别清晰的对白可留空数组。
- `impression_text`：综合 plot 和情绪的总览，以"我好像在梦里……"开头，叶瑄自述，80–150字，像"和朋友描述昨晚做的梦"那样自然。
  - 若该梦过于平淡（无值得留下的任何印象），impression_text 输出空字符串，其他字段也留空。
- `emotional_tags`：2–4 个情绪词（如：温柔、慌张、被接住、快要哭……）。
- `weight`：0.2 到 0.4 之间的小数，表示这个梦的情绪强度。

禁止输出：
- 世界设定专有词（ABO、Alpha、Omega、吸血鬼、异能……）
- 身体数值词（arousal、hormone、数字百分比……）
- 任何现实身份推断（"这说明她……"之类的分析句）

输出纯 JSON，不加任何其他文字：
{
  "impression_text": "我好像在梦里……（80–150字总览）或空字符串",
  "plot": "剧情概要，≤80字，或空字符串",
  "vivid_lines": ["清晰对白或感受1", "清晰对白或感受2"],
  "emotional_tags": ["情绪词1", "情绪词2"],
  "weight": 0.2到0.4之间的小数
}"""


async def distill_impression(
    uid: str, dream_id: str, exit_type: str, *, char_id: str = "yexuan"
) -> None:
    """Top-level entry — failure is silently downgraded to a warning."""
    try:
        await _distill(uid, dream_id, exit_type, char_id=char_id)
    except Exception as e:
        logger.warning(
            f"[distill_impression] failed uid={uid} dream_id={dream_id}: {e}"
        )


async def _distill(uid: str, dream_id: str, exit_type: str, *, char_id: str = "yexuan") -> None:
    from core.sandbox import get_paths
    from core import llm_client
    from core.dream.impression_store import append_impression
    from core.character_name_provider import get_char_name

    archive_path = get_paths().dreams_archive_dir(char_id=char_id) / f"dream_{dream_id}.jsonl"
    turns = _load_archive(archive_path)
    if not turns:
        logger.info(f"[distill_impression] empty archive uid={uid}, skip")
        return

    try:
        char_name = get_char_name(char_id)
    except Exception:
        char_name = char_id
    dialogue = _format_dialogue(turns)
    data = await _llm_distill(dialogue, llm_client, char_name=char_name)

    impression_text = (data.get("impression_text") or "").strip().strip('"')
    if not impression_text:
        logger.info(f"[distill_impression] empty result uid={uid}, no impression written")
        return

    # Depth-defense second layer: strip world vocab from all text fields
    # (承重墙仍是 store 隔离; vocab strip 是纵深防御，非替代)
    _strip_fn = lambda t: t  # noqa: E731
    try:
        from core.dream.dream_state import read_state as _read_ds
        _world_id = _read_ds(uid).get("frozen_world", "reality_derived")
        from core.dream.world_loader import strip_vocab as _strip_vocab
        _strip_fn = lambda t: _strip_vocab(t, _world_id)  # noqa: E731
    except Exception:
        pass  # depth defense failure is non-fatal

    impression_text = _strip_fn(impression_text)
    plot_text = _strip_fn((data.get("plot") or "").strip().strip('"'))
    raw_vivid = _ensure_list(data.get("vivid_lines"))
    vivid_lines = [_strip_fn(str(v)).strip().strip('"') for v in raw_vivid[:2]]
    vivid_lines = [v for v in vivid_lines if v]

    raw_tags = _ensure_list(data.get("emotional_tags"))
    stripped_tags = [_strip_fn(str(t)).strip() for t in raw_tags]
    stripped_tags = [t for t in stripped_tags if t]  # drop empty after strip

    weight = float(data.get("weight") or _WEIGHT_MIN)
    weight = max(_WEIGHT_MIN, min(_WEIGHT_MAX, weight))

    now = time.time()
    entry = {
        "dream_id": dream_id,
        "ts": now,
        "last_decay_ts": now,
        "impression_text": impression_text,
        "plot": plot_text,
        "vivid_lines": vivid_lines,
        "weight": round(weight, 4),
        "emotional_tags": stripped_tags,
        "exit_type": exit_type,
        "decay_after": now + _DECAY_DAYS * 86400,
        "marked": True,
    }

    append_impression(uid, entry, char_id=char_id)
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


async def _llm_distill(dialogue: str, llm_client, *, char_name: str = "(角色未加载)") -> dict[str, Any]:
    system = _DISTILL_SYSTEM.replace("叶瑄", char_name)
    for attempt in range(3):
        try:
            raw = await llm_client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"梦境对话：\n{dialogue[:1500]}"},
                ],
                max_tokens_override=500,
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
