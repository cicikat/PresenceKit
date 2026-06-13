from __future__ import annotations

import asyncio
import json

from core.activity import dream_seed, transcript
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope


UID = "dream_seed_user"
CHAR = "dream_seed_char"


def _seed_path():
    return resolve_path(MemoryScope.reality_scope(UID, CHAR), "dream_seed")


def test_start_and_activity_local_transcript(sandbox):
    session = dream_seed.start_session(UID, char_id=CHAR)

    assert session.activity_type == "dream_seed"
    assert dream_seed.append_turn(UID, session.session_id, "user", "海边", char_id=CHAR)
    assert dream_seed.append_turn(UID, session.session_id, "assistant", "夜里还是清晨？", char_id=CHAR)

    entries = transcript.load_recent(CHAR, UID, "dream_seed", session.session_id, limit=10)
    assert [entry["type"] for entry in entries] == ["user_chat", "assistant_chat"]
    assert not resolve_path(MemoryScope.reality_scope(UID, CHAR), "history").exists()
    assert not resolve_path(MemoryScope.reality_scope(UID, CHAR), "event_log").exists()


def test_seed_is_character_scoped_and_consumed_once(sandbox):
    assert dream_seed.save_seed(UID, "在海边等日出", char_id=CHAR)

    assert dream_seed.load_seed(UID, char_id=CHAR) == "在海边等日出"
    assert dream_seed.load_seed(UID, char_id="other_char") is None
    assert dream_seed.consume_seed(UID, char_id=CHAR) == "在海边等日出"
    assert dream_seed.consume_seed(UID, char_id=CHAR) is None


def test_expired_seed_is_not_loaded_or_consumed(sandbox):
    assert dream_seed.save_seed(UID, "旧梦", char_id=CHAR)
    path = _seed_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["created_at"] -= 13 * 3600
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert dream_seed.load_seed(UID, char_id=CHAR) is None
    assert dream_seed.consume_seed(UID, char_id=CHAR) is None
    assert path.exists()


def test_close_distills_seed_and_closes_session(sandbox, monkeypatch):
    session = dream_seed.start_session(UID, char_id=CHAR)
    dream_seed.append_turn(UID, session.session_id, "user", "去海边", char_id=CHAR)
    dream_seed.append_turn(UID, session.session_id, "assistant", "我们等日出。", char_id=CHAR)

    async def fake_chat(*args, **kwargs):
        return "清晨的海边，我们并肩等第一束日光。"

    from core import llm_client
    monkeypatch.setattr(llm_client, "chat", fake_chat)

    seed = asyncio.run(dream_seed.close_session(UID, session.session_id, char_id=CHAR))
    closed = dream_seed.get_session(UID, session.session_id, char_id=CHAR)

    assert seed == "清晨的海边，我们并肩等第一束日光。"
    assert closed is not None and closed.status == "closed"
    assert closed.state["seed_text"] == seed
    assert dream_seed.load_seed(UID, char_id=CHAR) == seed


def test_close_requires_at_least_two_transcript_entries(sandbox):
    session = dream_seed.start_session(UID, char_id=CHAR)
    dream_seed.append_turn(UID, session.session_id, "user", "海边", char_id=CHAR)

    assert asyncio.run(dream_seed.close_session(UID, session.session_id, char_id=CHAR)) is None
    assert dream_seed.get_session(UID, session.session_id, char_id=CHAR).status == "active"
    assert not _seed_path().exists()


def test_build_snapshot_injects_and_consumes_seed(sandbox, monkeypatch):
    from core.dream import dream_context
    from core.dream import dream_settings

    monkeypatch.setattr(
        dream_settings,
        "load",
        lambda uid: {"memory_access": dream_settings.MemoryAccess.card_only.value},
    )
    assert dream_seed.save_seed(UID, "雨夜的旧图书馆里一起找一本书", char_id=CHAR)

    snapshot = asyncio.run(dream_context.build_snapshot(UID, entry_reason="她刚睡着", char_id=CHAR))

    assert snapshot["entry_reason"] == "今晚的梦境设定：雨夜的旧图书馆里一起找一本书\n她刚睡着"
    assert dream_seed.load_seed(UID, char_id=CHAR) is None


def test_dream_seed_router_contract():
    from admin.routers.dream_seed import router

    routes = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", [])
    }
    assert ("POST", "/dream_seed/start") in routes
    assert ("GET", "/dream_seed/state") in routes
    assert ("POST", "/dream_seed/chat") in routes
    assert ("POST", "/dream_seed/close") in routes
