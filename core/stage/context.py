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


def render_projection_segment(stage: Stage, transcript: list[TranscriptEntry]) -> str:
    lines: list[str] = []
    for entry in transcript:
        speaker = "owner" if entry.speaker_id == "owner" else get_char_name(entry.speaker_id)
        lines.append(f"{speaker}：{entry.content}")
    return "\n".join(lines)
