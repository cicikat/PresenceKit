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
READ_SPECS = {
    "device_file_list_dir": ("list_dir", "List one authorized desktop directory; metadata only."),
    "device_file_stat": ("stat", "Inspect desktop file/directory metadata without reading its contents."),
    "device_file_find_name": ("find_name", "Find desktop paths by exact, substring or glob filename; never read contents."),
    "device_file_search_text": ("search_text", "Search authorized desktop text: literal or bounded regex; returns lines and excerpts."),
    "device_file_read": ("read_page", "Read a desktop UTF-8 file page with full hash, line range and has_more."),
}
for _name, (_, _description) in READ_SPECS.items():
    SPECS[_name] = (_description + " Use the same grant and returned cursor for continuation; truncated is incomplete.", {
        "device_id": "Desktop ID from device_workspace_list", "workspace_id": "Authorized workspace ID",
        "path": "Relative path in this workspace, or an explicitly authorized absolute local path; empty for root",
        "query": "Filename pattern or content query; empty for list, stat or read",
        "options": 'JSON object: mode=exact|substring|glob (names) or literal|regex (content), limit=1..100, depth=0..12, cursor, start=1..262144. search_text requires content_search=true.',
    })
NAMES = frozenset(SPECS)


async def execute(name, uid, char_id, args):
    actor = TaskPrincipal.reality(uid, char_id)
    parameters = SPECS[name][1]
    if set(args) != set(parameters) or any(not isinstance(value, str) for value in args.values()):
        return {"code": "invalid_device_file_arguments"}
    try:
        if name in READ_SPECS:
            options = json.loads(args["options"])
            if not isinstance(options, dict) or set(options) - {"mode", "limit", "depth", "cursor", "start", "content_search"}:
                return {"code": "invalid_device_file_arguments"}
            if ("mode" in options and (not isinstance(options["mode"], str)
                                      or options["mode"] not in {"exact", "substring", "glob", "literal", "regex"})
                    or "cursor" in options and not isinstance(options["cursor"], str)
                    or "content_search" in options and type(options["content_search"]) is not bool):
                return {"code": "invalid_device_file_arguments"}
            for key, default, low, high in (("limit", 50, 1, 100), ("depth", 6, 0, 12), ("start", 1, 1, 262144)):
                value = options.setdefault(key, default)
                if type(value) is not int or not low <= value <= high:
                    return {"code": "invalid_device_file_arguments"}
            action = service.Action(op=READ_SPECS[name][0], path=args["path"], query=json.dumps({
                **options, "query": args["query"],
            }, ensure_ascii=False))
            return await service.atomic_read(actor, args["device_id"], args["workspace_id"], action)
        if name == "device_workspace_list":
            return service.list_workspaces(actor)
        if name == "device_file_task_start":
            return service.start(actor, **args)
        if name == "device_file_task_control":
            return service.control(actor, **args)
        if name == "device_file_task_cancel":
            return service.status(actor, args["task_id"], cancel=True)
        return await service.check_progress(actor, args["task_id"])
    except ValueError:
        return {"code": "invalid_device_file_arguments"}
    except RuntimeError:
        return {"code": "device_file_task_unavailable"}


def register(registry):
    for name, (description, fields) in SPECS.items():
        properties = {key: {"type": "string", "description": value} for key, value in fields.items()}
        registry[name] = {
            "description": description,
            "category": "artifacts",
            "effect": "write" if name.endswith(("_start", "_cancel", "_control")) else "read",
            "func": execute,
            "keywords": [],
            "examples": ["Work on a file exported to my computer"],
            "parameters": {"type": "object", "properties": properties, "required": list(fields),
                           "additionalProperties": False},
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
