from types import SimpleNamespace

import pytest


def _stage():
    from core.stage.models import Stage, StageSettings

    return Stage(
        "relations-g",
        "owner",
        ("yexuan", "yexuanJ-5412", "hongcha"),
        settings=StageSettings(max_responders=2),
    )


def test_projection_participation_weights_and_clamp():
    from core.stage.projection import participation_memory_strength

    stage = _stage()
    segment = [
        SimpleNamespace(speaker_id="yexuan", content="第一句", _addressed="yexuan"),
        SimpleNamespace(speaker_id="yexuan", content="第二句", _addressed=False),
        SimpleNamespace(speaker_id="yexuanJ-5412", content="一句", _addressed=False),
    ]

    assert participation_memory_strength(stage, segment, "yexuan") == pytest.approx(0.8)
    assert participation_memory_strength(stage, segment, "yexuanJ-5412") == pytest.approx(0.55)
    assert participation_memory_strength(stage, segment, "hongcha") == pytest.approx(0.4)
    noisy = [SimpleNamespace(speaker_id="yexuan", content="x", _addressed="yexuan") for _ in range(10)]
    assert participation_memory_strength(stage, noisy, "yexuan") == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_projection_carries_speakers_and_group_attribution_prompt(sandbox, monkeypatch):
    from core.stage.models import TranscriptEntry
    from core.stage.projection import enqueue_reality_projection
    from core.stage.store import append_transcript, create_stage
    from core import llm_client

    stage = create_stage("projection-attribution", "owner", ["yexuan", "yexuanJ-5412"])
    append_transcript(stage, TranscriptEntry("owner", "请你们谈谈", 1, "t", "user"))
    append_transcript(stage, TranscriptEntry("yexuan", "我先说。", 2, "t", "user"))
    jobs = []
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda kind, payload: jobs.append((kind, payload)))
    await enqueue_reality_projection("projection-attribution")

    assert all("：" in payload["user_content"] for _, payload in jobs)
    captured = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="甲说了，乙回应。"))])

    monkeypatch.setattr(llm_client, "get_model_client", lambda _: SimpleNamespace(client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())), model="test"))
    assert await llm_client.summarize_turn("甲：你好\n乙：回应", "甲：补充", tags=["group_chat"])
    assert "保留名字归属" in captured["messages"][0]["content"]


@pytest.mark.asyncio
async def test_relation_handler_writes_provenance_then_cools_down(sandbox, monkeypatch):
    from core.stage.char_relations import handler_update_char_relations, load_relation
    from core.memory.provenance_log import query

    calls = []

    async def fake_chat(*args, **kwargs):
        calls.append(args)
        return '{"a_of_b":{"summary":"甲觉得乙很直接","valence":0.2},"b_of_a":{"summary":"乙认为甲值得商量","valence":0.4}}'

    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    payload = {
        "uid": "owner", "char_a": "yexuan", "char_b": "yexuanJ-5412",
        "excerpt": "甲→乙：回应", "timestamp": 100000.0,
        "write_envelope": {"source": "user_chat", "can_write_memory": True},
    }
    await handler_update_char_relations(payload)
    relation = load_relation("yexuan", "yexuanJ-5412")
    assert relation["interaction_count"] == 1
    assert relation["a_of_b"]["summary"]
    assert query("owner", "yexuan", artifact="char_relation")

    payload["timestamp"] += 60
    await handler_update_char_relations(payload)
    assert load_relation("yexuan", "yexuanJ-5412")["interaction_count"] == 2
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_relation_handler_rejects_test_envelope(sandbox, monkeypatch):
    from core.stage.char_relations import handler_update_char_relations, load_relation

    async def should_not_call(*args, **kwargs):
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr("core.llm_client.chat", should_not_call)
    await handler_update_char_relations({
        "uid": "owner", "char_a": "yexuan", "char_b": "yexuanJ-5412",
        "excerpt": "x", "write_envelope": {"can_write_memory": True, "is_test": True},
    })
    assert load_relation("yexuan", "yexuanJ-5412") is None


@pytest.mark.asyncio
async def test_only_direct_ai_pairs_enqueue_relation_updates(sandbox, monkeypatch):
    from core.stage.char_relations import enqueue_relation_updates
    from core.stage.models import TranscriptEntry
    from core.stage.store import append_transcript, create_stage

    stage = create_stage("pair-queue", "owner", ["yexuan", "yexuanJ-5412"])
    append_transcript(stage, TranscriptEntry("owner", "先说", 1, "round", "user"))
    append_transcript(stage, TranscriptEntry("yexuan", "我说", 2, "round", "user"))
    append_transcript(stage, TranscriptEntry("yexuanJ-5412", "我接你的话", 3, "round", "yexuan"))
    jobs = []
    monkeypatch.setattr("core.post_process.slow_queue.enqueue", lambda kind, payload: jobs.append((kind, payload)))

    assert await enqueue_relation_updates("pair-queue", "round") == 1
    assert jobs[0][0] == "update_char_relations"
    assert {jobs[0][1]["char_a"], jobs[0][1]["char_b"]} == {"yexuan", "yexuanJ-5412"}
    assert "owner" not in {jobs[0][1]["char_a"], jobs[0][1]["char_b"]}


def test_relation_presence_and_explicit_delete(sandbox):
    from core.stage.char_relations import _empty_relation, _save_relation, delete_relation
    from core.stage.context import render_presence
    from core.memory.provenance_log import query

    relation = _empty_relation("yexuan", "yexuanJ-5412")
    relation["a_of_b"]["summary"] = "甲会认真听乙说话"
    relation["b_of_a"]["summary"] = "乙觉得甲很有主见"
    assert _save_relation(relation)
    presence = render_presence(_stage(), viewer_id="yexuan")
    assert "角色间既有印象" in presence
    assert presence.count("的印象：") == 2
    assert delete_relation("yexuan", "yexuanJ-5412", uid="owner")
    records = query("owner", "yexuan", artifact="char_relation")
    assert any(record["trigger_signal"] == "explicit_forget" for record in records)
