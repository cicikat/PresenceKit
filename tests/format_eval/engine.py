"""Brief 73 的 case loader、断言器与在线配对 A/B 计算。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from core.memory.short_term import DEFAULT_SEGMENT_MIN_LEN
from core.output.segment_enforcer import enforce_paragraph_breaks
from tests.format_eval.scorer import FormatMetrics, ReplyScore, score_corpus, score_reply

CASES_DIR = Path(__file__).parent / "cases"
ONLINE_PROMPTS_PATH = Path(__file__).parent / "online_prompts.yaml"


@dataclass(frozen=True)
class OnlineRow:
    enforce: str
    metrics: FormatMetrics
    fallback_trigger_count: int
    fallback_trigger_ratio: float


def load_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data:
            continue
        data.setdefault("id", path.stem)
        assert data["id"] == path.stem, (
            f"case id({data['id']}) 与文件名({path.stem})不一致"
        )
        assert isinstance(data.get("reply"), str), f"{path.name}: reply 必须是字符串"
        assert set((data.get("expect") or {})) == {
            "strict", "loose", "paragraph_count", "unsegmented",
        }, f"{path.name}: expect 字段不完整"
        cases.append(data)
    return cases


def run_case(case: dict) -> ReplyScore:
    return score_reply(
        case["reply"],
        min_len=int(case.get("min_len", DEFAULT_SEGMENT_MIN_LEN)),
    )


def check_expectations(case: dict, result: ReplyScore) -> list[str]:
    expect = case["expect"]
    actual = {
        "strict": result.strict_compliant,
        "loose": result.loose_compliant,
        "paragraph_count": result.paragraph_count,
        "unsegmented": result.unsegmented,
    }
    return [
        f"{key} expected {expected!r}, got {actual[key]!r}"
        for key, expected in expect.items()
        if actual[key] != expected
    ]


def load_online_prompts() -> list[str]:
    data = yaml.safe_load(ONLINE_PROMPTS_PATH.read_text(encoding="utf-8")) or {}
    prompts = data.get("prompts") or []
    if not prompts or not all(isinstance(prompt, str) and prompt.strip() for prompt in prompts):
        raise ValueError(f"{ONLINE_PROMPTS_PATH}: prompts 必须是非空字符串列表")
    return prompts


def build_online_rows(
    raw_replies: Iterable[str],
    *,
    min_len: int = DEFAULT_SEGMENT_MIN_LEN,
) -> list[OnlineRow]:
    """用同一批原始回复做配对 A/B，隔离模型随机性且不改运行时配置。"""
    raw = list(raw_replies)
    enforced = [enforce_paragraph_breaks(reply, min_len=min_len) for reply in raw]
    triggered = sum(before != after for before, after in zip(raw, enforced, strict=True))
    total = len(raw)
    return [
        OnlineRow(
            enforce="off",
            metrics=score_corpus(raw, mode="strict", min_len=min_len),
            fallback_trigger_count=0,
            fallback_trigger_ratio=0.0,
        ),
        OnlineRow(
            enforce="on",
            metrics=score_corpus(enforced, mode="strict", min_len=min_len),
            fallback_trigger_count=triggered,
            fallback_trigger_ratio=(triggered / total if total else 0.0),
        ),
    ]


async def generate_online_replies(
    *,
    n: int,
    char_id: str,
    prompts: list[str] | None = None,
) -> list[str]:
    """走真实 ``Pipeline.run_llm`` 生成原始回复，不进入任何记忆写路径。"""
    if not char_id or not char_id.strip():
        raise ValueError("online format eval requires explicit char_id")
    if n < 1:
        raise ValueError("n must be >= 1")

    from core import character_loader
    from core.lore_engine import LoreEngine
    from core.pipeline import Pipeline

    character = character_loader.load(char_id)
    lore_engine = LoreEngine()
    lore_engine.load()
    if character.world_book:
        lore_engine.load_entries(character.world_book)
    pipeline = Pipeline(character, lore_engine, active_character_id=char_id)
    online_prompts = prompts or load_online_prompts()

    replies: list[str] = []
    for index in range(n):
        prompt = online_prompts[index % len(online_prompts)]
        messages = [
            {
                "role": "system",
                "content": (
                    f"你是{character.name}。{character.description or ''}\n"
                    "请像日常聊天一样自然回复用户，不要解释评测或输出元信息。"
                ).strip(),
            },
            {"role": "user", "content": prompt},
        ]
        reply = await pipeline.generation.run_llm(messages, char_id=char_id)
        replies.append(reply or "")
    return replies


def generate_online_replies_sync(**kwargs) -> list[str]:
    return asyncio.run(generate_online_replies(**kwargs))
