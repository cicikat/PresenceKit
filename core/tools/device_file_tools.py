"""Path C entry points for the desktop file work session."""
import json

from core.agent_runtime import device_file_agent as service
from core.agent_runtime.models import TaskPrincipal


SPECS = {
    "device_workspace_list": ("Find authorized workspaces on online desktops.", {}),
    "device_file_task_start": (
        "Start a bounded local-file work session on exactly one authorized desktop workspace. "
        "Include the requested target and changes in goal. A queued receipt means work has not finished.",
        {"device_id": "Desktop ID from device_workspace_list", "workspace_id": "Authorized workspace ID",
         "goal": "The user's requested local file operation, including filename and revision or version when known"},
    ),
    "device_file_task_status": (
        "Read the same local-file task's outcome, saved versions and verification receipts.",
        {"task_id": "Task ID from device_file_task_start"},
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
        return service.status(actor, args["task_id"], cancel=name.endswith("_cancel"))
    except (ValueError, RuntimeError):
        return {"code": "device_file_task_unavailable"}


def register(registry):
    for name, (description, fields) in SPECS.items():
        registry[name] = {
            "description": description,
            "category": "artifacts",
            "effect": "write" if name.endswith(("_start", "_cancel")) else "read",
            "func": execute,
            "keywords": [],
            "examples": ["Work on a file exported to my computer"],
            "parameters": {"type": "object", "properties": {
                key: {"type": "string", "description": value} for key, value in fields.items()
            }, "required": list(fields)},
            "persist": False,
            "write_short_term": False,
            "echo_event_log": False,
        }


def format_result(result):
    return json.dumps(result, ensure_ascii=False)
