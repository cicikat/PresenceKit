"""Prompt-facing views of a shared Stage transcript."""
from __future__ import annotations

from core.character_name_provider import get_char_name
from core.stage.models import Stage, TranscriptEntry


def render_presence(stage: Stage, *, viewer_id: str, chain_reply: bool = False) -> str:
    me = get_char_name(viewer_id)
    others = [get_char_name(c) for c in stage.roster if c != viewer_id]
    joined = "、".join(others) if others else "没有其他角色"
    text = (
        "【群聊在场感】\n"
        f"现在你进入了群聊，你是「{me}」，在场的还有：{joined}。"
        "你说的话其他在场角色也看得到。\n"
        f"保持「{me}」自己的性格、说话方式和立场——"
        "看到别人怎么说，不要趋同、不要附和复述别人刚说过的话；"
        "有不同就表达不同，有自己的角度就说出来。"
        "你可以直接回应、反驳、补充或调侃其他角色（点名也行），"
        "像真实的多人对话那样彼此接话，而不是各说各的。"
    )
    if chain_reply:
        text += "\n你正在回应上一位角色：回应但不要复述或简单附和上一位的话，说出你自己的看法或岔开。"
    try:
        from core.stage.char_relations import load_relation

        relation_lines: list[str] = []
        for index, char_a in enumerate(stage.roster):
            for char_b in stage.roster[index + 1:]:
                relation = load_relation(char_a, char_b)
                if not relation:
                    continue
                name_a, name_b = get_char_name(char_a), get_char_name(char_b)
                a_of_b = relation.get("a_of_b", {}).get("summary", "")
                b_of_a = relation.get("b_of_a", {}).get("summary", "")
                if a_of_b:
                    relation_lines.append(f"{name_a}对{name_b}的印象：{a_of_b}")
                if b_of_a:
                    relation_lines.append(f"{name_b}对{name_a}的印象：{b_of_a}")
                moments = relation.get("recent_moments") or []
                if moments:
                    relation_lines.append(f"{name_a}和{name_b}之间的往事：{moments[-1]}")
        if relation_lines:
            text += "\n\n【角色间既有印象】\n" + "\n".join(relation_lines)
    except Exception:
        # Presence context is optional and must never block a Stage turn.
        pass
    try:
        from core.stage.private_exchange import read_presence_hint

        hint = read_presence_hint(viewer_id)
        if hint:
            text += f"\n\n（{hint}。）"
    except Exception:
        pass
    return text


def render_transcript(
    stage: Stage,
    transcript: list[TranscriptEntry],
    *,
    viewer_id: str,
    limit: int = 40,
    current_turn_id: str | None = None,
) -> str:
    lines: list[str] = []
    last_other: tuple[str, str] | None = None
    for entry in transcript[-limit:]:
        content = entry.content
        if entry.speaker_id == "owner":
            speaker = "owner"
        else:
            # Prompt views are intentionally lossy for every AI line. The shared
            # transcript and delivery retain the exact original text.
            from core.memory.short_term import _sanitize_assistant_message

            content = _sanitize_assistant_message(entry.content, stage.owner_uid)
            if entry.speaker_id == viewer_id:
                speaker = "你"
            else:
                speaker = get_char_name(entry.speaker_id)
                last_other = (speaker, content)
        if entry.speaker_id == viewer_id:
            speaker = "你"
        fresh = (
            current_turn_id is not None
            and entry.turn_id == current_turn_id
            and entry.speaker_id not in ("owner", viewer_id)
        )
        prefix = "（刚说）" if fresh else ""
        lines.append(f"{prefix}{speaker}：{content}")
    text = "\n".join(lines)
    if last_other is not None:
        name, content = last_other
        snippet = content[:30]
        text += (
            f"\n\n（注意：{name} 刚说了「{snippet}…」。"
            "别复述或简单附和这句，从你自己的立场出发回应或另起话头。）"
        )
    return text


def render_private_presence(_viewer_id: str, _other_id: str) -> str:
    """§2 私下语域框定层 — the two required system lines for a private exchange (Brief 86).

    Both are mandatory. Line 1 licenses the private register; line 2 is the
    anti-drift anchor (DESIGN.md §十一 决策 9.5) — without it, "the owner can't
    see this" reliably drifts into conspiratorial narrative that then leaks
    back into the shared char_relations layer via the round's reflow.
    """
    from core.config_loader import get_user_display_name

    user_name = get_user_display_name() or "TA"
    return (
        f"这段对话{user_name}看不到，不需要表演给任何人，用你们私下的语气。\n"
        f"私下语域不等于秘密——你们不讨论对{user_name}隐瞒什么，也不形成针对任何人的共识。"
        "就是两个熟人闲聊。"
    )


def render_private_transcript(turns: list[tuple[str, str]], *, viewer_id: str) -> str:
    lines = []
    for speaker_id, content in turns:
        speaker = "你" if speaker_id == viewer_id else get_char_name(speaker_id)
        lines.append(f"{speaker}：{content}")
    return "\n".join(lines)


def render_projection_segment(stage: Stage, transcript: list[TranscriptEntry]) -> str:
    from core.config_loader import get_user_display_name

    lines: list[str] = []
    for entry in transcript:
        speaker = get_user_display_name() if entry.speaker_id == "owner" else get_char_name(entry.speaker_id)
        lines.append(f"{speaker}：{entry.content}")
    return "\n".join(lines)
