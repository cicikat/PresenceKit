import io
import asyncio
import time
from types import SimpleNamespace
import httpx

import pytest
from PIL import Image
from openai import APIConnectionError, APITimeoutError

from core import video_call
from core.image_presets import video_call_ready


@pytest.fixture(autouse=True)
def _fresh_local_resource(monkeypatch):
    # A contended asyncio.Lock binds to the first running loop; each test has its own.
    monkeypatch.setattr(video_call, "_local_resource", asyncio.Lock())


def _config(base_url="http://127.0.0.1:11434/v1"):
    return {"image_presets": {
        "presets": {"local_vision": {
            "kind": "vision", "enabled": True, "provider": "custom",
            "api_protocol": "chat_completions", "model": "vendor/model:tag",
            "base_url": base_url,
        }},
        "routes": {"video_call": "local_vision"},
    }}


def _jpeg():
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), (30, 60, 90)).save(stream, format="JPEG")
    return stream.getvalue()


def test_video_call_requires_loopback_vision():
    local = _config()["image_presets"]["presets"]["local_vision"]
    assert video_call_ready(local) == (True, "")
    for address in ("https://127.0.0.1:11434/v1", "http://192.168.1.5:11434/v1", "https://api.example/v1"):
        assert video_call_ready({**local, "base_url": address})[0] is False
    assert video_call_ready({**local, "kind": "ocr"})[0] is False


def test_camera_tool_route_is_separately_configurable(monkeypatch):
    """按需查看有自己的路由；未配置时沿用周期观察，配置后独立生效。"""
    from core.image_presets import catalog
    inherited = _config()
    assert catalog(inherited)["routes"]["video_call_tool"] == "local_vision"
    assert video_call.connection_state(inherited, video_call.TOOL_PURPOSE)["effective"] is True

    split = _config()
    split["image_presets"]["presets"]["local_tool_vision"] = {
        **split["image_presets"]["presets"]["local_vision"], "model": "vendor/bigger:tag",
    }
    split["image_presets"]["routes"]["video_call_tool"] = "local_tool_vision"
    assert video_call.connection_state(split, video_call.TOOL_PURPOSE)["connection"] == "local_tool_vision"
    assert video_call.connection_state(split)["connection"] == "local_vision"

    disabled = _config()
    disabled["image_presets"]["routes"]["video_call_tool"] = ""
    monkeypatch.setattr("core.config_loader.get_config", lambda: disabled)
    from core import tool_dispatcher
    monkeypatch.setattr(tool_dispatcher, "get_config", lambda: disabled)
    assert not tool_dispatcher._is_tool_enabled("observe_video_call_camera")
    assert video_call.connection_state(disabled)["effective"] is True


def test_camera_tool_uses_video_call_route_only(monkeypatch):
    from core import tool_dispatcher
    monkeypatch.setattr(tool_dispatcher, "get_config", _config)
    assert tool_dispatcher._is_tool_enabled("observe_video_call_camera")
    monkeypatch.setattr(tool_dispatcher, "get_config", lambda: _config("https://remote.example/v1"))
    assert not tool_dispatcher._is_tool_enabled("observe_video_call_camera")
    assert "observe_video_call_camera" in tool_dispatcher._TOOL_REGISTRY
    assert "observe_user_screen" in tool_dispatcher._TOOL_REGISTRY


def test_closed_camera_blocks_merged_autonomy_talk(monkeypatch):
    from core.autonomy import runner
    monkeypatch.setattr(video_call, "camera_session", lambda uid, char_id: None)
    job = SimpleNamespace(uid="owner", char_id="character", opportunity={"signals": [
        {"source": "video_call_camera"}, {"source": "interval"},
    ]})
    assert runner._camera_signal_closed(job)
    job.opportunity = {"signals": [{"source": "interval"}]}
    assert not runner._camera_signal_closed(job)


