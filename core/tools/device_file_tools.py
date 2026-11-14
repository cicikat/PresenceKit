"""Path C entry points for the desktop file work session."""
import json

from core.agent_runtime import device_file_agent as service
from core.agent_runtime.models import TaskPrincipal
from core.tools.tool_result import ToolResult


SPECS = {
    "device_workspace_list": ("Find authorized workspaces and any active local-file task. "
                              "Use this directly for edits on the owner's desktop.", {}),
    "device_file_task_start": (
        "Start a bounded local-file work session on exactly one authorized desktop workspace. "
        "Include the exact path or filename, location hints and requested changes in goal. "
        "Reuse the current task ID if already running.",
        {"device_id": "Desktop ID from device_workspace_list", "workspace_id": "Authorized workspace ID",
         "goal": "The user's requested local file operation, including filename and revision or version when known"},
    ),
    "device_file_task_status": (
        "Briefly wait for progress on the same task and read its saved versions and verification. "
        "If it is still running, share its progress instead of launching another task.",
        {"task_id": "Task ID from device_file_task_start"},
    ),
    "device_file_task_control": (
        "Pause, resume, or steer an active file task to follow the owner's latest correction.",
        {"task_id": "Task ID from device_file_task_start", "action": "pause, resume, or steer",
         "instruction": "New owner instruction for steer, otherwise an empty string"},
    ),
    "device_file_task_cancel": (
        "Stop the same local-file task before another file action. Consult its saved changes afterwards.",
        {"task_id": "Task ID from device_file_task_start"},
    ),
}
NAMES = frozenset(SPECS)


async def execute(name, uid, char_id, args):
    actor = TaskPrincipal.reality(uid, char_id)
    parameters = SPECS[name][1]
    if set(args) != set(parameters) or any(not isinstance(value, str) for value in args.values()):
        return {"code": "invalid_device_file_arguments"}
    try:
        if name == "device_workspace_list":
            return service.list_workspaces(actor)
        if name == "device_file_task_start":
            return service.start(actor, **args)
        if name == "device_file_task_control":
            return service.control(actor, **args)
        if name == "device_file_task_cancel":
            return service.status(actor, args["task_id"], cancel=True)
        return await service.check_progress(actor, args["task_id"])
    except (ValueError, RuntimeError):
        return {"code": "device_file_task_unavailable"}


def register(registry):
    for name, (description, fields) in SPECS.items():
        registry[name] = {
            "description": description,
            "category": "artifacts",
            "effect": "write" if name.endswith(("_start", "_cancel", "_control")) else "read",
            "func": execute,
            "keywords": [],
            "examples": ["Work on a file exported to my computer"],
            "parameters": {"type": "object", "properties": {
                key: {"type": "string", "description": value} for key, value in fields.items()
            }, "required": list(fields)},
            "persist": False,
            "live_owner_only": True,
            "write_short_term": False,
            "echo_event_log": False,
        }


def format_result(result):
    state = result.get("status")
    unknown = state == "outcome_unknown" or result.get("receipt_pending") is True
    failed = state in {"failed", "canceled", "cancelled", "expired", "paused"} or (
        result.get("code") and "status" not in result
    )
    result = {**result, "request_status": "completed", "task_status": state}
    content = json.dumps(result, ensure_ascii=False)
    return ToolResult(raw_data=content, safe_summary=content,
                      meta={"validity": "outcome_unknown" if unknown else
                            "execution_failed" if failed else "current_turn"})
