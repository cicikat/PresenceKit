"""PromptLayer — lightweight typed descriptor for a single prompt message.

Internal to the project.  Must never be serialised and sent to an LLM API as-is.
Use prompt_layer_to_message() to get a prompt-builder-compatible dict (includes
the internal ``_layer`` key), or sanitize_messages() to produce API-safe dicts
(strips all underscore-prefixed internal keys before the network call).

R4-A: introduces the type + LLM boundary sanitize.
R4-B: will enforce drop_priority on all droppable layers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptLayer:
    """Immutable descriptor for one prompt layer.

    Attributes:
        name:          Unique layer id, e.g. ``"6c_episodic"``.
        content:       Text content to be sent to the LLM.
        role:          OpenAI message role — ``"system"``, ``"user"``, or ``"assistant"``.
        drop_priority: Lower value = dropped first when the token budget is exceeded.
                       ``None`` means the layer is never auto-dropped.
                       R4-B will require all droppable layers to declare this.
    """

    name: str
    content: str
    role: str = "system"
    drop_priority: int | None = None


def prompt_layer_to_message(layer: PromptLayer) -> dict:
    """Convert a PromptLayer to a message dict for prompt_builder.

    The returned dict contains ``_layer`` so the existing token-pruning logic
    can identify and drop this message.  Call sanitize_messages() on the full
    list before handing it to the LLM API.
    """
    return {
        "role": layer.role,
        "content": layer.content,
        "_layer": layer.name,
    }


def sanitize_messages(messages: list[dict]) -> list[dict]:
    """Return a new list of API-safe message dicts.

    Strips every key whose name starts with ``_`` (internal fields such as
    ``_layer``, ``_debug``, ``_drop_priority``).  Never mutates the original
    list or any of the original dicts.
    """
    return [
        {k: v for k, v in m.items() if not k.startswith("_")}
        for m in messages
    ]
