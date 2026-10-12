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
    asyncio.run(lorebook.add_lore_entry(lorebook.LoreEntry(keyword=["secret"], content="Alpha lore"), char_id=TEST_CHAR_ID, auth=None))
    assert _load_jailbreak(char_id=TEST_CHAR_ID) == "Alpha private"
    assert _load_jailbreak(char_id=TEST_PEER_CHAR_ID) == ""
    assert asyncio.run(jailbreak_entries.get_entries(char_id=TEST_PEER_CHAR_ID, auth=None)) == {"entries": []}
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