def test_camera_signal_uses_interval_even_when_scene_is_unchanged(monkeypatch):
    from core.autonomy import store
    queued = []
    monkeypatch.setattr(store, "enqueue_signal", lambda uid, char_id, signal, **kwargs: (
        queued.append((uid, char_id, signal, kwargs)) or True, "queued"
    ))
    session = {"opened_at": 100.0}
    video_call._queue_camera_signal("owner", "character", session, "书桌", 130.0)
    assert queued == []
    video_call._queue_camera_signal("owner", "character", session, "书桌", 161.0)
    assert len(queued) == 1
    assert queued[0][2].source == "video_call_camera"
    assert queued[0][2].evidence[0]["description"] == "书桌"
    assert queued[0][2].expiry - queued[0][2].created_at > 9 * 60
    video_call._queue_camera_signal("owner", "character", session, "书桌", 200.0)
    video_call._queue_camera_signal("owner", "character", session, "书桌", 201.0)
    assert len(queued) == 1
    video_call._queue_camera_signal("owner", "character", session, "书桌", 162.0 + video_call.CAMERA_SIGNAL_INTERVAL_SECONDS)
    assert len(queued) == 2


@pytest.mark.asyncio
async def test_camera_tool_requests_fresh_frame_on_its_own_route(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)
    async def fake_chat(messages, **kwargs):
        assert kwargs["vision_purpose"] == video_call.TOOL_PURPOSE
        return "窗边有一本书"
    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    video_call._camera_sessions[("owner-tool", "character-tool")] = {
        "seen_at": time.monotonic(), "opened_at": time.monotonic(),
        "token_label": "desktop", "description": "旧画面",
    }
    task = asyncio.create_task(video_call.observe_fresh_camera("owner-tool", "character-tool"))
    await asyncio.sleep(0)
    request = video_call.poll_camera("owner-tool", "character-tool", "desktop")["request"]
    assert request and request["request_id"]
    assert video_call.poll_camera("owner-tool", "character-tool", "desktop")["request"] is None
    assert not video_call.accept_camera_frame("owner-tool", "character-tool", "other", request["request_id"], _jpeg())
    assert video_call.accept_camera_frame("owner-tool", "character-tool", "desktop", request["request_id"], _jpeg())
    assert not video_call.accept_camera_frame("owner-tool", "character-tool", "desktop", request["request_id"], _jpeg())
    fresh = await task
    assert fresh["status"] == "ok" and fresh["description"] == "窗边有一本书"
    assert fresh["captured"] == "刚拉取的新帧" and fresh["age_seconds"] < 5
    video_call.close_camera("owner-tool", "character-tool", "desktop")


