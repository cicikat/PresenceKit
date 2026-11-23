"""tests/test_private_exchange.py — Brief 86 角色私下往来：闲时受控会话与关系回流

覆盖（Brief 86 验收）：
  1. enabled=false → 零调用；窗口外不触发
  2. pair 选择：低 interaction_count + 久未私下往来者优先（纯规则）
  3. 单次会话 LLM 调用数硬顶 = max_turns
  4. 私下语域框定层两条必须存在
  5. fail-open：生成失败 → 零落盘、零回流（transcript / char_relations / presence 均不写）
  6. 负向断言：五大记忆库 + event_log + 向量库零写入
  7. char_relations 回流复用现有 update_char_relations 路径
  8. presence stamp 12h TTL
  9. 观测端点返回 transcript 尾部 + 鉴权
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID, TEST_THIRD_CHAR_ID

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.scheduler.triggers.private_exchange as pe_trigger
import core.stage.private_exchange as pe_store

_A, _B = TEST_CHAR_ID, TEST_PEER_CHAR_ID


def _mock_config(**private_exchange_overrides):
    cfg = {"scheduler": {"owner_id": "owner"}}
    cfg["private_exchange"] = {"enabled": True, "daily_limit": 1, "max_turns": 6}
    cfg["private_exchange"].update(private_exchange_overrides)
    return cfg


# ── enabled / window 硬闸 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_config_makes_zero_calls(sandbox, monkeypatch):
    monkeypatch.setattr(
        "core.config_loader.get_config", lambda: _mock_config(enabled=False)
    )

    def _boom(*a, **kw):
        raise AssertionError("select_pair must not be called when disabled")

    monkeypatch.setattr(pe_trigger, "select_pair", _boom)
    await pe_trigger._check_private_exchange()  # must return silently


def test_in_deep_night_window_boundaries():
    assert pe_trigger._in_deep_night_window(datetime(2026, 1, 1, 23, 30)) is True
    assert pe_trigger._in_deep_night_window(datetime(2026, 1, 1, 4, 0)) is True
    assert pe_trigger._in_deep_night_window(datetime(2026, 1, 1, 14, 0)) is False


@pytest.mark.asyncio
async def test_check_skips_outside_window(sandbox, monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", lambda: _mock_config())
    monkeypatch.setattr(pe_trigger, "_in_deep_night_window", lambda: False)

    def _boom(*a, **kw):
        raise AssertionError("select_pair must not be called outside the window")

    monkeypatch.setattr(pe_trigger, "select_pair", _boom)
    await pe_trigger._check_private_exchange()


# ── pair 选择（纯规则）────────────────────────────────────────────────────────

def test_select_pair_prefers_low_interaction_and_long_gap(sandbox):
    from core.stage.char_relations import _empty_relation, _save_relation

    warm = _empty_relation(_A, TEST_THIRD_CHAR_ID)
    warm["interaction_count"] = 5
    assert _save_relation(warm)
    pe_store.append_entry(_A, TEST_THIRD_CHAR_ID, speaker_id=_A, content="刚聊过")

    cold = _empty_relation(_A, _B)
    cold["interaction_count"] = 0
    assert _save_relation(cold)
    # never privately exchanged → last_exchange_ts()==0 → huge hours_since

    picked = pe_trigger.select_pair([_A, _B, TEST_THIRD_CHAR_ID])
    assert picked == tuple(sorted((_A, _B)))


def test_select_pair_none_without_candidates(sandbox):
    assert pe_trigger.select_pair([_A, _B, TEST_THIRD_CHAR_ID]) is None


# ── 私下语域框定层 ─────────────────────────────────────────────────────────────

def test_private_domain_framing_has_both_required_lines():
    from core.stage.context import render_private_presence

    text = render_private_presence(_A, _B)
    assert "看不到，不需要表演给任何人" in text
    assert "私下语域不等于秘密" in text
    assert "不形成针对任何人的共识" in text


def test_private_domain_framing_injects_both_identities():
    """Brief 106 §1: the char doesn't know who it is or who it's talking to
    without this — the card's only intimacy template is toward the user, so
    the model mistakes "private + intimate register" for a lover relationship."""
    from core.character_name_provider import get_char_name
    from core.stage.context import render_private_presence

    text = render_private_presence(_A, _B)
    assert f"你是{get_char_name(_A)}" in text
    assert get_char_name(_B) in text


def test_private_domain_framing_injects_existing_impression(sandbox):
    from core.stage.char_relations import _empty_relation, _save_relation
    from core.stage.context import render_private_presence

    relation = _empty_relation(_A, _B)
    first, _second = sorted((_A, _B))
    side = "a_of_b" if _A == first else "b_of_a"
    relation[side] = {"summary": "上次帮我修过琴", "valence": 0.4, "updated_at": ""}
    assert _save_relation(relation)

    text = render_private_presence(_A, _B)
    assert "上次帮我修过琴" in text


def test_private_domain_framing_omits_impression_when_none(sandbox):
    from core.stage.context import render_private_presence

    text = render_private_presence(_A, _B)
    assert "的印象：" not in text


@pytest.mark.asyncio
async def test_generate_private_skips_fetch_context():
    from core.stage.views import StageCharacterView
    from unittest.mock import patch

    captured = {}

    class FakePipeline:
        context = property(lambda self: self)
        async def fetch(self, *a, **kw):
            raise AssertionError("private exchange must use the lightweight path")

        def build_prompt(self, uid, content, context, **kwargs):
            captured["content"] = content
            captured["stage_presence"] = context["stage_presence"]
            captured["stage_transcript"] = context["stage_transcript"]
            captured["stage_transcript_private"] = context["stage_transcript_private"]
            return ([{"role": "user", "content": content}], {"token_estimate": 1})

        async def run_llm(self, messages, *, char_id=None):
            return "回复内容"

    view = object.__new__(StageCharacterView)
    view.char_id = _A
    view.pipeline = FakePipeline()

    with patch("core.observe.prompt_capture.set_capture_origin") as set_origin:
        reply = await view.generate_private(_B, [], owner_uid="owner")

    assert reply == "回复内容"
    set_origin.assert_called_once_with({
        "origin": "private_exchange",
        "pair": sorted((_A, _B)),
        "speaker": _A,
    })
    assert captured["stage_transcript"] == ""
    assert captured["stage_transcript_private"] is True
    assert "看不到" in captured["stage_presence"]
    assert captured["content"].startswith("（旁白指引，不是任何人的发言。）")


@pytest.mark.asyncio
async def test_generate_private_instruction_prefixed_for_continuation_and_opener():
    """Brief 106 §3: the instruction rides the user-role slot (layer 12), which
    the model otherwise reads as someone present speaking. The narrator prefix
    must lead the final text in every branch — continuation, cold-open, and
    cold-open-with-opener-material (prefix must stay first, not get buried
    after the opener line)."""
    from core.stage.views import StageCharacterView
    from unittest.mock import patch

    captured = []

    class FakePipeline:
        context = property(lambda self: self)
        async def fetch(self, *a, **kw):
            raise AssertionError("private exchange must use the lightweight path")

        def build_prompt(self, uid, content, context, **kwargs):
            captured.append(content)
            return ([{"role": "user", "content": content}], {"token_estimate": 1})

        async def run_llm(self, messages, *, char_id=None):
            return "回复内容"

    view = object.__new__(StageCharacterView)
    view.char_id = _A
    view.pipeline = FakePipeline()

    prefix = "（旁白指引，不是任何人的发言。）"
    with patch("core.observe.prompt_capture.set_capture_origin"):
        await view.generate_private(_B, [(_B, "在吗")], owner_uid="owner")
        await view.generate_private(_B, [], owner_uid="owner", opener_material="今晚月色不错。")

    continuation, cold_open_with_material = captured
    assert continuation.startswith(prefix)
    assert cold_open_with_material.startswith(prefix)
    assert "今晚月色不错。" in cold_open_with_material


# ── LLM 调用预算硬顶 + 落盘 + 回流 ──────────────────────────────────────────────

class _FakeView:
    call_log: list[tuple[str, str, int]] = []
    fail_on_call_index: int | None = None
    empty_on_call_index: int | None = None

    def __init__(self, char_id):
        self.char_id = char_id

    async def generate_private(self, other_id, turns, *, owner_uid, opener_material=""):
        idx = len(_FakeView.call_log)
        _FakeView.call_log.append((self.char_id, other_id, len(turns)))
        if _FakeView.fail_on_call_index == idx:
            raise RuntimeError("boom")
        if _FakeView.empty_on_call_index == idx:
            return ""
        return f"{self.char_id}第{len(turns)}轮"


@pytest.fixture(autouse=True)
def _reset_fake_view():
    _FakeView.call_log = []
    _FakeView.fail_on_call_index = None
    _FakeView.empty_on_call_index = None
    yield


@pytest.mark.asyncio
async def test_run_session_llm_call_count_equals_max_turns(sandbox, monkeypatch):
    monkeypatch.setattr("core.stage.views.StageCharacterView", _FakeView)
    enqueued = []
    monkeypatch.setattr(
        "core.post_process.slow_queue.enqueue", lambda t, p: enqueued.append((t, p))
    )

    await pe_trigger._run_session(_A, _B, max_turns=4)

    assert len(_FakeView.call_log) == 4
    speakers = [c[0] for c in _FakeView.call_log]
    assert speakers == [_A, _B, _A, _B]

    transcript = pe_store.load_transcript(_A, _B)
    assert len(transcript) == 4

    assert len(enqueued) == 1
    task_type, payload = enqueued[0]
    assert task_type == "update_char_relations"
    assert {payload["char_a"], payload["char_b"]} == {_A, _B}
    assert payload["write_envelope"]["can_write_memory"] is True

    assert pe_store.read_presence_hint(_A) != ""
    assert pe_store.read_presence_hint(_B) != ""


@pytest.mark.asyncio
async def test_run_session_never_exceeds_max_turns_budget(sandbox, monkeypatch):
    monkeypatch.setattr("core.stage.views.StageCharacterView", _FakeView)
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda t, p: None)

    await pe_trigger._run_session(_A, _B, max_turns=2)

    assert len(_FakeView.call_log) <= 2


# ── fail-open: 生成失败 → 零落盘零回流 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_generation_failure_persists_nothing(sandbox, monkeypatch):
    monkeypatch.setattr("core.stage.views.StageCharacterView", _FakeView)
    _FakeView.fail_on_call_index = 2  # fails on the 3rd of 6 default turns
    enqueued = []
    monkeypatch.setattr(
        "core.post_process.slow_queue.enqueue", lambda t, p: enqueued.append((t, p))
    )

    await pe_trigger._run_session(_A, _B, max_turns=6)

    assert pe_store.load_transcript(_A, _B) == []
    assert enqueued == []
    assert pe_store.read_presence_hint(_A) == ""
    assert pe_store.read_presence_hint(_B) == ""


@pytest.mark.asyncio
async def test_empty_reply_also_fail_opens(sandbox, monkeypatch):
    monkeypatch.setattr("core.stage.views.StageCharacterView", _FakeView)
    _FakeView.empty_on_call_index = 1
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda t, p: None)

    await pe_trigger._run_session(_A, _B, max_turns=6)

    assert pe_store.load_transcript(_A, _B) == []


# ── 负向断言：五大记忆库 + event_log + 向量库零写入 ─────────────────────────────

@pytest.mark.asyncio
async def test_no_writes_to_memory_stores(sandbox, monkeypatch):
    from core.sandbox import get_paths

    monkeypatch.setattr("core.stage.views.StageCharacterView", _FakeView)
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda t, p: None)

    await pe_trigger._run_session(_A, _B, max_turns=4)

    # No per-user memory root should exist at all for either character — the
    # private exchange never touches short_term/mid_term/episodic/identity/event_log.
    assert not get_paths().memory_char_root(char_id=_A).exists()
    assert not get_paths().memory_char_root(char_id=_B).exists()


# ── 每日预算：跨所有 pair 合计 ───────────────────────────────────────────────────

def test_daily_budget_exhausted_after_limit(sandbox):
    assert pe_trigger._consume_daily_budget(1) is True
    assert pe_trigger._consume_daily_budget(1) is False


def test_daily_budget_resets_on_new_logical_day(sandbox, monkeypatch):
    from datetime import date

    monkeypatch.setattr(pe_trigger, "_load_budget_state", lambda: {"logical_day": "2000-01-01", "count": 1})
    monkeypatch.setattr("core.scheduler.rhythm.logical_day", lambda: date(2026, 7, 17))
    assert pe_trigger._consume_daily_budget(1) is True


# ── presence stamp 12h TTL ───────────────────────────────────────────────────

def test_presence_hint_within_ttl(sandbox, monkeypatch):
    import time

    now = time.time()
    pe_store.write_presence_stamp(_A, _B, ts=now - 3600)
    hint = pe_store.read_presence_hint(_A)
    assert "聊了会儿" in hint


def test_presence_hint_expires_after_12h(sandbox):
    import time

    now = time.time()
    pe_store.write_presence_stamp(_A, _B, ts=now - 13 * 3600)
    assert pe_store.read_presence_hint(_A) == ""


def test_presence_hint_missing_returns_empty(sandbox):
    assert pe_store.read_presence_hint(_A) == ""


# ── 观测端点 ──────────────────────────────────────────────────────────────────

VALID_TOKEN = "pe-test-secret"
_app = FastAPI()

from admin.routers.relations import router as _relations_router  # noqa: E402

_app.include_router(_relations_router, prefix="/relations")


@pytest.fixture(autouse=True)
def _patch_secret(monkeypatch):
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: VALID_TOKEN)


@pytest.fixture()
def client(sandbox):
    return TestClient(_app, raise_server_exceptions=True)


def _auth():
    return {"Authorization": f"Bearer {VALID_TOKEN}"}


def test_private_log_endpoint_returns_transcript_tail(client, sandbox):
    for i in range(5):
        pe_store.append_entry(_A, _B, speaker_id=_A if i % 2 == 0 else _B, content=f"第{i}句")

    r = client.get(f"/relations/private-log?char_a={_A}&char_b={_B}&limit=3", headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 3
    assert data["entries"][-1]["content"] == "第4句"


def test_private_log_endpoint_requires_auth(client, sandbox):
    r = client.get(f"/relations/private-log?char_a={_A}&char_b={_B}")
    assert r.status_code in (401, 403)


def test_private_log_endpoint_empty_when_no_transcript(client, sandbox):
    r = client.get(f"/relations/private-log?char_a={_A}&char_b={_B}", headers=_auth())
    assert r.status_code == 200
    assert r.json()["entries"] == []
