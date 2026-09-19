from __future__ import annotations

import asyncio
import json

import pytest


UID = "agent-task-owner"
CHAR = "agent-task-character"


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    IDLE = "idle"
    status = IDLE

    def set_waiting_confirm(self, _tool_name, _tool_args):
        self.status = self.WAITING_CONFIRM


def _config(monkeypatch, root, *, revision=1, operations=None):
    cfg = {
        "workspace_access": {
            "enabled": True,
            "roots": [{"id": "project", "path": str(root)}],
            "permissions": {
                "read": True,
                "list": True,
                "create": True,
                "update": True,
                "delete": False,
            },
            "max_file_bytes": 100_000,
            "max_total_bytes": 1_000_000,
        },
        "process_runner": {"enabled": True},
        "agent_tasks": {
            "enabled": True,
            "max_concurrent_per_character": 1,
            "max_steps": 8,
            "max_seconds": 120,
            "max_tokens": 1000,
            "workspace_manifests": {
                "project": {
                    "enabled": True,
                    "revision": revision,
                    "operations": operations or ["read", "create", "update", "run"],
                }
            },
        },
    }
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    monkeypatch.setattr("core.agent_runtime.workspace._project_data", lambda: root / "private-data")
    return cfg


async def _wait_terminal(task_id: str):
    from core.agent_runtime.agent_tasks import get_agent_task
    from core.agent_runtime.models import TaskPrincipal

    principal = TaskPrincipal.reality(UID, CHAR)
    for _ in range(100):
        result = get_agent_task(principal, task_id)
        if result["status"] in {"succeeded", "failed", "canceled", "outcome_unknown"}:
            return result
        await asyncio.sleep(0.05)
    raise AssertionError("Agent task did not finish")


@pytest.mark.asyncio
async def test_chat_tool_runs_bounded_worker_and_returns_artifacts(monkeypatch, tmp_path, sandbox):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "sample.txt").write_text("before", encoding="utf-8", newline="\n")
    (root / "verify.py").write_text(
        "from pathlib import Path\nassert Path('sample.txt').read_text() == 'after'\nprint('ok')\n",
        encoding="utf-8",
        newline="\n",
    )
    _config(monkeypatch, root)

    async def fake_chat(*_args, **_kwargs):
        return json.dumps({
            "summary": "updated and verified",
            "actions": [
                {"op": "update", "path": "sample.txt", "content": "after"},
                {"op": "run", "program": "verify.py", "args": []},
            ],
        })

    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    from core import tool_dispatcher

    started = await tool_dispatcher.execute_structured(
        "start_agent_task",
        {
            "goal": "Update the sample and verify it.",
            "workspace_id": "project",
            "request_id": "request-0001",
            "input_refs": ["sample.txt"],
        },
        UID, UID, False, _Session(), origin="assistant_loop", char_id=CHAR,
    )
    body = json.loads(started.result[started.result.index("{"):])
    assert body["status"] in {"queued", "running"}
    task_id = body["task_id"]

    finished = await _wait_terminal(task_id)
    assert finished["status"] == "succeeded"
    assert (root / "sample.txt").read_text(encoding="utf-8") == "after"
    assert finished["result"]["summary"] == "updated and verified"
    assert finished["result"]["artifacts"][0]["ref"] == "sample.txt"
    assert finished["result"]["checks"][0]["succeeded"] is True

    receipt_text = sandbox.agent_runtime_task_state(UID, char_id=CHAR).read_text(encoding="utf-8")
    assert "Update the sample" not in receipt_text
    assert "before" not in receipt_text
    assert str(root) not in receipt_text
    from core.autonomy import store as autonomy_store
    pending = autonomy_store.load(UID, CHAR)["pending_signals"]
    assert pending[-1]["signal"]["source"] == "agent_task_completed"
    assert pending[-1]["signal"]["evidence"][0]["task_id"] == task_id