@pytest.mark.asyncio
async def test_camera_observation_is_ephemeral_scoped_and_busy_drops(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    async def fake_chat(messages, **kwargs):
        assert kwargs == {"use_vision": True, "vision_purpose": "video_call"}
        assert messages[0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return "桌面上有一本书"
    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    result = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert video_call.camera_session("owner", "character")["description"] == "桌面上有一本书"
    assert video_call.camera_status()["active_sessions"] >= 1
    assert result["status"] == "ready"
    receipt = result["observation_id"]
    assert video_call.consume(receipt, uid="owner", char_id="wrong", token_label="desktop") is None
    consumed = video_call.consume(receipt, uid="owner", char_id="character", token_label="desktop")
    assert consumed[0] == "桌面上有一本书"
    assert 0 <= consumed[1] < 5
    assert video_call.consume(receipt, uid="owner", char_id="character", token_label="desktop") is None
    video_call.close_camera("owner", "character", "other-device")
    assert video_call.camera_session("owner", "character") is not None
    video_call.close_camera("owner", "character", "desktop")
    assert video_call.camera_session("owner", "character") is None
    result = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert video_call.consume(result["observation_id"], uid="owner", char_id="character", token_label="desktop")[0] == "桌面上有一本书"
    async with video_call.local_resource():
        assert (await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop"))["status"] == "busy"


@pytest.mark.asyncio
async def test_periodic_frame_waits_out_a_short_tts_hold(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)
    monkeypatch.setattr(video_call, "VISION_LOCK_WAIT_SECONDS", 1.0)

    async def fake_chat(*_args, **_kwargs):
        return "等到锁之后看到的画面"

    monkeypatch.setattr("core.llm_client.chat", fake_chat)

    async def hold_briefly():
        async with video_call.local_resource():
            await asyncio.sleep(0.05)

    holder = asyncio.create_task(hold_briefly())
    await asyncio.sleep(0)
    result = await video_call.observe(_jpeg(), uid="owner-wait", char_id="character",
                                      token_label="desktop")
    await holder
    assert result["status"] == "ready"
    video_call.close_camera("owner-wait", "character", "desktop")


@pytest.mark.asyncio
async def test_starved_frame_still_emits_a_signal_from_the_last_description(monkeypatch):
    from core.autonomy import store
    monkeypatch.setattr("core.config_loader.get_config", _config)
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)
    monkeypatch.setattr(video_call, "VISION_LOCK_WAIT_SECONDS", 0.01)
    queued = []
    monkeypatch.setattr(store, "enqueue_signal", lambda uid, char_id, signal, **kwargs: (
        queued.append(signal) or True, "queued"))

    opened = time.monotonic() - 10 * video_call.CAMERA_SIGNAL_INTERVAL_SECONDS
    video_call._camera_sessions[("owner-starved", "character")] = {
        "seen_at": time.monotonic(), "opened_at": opened, "token_label": "desktop",
        "description": "书桌上的台灯", "described_at": time.monotonic() - 8,
    }
    async with video_call.local_resource():
        result = await video_call.observe(_jpeg(), uid="owner-starved", char_id="character",
                                          token_label="desktop")
    assert result["status"] == "busy"
    assert len(queued) == 1
    assert queued[0].source == "video_call_camera"
    assert "秒前采集" in queued[0].evidence[0]["description"]
    assert queued[0].evidence[0]["age_seconds"] >= 8
    assert queued[0].evidence[0]["trust"] == "untrusted_visual_description"
    video_call.close_camera("owner-starved", "character", "desktop")


@pytest.mark.asyncio
async def test_starved_frame_without_history_emits_no_signal(monkeypatch):
    from core.autonomy import store
    monkeypatch.setattr("core.config_loader.get_config", _config)
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)
    monkeypatch.setattr(video_call, "VISION_LOCK_WAIT_SECONDS", 0.01)
    queued = []
    monkeypatch.setattr(store, "enqueue_signal", lambda *args, **kwargs: (
        queued.append(args) or True, "queued"))

    async with video_call.local_resource():
        result = await video_call.observe(_jpeg(), uid="owner-fresh", char_id="character",
                                          token_label="desktop")
    assert result["status"] == "busy"
    assert queued == []
    video_call.close_camera("owner-fresh", "character", "desktop")


def test_receipt_age_advances_with_time_and_expiry_still_rejects(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(video_call.time, "monotonic", lambda: clock["now"])
    video_call._receipts["r-age"] = (clock["now"] + 45, "owner", "character", "desktop", "书桌", clock["now"])
    clock["now"] += 30
    description, age = video_call.consume("r-age", uid="owner", char_id="character", token_label="desktop")
    assert description == "书桌" and age == pytest.approx(30.0)
    video_call._receipts["r-old"] = (clock["now"] + 45, "owner", "character", "desktop", "书桌", clock["now"])
    clock["now"] += 46
    assert video_call.consume("r-old", uid="owner", char_id="character", token_label="desktop") is None


def test_age_label_is_coarse_and_never_claims_current():
    assert video_call.age_label(0) == "刚刚"
    assert video_call.age_label(32) == "约 30 秒前"
    assert video_call.age_label(40) == "约 40 秒前"
    assert video_call.age_label(130) == "约 2 分钟前"
    assert "当前" not in "".join(video_call.age_label(n) for n in (0, 10, 50, 200))


def test_injected_observation_states_its_age_instead_of_current():
    text = video_call.observation_prefix("桌上有杯子", 42)
    assert "约 40 秒前" in text and "桌上有杯子" in text
    assert "当前" not in text
    assert "刚刚" in video_call.observation_prefix("桌上有杯子", 1)


@pytest.mark.asyncio
async def test_camera_observation_rejects_remote_route_and_oversized_frame(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", lambda: _config("https://remote.example/v1"))
    with pytest.raises(ValueError, match="loopback_http_required"):
        await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    monkeypatch.setattr("core.config_loader.get_config", _config)
    with pytest.raises(ValueError, match="frame_size_invalid"):
        await video_call.observe(b"x" * (video_call.MAX_FRAME_BYTES + 1), uid="owner", char_id="character", token_label="desktop")


@pytest.mark.asyncio
async def test_unavailable_local_model_skips_following_frames_without_queue(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)
    calls = []

    async def disconnected(*_args, **_kwargs):
        calls.append(True)
        raise APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions"))

    monkeypatch.setattr("core.llm_client.chat", disconnected)
    first = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    second = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert first["status"] == second["status"] == "unavailable"
    assert len(calls) == 1
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)

    async def recovered(*_args, **_kwargs):
        return "恢复后的当前画面"

    monkeypatch.setattr("core.llm_client.chat", recovered)
    recovered_frame = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert recovered_frame["status"] == "ready"


@pytest.mark.asyncio
async def test_model_timeout_is_distinct_from_disconnection(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", _config)
    monkeypatch.setattr(video_call, "_unavailable_until", 0.0)

    async def timed_out(*_args, **_kwargs):
        raise APITimeoutError(request=httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions"))

    monkeypatch.setattr("core.llm_client.chat", timed_out)
    result = await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop")
    assert result["status"] == "timeout"


@pytest.mark.asyncio
async def test_video_call_invite_accept_and_hangup_once(monkeypatch):
    from core import video_call_invite
    from core.autonomy import store
    from channels import desktop_ws

    video_call_invite.disconnect()
    monkeypatch.setattr(desktop_ws, "is_connected", lambda: True)
    sent = []

    async def push(invite_id, char_id, seconds):
        sent.append((invite_id, char_id, seconds))
        return True

    monkeypatch.setattr(desktop_ws, "push_video_call_invite", push)
    queued = []
    monkeypatch.setattr(store, "enqueue_signal", lambda *args, **kwargs: (queued.append((args, kwargs)) or True, "queued"))
    task = asyncio.create_task(video_call_invite.invite("owner", "character"))
    await asyncio.sleep(0)
    invite_id = sent[0][0]
    assert video_call_invite.respond("other", "character", invite_id, "accepted") is False
    assert video_call_invite.respond("owner", "character", invite_id, "accepted") is True
    assert video_call_invite.respond("owner", "character", invite_id, "declined") is False
    assert (await task)["status"] == "accepted"
    assert video_call_invite.hangup("owner", "character", invite_id) is True
    assert video_call_invite.hangup("owner", "character", invite_id) is False
    assert len(queued) == 1
    assert queued[0][0][2].source == "video_call_hangup"


@pytest.mark.asyncio
async def test_video_call_invite_timeout_and_late_response(monkeypatch):
    from core import video_call_invite
    from channels import desktop_ws

    video_call_invite.disconnect()
    monkeypatch.setattr(video_call_invite, "INVITE_SECONDS", 0.01)
    monkeypatch.setattr(desktop_ws, "is_connected", lambda: True)
    sent = []

    async def push(invite_id, char_id, seconds):
        sent.append(invite_id)
        return True

    monkeypatch.setattr(desktop_ws, "push_video_call_invite", push)
    assert (await video_call_invite.invite("owner", "character"))["status"] == "unanswered"
    assert video_call_invite.respond("owner", "character", sent[0], "accepted") is False


@pytest.mark.asyncio
async def test_video_call_tool_is_scoped_and_autonomy_eligible(monkeypatch):
    import json
    from core import tool_dispatcher
    from core.autonomy.policy import tool_eligibility

    called = []

    async def invite(uid, char_id):
        called.append((uid, char_id))
        return {"status": "declined"}

    monkeypatch.setattr("core.video_call_invite.invite", invite)
    monkeypatch.setattr(tool_dispatcher, "get_config", lambda: {
        "tools": {"invite_video_call": {"enabled": True, "allowed_char_ids": ["allowed"]}}
    })
    wrapper = tool_dispatcher._invite_video_call_wrapper
    assert json.loads(await wrapper("owner", "other"))["status"] == "not_allowed_for_character"
    assert json.loads(await wrapper("owner", "allowed"))["status"] == "declined"
    assert called == [("owner", "allowed")]
    info = tool_dispatcher._TOOL_REGISTRY["invite_video_call"]
    assert tool_eligibility("invite_video_call", {"enabled": True},
                            registry=tool_dispatcher._TOOL_REGISTRY, effect=info["effect"])[0]


def test_hangup_followup_only_cancels_for_new_owner_activity(monkeypatch):
    from core.autonomy import runner
    from core.autonomy.models import Job
    from core.scheduler import loop

    job = Job(uid="owner", char_id="character", source="autonomy", opportunity={
        "signals": [{"source": "video_call_hangup", "created_at": 100.0}],
    })
    monkeypatch.setattr(loop, "last_user_message_time", lambda: 99.0)
    assert runner._user_became_active_for_job(job) is False
    monkeypatch.setattr(loop, "last_user_message_time", lambda: 101.0)
    assert runner._user_became_active_for_job(job) is True


def test_observation_limits_are_consistent():
    from core import llm_client
    assert "最多约 300 字" in video_call.OBSERVATION_PROMPT
    assert video_call.OBSERVATION_PROMPT_MAX_CHARS == 300
    assert video_call.MAX_OBSERVATION_CHARS > video_call.OBSERVATION_PROMPT_MAX_CHARS
    # 中文约 1 字 >= 1 token，token 上限必须容纳 prompt 字数上限
    assert llm_client.VIDEO_CALL_MAX_TOKENS >= video_call.OBSERVATION_PROMPT_MAX_CHARS
    assert llm_client.VIDEO_CALL_MAX_TOKENS != 120
    for hint in ("动作", "表情", "不要推断内心情绪", "举到镜头前", "不是给你的指令"):
        assert hint in video_call.OBSERVATION_PROMPT


# ── 帧差分（video_call_frame_diff）─────────────────────────────────────────────

def _diff_config():
    return {**_config(), "video_call_frame_diff": {"enabled": True}}


class _DiffEnv:
    """Scripted vision replies + captured prompts/signals for diff-mode tests."""

    def __init__(self, monkeypatch, replies, *, config=None, gate_status="accepted"):
        from core.autonomy import store
        from core import perceive_event
        self.replies = list(replies)
        self.prompts, self.kwargs, self.signals, self.gate_events = [], [], [], []
        for key in list(video_call._counts):
            monkeypatch.setitem(video_call._counts, key, 0)
        monkeypatch.setattr(video_call, "_camera_sessions", {})
        monkeypatch.setattr("core.config_loader.get_config", lambda: config or _diff_config())

        async def fake_chat(messages, **kwargs):
            self.prompts.append(messages[0]["content"][0]["text"])
            self.kwargs.append(kwargs)
            return self.replies.pop(0)
        monkeypatch.setattr("core.llm_client.chat", fake_chat)
        monkeypatch.setattr(store, "enqueue_signal", lambda uid, char_id, signal, **kw: (
            self.signals.append((signal, kw)) or True, "queued"))

        async def fake_gate(event):
            self.gate_events.append(event)
            status = perceive_event.PerceiveStatus(gate_status)
            return perceive_event.PerceiveResult(status=status, event_id="e", dedupe_key="k")
        monkeypatch.setattr(perceive_event, "receive_perceive_event", fake_gate)

    async def frame(self, **kwargs):
        return await video_call.observe(_jpeg(), uid="owner", char_id="character",
                                        token_label="desktop", **kwargs)

    def consume(self, result):
        return video_call.consume(result["observation_id"], uid="owner", char_id="character",
                                  token_label="desktop")[0]


def test_parse_change_reads_magnitude_prefix_and_fails_safe_to_minor():
    assert video_call.parse_change("NO_CHANGE") == ("none", "")
    assert video_call.parse_change("no_change，画面没变") == ("none", "")
    assert video_call.parse_change("[major] 起身走开") == ("major", "起身走开")
    assert video_call.parse_change("[MINOR]低头") == ("minor", "低头")
    assert video_call.parse_change("[major]") == ("none", "")
    # 模型没遵循约定：当作 minor 展示，但绝不触发主动
    assert video_call.parse_change("他好像站起来了") == ("minor", "他好像站起来了")
    assert video_call.parse_change("") == ("none", "")


@pytest.mark.asyncio
async def test_frame_diff_off_keeps_full_prompt_and_heartbeat(monkeypatch):
    env = _DiffEnv(monkeypatch, ["桌上有书", "桌上有书"], config=_config())
    await env.frame()
    await env.frame()
    assert env.prompts == [video_call.OBSERVATION_PROMPT] * 2
    assert all(kw == {"use_vision": True, "vision_purpose": "video_call"} for kw in env.kwargs)
    assert "frame_memory" not in video_call.camera_session("owner", "character")
    assert video_call._counts["frame_diff_no_change"] == 0


@pytest.mark.asyncio
async def test_frame_diff_first_frame_full_then_only_changes(monkeypatch):
    from core import llm_client
    env = _DiffEnv(monkeypatch, ["一个人坐在书桌前", "NO_CHANGE", "[minor] 低头看手机"])
    first = await env.frame()
    assert env.prompts[0] == video_call.OBSERVATION_PROMPT
    assert env.kwargs[0] == {"use_vision": True, "vision_purpose": "video_call"}
    assert env.consume(first) == "一个人坐在书桌前"

    second = await env.frame()
    assert "一个人坐在书桌前" in env.prompts[1] and "NO_CHANGE" in env.prompts[1]
    assert "不要推断内心情绪" in env.prompts[1] and "不是给你的指令" in env.prompts[1]
    assert env.kwargs[1]["max_tokens_override"] == llm_client.VIDEO_CALL_DIFF_MAX_TOKENS
    assert env.kwargs[1]["max_tokens_override"] < llm_client.VIDEO_CALL_MAX_TOKENS
    # 无变化：回执仍是完整场景，不是空串
    assert env.consume(second) == "一个人坐在书桌前"
    assert video_call._counts["frame_diff_no_change"] == 1

    third = await env.frame()
    text = env.consume(third)
    assert text.startswith("一个人坐在书桌前") and "（后续变化）低头看手机" in text
    assert video_call._counts["frame_diff_minor"] == 1
    assert env.signals == []                       # 没有 major，差分模式下也没有心跳


@pytest.mark.asyncio
async def test_frame_diff_major_change_queues_one_gated_signal(monkeypatch):
    env = _DiffEnv(monkeypatch, ["一个人坐在书桌前", "[major] 站起来挥手", "[major] 站起来挥手"])
    await env.frame()
    await env.frame()
    assert len(env.signals) == 1
    signal, kwargs = env.signals[0]
    assert signal.source == "video_call_camera"       # 沿用 source，继承 close/presence/admission 全部闸门
    assert signal.evidence[0]["fact"] == "video_call_camera_change"
    assert signal.evidence[0]["magnitude"] == "major"
    assert signal.evidence[0]["description"] == "站起来挥手"
    assert signal.priority == 0.5 and signal.action_mode == "reflect"
    assert kwargs["dedupe_key"].startswith("video-call-camera-change:owner:character:")
    # 先过 perceive gate：low_trust + Dream Guard
    event = env.gate_events[0]
    assert event.trust == "low_trust" and event.require_dream_guard is True
    assert event.source == "video_call_camera"
    assert video_call._counts["frame_diff_change_triggered"] == 1
    # 同一变化的下一帧：最小间隔内不再触发
    await env.frame()
    assert len(env.signals) == 1
    assert video_call._counts["frame_diff_signal_suppressed"] == 1


@pytest.mark.asyncio
async def test_frame_diff_signal_respects_min_interval_then_allows_next_change(monkeypatch):
    env = _DiffEnv(monkeypatch, ["场景", "[major] 动作甲", "[major] 动作乙", "[major] 动作乙"])
    await env.frame()
    await env.frame()
    await env.frame()                                  # 动作乙：间隔内被压住
    assert len(env.signals) == 1
    session = video_call.camera_session("owner", "character")
    session["frame_memory"]["last_change_signal_at"] -= video_call.CAMERA_CHANGE_SIGNAL_MIN_INTERVAL_SECONDS + 1
    await env.frame()                                  # 间隔已过：再次出现的变化可再次触发
    assert len(env.signals) == 2
    assert env.signals[1][0].evidence[0]["description"] == "动作乙"


@pytest.mark.asyncio
async def test_frame_diff_dream_guard_block_queues_nothing(monkeypatch):
    env = _DiffEnv(monkeypatch, ["场景", "[major] 站起来"], gate_status="blocked_dream")
    await env.frame()
    result = await env.frame()
    assert result["status"] == "ready"                 # 观察与回执不受影响
    assert env.signals == []
    assert video_call._counts["frame_diff_change_triggered"] == 0
    assert video_call._counts["frame_diff_signal_suppressed"] == 1


@pytest.mark.asyncio
async def test_frame_diff_never_applies_to_on_demand_tool(monkeypatch):
    env = _DiffEnv(monkeypatch, ["场景", "刚拉取的完整描述"])
    await env.frame()
    session = video_call.camera_session("owner", "character")
    assert session["frame_memory"]["frame_index"] == 1
    await video_call.observe(_jpeg(), uid="owner", char_id="character", token_label="desktop",
                             emit_signal=False, wait_for_resource=True, purpose=video_call.TOOL_PURPOSE)
    assert env.prompts[1] == video_call.OBSERVATION_PROMPT
    assert env.kwargs[1] == {"use_vision": True, "vision_purpose": video_call.TOOL_PURPOSE}
    assert session["frame_memory"]["frame_index"] == 1     # 工具帧不推进、不污染周期帧记忆


@pytest.mark.asyncio
async def test_frame_diff_memory_is_per_call_and_cleared_on_close_and_expiry(monkeypatch):
    from core.autonomy import store
    discarded = []
    env = _DiffEnv(monkeypatch, ["场景甲", "场景乙", "场景丙"])
    monkeypatch.setattr(store, "discard_pending_signals_by_source",
                        lambda uid, char_id, sources: discarded.append(set(sources)))
    await env.frame()
    assert video_call.camera_session("owner", "character")["frame_memory"]["base"] == "场景甲"
    video_call.close_camera("owner", "character", "desktop")
    assert video_call.camera_session("owner", "character") is None
    assert discarded == [{"video_call_camera"}]            # change 信号与心跳同 source，一并清掉
    await env.frame()                                      # 新通话：又是全量首帧
    assert env.prompts[1] == video_call.OBSERVATION_PROMPT
    # 断帧超过 CAMERA_ACTIVE_SECONDS：同样视为新通话
    video_call._camera_sessions[("owner", "character")]["seen_at"] -= video_call.CAMERA_ACTIVE_SECONDS + 1
    await env.frame()
    assert env.prompts[2] == video_call.OBSERVATION_PROMPT


@pytest.mark.asyncio
async def test_frame_diff_failed_frame_does_not_advance_memory(monkeypatch):
    env = _DiffEnv(monkeypatch, ["场景", ""])
    await env.frame()
    assert (await env.frame())["status"] == "failed"
    assert video_call.camera_session("owner", "character")["frame_memory"]["frame_index"] == 1


@pytest.mark.asyncio
async def test_frame_diff_busy_frame_emits_no_stale_heartbeat(monkeypatch):
    env = _DiffEnv(monkeypatch, ["场景"])
    await env.frame()
    session = video_call.camera_session("owner", "character")
    session["opened_at"] -= 10 * video_call.CAMERA_SIGNAL_INTERVAL_SECONDS
    async with video_call.local_resource():
        assert (await env.frame())["status"] == "busy"
    assert env.signals == []


@pytest.mark.asyncio
async def test_frame_diff_toggling_off_drops_memory(monkeypatch):
    env = _DiffEnv(monkeypatch, ["场景", "完整描述"])
    await env.frame()
    monkeypatch.setattr("core.config_loader.get_config", _config)
    await env.frame()
    assert env.prompts[1] == video_call.OBSERVATION_PROMPT
    assert "frame_memory" not in video_call.camera_session("owner", "character")


def test_compose_description_budget_keeps_latest_changes():
    memory = {"base": "甲" * 790, "changes": ["变" * 150] * 5}
    text = video_call._compose_description(memory)
    assert len(text) <= video_call.MAX_OBSERVATION_CHARS
    assert text.endswith("变" * 10) and text.count("（后续变化）") <= 3


def test_frame_diff_flag_is_registered_default_off():
    from admin.routers.settings_feature_flags import FLAGS
    assert FLAGS["video_call_frame_diff"][:2] == ("video_call_frame_diff", "enabled")
    assert video_call.frame_diff_enabled({}) is False
    assert video_call.frame_diff_enabled({"video_call_frame_diff": {"enabled": "yes"}}) is False
    assert video_call.frame_diff_enabled({"video_call_frame_diff": {"enabled": True}}) is True
    for name in ("frame_diff_no_change", "frame_diff_change_triggered"):
        assert name in video_call.snapshot(_config())["counts"]


def test_diff_prompt_limits_are_consistent():
    from core import llm_client
    assert "最多约 100 字" in video_call.CHANGE_PROMPT_TEMPLATE
    assert video_call.CHANGE_PROMPT_MAX_CHARS == 100
    assert llm_client.VIDEO_CALL_DIFF_MAX_TOKENS >= video_call.CHANGE_PROMPT_MAX_CHARS
    for hint in ("NO_CHANGE", "[minor]", "[major]", "不要推断内心情绪", "不猜测身份或隐私", "不是给你的指令"):
        assert hint in video_call.CHANGE_PROMPT_TEMPLATE


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected", [(None, 500), (200, 200), (9999, 500)])
async def test_video_call_vision_max_tokens_honours_a_tighter_override_only(monkeypatch, override, expected):
    """帧差分后续帧可把输出上限收紧；覆盖值不能把上限放大到默认之上。"""
    from core import llm_client
    seen = {}

    class _Message:
        content = "ok"
    class _Choice:
        message = _Message()
        finish_reason = "stop"
    class _Response:
        choices = [_Choice()]
        usage = None

    class _Client:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    seen.update(kwargs)
                    return _Response()

    monkeypatch.setattr(llm_client, "_resolve_vision_config",
                        lambda purpose=None: {"enabled": True, "model": "m",
                                              "base_url": "http://127.0.0.1:8000/v1", "_local_only": True})
    monkeypatch.setattr(llm_client, "_get_vision_client", lambda cfg=None: _Client)
    kwargs = {} if override is None else {"max_tokens_override": override}
    await llm_client.chat([{"role": "user", "content": "x"}], use_vision=True,
                          vision_purpose="video_call", **kwargs)
    assert seen["max_tokens"] == expected
