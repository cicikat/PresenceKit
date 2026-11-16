"""
tests/test_dream_scenario_crud.py — 梦境剧本 CRUD 端点契约测试（Brief 96 §2）

Covers:
  ① GET /dream/scenarios 空列表（目录不存在）→ 不报错
  ② POST 新建 → GET 列表 / GET 详情 均可读到
  ③ POST 重复 id → 409
  ④ POST/PUT YAML 解析失败 → 422（具体信息，不是 500）
  ⑤ POST/PUT schema 校验失败（缺 stages）→ 422（复用 scenario_loader 真实 schema）
  ⑥ YAML 内 id 与路径 id 不一致 → 422
  ⑦ PUT/DELETE 时若正在被进行中的梦引用该剧本 → 拒绝
  ⑧ DELETE 后 GET → 404
  ⑨ validate 草稿 YAML → canonical serialize 且不落盘
  ⑩ validate 编辑 ID mismatch / duplicate stage ID → 422
"""

import asyncio

import pytest
from fastapi import HTTPException
from unittest.mock import patch

_UID = "dream_scenario_crud_test"

_VALID_YAML = """id: crud_demo
title: CRUD Demo
stages:
  - id: s1
    name: Stage One
    dramatic_task: task text
    entry_pressure: pressure text
    exit_signs:
      - a concrete action happens
"""


def _run(coro):
    return asyncio.run(coro)


def _as(uid):
    return patch('admin.routers.dream_common._owner_uid', return_value=uid)


# ═══════════════════════════════════════════════════════════════════════════
# ① 空列表
# ═══════════════════════════════════════════════════════════════════════════

def test_list_scenarios_empty_when_dir_missing(sandbox):
    from admin.routers.dream import list_dream_scenarios

    with _as(_UID):
        result = _run(list_dream_scenarios())
    assert result == {"scenarios": []}


# ═══════════════════════════════════════════════════════════════════════════
# ② 新建 → 列表 / 详情
# ═══════════════════════════════════════════════════════════════════════════

def test_create_then_list_and_get(sandbox):
    from admin.routers.dream import create_dream_scenario, list_dream_scenarios, get_dream_scenario

    with _as(_UID):
        result = _run(create_dream_scenario({"id": "crud_demo", "yaml": _VALID_YAML}))
        assert result == {"ok": True, "id": "crud_demo"}

        listed = _run(list_dream_scenarios())
        assert listed["scenarios"] == [{
            "id": "crud_demo",
            "title": "CRUD Demo",
            "source": "user",
            "progressable": True,
            "unprogressable_stage_ids": [],
        }]

        detail = _run(get_dream_scenario("crud_demo"))
        assert detail["id"] == "crud_demo"
        assert "CRUD Demo" in detail["yaml"]
        assert detail["document"]["title"] == "CRUD Demo"
        assert detail["source"] == "user"

    on_disk = sandbox.dream_scenarios_dir() / "crud_demo.yaml"
    assert on_disk.exists()


# ═══════════════════════════════════════════════════════════════════════════
# ③ 重复 id
# ═══════════════════════════════════════════════════════════════════════════

def test_create_duplicate_rejected(sandbox):
    from admin.routers.dream import create_dream_scenario

    with _as(_UID):
        _run(create_dream_scenario({"id": "dupe_script", "yaml": _VALID_YAML.replace("crud_demo", "dupe_script")}))
        with pytest.raises(HTTPException) as exc:
            _run(create_dream_scenario({"id": "dupe_script", "yaml": _VALID_YAML.replace("crud_demo", "dupe_script")}))
    assert exc.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════
# ④ YAML 解析失败
# ═══════════════════════════════════════════════════════════════════════════

def test_create_invalid_yaml_syntax_422(sandbox):
    from admin.routers.dream import create_dream_scenario

    bad_yaml = "id: [unterminated\n  - broken"
    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(create_dream_scenario({"id": "bad_yaml", "yaml": bad_yaml}))
    assert exc.value.status_code == 422
    assert "YAML" in exc.value.detail


def test_create_yaml_must_be_mapping_422(sandbox):
    from admin.routers.dream import create_dream_scenario

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(create_dream_scenario({"id": "list_not_mapping", "yaml": "- a\n- b\n"}))
    assert exc.value.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ schema 校验失败（复用 scenario_loader 真实 schema，不是另一套）
# ═══════════════════════════════════════════════════════════════════════════

def test_create_missing_stages_rejected_with_field_detail(sandbox):
    from admin.routers.dream import create_dream_scenario

    yaml_no_stages = "id: no_stages\ntitle: No Stages\n"
    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(create_dream_scenario({"id": "no_stages", "yaml": yaml_no_stages}))
    assert exc.value.status_code == 422
    assert "stage" in exc.value.detail.lower()


def test_update_missing_stage_field_rejected(sandbox):
    from admin.routers.dream import create_dream_scenario, update_dream_scenario

    with _as(_UID):
        _run(create_dream_scenario({"id": "edit_target", "yaml": _VALID_YAML.replace("crud_demo", "edit_target")}))
        bad_yaml = "id: edit_target\ntitle: Edit Target\nstages:\n  - id: s1\n    name: only name\n"
        with pytest.raises(HTTPException) as exc:
            _run(update_dream_scenario("edit_target", {"yaml": bad_yaml}))
    assert exc.value.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# ⑥ YAML 内 id 与路径 id 不一致
