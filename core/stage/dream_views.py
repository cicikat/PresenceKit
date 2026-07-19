"""
Per-character generation view for a group dream turn (Dream Stage).

Independent from `core.stage.views.StageCharacterView` by design (Brief 100
§2): the reality view's two `RuntimeError` guards stay untouched, and this
class never touches `Pipeline.fetch_context()` / `Pipeline.build_prompt()` or
any reality `MemoryScope` — it only reads the group dream's frozen shared
state and calls `core.dream.dream_prompt.build_dream_prompt()` +
`core.llm_client.chat()` directly, exactly like the solo dream pipeline does.
"""
from __future__ import annotations

import logging

from core.character_name_provider import get_char_name
from core.dream.body_projection import project_body_for_yexuan
from core.dream.body_state import BodyState
from core.dream.dream_pipeline import _load_presets_text
from core.dream.dream_prompt import build_dream_prompt
from core.dream.dream_state import DreamStatus
from core.dream.world_loader import load_dream_lore_entries, match_dream_lore
from core.stage.dream_settings import load as load_dream_group_settings, resolve_jailbreak_presets
from core.stage.dream_state import read_state as read_dream_group_state
from core.stage.models import Stage, TranscriptEntry

logger = logging.getLogger(__name__)


def _render_dg_layer(stage: Stage, *, viewer_id: str) -> str:
    """DG·梦内在场感: roster names + single-side pronoun contract extension."""
    me = get_char_name(viewer_id)
    others = [get_char_name(c) for c in stage.roster if c != viewer_id]
    joined = "、".join(others) if others else "没有其他角色"
    return (
        f"这是一场你们共同的梦，在场的角色有：{joined}。你是「{me}」。\n"
        "只演你自己这一轮：不替其他角色说话或做动作，不替用户（你称之为「你」）配台词，"
        "也不要用「她」称呼在场的其他角色。"
        "可以直接回应、接话或岔开其他角色刚说的话，像真实的多人对话一样。"
    )


def _render_group_dream_transcript(
    transcript: list[TranscriptEntry], *, viewer_id: str, limit: int = 30,
) -> str:
    """D9 shared transcript block — speaker-prefixed, no reality sanitizer (Brief 100 §2)."""
    lines: list[str] = []
    for entry in transcript[-limit:]:
        if entry.speaker_id == "owner":
            speaker = "你"
        elif entry.speaker_id == viewer_id:
            speaker = "我"
        else:
            speaker = get_char_name(entry.speaker_id)
        lines.append(f"{speaker}：{entry.content}")
    return "\n".join(lines)


