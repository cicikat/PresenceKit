from tests.fixtures.runtime_capabilities import read_registered_character
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID
import asyncio
import json

from core.prompt_asset_selection import read_document, update, for_character
from core.prompt_builder import _load_jailbreak
from core.lore_engine import LoreEngine
from admin.routers import jailbreak_entries, lorebook
from scripts.migrate_character_user_facts import migrate


def test_switch_preserves_owned_asset_bindings(sandbox):
    document = {"active_character": TEST_CHAR_ID, "enabled_lorebooks": ["alpha-book"], "enabled_jailbreaks": ["alpha-prompt"]}
    result = update(document, {"active_character": TEST_PEER_CHAR_ID})
    assert result["enabled_lorebooks"] == []
    assert result["enabled_jailbreaks"] == []
    assert for_character(TEST_CHAR_ID, result)["enabled_lorebooks"] == ["alpha-book"]
    result = update(result, {"enabled_lorebooks": ["beta-book"]})
    assert update(result, {"active_character": TEST_CHAR_ID})["enabled_lorebooks"] == ["alpha-book"]


def test_managed_entries_crud_and_prompt_do_not_cross_characters(sandbox):
    asyncio.run(jailbreak_entries.add_entry(jailbreak_entries.JbEntry(title="Private", content="Alpha private"), char_id=TEST_CHAR_ID, auth=None))
    asyncio.run(lorebook.add_lore_entry(lorebook.LoreEntry(keyword=["secret"], content="Alpha lore"), char_id=TEST_CHAR_ID, auth=None, read_character=read_registered_character))
    assert _load_jailbreak(char_id=TEST_CHAR_ID) == "Alpha private"
    assert _load_jailbreak(char_id=TEST_PEER_CHAR_ID) == ""
    assert asyncio.run(jailbreak_entries.get_entries(char_id=TEST_PEER_CHAR_ID, auth=None)) == {"entries": [], "char_id": TEST_PEER_CHAR_ID}
    engine = LoreEngine()
    engine.load(char_id=TEST_CHAR_ID)
    assert engine.match("secret") == ["Alpha lore"]
    engine.load(char_id=TEST_PEER_CHAR_ID)
    assert engine.match("secret") == []


def test_legacy_prompt_only_imported_with_explicit_owner(sandbox):
    source = sandbox.legacy_prompt_entries("jailbreak_entries")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'{"entries":[{"content":"private"}]}')
    assert _load_jailbreak(char_id=TEST_CHAR_ID) == ""
    assert migrate("", TEST_CHAR_ID, artifact="jailbreak_entries")["status"] == "dry_run"
    assert migrate("", TEST_CHAR_ID, artifact="jailbreak_entries", apply=True)["status"] == "imported"
    assert _load_jailbreak(char_id=TEST_CHAR_ID) == "private"
    assert _load_jailbreak(char_id=TEST_PEER_CHAR_ID) == ""
    assert json.loads(source.read_text(encoding="utf-8"))["entries"][0]["content"] == "private"


def test_lore_http_edit_replaces_engine_without_mutating_old_snapshot(sandbox, monkeypatch):
    """A live authored edit preserves in-flight lore and retains embedded card entries."""
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.character_dependencies import require_character_reader, require_character_reloader
    from core.character_session import CharacterSession
    from core.safe_write import safe_write_json

    safe_write_json(sandbox.active_prompt_assets(), {
        "active_character": TEST_CHAR_ID, "enabled_lorebooks": [], "enabled_jailbreaks": [],
    })
    card = SimpleNamespace(world_book=[{"keywords": ["card-key"], "content": "embedded lore"}])
    state = CharacterSession(card, LoreEngine(card.world_book), TEST_CHAR_ID)
    old = state.context_snapshot()
    monkeypatch.setattr("core.character_loader.load", lambda char_id: card)
    app = FastAPI()
    app.include_router(lorebook.router)
    monkeypatch.setenv("YEXUAN_ADMIN_SECRET", "test-only-lore")
    app.dependency_overrides[require_character_reader] = lambda: state.context_snapshot
    app.dependency_overrides[require_character_reloader] = lambda: state.reload_character
    with TestClient(app) as client:
        result = client.post("/lorebook", headers={"Authorization": "Bearer test-only-lore"}, params={"char_id": TEST_CHAR_ID}, json={
            "keyword": ["edited-key"], "content": "edited lore",
        })
    assert result.status_code == 200, result.text
    assert old.lore_engine.match("card-key edited-key") == ["embedded lore"]
    assert state.lore_engine.match("card-key edited-key") == ["edited lore", "embedded lore"]