@pytest.mark.asyncio
async def test_start_is_idempotent_and_new_request_id_creates_new_task(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    from core.agent_runtime import agent_tasks
    from core.agent_runtime.models import CausationRef, TaskPrincipal

    async def no_schedule(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent_tasks, "_schedule", no_schedule)
    principal = TaskPrincipal.reality(UID, CHAR)
    kwargs = dict(
        goal="Inspect project", workspace_id="project", task_type="inspect",
        input_refs=[], requested_budget={}, causation_ref=CausationRef("tool_request", "same"),
        source="reality_turn",
    )
    first = await agent_tasks.start_agent_task(principal, idempotency_key="same-call", **kwargs)
    duplicate = await agent_tasks.start_agent_task(principal, idempotency_key="same-call", **kwargs)
    second = await agent_tasks.start_agent_task(principal, idempotency_key="new-call", **kwargs)
    assert duplicate["task_id"] == first["task_id"]
    assert second["task_id"] != first["task_id"]


@pytest.mark.asyncio
async def test_manifest_revision_change_stops_before_write(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "sample.txt"
    target.write_text("before", encoding="utf-8")
    cfg = _config(monkeypatch, root)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_chat(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return json.dumps({"summary": "change", "actions": [
            {"op": "update", "path": "sample.txt", "content": "after"}
        ]})

    monkeypatch.setattr("core.llm_client.chat", delayed_chat)
    from core.agent_runtime.agent_tasks import start_agent_task
    from core.agent_runtime.models import CausationRef, TaskPrincipal
    principal = TaskPrincipal.reality(UID, CHAR)
    started = await start_agent_task(
        principal, goal="Change sample", workspace_id="project", idempotency_key="revision",
        causation_ref=CausationRef("tool_request", "revision"), source="reality_turn",
    )
    await entered.wait()
    cfg["agent_tasks"]["workspace_manifests"]["project"]["revision"] = 2
    release.set()
    finished = await _wait_terminal(started["task_id"])
    assert finished["status"] == "failed"
    assert finished["error_code"] == "workspace_grant_changed"
    assert target.read_text(encoding="utf-8") == "before"


def test_cross_character_query_and_model_authority_fields_are_rejected(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    from core.agent_runtime import agent_tasks
    from core.agent_runtime.models import TaskPrincipal
    from core.agent_runtime.task_manager import TaskManagerError
    from core.tool_dispatcher import _schema_errors, _TOOL_REGISTRY

    with pytest.raises(TaskManagerError, match="task_not_found"):
        agent_tasks.get_agent_task(TaskPrincipal.reality(UID, "other-character"), "0" * 32)

    schema = _TOOL_REGISTRY["start_agent_task"]["parameters"]
    errors = _schema_errors({
        "goal": "x", "workspace_id": "project", "request_id": "request-0002",
        "shell": "rm -rf .", "confirmed": True, "char_id": "other-character",
    }, schema)
    assert errors


def test_autonomy_requires_explicit_allowlist_and_manifest(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    from core.autonomy.policy import tool_eligibility
    from core.tool_dispatcher import _TOOL_REGISTRY

    allowed, reason = tool_eligibility(
        "start_agent_task", {"enabled": True}, registry=_TOOL_REGISTRY, effect="write"
    )
    assert allowed is True
    assert reason == "eligible"
    assert _TOOL_REGISTRY["start_agent_task"]["dangerous"] is False

    from core.deployment_capabilities import tool_allowed
    assert tool_allowed("start_agent_task", {"deployment": {"mode": "remote_server"}}) == (
        False, "disabled_remote_server_local_capability"
    )


def test_start_discovery_requires_server_gate_but_can_report_missing_grant(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    cfg = _config(monkeypatch, root)
    from core.tool_dispatcher import get_tools_schema

    names = {item["function"]["name"] for item in get_tools_schema(categories=["info"])}
    assert "start_agent_task" in names
    cfg["agent_tasks"]["workspace_manifests"] = {}
    names = {item["function"]["name"] for item in get_tools_schema(categories=["info"])}
    assert "start_agent_task" in names
    cfg["agent_tasks"]["enabled"] = False
    names = {item["function"]["name"] for item in get_tools_schema(categories=["info"])}
    assert "start_agent_task" not in names


@pytest.mark.asyncio
async def test_missing_workspace_grant_waits_for_admin_approval(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    cfg = _config(monkeypatch, root)
    cfg["agent_tasks"]["workspace_manifests"] = {}
    from core import tool_dispatcher

    outcome = await tool_dispatcher.execute_structured(
        "start_agent_task",
        {"goal": "Inspect the project", "workspace_id": "project", "request_id": "request-approval"},
        UID, UID, False, _Session(), origin="assistant_loop", char_id=CHAR,
    )
    body = json.loads(outcome.result[outcome.result.index("{"):])
    assert body == {
        "status": "waiting_approval",
        "error_code": "workspace_grant_required",
        "message": "等待管理面授权该 workspace 后再重试；当前请求未执行。",
    }


def test_central_observability_is_authenticated_and_metadata_only(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    secret = "agent-task-observability-secret"
    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: secret)
    from admin.admin_server import app
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/observability/character-file-autonomy").status_code == 401
    response = client.get(
        "/observability/character-file-autonomy",
        params={"uid": UID, "char_id": CHAR},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert response.status_code == 200
    payload = response.json()
    task_state = payload["capabilities"]["agent_tasks"]
    assert task_state["configured"] is True
    assert task_state["effective"] is True
    assert task_state["workspace_grants"][0]["revision"] == 1
    assert task_state["quota"]["max_concurrent_per_character"] == 1
    assert task_state["redaction"]["version"]
    blob = json.dumps(payload)
    assert str(root) not in blob
    assert "Inspect the project" not in blob
    assert "api_key" not in blob


@pytest.mark.asyncio
async def test_budget_limit_and_running_cancel_are_terminal(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def too_many_actions(*_args, **_kwargs):
        return json.dumps({"summary": "too much", "actions": [
            {"op": "read", "path": "missing.txt"} for _ in range(9)
        ]})

    monkeypatch.setattr("core.llm_client.chat", too_many_actions)
    from core.agent_runtime.agent_tasks import cancel_agent_task, start_agent_task
    from core.agent_runtime.models import CausationRef, TaskPrincipal
    principal = TaskPrincipal.reality(UID, CHAR)
    limited = await start_agent_task(
        principal, goal="Exceed budget", workspace_id="project",
        requested_budget={"steps": 2}, idempotency_key="over-limit",
        causation_ref=CausationRef("tool_request", "over-limit"), source="reality_turn",
    )
    limited_done = await _wait_terminal(limited["task_id"])
    assert limited_done["status"] == "failed"
    assert limited_done["error_code"] == "task_step_budget_exceeded"

    async def wait_plan(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return json.dumps({"summary": "done", "actions": []})

    monkeypatch.setattr("core.llm_client.chat", wait_plan)
    running = await start_agent_task(
        principal, goal="Wait", workspace_id="project", idempotency_key="cancel-running",
        causation_ref=CausationRef("tool_request", "cancel-running"), source="reality_turn",
    )
    await entered.wait()
    requested = cancel_agent_task(principal, running["task_id"])
    assert requested["cancel_requested"] is True
    release.set()
    canceled = await _wait_terminal(running["task_id"])
    assert canceled["status"] == "canceled"
