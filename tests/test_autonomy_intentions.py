
from types import SimpleNamespace
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.agent_runtime import intentions
from core.autonomy import runner, store
from core.autonomy.models import Job, Opportunity, Run, Signal
from core.mail import gmail, gmail_pending
from core.tool_dispatcher import execute_structured, get_tools_schema
from test_gmail_messages import connected  # noqa: F401
from test_gmail_oauth import mailbox  # noqa: F401


@pytest.fixture(autouse=True)
def unrelated_native_reader_unavailable(monkeypatch):
    # The intent scenarios do not use the optional local reader.
    monkeypatch.setattr("core.xiaohongshu_service.status", lambda: {"installed": False})


def _state():
    state = store.load("owner", "character")
    state["config"].update(enabled=True, talk_enabled=False, character_intents_enabled=True, max_write_tools=2)
    assert store.replace_config("owner", "character", state["config"])
    return state


def _calls(name, **arguments):
    return [{"id": f"call-{name}", "name": name, "arguments": arguments}]


async def _evaluate(monkeypatch, state, turns, *, seen=None):
    monkeypatch.setattr(runner, "_user_became_active", lambda *_: False)
    monkeypatch.setattr(runner.talk_gate, "check", lambda *a, **k: ("hard", "talk_disabled"))
    # Stub unrelated tool discovery at the integration boundary, while using
    # the real schema and dispatcher for this scenario's tools.
    names = frozenset({"list_character_intents", "propose_character_intent", "revise_character_intent",
                       "gmail_mark_handled", "gmail_reply_message", "write_track_note"})
    schemas = get_tools_schema(uid="owner", char_id="character", only_names=names, autonomy=True)
    monkeypatch.setattr(runner, "_runtime_tools", lambda *_: (list(schemas), None))
    scripted = iter(turns)

    async def chat_turn(messages, tools, **_kwargs):
        calls = next(scripted, [])
        if seen is not None:
            seen.append([dict(row) for row in messages])
        allowed = {tool["function"]["name"] for tool in tools}
        assert all(call["name"] in allowed for call in calls)
        return SimpleNamespace(tool_calls=calls, continuation_items=[], assistant_message={"role": "assistant"})

    monkeypatch.setattr("core.llm_client.chat_turn", chat_turn)
    opportunity = Opportunity.merge([Signal(source="interval", reason="scheduled quiet evaluation",
                                             expiry=time.time() + 600)])
    job = Job(uid="owner", char_id="character", source="autonomy", opportunity=opportunity.to_dict())
    return await runner._run_locked(job, state, Run(uid=job.uid, char_id=job.char_id, source=job.source, job_id=job.id))


@pytest.mark.asyncio
async def test_pending_mail_can_seed_a_private_deferred_intent_without_talking(sandbox, connected, monkeypatch):
    now = time.time()
    record = gmail.load()
    gmail_pending.add_discovered(record, [("mail-a", 1), ("mail-b", 2), ("mail-c", 3)], now)
    gmail.save(record)
    state = _state()
    private_goal = "private test intention"
    scripted = _calls("propose_character_intent", goal=private_goal, source_refs=["fact_1"], next_check_at=now + 120)
    seen = []
    first = await _evaluate(monkeypatch, state, [scripted, []], seen=seen)
    assert first.disposition == "completed_tools_only" and not first.talk_sent
    assert any("pending_count" in row["content"] for row in seen[0] if row.get("_layer") == "character_intent_context")
    for _ in range(2):
        result = await _evaluate(monkeypatch, state, [scripted, []], seen=seen)
        assert not result.talk_sent
    rows, counts = intentions.snapshot("owner", "character")
    assert len(rows) == 1 and counts["proposed"] == 1
    row = rows[0]
    assert row["status"] == "deferred" and row["next_check_at"] == now + 120
    assert row["source_anchors"][0]["kind"] == "mail_pending"
    assert private_goal not in str(result.prompt_snapshot)
    assert private_goal not in str(intentions.observation("owner", "character"))
    assert private_goal in str(seen[-1])
    assert not intentions.snapshot("owner", "another-character")[0]
    assert not intentions.snapshot("another-owner", "character")[0]


