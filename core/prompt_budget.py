"""Measure and trim fully rendered prompt sections without accessing runtime state."""

from dataclasses import dataclass
from itertools import groupby

from core.prompt_sections import arrange_sections

SOFT_LIMIT = 15_000
HARD_LIMIT = 30_000
TRIM_TARGET = 28_000


@dataclass(frozen=True)
class PromptBudget:
    messages: list[dict]
    original_layers: list[str]
    original_chars: int
    final_chars: int
    removed_layers: list[str]


def apply_budget(messages: list[dict]) -> PromptBudget:
    """Keep priority batches atomic and measure section wrappers after each drop."""
    arranged = arrange_sections(messages)
    original_layers = [message.get("_report_layer") or message.get("_layer", "unknown") for message in arranged]
    original_chars = sum(len(message["content"]) for message in arranged)
    final_chars = original_chars
    removed_layers: list[str] = []
    if original_chars > HARD_LIMIT:
        prioritized = sorted(
            ((index, message) for index, message in enumerate(messages) if message.get("_drop_priority") is not None),
            key=lambda item: item[1]["_drop_priority"],
        )
        removed: set[int] = set()
        for _, group in groupby(prioritized, key=lambda item: item[1]["_drop_priority"]):
            for index, message in group:
                removed.add(index)
                removed_layers.append(message.get("_layer", "?"))
            arranged = arrange_sections([message for index, message in enumerate(messages) if index not in removed])
            final_chars = sum(len(message["content"]) for message in arranged)
            if final_chars <= TRIM_TARGET:
                break
    return PromptBudget(arranged, original_layers, original_chars, final_chars, removed_layers)