class DreamStageCharacterView:
    """A scoped dream generation view. Never calls Pipeline.fetch_context/build_prompt."""

    def __init__(self, char_id: str):
        from core import character_loader

        self.char_id = char_id
        self._character = character_loader.load(char_id)

    async def generate(
        self,
        stage: Stage,
        transcript: list[TranscriptEntry],
        turn_id: str,
        triggered_by: str,
    ) -> str:
        if stage.domain != "dream":
            raise RuntimeError("DreamStageCharacterView cannot generate for reality domain")

        state = read_dream_group_state(stage.group_id)
        if state.get("status") not in (DreamStatus.DREAM_ACTIVE.value, DreamStatus.DREAM_CLOSING.value):
            raise RuntimeError(f"group dream not active: group={stage.group_id!r}")

        per_char_snapshots = state.get("per_char_snapshots") or {}
        snapshot = per_char_snapshots.get(self.char_id) or {}
        char_tension = float((state.get("char_tension") or {}).get(self.char_id, 0.0))
        body = BodyState.from_dict(state.get("body_state") or {})
        settings = load_dream_group_settings(stage.group_id)
        boundary_level = settings.get("boundary_level", "body_perceptible")
        projection = project_body_for_yexuan(body, boundary_level, char_tension)

        world_id = state.get("frozen_world", "reality_derived")
        dg_text = _render_dg_layer(stage, viewer_id=self.char_id)
        shared_block = _render_group_dream_transcript(transcript, viewer_id=self.char_id)

        lore_entries: list[str] = []
        if settings.get("enable_dream_lorebook", True):
            try:
                dream_lore = load_dream_lore_entries(world_id)
                if dream_lore:
                    recent_as_dicts = [{"content": e.content} for e in transcript[-6:]]
                    lore_entries = match_dream_lore(dream_lore, shared_block, recent_as_dicts)
            except Exception:
                logger.debug("[dream_views] group dream lorebook match skipped", exc_info=True)

        jailbreak_presets = resolve_jailbreak_presets(settings, self.char_id)
        jailbreak_text, jailbreak_status = _load_presets_text(jailbreak_presets)

        char_name = getattr(self._character, "name", None) or self.char_id
        if triggered_by in stage.roster and triggered_by != self.char_id:
            peer_name = get_char_name(triggered_by)
            instruction = (
                f"参考上面的共享梦境对话，你在回应{peer_name}刚才那句话。"
                f"说出你（{char_name}）此刻要说的下一句话，只输出这句话本身，"
                "不加称呼、不加引号、不重复你之前说过的内容。"
            )
        else:
            instruction = (
                f"参考上面的共享梦境对话，说出你（{char_name}）此刻要说的下一句话，"
                "只输出这句话本身，不加称呼、不加引号。"
            )

        _capture_data: dict = {}

        def _capture_hook(data: dict) -> None:
            _capture_data.update(data)

        messages = build_dream_prompt(
            character=self._character,
            user_id=stage.owner_uid,
            user_message=instruction,
            context_snapshot=snapshot,
            dream_history=[],
            local_state={
                "scene_state": state.get("scene_state"),
                "symbolic_anchors": list(state.get("symbolic_anchors") or []),
                "body_state": body.to_dict(),
            },
            lore_entries=lore_entries,
            jailbreak_text=jailbreak_text,
            jailbreak_preset_name=",".join(jailbreak_presets),
            jailbreak_preset_status=jailbreak_status,
            body_projection_text=projection["d5_text"],
            yexuan_tension=char_tension,
            world_id=world_id,
            lucid_mode="lucid_shared",
            dream_mode="sandbox",
            scenario_core=None,
            mirror_core=None,
            dream_domain="group",
            dg_layer_text=dg_text,
            shared_transcript_block=shared_block,
            _capture_hook=_capture_hook,
        )

        from core import llm_client

        reply = await llm_client.chat(messages, call_category="dream_stage", char_id=self.char_id)
        reply = (reply or "").strip()

        # ── Dream prompt capture (admin panel observer) ─────────────────────────
        # Reuses the solo-dream ring buffer keyed by owner_uid — `origin` is how
        # the admin panel tells group-dream turns apart from solo ones for the
        # same uid, mirroring how the reality prompt-layers viewer already
        # disambiguates group vs 1v1 turns (see _isStagePromptForGroup in
        # admin/static/index.html).
        if _capture_data:
            try:
                from core.observe.dream_capture import capture_dream, update_dream_llm_output

                _capture_data["user_message"] = instruction
                _capture_data["dream_id"] = state.get("dream_id")
                _capture_data["origin"] = {
                    "origin": "stage",
                    "group_id": stage.group_id,
                    "char_id": self.char_id,
                }
                capture_dream(stage.owner_uid, _capture_data)
                update_dream_llm_output(stage.owner_uid, reply)
            except Exception:
                logger.debug("[dream_views] dream capture failed", exc_info=True)

        return reply


class DreamStageViewRegistry:
    def __init__(self) -> None:
        self._views: dict[str, DreamStageCharacterView] = {}

    def get(self, char_id: str) -> DreamStageCharacterView:
        view = self._views.get(char_id)
        if view is None:
            view = DreamStageCharacterView(char_id)
            self._views[char_id] = view
        return view