@pytest.mark.asyncio
async def test_authorized_existing_tool_receipt_completes_and_unknown_reply_never_replays(sandbox, connected, monkeypatch):
    record = gmail.load()
    now = time.time()
    gmail_pending.add_discovered(record, [("pending-original", 1)], now)
    gmail.save(record)
    state = _state()
    pending = gmail_pending.list_items("owner", "character")["items"][0]
    await _evaluate(monkeypatch, state, [
        _calls("propose_character_intent", goal="handle a known item", source_refs=["fact_1"]), [],
    ])
    item = intentions.snapshot("owner", "character")[0][0]
    result = await _evaluate(monkeypatch, state, [
        _calls("revise_character_intent", intent_id=item["id"], revision=item["revision"],
               action="ready", tool="gmail_mark_handled", target=pending["pending_id"]),
        _calls("gmail_mark_handled", pending_id=pending["pending_id"], revision=pending["revision"]), [],
    ])
    final = intentions.snapshot("owner", "character")[0][0]
    assert result.disposition == "completed_tools_only"
    assert final["status"] == "done" and final["result_evidence"] == [{"kind": "mail_handled"}]
    assert gmail.status("owner", "character")["pending_count"] == 0

    # Recreate pending work after the earlier successful item, then simulate a
    # provider accepting a write whose response is lost.
    record = gmail.load()
    gmail_pending.add_discovered(record, [("pending-second", 2)], now)
    gmail.save(record)
    pending = gmail_pending.list_items("owner", "character")["items"][0]
    anchor = {"kind": "mail_pending", "id": gmail_pending.pending_anchor(record),
              "mailbox": gmail_pending.mailbox_key(record)}
    row = intentions.propose("owner", "character", goal="reply", motivation="", anchors=[anchor])["intent"]
    if row["status"] == "done":
        # The same source deliberately deduplicates across normal ticks.
        # Clear completed entries before trying a distinct action on that source.
        assert intentions.clear_terminal("owner", "character") == 1
        row = intentions.propose("owner", "character", goal="reply", motivation="", anchors=[anchor])["intent"]
    posts = []

    async def request(method, url, **_kwargs):
        if method == "GET":
            return {"id": url.rsplit("/", 1)[-1], "threadId": "thread", "labelIds": ["INBOX"],
                    "payload": {"headers": [
                        {"name": "Message-ID", "value": "<fixture@example.test>"},
                        {"name": "From", "value": "sender2@example.test"},
                    ]}}
        posts.append(method)
        raise gmail.GmailError("outcome_unknown")

    monkeypatch.setattr(gmail, "request", request)
    args = {"message_id": pending["pending_id"], "request_id": "first-write", "body": "hello"}
    failed = await _evaluate(monkeypatch, state, [
        _calls("revise_character_intent", intent_id=row["id"], revision=row["revision"],
               action="ready", tool="gmail_reply_message", target=pending["pending_id"]),
        _calls("gmail_reply_message", **args),
    ])
    assert failed.disposition == "tool_outcome_unknown"
    assert intentions.observation("owner", "character")["states"]["outcome_unknown"] == 1
    replay = await _evaluate(monkeypatch, state, [
        _calls("gmail_reply_message", **{**args, "request_id": "different-key"}),
    ])
    assert replay.disposition == "tool_call_denied"
    assert posts == ["POST"]


@pytest.mark.asyncio
async def test_reality_tool_origin_and_forged_anchor_are_rejected(sandbox, monkeypatch):
    state = _state()
    names = {tool["function"]["name"] for tool in get_tools_schema(
        uid="owner", char_id="character", only_names=frozenset({"propose_character_intent"}),
    )}
    assert "propose_character_intent" not in names
    assert "propose_character_intent" in {
        tool["function"]["name"] for tool in get_tools_schema(
            uid="owner", char_id="character", autonomy=True,
            only_names=frozenset({"propose_character_intent"}),
        )
    }
    denied = await execute_structured("propose_character_intent", {"goal": "private"},
                                     "owner", "owner", False, object(),
                                     origin="assistant_loop", char_id="character")
    assert denied.status == "tool_failed"
    await _evaluate(monkeypatch, state, [
        _calls("propose_character_intent", goal="private", source_refs=["fake-anchor"]), [],
    ])
    assert not intentions.snapshot("owner", "character")[0]


