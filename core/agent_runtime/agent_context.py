"""Bounded Reality context for the same character's work decisions."""
from core import character_loader, character_self, config_loader
from core.agent_runtime.models import TaskPrincipal
from core.memory import event_log, user_facts, user_identity
from core.prompt_sections import character_description
from core.sensitive_redaction import redact_for_export


async def build_agent_context(actor: TaskPrincipal, query: str) -> dict[str, str]:
    if actor.realm != "reality":
        raise ValueError("realm_forbidden")
    character = character_loader.load(actor.char_id)
    uid, char_id = actor.uid, actor.char_id
    identity = await user_identity.format_for_prompt(uid, char_id=char_id)
    recent = await event_log.search(uid, query, char_id=char_id)
    return {
        "character_name": character.name[:100],
        "character_rules": redact_for_export(character.system_prompt.replace("{perception_block}", ""))[:2200],
        "character_description": redact_for_export(character_description(character, max_field_chars=800)),
        "user_name": config_loader.get_user_display_name() or user_facts.get_user_pronoun(uid, char_id=char_id),
        "user_facts": redact_for_export(user_facts.format_for_prompt(uid, char_id=char_id))[:800],
        "relationship_observations": redact_for_export(identity)[:800],
        "related_reality_history": redact_for_export(recent)[:1100],
        "working_habits": str(character_self.load_agent_md_snapshot(uid, char_id).get("content") or "")[:500],
    }