# ═══════════════════════════════════════════════════════════════════════════

def test_create_id_mismatch_rejected(sandbox):
    from admin.routers.dream import create_dream_scenario

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(create_dream_scenario({"id": "path_id", "yaml": _VALID_YAML}))  # yaml declares id: crud_demo
    assert exc.value.status_code == 422
    assert "id" in exc.value.detail.lower()


# ═══════════════════════════════════════════════════════════════════════════
# ⑦ 正在被进行中的梦引用 → 拒绝编辑/删除
# ═══════════════════════════════════════════════════════════════════════════

def test_update_and_delete_blocked_while_scenario_active(sandbox):
    from admin.routers.dream import create_dream_scenario, update_dream_scenario, delete_dream_scenario
    from core.dream.dream_state import write_state, DreamStatus

    script_id = "active_script"
    yaml_text = _VALID_YAML.replace("crud_demo", script_id)
    with _as(_UID):
        _run(create_dream_scenario({"id": script_id, "yaml": yaml_text}))
        write_state(_UID, {
            "status": DreamStatus.DREAM_ACTIVE.value,
            "dream_mode": "scenario",
            "scenario_core": {"script_id": script_id, "current_stage_id": "s1"},
            "dream_id": "d1",
        })

        with pytest.raises(HTTPException) as exc:
            _run(update_dream_scenario(script_id, {"yaml": yaml_text}))
        assert exc.value.status_code == 409

        with pytest.raises(HTTPException) as exc:
            _run(delete_dream_scenario(script_id))
        assert exc.value.status_code == 409

        # positive control: after the dream ends, edit/delete succeed
        write_state(_UID, {"status": DreamStatus.REALITY_CHAT.value})
        result = _run(update_dream_scenario(script_id, {"yaml": yaml_text}))
        assert result["ok"] is True


# ═══════════════════════════════════════════════════════════════════════════
# ⑧ 删除后 GET → 404
# ═══════════════════════════════════════════════════════════════════════════

def test_delete_then_get_404(sandbox):
    from admin.routers.dream import create_dream_scenario, delete_dream_scenario, get_dream_scenario

    script_id = "to_be_deleted"
    with _as(_UID):
        _run(create_dream_scenario({"id": script_id, "yaml": _VALID_YAML.replace("crud_demo", script_id)}))
        result = _run(delete_dream_scenario(script_id))
        assert result == {"ok": True, "deleted": script_id}

        with pytest.raises(HTTPException) as exc:
            _run(get_dream_scenario(script_id))
    assert exc.value.status_code == 404


def test_structured_document_round_trip(sandbox):
    from admin.routers.dream import create_dream_scenario, get_dream_scenario

    document = {
        "id": "json_editor",
        "title": "JSON Editor",
        "private_truths": [{
            "id": "hidden_identity",
            "truth": "The stranger already knows who he is.",
            "disclosure": {
                "opening": {"policy": "hint_only", "allowed_hints": ["A familiar gesture"]},
            },
        }],
        "stages": [{
            "id": "opening",
            "name": "Opening",
            "dramatic_task": "Begin the conflict",
            "entry_pressure": "The door closes",
            "exit_signs": ["A choice is made"],
            "not_yet_allowed": ["No instant escape"],
            "drift_pressure": {"after_turns": 3, "instruction": "Raise the stakes"},
        }],
    }
    with _as(_UID):
        _run(create_dream_scenario({"id": "json_editor", "document": document}))
        detail = _run(get_dream_scenario("json_editor"))

    assert detail["document"] == document
    assert "drift_pressure:" in detail["yaml"]
    assert "private_truths:" in detail["yaml"]


def test_validate_yaml_draft_returns_canonical_document_without_writing(sandbox):
    from admin.routers.dream import validate_dream_scenario

    yaml_text = """# editor comment\nid: draft_yaml\ntitle: 多行标题\nstages:\n  - id: opening\n    name: 开场\n    dramatic_task: |\n      第一行\n      第二行\n    entry_pressure: 入口压力\n    exit_signs:\n      - 完成一个具体行动\n"""
    with _as(_UID):
        result = _run(validate_dream_scenario({"yaml": yaml_text}))

    assert result["ok"] is True
    assert result["id"] == "draft_yaml"
    assert result["document"]["stages"][0]["dramatic_task"] == "第一行\n第二行\n"
    assert "# editor comment" not in result["yaml"]
    assert "多行标题" in result["yaml"]
    assert not list(sandbox.dream_scenario_read_dirs()[0].glob("*.yaml"))


