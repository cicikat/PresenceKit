import json
import sys
import time
from types import SimpleNamespace

import pytest


class _FakePipeline:
    async def fetch_context(self, uid, query):
        return {}

    def build_prompt(self, uid, prompt, context, **kwargs):
        return [{"role": "user", "content": prompt}], {}

    async def run_llm(self, messages):
        return "reply"


def _install_fastapi_stub(monkeypatch):
    class HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class APIRouter:
        def get(self, *args, **kwargs):
            return lambda fn: fn

        def post(self, *args, **kwargs):
            return lambda fn: fn

        def put(self, *args, **kwargs):
            return lambda fn: fn

        def delete(self, *args, **kwargs):
            return lambda fn: fn

    def marker(default=None, *args, **kwargs):
        return default

    class HTTPBearer:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self):
            return None

    class HTTPAuthorizationCredentials:
        credentials = ""

    monkeypatch.setitem(
        sys.modules,
        "fastapi",
        SimpleNamespace(
            APIRouter=APIRouter,
            Body=marker,
            Depends=marker,
            File=marker,
            Form=marker,
            HTTPException=HTTPException,
            Query=marker,
            Request=object,
            UploadFile=object,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "fastapi.security",
        SimpleNamespace(
            HTTPBearer=HTTPBearer,
            HTTPAuthorizationCredentials=HTTPAuthorizationCredentials,
        ),
    )


@pytest.mark.asyncio
async def test_owner_chat_turn_marks_user_active(monkeypatch):
    _install_fastapi_stub(monkeypatch)
    from admin.routers import chat
    from core.scheduler import loop

    turns = []

    async def fake_record_assistant_turn(**kwargs):
        turns.append(kwargs)
        return SimpleNamespace(
            fanout_failures={},
            emotion="neutral",
            turn_id="t1",
            written_to_memory=True,
        )

    monkeypatch.setattr(loop, "_last_user_message_time", 0.0)
    monkeypatch.setattr("core.pipeline_registry.get", lambda: _FakePipeline())
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "u1"}})
    async def fake_probe(message, user_id):
        return ""

    monkeypatch.setattr(chat, "_probe_and_execute_tools", fake_probe)
    monkeypatch.setattr("channels.registry.get", lambda name: None)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_record_assistant_turn)
    monkeypatch.setattr("core.memory.user_profile.get_affection_level", lambda uid: {"value": 0, "label": "n/a"})

    await chat.run_owner_chat_turn("hello", "desktop")

    assert loop._user_active_recently()
    assert turns and turns[0]["user_text"] == "hello"


@pytest.mark.asyncio
async def test_mobile_chat_marks_user_active_before_shared_owner_turn(monkeypatch):
    _install_fastapi_stub(monkeypatch)
    from admin.routers import mobile
    from core.scheduler import loop

    async def fake_run_owner_chat_turn(message, channel_name):
        return {"reply": "ok", "channel": channel_name}

    monkeypatch.setattr(loop, "_last_user_message_time", 0.0)
    monkeypatch.setattr(mobile, "_get_mobile_channel", lambda: None)
    monkeypatch.setattr("admin.routers.chat.run_owner_chat_turn", fake_run_owner_chat_turn)

    result = await mobile.mobile_chat({"message": "hello"}, auth=None)

    assert result == {"reply": "ok", "channel": "mobile"}
    assert loop._user_active_recently()


@pytest.mark.asyncio
async def test_execute_prompt_blocked_send_does_not_mark_or_after_send(monkeypatch):
    from core.scheduler import execution, loop

    marks = []
    after_send = []

    async def blocked_send(prompt, search_query="", trigger_name="", **kwargs):
        return None

    monkeypatch.setattr(loop, "_pipeline_send", blocked_send)
    monkeypatch.setattr(loop, "_mark", lambda name: marks.append(name))

    result = await execution.execute_prompt(
        trigger_name="random_message",
        prompt_factory=lambda: "prompt",
        dry_run=False,
        would_mark=["random_message"],
        after_send=lambda: after_send.append("called"),
    )

    assert result.sent is False
    assert marks == []
    assert after_send == []


@pytest.mark.asyncio
async def test_execute_prompt_dry_run_still_records(monkeypatch, sandbox):
    from core.scheduler import execution

    result = await execution.execute_prompt(
        trigger_name="random_message",
        prompt_factory=lambda: "prompt",
        dry_run=True,
        would_mark=["random_message"],
        would_mark_done=["r1"],
    )

    assert result.sent is False
    rows = sandbox.execute_dryrun_log().read_text(encoding="utf-8").splitlines()
    assert rows
    row = json.loads(rows[-1])
    assert row["trigger_name"] == "random_message"
    assert row["would_mark_done"] == ["r1"]


@pytest.mark.asyncio
async def test_pipeline_send_high_priority_exempt_from_active_window(monkeypatch):
    from core.scheduler import loop

    recorded = []

    async def fake_record_assistant_turn(**kwargs):
        recorded.append(kwargs)
        return SimpleNamespace(fanout_failures={})

    monkeypatch.setattr("core.pipeline_registry.get", lambda: _FakePipeline())
    monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
    monkeypatch.setattr(loop, "_last_user_message_time", time.time())
    monkeypatch.setattr("core.scheduler.triggers.birthday._is_birthday_period", lambda: False)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", fake_record_assistant_turn)

    result = await loop._pipeline_send("prompt", trigger_name="hr_critical")

    assert result == "reply"
    assert recorded and recorded[0]["trigger_name"] == "hr_critical"