@pytest.mark.asyncio
async def test_counted_listening_fact_supports_a_real_track_note(sandbox, monkeypatch):
    from core.listening_store import read_note, set_participant, upsert_track
    from core.player_adapter import FakePlayerHost

    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    monkeypatch.setattr("core.listening_tools.music_control_enabled", lambda: True)
    track = upsert_track("owner", provider="local", source_id="shared-track", title="Private title", duration_s=50)
    other = upsert_track("owner", provider="local", source_id="brief-track", title="Brief title", duration_s=50)
    host = FakePlayerHost("owner")
    set_participant("owner", "character")
    host.play(track["track_id"])
    for step in range(1, 7):
        host.progress(5, position_s=step * 5)
    host.stop()
    host.play(other["track_id"])
    host.progress(2, position_s=2)
    host.stop()

    state = _state()
    seen = []
    await _evaluate(monkeypatch, state, [
        _calls("propose_character_intent", goal="make a private note", source_refs=["fact_1"]), [],
    ], seen=seen)
    row = intentions.snapshot("owner", "character")[0][0]
    assert row["source_anchors"][0]["track_id"] == track["track_id"]
    projection = next(item["content"] for item in seen[0] if item.get("_layer") == "character_intent_context")
    assert other["track_id"] not in projection and "Private title" not in projection
    assert "unavailable" in projection
    result = await _evaluate(monkeypatch, state, [
        _calls("revise_character_intent", intent_id=row["id"], revision=row["revision"],
               action="ready", tool="write_track_note", target=track["track_id"]),
        _calls("write_track_note", track_id=track["track_id"], body="A private note"), [],
    ], seen=seen)
    assert result.disposition == "completed_tools_only"
    note = read_note("owner", "character", track["track_id"])
    assert note is not None, result.events
    assert note["body"] == "A private note"
    assert not read_note("owner", "another-character", track["track_id"])
    assert intentions.observation("owner", "character")["states"] == {"done": 1}


def test_interrupted_receipt_becomes_unknown_on_process_change(sandbox, monkeypatch):
    row = intentions.propose("owner", "character", goal="prepare work", motivation="", anchors=[])["intent"]
    intentions.revise("owner", "character", identity=row["id"], revision=row["revision"],
                      action="ready", run_id="one", tool="start_agent_task", target="request-one")
    args = {"request_id": "request-one", "goal": "work"}
    assert intentions.claim("owner", "character", "one", "start_agent_task", args, timeout=30)
    monkeypatch.setattr(intentions, "process_instance_id", lambda: "different-process")
    assert intentions.observation("owner", "character")["states"] == {"outcome_unknown": 1}
    with pytest.raises(intentions.IntentError, match="intent_previous_outcome_unresolved"):
        intentions.claim("owner", "character", "two", "start_agent_task", args, timeout=30)


def test_revision_cancel_and_redacted_admin_observation(sandbox, monkeypatch):
    from admin import auth
    from admin.routers import autonomy as routes

    _state()
    monkeypatch.setattr(routes, "_scope", lambda: ("owner", "character"))
    monkeypatch.setattr(auth, "resolve_token", lambda token: auth.TokenInfo(
        token, frozenset({"admin"} if token == "admin" else {"state.read"}), None,
    ))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    admin = {"Authorization": "Bearer admin"}
    reader = {"Authorization": "Bearer reader"}
    private_goal = "confidential goal"
    row = intentions.propose("owner", "character", goal=private_goal, motivation="", anchors=[])["intent"]
    response = client.get("/observability/character-intentions", headers=reader)
    assert response.status_code == 200 and private_goal not in response.text
    assert response.json()["states"] == {"proposed": 1}
    cancel = {"intent_id": row["id"], "revision": row["revision"]}
    assert client.post("/admin/autonomy/intents/cancel", headers=reader, json=cancel).status_code == 403
    assert client.post("/admin/autonomy/intents/cancel", headers=admin,
                       json={**cancel, "revision": row["revision"] + 1}).status_code == 409
    assert client.post("/admin/autonomy/intents/cancel", headers=admin, json=cancel).json()["status"] == "abandoned"
    assert client.post("/admin/autonomy/intents/clear", headers=admin).json()["cleared"] == 1