def test_validate_field_outline_yaml_converts_to_canonical_scenario(sandbox):
    from admin.routers.dream import validate_dream_scenario

    outline = """id: outline_demo
title: Outline demo
private_truths:
id: hidden_fact
truth: The house changes around the visitor.
disclosure:
opening:
policy: hint_only
allowed_hints:
The walls move slightly.
The clock skips a beat.
acceptance:
policy: reveal_required
stages:
id: opening
name: Opening
dramatic_task: >
The visitor wakes in a strange house.
entry_pressure: >
The door locks behind them.
exit_signs:
The visitor notices the moving walls.
not_yet_allowed:
The visitor leaves immediately.
drift_pressure:
after_turns: 3
instruction: >
Raise the unease without resolving the mystery.
id: acceptance
name: Acceptance
dramatic_task: >
The truth can no longer remain hidden.
entry_pressure: >
The room changes shape.
exit_signs:
The visitor acknowledges the truth.
"""

    with _as(_UID):
        result = _run(validate_dream_scenario({"yaml": outline}))

    assert result["id"] == "outline_demo"
    assert [stage["id"] for stage in result["document"]["stages"]] == ["opening", "acceptance"]
    truth = result["document"]["private_truths"][0]
    assert truth["disclosure"]["opening"]["allowed_hints"] == [
        "The walls move slightly.", "The clock skips a beat."
    ]
    assert "stages:\n- id: opening" in result["yaml"]


def test_validate_yaml_draft_rejects_current_id_mismatch_and_duplicate_stage(sandbox):
    from admin.routers.dream import validate_dream_scenario

    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(validate_dream_scenario({"id": "current_id", "yaml": _VALID_YAML}))
    assert exc.value.status_code == 422
    assert "id" in exc.value.detail.lower()

    duplicate = """id: duplicate_stage
title: Duplicate Stage
stages:
  - id: s1
    name: Stage One
    dramatic_task: task one
    entry_pressure: pressure one
  - id: s1
    name: Stage Two
    dramatic_task: task two
    entry_pressure: pressure two
"""
    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(validate_dream_scenario({"yaml": duplicate}))
    assert exc.value.status_code == 422
    assert "duplicate stage" in exc.value.detail.lower()


def test_new_or_edited_scenario_must_have_completion_signals_but_legacy_is_marked(sandbox, tmp_path, monkeypatch):
    from admin.routers.dream import create_dream_scenario, list_dream_scenarios

    stuck = {
        "id": "stuck_demo",
        "title": "Stuck Demo",
        "stages": [{
            "id": "opening",
            "name": "Opening",
            "dramatic_task": "task",
            "entry_pressure": "pressure",
        }],
    }
    with _as(_UID):
        with pytest.raises(HTTPException) as exc:
            _run(create_dream_scenario({"id": "stuck_demo", "document": stuck}))
    assert exc.value.status_code == 422
    assert "cannot progress" in exc.value.detail

    legacy_dir = tmp_path / "legacy-scenarios"
    legacy_dir.mkdir()
    legacy = dict(stuck)
    legacy["id"] = "stuck_legacy"
    import yaml
    (legacy_dir / "stuck_legacy.yaml").write_text(yaml.safe_dump(legacy), encoding="utf-8")
    monkeypatch.setattr(sandbox, "dream_scenario_read_dirs", lambda: (tmp_path / "user-scenarios", legacy_dir))
    with _as(_UID):
        listed = _run(list_dream_scenarios())
    assert listed["scenarios"] == [{
        "id": "stuck_legacy",
        "title": "Stuck Demo",
        "source": "legacy",
        "progressable": False,
        "unprogressable_stage_ids": ["opening"],
    }]


def test_legacy_scenario_is_read_only_and_update_creates_userdata_override(sandbox, tmp_path, monkeypatch):
    from admin.routers.dream import (
        delete_dream_scenario,
        get_dream_scenario,
        list_dream_scenarios,
        update_dream_scenario,
    )

    user_dir = tmp_path / "userdata" / "characters" / "dream" / "scenarios"
    legacy_dir = tmp_path / "data" / "dream" / "scenarios"
    legacy_dir.mkdir(parents=True)
    legacy_path = legacy_dir / "legacy_demo.yaml"
    legacy_path.write_text(_VALID_YAML.replace("crud_demo", "legacy_demo"), encoding="utf-8")
    monkeypatch.setattr(sandbox, "dream_scenario_read_dirs", lambda: (user_dir, legacy_dir))
    monkeypatch.setattr(sandbox, "dream_scenario_write_path", lambda script_id: user_dir / f"{script_id}.yaml")

    with _as(_UID):
        listed = _run(list_dream_scenarios())
        assert listed["scenarios"] == [{
            "id": "legacy_demo",
            "title": "CRUD Demo",
            "source": "legacy",
            "progressable": True,
            "unprogressable_stage_ids": [],
        }]
        detail = _run(get_dream_scenario("legacy_demo"))
        assert detail["source"] == "legacy"

        with pytest.raises(HTTPException) as exc:
            _run(delete_dream_scenario("legacy_demo"))
        assert exc.value.status_code == 409

        updated = dict(detail["document"])
        updated["title"] = "User Override"
        result = _run(update_dream_scenario("legacy_demo", {"document": updated}))
        assert result["source"] == "user"

    assert user_dir.joinpath("legacy_demo.yaml").exists()
    assert "CRUD Demo" in legacy_path.read_text(encoding="utf-8")
