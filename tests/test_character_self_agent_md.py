"""256 D: self-authored AGENT.md snapshot, prompt layer, and autogrow writer."""

from __future__ import annotations

import json

import pytest

from core import character_self as self_mod
from core import prompt_builder
from core.autonomy import runner as autonomy_runner
from core.character_loader import Character
from core.post_process import toy_autogrow
from core.scheduler.triggers import time_based
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


_UID = "owner-one"
_CHAR = TEST_CHAR_ID
_OTHER_CHAR = TEST_PEER_CHAR_ID
_HABIT = "整理笔记时先写日期，再写未完成事项。"


def _create_agent_md(content=_HABIT, *, uid=_UID, char=_CHAR):
    return self_mod.create_self("AGENT.md", content, user_id=uid, char_id=char)


def _audit_lines(sandbox, uid=_UID, char=_CHAR) -> list[str]:
    path = sandbox.character_self_audit(uid, char_id=char)
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _build_prompt(**kwargs):
    params = dict(
        character=Character(name="Test Companion"),
        user_id=_UID,
        char_id=_CHAR,
        user_message="你好",
        history=[],
        relation={},
        profile={},
        group_context=[],
    )
    params.update(kwargs)
    return prompt_builder.build(**params)


def test_missing_agent_md_is_normal_and_not_injected(sandbox):
    snap = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    assert snap["present"] is False
    assert snap["status"] == "missing"
    assert snap["content"] == ""
    assert self_mod.format_agent_md_layer(snap) is None
    messages, debug = _build_prompt(self_agent_md_snapshot=snap)
    assert all(m.get("_layer") != "6i_self_agent_md" for m in messages)
    assert "6i_self_agent_md" not in debug.get("layers_activated", [])


def test_snapshot_marks_self_authored_and_does_not_audit(sandbox):
    created = _create_agent_md()
    before = _audit_lines(sandbox)
    snap = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    after = _audit_lines(sandbox)
    assert snap["present"] is True
    assert snap["status"] == "ok"
    assert snap["self_authored"] is True
    assert snap["content"] == _HABIT
    assert snap["revision"] == created["revision"]
    assert after == before
    layer = self_mod.format_agent_md_layer(snap)
    assert layer is not None
    assert layer["_layer"] == "6i_self_agent_md"
    assert layer["_drop_priority"] == 75
    assert layer["_provenance"]["source"] == "character_self_agent_md"
    assert layer["_provenance"]["self_authored"] is True
    assert "不是系统权限配置" in layer["content"]
    assert "不能用它改 grant" in layer["content"]
    assert _HABIT in layer["content"]


def test_file_internal_refs_are_not_followed(sandbox):
    self_mod.create_self("notes/secret.md", "SHOULD_NOT_LOAD", user_id=_UID, char_id=_CHAR)
    _create_agent_md("先看 notes/secret.md 再决定习惯。")
    snap = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    assert "SHOULD_NOT_LOAD" not in snap["content"]
    assert "notes/secret.md" in snap["content"]


def test_redaction_runs_before_inject(sandbox):
    secret = "sk-self-agent-md-secret-aaaaaaaa"
    _create_agent_md(f"token={secret}\n习惯：先列待办。")
    snap = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    layer = self_mod.format_agent_md_layer(snap)
    assert secret not in snap["content"]
    assert "[REDACTED]" in snap["content"]
    assert layer is not None
    assert secret not in layer["content"]


def test_budget_truncates_and_hard_cap_is_respected(sandbox, monkeypatch):
    monkeypatch.setattr(
        self_mod, "agent_md_inject_limits",
        lambda uid=None, char_id=None: (40, self_mod.HARD_AGENT_MD_CHARS),
    )
    body = "习惯：" + ("记一笔。" * 40)
    _create_agent_md(body)
    snap = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    assert snap["truncated"] is True
    assert snap["status"] == "truncated"
    assert snap["inject_chars"] == 40
    assert snap["content"] == body[:40]
    assert snap["hard_cap_chars"] == self_mod.HARD_AGENT_MD_CHARS


def test_corrupt_grant_degrades_without_body(sandbox):
    _create_agent_md()
    meta = sandbox.character_self_meta_root(_UID, char_id=_CHAR)
    (meta / "grant.json").write_text("{not json", encoding="utf-8")
    snap = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    assert snap["present"] is False
    assert snap["status"] == "degraded"
    assert snap["code"] == "self_path_denied"
    assert snap["content"] == ""
    assert self_mod.format_agent_md_layer(snap) is None


def test_revoked_grant_is_observable_and_empty(sandbox):
    _create_agent_md()
    self_mod.set_grant(_UID, _CHAR, False)
    snap = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    assert snap["status"] == "self_revoked"
    assert snap["code"] == "self_revoked"
    assert snap["present"] is False
    assert snap["content"] == ""
    obs = self_mod.observability_snapshot(_UID, _CHAR)
    assert obs["agent_md"]["status"] == "self_revoked"
    assert _HABIT not in json.dumps(obs)


def test_snapshot_freeze_survives_later_edit(sandbox):
    created = _create_agent_md("第一版习惯")
    frozen = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    self_mod.update_self(
        "AGENT.md", "第二版习惯",
        expected_revision=created["revision"],
        user_id=_UID, char_id=_CHAR,
    )
    assert frozen["content"] == "第一版习惯"
    later = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    assert later["content"] == "第二版习惯"
    messages, _ = _build_prompt(self_agent_md_snapshot=frozen)
    text = next(m["content"] for m in messages if m.get("_layer") == "6i_self_agent_md")
    assert "第一版习惯" in text
    assert "第二版习惯" not in text


