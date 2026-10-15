"""Dream-only Scenario prompt profile and character projection.

The Scenario profile is deliberately a projection layer. It never mutates a
Character and never changes Reality prompt consumers. The profile keeps only
the authored identity/action material that a scripted stage needs; general
Dream world and body layers remain explicitly excluded.
"""
from __future__ import annotations

from core.prompt_language import text as _pt

from typing import Any

SCENARIO_PROMPT_PROFILE = "scenario"
SCENARIO_PROMPT_PROFILE_VERSION = "v2"
SCENARIO_PROFILE_EXCLUDED_LAYERS: frozenset[str] = frozenset({
    "D2_world_ruleset",
    "D3_mes_example",
    "D4_frozen_reality",
    "D4.5_hidden_state",
    "D5_body_projection",
    "D6_scene_anchors",
    "D7_dream_tension",
    "DM_mirror",
    "D_lorebook",
})

_SCENARIO_IDENTITY_LIMIT = 1800

SCENARIO_DIRECTOR = """剧本模式导演注记（D8S）：
· 保持 say/do/env/feel 的既有输出格式；只描写角色自己的可见行动、台词、环境和感受。
· 当前阶段的公开立场必须落实为改变现场状态、位置、信息、资源、限制或选择空间的具体行动。
· 只有口头威胁、重复警告、气氛描写或空泛宣言不算推进；除非当前阶段明确要求观察或停顿，本轮至少完成一个具体行动。
· 不替 DS 决定角色立场、完成条件、下一阶段，也不替用户决定动作、感受或台词。
· 不透露后续阶段、私密真相的未授权部分或系统控制字段；控制块只使用当前 DS 提供的短 ID。
· 角色卡的 scenario_directive 只补充本剧本角色的表达/行动边界，不得覆盖当前 stage 的任务与约束。"""


def _bounded_text(value: Any, limit: int = _SCENARIO_IDENTITY_LIMIT) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text[:limit] if text else ""


def scenario_identity_projection(character: Any) -> str:
    """Return an authored, bounded Scenario-only identity projection.

    ``scenario_identity`` is preferred. Legacy cards fall back to bounded
    ``description`` + ``personality`` only; system_prompt, Reality scenario,
    and post-history instructions are intentionally never read.
    """
    try:
        ext = getattr(character, "presence_ext", {}) or {}
        behavior = ext.get("dream_behavior") if isinstance(ext, dict) else None
        behavior = behavior if isinstance(behavior, dict) else {}
        authored = _bounded_text(behavior.get("scenario_identity"))
        if authored:
            return authored

        parts: list[str] = []
        description = _bounded_text(getattr(character, "description", ""))
        personality = _bounded_text(getattr(character, "personality", ""))
        if description:
            parts.append((_pt('外貌与基础设定：\n') + f'{description}'))
        if personality:
            parts.append((_pt('基础性格与说话方式：\n') + f'{personality}'))
        return "\n\n".join(parts)
    except Exception:
        return ""


def scenario_profile_layer_note(layer_id: str) -> str:
    """Return the stable inspector note for a profile-excluded layer."""
    return "scenario_profile" if layer_id in SCENARIO_PROFILE_EXCLUDED_LAYERS else ""
