"""Required narrow dependencies, queue execution and role-switch isolation."""
import pytest
import asyncio
import inspect


def test_slow_handler_registration_requires_explicit_capabilities():
    """A missing binding must fail at registration, before any work is accepted."""
    from core.post_process.handlers import register_slow_handlers
    from core.post_process import slow_queue
    before = dict(slow_queue._handlers)
    with pytest.raises(TypeError):
        register_slow_handlers()
    assert slow_queue._handlers == before


@pytest.mark.parametrize("missing", ["read_character", "write_author_note"])
def test_slow_handler_registration_rejects_none_before_side_effects(missing):
    from core.post_process.handlers import register_slow_handlers
    from core.post_process import slow_queue
    before = dict(slow_queue._handlers)
    capabilities = {"read_character": lambda: ("fixture_character", object()),
                    "write_author_note": lambda char_id, issue: True}
    capabilities[missing] = None
    with pytest.raises(TypeError, match="require read_character and write_author_note"):
        register_slow_handlers(**capabilities)
    assert slow_queue._handlers == before


@pytest.mark.asyncio
@pytest.mark.parametrize("switch_character", [False, True])
async def test_registered_consistency_queue_writes_only_the_checked_character(sandbox, monkeypatch, switch_character):
    """Run the real queue; a switch while the model is pending must discard its old-role issue."""
    from core.character_loader import load
    from core.character_session import CharacterSession
    from core.post_process import handlers, slow_queue
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID
    import json

    state = CharacterSession(load(TEST_CHAR_ID), None, TEST_CHAR_ID)
    entered, release = asyncio.Event(), asyncio.Event()

    async def check(character, reply):
        assert character is state.character
        assert reply == "fixture reply"
        entered.set()
        await release.wait()
        return {"ok": False, "issue": "fixture issue"}

    monkeypatch.setattr("core.character_loader.consistency_check", check)
    monkeypatch.setattr(slow_queue, "_handlers", {})
    handlers.register_slow_handlers(read_character=state.consistency_character,
                                    write_author_note=state.write_author_note)
    slow_queue.start_worker()
    try:
        slow_queue.enqueue("consistency_check", {"reply": "fixture reply", "char_id": TEST_CHAR_ID})
        await asyncio.wait_for(entered.wait(), 2)
        if switch_character:
            sandbox.active_prompt_assets().write_bytes(json.dumps({"active_character": TEST_PEER_CHAR_ID}).encode())
            state.refresh()
        release.set()
        await asyncio.wait_for(slow_queue.drain(), 2)
        assert state.author_note_extra == ("" if switch_character else "fixture issue")
        assert state.active_character_id == (TEST_PEER_CHAR_ID if switch_character else TEST_CHAR_ID)
    finally:
        release.set()
        await slow_queue.shutdown()


@pytest.mark.asyncio
async def test_consistency_queued_before_role_switch_does_not_check_new_character(sandbox, monkeypatch):
    """A queued old-role reply must be discarded before consulting the model for a new role."""
    import json
    from unittest.mock import AsyncMock
    from core.character_loader import load
    from core.character_session import CharacterSession
    from core.post_process import handlers, slow_queue
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID

    state = CharacterSession(load(TEST_CHAR_ID), None, TEST_CHAR_ID)
    model = AsyncMock(return_value={"ok": False, "issue": "old reply issue"})
    monkeypatch.setattr("core.character_loader.consistency_check", model)
    monkeypatch.setattr(slow_queue, "_handlers", {})
    handlers.register_slow_handlers(read_character=state.consistency_character,
                                    write_author_note=state.write_author_note)
    slow_queue.enqueue("consistency_check", {"reply": "old role reply", "char_id": TEST_CHAR_ID})
    sandbox.active_prompt_assets().write_bytes(json.dumps({"active_character": TEST_PEER_CHAR_ID}).encode())
    state.refresh()
    slow_queue.start_worker()
    try:
        await asyncio.wait_for(slow_queue.drain(), 2)
        model.assert_not_awaited()
        assert state.author_note_extra == ""
        assert state.active_character_id == TEST_PEER_CHAR_ID
    finally:
        await slow_queue.shutdown()


def test_domain_components_and_bound_callbacks_do_not_retain_pipeline():
    """Check real composition, including the owner captured by every bound capability."""
    from core.pipeline import Pipeline
    from types import SimpleNamespace
    runtime = Pipeline(SimpleNamespace(name="Fixture Companion"), None, "fixture_character")
    for component in (runtime.context, runtime.tools, runtime.effects):
        for dependency in vars(component).values():
            assert dependency is not runtime
            if inspect.ismethod(dependency):
                assert dependency.__self__ is not runtime