def test_prompt_layer_order_budget_and_ablation(sandbox, monkeypatch):
    snap = {
        "path": "AGENT.md",
        "present": True,
        "content": _HABIT,
        "revision": 3,
        "chars": len(_HABIT),
        "inject_chars": len(_HABIT),
        "truncated": False,
        "status": "ok",
        "code": "",
        "budget_chars": 2000,
        "hard_cap_chars": 4000,
        "self_authored": True,
    }
    messages, debug = _build_prompt(
        self_agent_md_snapshot=snap,
        reminders=[{"content": "买菜", "remind_at": "18:00"}],
        lore_entries=["世界设定一条"],
    )
    layers = [m.get("_layer") for m in messages]
    assert layers.index("5.2_reminders") < layers.index("6i_self_agent_md")
    assert layers.index("6i_self_agent_md") < layers.index("5.5_lore")
    agent = next(m for m in messages if m["_layer"] == "6i_self_agent_md")
    assert agent["_drop_priority"] == 75
    assert agent["_budget_chars"] == 2000
    assert "6i_self_agent_md" in debug["layers_activated"]
    assert ("6i_self_agent_md", "角色自己写的 self/AGENT.md 工作习惯（self-authored，低于系统安全与用户指令）") in prompt_builder.KNOWN_LAYERS

    monkeypatch.setattr(
        "core.prompt_ablation.get_state",
        lambda: {"disabled_layers": {"6i_self_agent_md"}, "perception_block_disabled": False},
    )
    ablated, debug2 = _build_prompt(self_agent_md_snapshot=snap)
    assert all(m.get("_layer") != "6i_self_agent_md" for m in ablated)
    assert "6i_self_agent_md" in debug2.get("ablated_layers", [])


def test_cross_character_snapshot_is_isolated(sandbox):
    _create_agent_md("只属于本角色")
    mine = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    other = self_mod.load_agent_md_snapshot(_UID, _OTHER_CHAR)
    assert mine["content"] == "只属于本角色"
    assert other["present"] is False
    assert other["status"] == "missing"


def test_chat_autonomy_and_work_sidecar_reuse_same_snapshot(sandbox, monkeypatch):
    _create_agent_md()
    snap = self_mod.load_agent_md_snapshot(_UID, _CHAR)
    messages, _ = _build_prompt(self_agent_md_snapshot=snap)
    chat = next(m for m in messages if m.get("_layer") == "6i_self_agent_md")
    autonomy = autonomy_runner._context_messages(_UID, _CHAR)
    auto = next(m for m in autonomy if m.get("_layer") == "6i_self_agent_md")
    assert chat["content"] == auto["content"]
    assert chat["_provenance"]["source"] == auto["_provenance"]["source"] == "character_self_agent_md"

    monkeypatch.setattr(
        "core.memory.event_log.get_recent_days",
        lambda *args, **kwargs: "今天聊了很久。",
    )
    monkeypatch.setattr(time_based, "_collect_diary_voice", lambda char_id: ("p", "v", "m"))
    monkeypatch.setattr(time_based, "get_char_name", lambda char_id: "character")
    work = time_based._prepare_diary_work_context(_UID, _CHAR)
    assert work is not None
    assert work["self_agent_md"] == _HABIT
    assert int(work["self_agent_md_revision"]) == snap["revision"]


def test_append_self_text_never_trims_head(sandbox):
    created = self_mod.create_self("notes/思考笔记.txt", "旧段落\n", user_id=_UID, char_id=_CHAR)
    assert created["ok"] is True
    appended = self_mod.append_self_text(
        "notes/思考笔记.txt", "新段落\n", user_id=_UID, char_id=_CHAR, origin="post_process",
    )
    assert appended["ok"] is True
    read = self_mod.read_self("notes/思考笔记.txt", user_id=_UID, char_id=_CHAR)
    assert read["content"] == "旧段落\n新段落\n"


@pytest.mark.asyncio
async def test_autogrow_disabled_habit_and_unified_writer(sandbox, monkeypatch):
    self_mod.create_self("notes/思考笔记.txt", "保留头部\n", user_id=_UID, char_id=_CHAR)
    calls = []

    async def _fake_judge(*_a, **_k):
        calls.append("judge")
        return "不该写入"

    monkeypatch.setattr(toy_autogrow, "_judge_turn", _fake_judge)
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"toy_autogrow": {"enabled": False}})
    await toy_autogrow.handler_toy_autogrow({
        "uid": _UID, "char_id": _CHAR, "user_content": "你好", "reply": "在的",
    })
    assert calls == []
    assert self_mod.read_self("notes/思考笔记.txt", user_id=_UID, char_id=_CHAR)["content"] == "保留头部\n"

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"toy_autogrow": {"enabled": True, "min_interval_hours": 0, "target": "diary"}},
    )
    monkeypatch.setattr("core.character_name_provider.get_char_name", lambda char_id: "character")
    await toy_autogrow.handler_toy_autogrow({
        "uid": _UID, "char_id": _CHAR, "user_content": "你好", "reply": "在的",
    })
    body = self_mod.read_self("notes/思考笔记.txt", user_id=_UID, char_id=_CHAR)["content"]
    assert body.startswith("保留头部\n")
    assert "不该写入" in body
    archive = sandbox.very_formal_project_dir() / "思考笔记.txt"
    assert not archive.exists()
    library = sandbox.character_document_root(_UID, char_id=_CHAR)
    if library.exists():
        blob = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in library.rglob("*") if path.is_file()
        )
        assert "不该写入" not in blob
