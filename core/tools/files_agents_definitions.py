"""Files Agents tool definitions and invocation adapters.

Domain implementations and trusted invocation bindings have one owner.
"""

from core.tools import device_file_tools as _device_file_tools
from core.tools import gmail_tools as _gmail_tools
from core.tools import character_intent_tools as _intent_tools
import hashlib
import json
import uuid


async def _fs_list_wrapper(
    path: str | None = None,
    depth: int = 1,
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    from core.tools.fs_browse import fs_list
    return fs_list(path=path, depth=depth, user_id=user_id, char_id=char_id)


async def _fs_read_wrapper(
    path: str,
    offset: int = 0,
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    from core.tools.fs_browse import fs_read
    return fs_read(path=path, offset=offset, user_id=user_id, char_id=char_id)


async def _workspace_list_wrapper(path: str | None = None, depth: int = 1, *, user_id: str | None = None, char_id: str | None = None) -> str:
    from core.agent_runtime.workspace import list_workspace
    from core.agent_runtime.models import TaskPrincipal
    return json.dumps(list_workspace(TaskPrincipal.reality(user_id or "", char_id or ""), path, depth), ensure_ascii=False)


async def _workspace_read_wrapper(path: str, *, user_id: str | None = None, char_id: str | None = None) -> str:
    from core.agent_runtime.workspace import read_workspace
    from core.agent_runtime.models import TaskPrincipal
    return read_workspace(TaskPrincipal.reality(user_id or "", char_id or ""), path)


def _workspace_tool_request(idem: str):
    """Workspace execute() has no canonical turn_id; bind the request fingerprint."""
    from core.agent_runtime.models import CausationRef
    return CausationRef("tool_request", idem)


async def _workspace_write_wrapper(path: str, content: str, *, operation: str, confirmed: bool = False, user_id: str | None = None, char_id: str | None = None) -> str:
    from core.agent_runtime.workspace import write_workspace
    from core.agent_runtime.models import TaskPrincipal, RetryPolicy
    from core.agent_runtime.task_manager import create_task, claim_next, complete_task, fail_task
    principal = TaskPrincipal.reality(user_id or "", char_id or "")
    capability = f"workspace.{operation}"
    idem = hashlib.sha256((operation + "\0" + path + "\0" + content).encode("utf-8")).hexdigest()
    receipt, created = create_task(
        principal,
        capability=capability,
        source="tool",
        idempotency_key=idem,
        ttl_seconds=300,
        retry_policy=RetryPolicy.NEVER.value,
        causation_ref=_workspace_tool_request(idem),
        request_fingerprint=idem,
    )
    if not created and receipt["status"] in {"succeeded", "failed", "canceled", "expired", "outcome_unknown"}:
        return json.dumps({"receipt": receipt["task_id"], "status": receipt["status"], "duplicate": True}, ensure_ascii=False)
    lease = claim_next(principal, task_id=receipt["task_id"], capabilities={capability})
    if lease is None:
        return json.dumps({"receipt": receipt["task_id"], "status": receipt["status"], "duplicate": True}, ensure_ascii=False)
    try:
        result = write_workspace(principal, path, content, overwrite=operation == "update", operation=operation)
        if lease:
            complete_task(principal, lease, result_metadata={"outcome_code": "written", "counters": {"bytes": result["size"], "version": result["version"]}})
        return json.dumps({"receipt": receipt["task_id"], **result}, ensure_ascii=False)
    except Exception as exc:
        if lease:
            fail_task(principal, lease, error_code=getattr(exc, "code", "workspace_write_failed"))
        raise


async def _workspace_create_wrapper(path: str, content: str, *, user_id: str | None = None, char_id: str | None = None) -> str:
    return await _workspace_write_wrapper(path, content, operation="create", user_id=user_id, char_id=char_id)


async def _workspace_update_wrapper(path: str, content: str, *, confirmed: bool = False, user_id: str | None = None, char_id: str | None = None) -> str:
    return await _workspace_write_wrapper(path, content, operation="update", confirmed=confirmed, user_id=user_id, char_id=char_id)


async def _workspace_delete_wrapper(path: str, *, confirmed: bool = False, user_id: str | None = None, char_id: str | None = None) -> str:
    from core.agent_runtime.workspace import delete_workspace
    from core.agent_runtime.models import TaskPrincipal, RetryPolicy
    from core.agent_runtime.task_manager import create_task, claim_next, complete_task, fail_task
    principal = TaskPrincipal.reality(user_id or "", char_id or "")
    idem = hashlib.sha256(("delete\0" + path).encode("utf-8")).hexdigest()
    receipt, created = create_task(
        principal,
        capability="workspace.delete",
        source="tool",
        idempotency_key=idem,
        ttl_seconds=300,
        retry_policy=RetryPolicy.NEVER.value,
        causation_ref=_workspace_tool_request(idem),
        request_fingerprint=idem,
    )
    if not created and receipt["status"] in {"succeeded", "failed", "canceled", "expired", "outcome_unknown"}:
        return json.dumps({"receipt": receipt["task_id"], "status": receipt["status"], "duplicate": True}, ensure_ascii=False)
    lease = claim_next(principal, task_id=receipt["task_id"], capabilities={"workspace.delete"})
    if lease is None:
        return json.dumps({"receipt": receipt["task_id"], "status": receipt["status"], "duplicate": True}, ensure_ascii=False)
    try:
        result = delete_workspace(principal, path, confirmed=True)
        if lease:
            complete_task(principal, lease, result_metadata={"outcome_code": "deleted", "counters": {"version": result["version"]}})
        return json.dumps({"receipt": receipt["task_id"], **result}, ensure_ascii=False)
    except Exception as exc:
        if lease:
            fail_task(principal, lease, error_code=getattr(exc, "code", "workspace_delete_failed"))
        raise


async def _workspace_undo_wrapper(path: str, *, confirmed: bool = False, user_id: str | None = None, char_id: str | None = None) -> str:
    from core.agent_runtime.workspace import undo_workspace
    from core.agent_runtime.models import TaskPrincipal, RetryPolicy
    from core.agent_runtime.task_manager import create_task, claim_next, complete_task, fail_task
    principal = TaskPrincipal.reality(user_id or "", char_id or "")
    idem = hashlib.sha256(("undo\0" + path).encode("utf-8")).hexdigest()
    receipt, created = create_task(
        principal,
        capability="workspace.update",
        source="tool",
        idempotency_key=idem,
        ttl_seconds=300,
        retry_policy=RetryPolicy.NEVER.value,
        causation_ref=_workspace_tool_request(idem),
        request_fingerprint=idem,
    )
    if not created and receipt["status"] in {"succeeded", "failed", "canceled", "expired", "outcome_unknown"}:
        return json.dumps({"receipt": receipt["task_id"], "status": receipt["status"], "duplicate": True}, ensure_ascii=False)
    lease = claim_next(principal, task_id=receipt["task_id"], capabilities={"workspace.update"})
    if lease is None:
        return json.dumps({"receipt": receipt["task_id"], "status": receipt["status"], "duplicate": True}, ensure_ascii=False)
    try:
        result = undo_workspace(principal, path, confirmed=True)
        if lease:
            complete_task(principal, lease, result_metadata={"outcome_code": "undone", "counters": {"version": result["version"]}})
        return json.dumps({"receipt": receipt["task_id"], **result}, ensure_ascii=False)
    except Exception as exc:
        if lease:
            fail_task(principal, lease, error_code=getattr(exc, "code", "workspace_undo_failed"))
        raise


async def _process_run_wrapper(
    program: str,
    args: list[str] | None = None,
    interpreter: str = "python",
    wall_seconds: int = 60,
    output_bytes: int = 262144,
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    """Run one explicitly requested, workspace-scoped temporary program."""
    from core.agent_runtime.models import TaskPrincipal
    from core.agent_runtime.process_runner import ProcessLimits, run_process_task

    principal = TaskPrincipal.reality(user_id or "", char_id or "")
    result = await run_process_task(
        principal,
        program=program,
        args=args,
        interpreter=interpreter,
        idempotency_key=hashlib.sha256(
            json.dumps([program, args or [], interpreter, wall_seconds, output_bytes], separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        limits=ProcessLimits(wall_seconds=wall_seconds, output_bytes=output_bytes),
    )
    return json.dumps({
        "receipt": result.get("receipt"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "returncode": result.get("returncode"),
        "truncated": bool(result.get("truncated")),
        "duplicate": bool(result.get("duplicate", False)),
    }, ensure_ascii=False)


async def _start_agent_task_wrapper(
    goal: str,
    workspace_id: str,
    request_id: str,
    task_type: str = "coding",
    input_refs: list[str] | None = None,
    budget: dict | None = None,
    *,
    user_id: str,
    char_id: str,
    origin: str,
) -> str:
    from core.agent_runtime.agent_tasks import AgentTaskError, start_agent_task
    from core.agent_runtime.models import CausationRef, TaskPrincipal

    fingerprint = hashlib.sha256(
        json.dumps(
            [origin, request_id, goal, workspace_id, task_type, input_refs or [], budget or {}],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    try:
        result = await start_agent_task(
            TaskPrincipal.reality(user_id, char_id),
            goal=goal,
            workspace_id=workspace_id,
            task_type=task_type,
            input_refs=input_refs,
            requested_budget=budget,
            idempotency_key=f"agent-task:{origin}:{request_id}",
            causation_ref=CausationRef("tool_request", fingerprint),
            source="autonomy" if origin == "autonomy_loop" else "tool",
        )
        return json.dumps(result, ensure_ascii=False)
    except AgentTaskError as exc:
        if exc.code in {
            "workspace_id_not_found",
            "workspace_manifest_denied",
            "workspace_manifest_expired",
        }:
            return json.dumps({
                "status": "waiting_approval",
                "error_code": "workspace_grant_required",
                "message": "等待管理面授权该 workspace 后再重试；当前请求未执行。",
            }, ensure_ascii=False)
        return json.dumps({"status": "failed", "error_code": exc.code}, ensure_ascii=False)


async def _get_agent_task_wrapper(
    task_id: str, *, user_id: str, char_id: str, origin: str
) -> str:
    from core.agent_runtime.agent_tasks import get_agent_task
    from core.agent_runtime.models import TaskPrincipal

    try:
        result = get_agent_task(TaskPrincipal.reality(user_id, char_id), task_id)
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        code = getattr(exc, "code", "agent_task_not_found")
        return json.dumps({"status": "failed", "error_code": code}, ensure_ascii=False)


async def _cancel_agent_task_wrapper(
    task_id: str, *, user_id: str, char_id: str, origin: str
) -> str:
    from core.agent_runtime.agent_tasks import cancel_agent_task
    from core.agent_runtime.models import TaskPrincipal

    try:
        result = cancel_agent_task(TaskPrincipal.reality(user_id, char_id), task_id)
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        code = getattr(exc, "code", "agent_task_not_found")
        return json.dumps({"status": "failed", "error_code": code}, ensure_ascii=False)


async def _browser_automation_wrapper(
    url: str,
    operation: str,
    selector: str = "",
    value: str = "",
    path: str = "",
    confirmed: bool = False,
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    """Run one bounded browser task; page data never enters task receipts."""
    from core.agent_runtime import browser
    from core.agent_runtime.models import TaskPrincipal
    principal = TaskPrincipal.reality(user_id or "", char_id or "")
    idem = hashlib.sha256(json.dumps([url, operation, selector, value, path], separators=(",", ":")).encode()).hexdigest()
    try:
        call_params = {"selector": selector, "value": value, "path": path} if any((selector, value, path)) else {}
        receipt = browser.create_task(principal, url=url, operation=operation, params=call_params, idempotency_key=idem, confirmed=confirmed)
        if receipt["status"] == "waiting_confirm":
            receipt = browser.confirm_task(principal, receipt["task_id"])
        result = await browser.run_task(principal, receipt["task_id"], url=url, operation=operation, confirmed=confirmed, params=call_params)
        return json.dumps({"receipt": result.get("receipt"), "result": result.get("result")}, ensure_ascii=False)
    except browser.BrowserError as exc:
        return json.dumps({"status": "failed", "error_code": exc.code}, ensure_ascii=False)


async def _manage_self_capability_wrapper(
    action: str,
    capability_id: str,
    value=None,
    reason: str = "",
    expected_revision: int = -1,
    action_id: str = "",
    *,
    user_id: str,
    char_id: str,
    origin: str,
) -> str:
    """Narrow internal gateway; it never accepts a config path or secret."""
    from core.self_management.models import CapabilityChange
    from core.self_management.registry import _canonical
    from core.self_management.service import agent_change

    result = agent_change(
        user_id,
        char_id,
        CapabilityChange(
            action=action,
            capability_id=capability_id,
            value=value,
            reason=reason,
            expected_revision=expected_revision,
            action_id=action_id,
        ),
        source=origin,
    )
    from core.self_management.service import view

    canonical_id = _canonical(capability_id)
    row = next((item for item in view(user_id, char_id)["capabilities"]
                if item["capability_id"] == canonical_id), None)
    return json.dumps({
        "operation_succeeded": result.ok,
        "error_code": None if result.ok else result.code,
        "capability_id": canonical_id,
        "current_value": row.get("effective_value") if row and isinstance(row.get("effective_value"), (bool, int)) else None,
        "revision": result.revision,
    }, ensure_ascii=False)


async def _list_self_capabilities_wrapper(*, user_id: str, char_id: str) -> str:
    from core.self_management.policy import can_agent_manage
    from core.self_management.service import view
    from core.self_management.store import append_audit

    snapshot = view(user_id, char_id)
    rows = []
    for row in snapshot["capabilities"]:
        grant = row.get("grant") or {}
        can_modify, _ = can_agent_manage(user_id, char_id, row["capability_id"])
        rows.append({
            "key": row["capability_id"], "type": row["value_type"],
            "current_value": row.get("effective_value") if isinstance(row.get("effective_value"), (bool, int)) else None,
            "allowed_values": grant.get("constraints") or {},
            "can_self_modify": can_modify,
            "requires_owner_authorization": not can_modify,
        })
    append_audit(user_id, char_id, {"actor": "agent_inspection", "result": "inspected",
                                    "event_id": uuid.uuid4().hex, "capability_id": "*"})
    return json.dumps({"revision": snapshot["revision"], "capabilities": rows}, ensure_ascii=False)


async def _read_self_action_history_wrapper(
    time_range: str = "24h", capability_id: str = "", tool: str = "", status: str = "",
    *, user_id: str, char_id: str,
) -> str:
    from core.self_management.store import append_audit, query_audit
    from core.tool_audit import query as query_tool_audit

    actions = query_audit(user_id, char_id, time_range=time_range,
                          capability_id=capability_id, status=status)
    append_audit(user_id, char_id, {"actor": "agent_inspection", "result": "inspected",
                                    "event_id": uuid.uuid4().hex, "capability_id": capability_id or "*"})
    tool_calls = query_tool_audit(user_id, char_id, time_range=time_range,
                                  tool=tool, status=status, limit=50)
    return json.dumps({"actions": actions, "tool_calls": tool_calls}, ensure_ascii=False)


async def _invoke_device_workspace_list(call):
    func = call.func
    tool_name = call.tool_name
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    result = _device_file_tools.format_result(await func(tool_name, user_id, char_id, tool_args))

    return result


async def _invoke_gmail_list_messages(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    result = await _gmail_tools.execute(func, user_id, char_id, tool_args)

    return result


async def _invoke_list_character_intents(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    result = await _intent_tools.execute(func, user_id, char_id, tool_args)

    return result


async def _invoke_self_tool_define(call):
    func = call.func
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    origin = call.origin
    target_id = call.target_id
    is_group = call.is_group
    session_state = call.session_state
    allowed_tool_names = call.allowed_tool_names
    for key in ("user_id", "uid", "char_id", "owner", "realm", "origin"):
        if key in tool_args:
            result = "grant_principal_mismatch"
            break
    else:
        result = await func(
            user_id=user_id, char_id=char_id, origin=origin, target_id=target_id,
            is_group=is_group, session_state=session_state,
            allowed_tool_names=allowed_tool_names, **tool_args,
        )

    return result


async def _invoke_fs_list(call):
    func = call.func
    tool_name = call.tool_name
    tool_args = call.tool_args
    user_id = call.user_id
    char_id = call.char_id
    origin = call.origin
    for key in ("user_id", "uid", "char_id", "owner", "realm"):
        if key in tool_args:
            result = "grant_principal_mismatch"
            break
    else:
        if tool_name in {"start_agent_task", "get_agent_task", "cancel_agent_task"}:
            result = await func(user_id=user_id, char_id=char_id, origin=origin, **tool_args)
        else:
            result = await func(user_id=user_id, char_id=char_id, **tool_args)

    return result


def register_tools(registry):
    registry["fs_list"] = {
        "func": _fs_list_wrapper,
        "description": (
            "列出后端仓库/内部目录或本机普通目录的文件和子目录；此工具只读。"
            "省略 path 时返回发现提示（含常用 allow_roots）。需要读取文件正文时使用 fs_read。"
            "远程部署只能列本进程后端资料，不能列用户电脑。"
        ),
        "dangerous": False,
        "category": "fs",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要浏览的目录路径；相对路径按常用 allow_roots 解析。省略时返回发现提示。",
                },
                "depth": {
                    "type": "integer",
                    "enum": [1, 2],
                    "description": "列举的目录深度，只能为 1 或 2；省略时为 1。",
                },
            },
            "required": [],
        },
        "examples": ["看看这个目录里有什么", "列一下这个文件夹"],
        "keywords": ["列目录", "看看目录", "文件夹里有什么"],
        "trace_args": ["path"],
    }

    registry["fs_read"] = {
        "func": _fs_read_wrapper,
        "description": (
            "读取后端代码/配置/日志或本机普通文本文件；此工具只读。"
            "敏感值会先脱敏再截断。私钥和凭据库会被拒绝。远程部署只能读本进程后端资料。"
        ),
        "dangerous": False,
        "category": "fs",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要读取的文件路径；相对路径按常用 allow_roots 解析，绝对路径可以是本机普通文件。",
                },
                "offset": {
                    "type": "integer",
                    "description": "脱敏后的字符偏移，用于分页；省略时从开头读取。",
                },
            },
            "required": ["path"],
        },
        "examples": ["读一下这个文件", "打开看看这个文档写了什么"],
        "keywords": ["读文件", "打开这个文件", "看看这个文档"],
        "trace_args": ["path"],
    }

    registry["workspace_list"] = {
        "func": _workspace_list_wrapper,
        "description": "列出已授权 workspace 的目录内容，只返回受限元数据。",
        "dangerous": False, "category": "fs",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "可选的已授权工作区目录；相对路径使用第一个配置根目录。"},
            "depth": {"type": "integer", "enum": [1, 2], "description": "目录遍历深度，只允许 1 或 2。"},
        }, "required": []},
        "examples": ["列出工作区文件"], "keywords": ["工作区", "列目录"], "trace_args": ["path"],
    }

    registry["workspace_read"] = {
        "func": _workspace_read_wrapper,
        "description": "读取已授权 workspace 中的文本文件。",
        "dangerous": False, "category": "fs",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "已授权工作区内的 UTF-8 文本文件路径。"},
        }, "required": ["path"]},
        "examples": ["读取工作区文档"], "keywords": ["工作区", "读文件"], "trace_args": ["path"],
    }

    registry["workspace_create"] = {
        "func": _workspace_create_wrapper,
        "description": "在已授权 workspace 创建新的文本文件；文件已存在时拒绝。",
        "dangerous": False, "category": "fs", "effect": "write",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "已授权工作区内尚不存在的文本文件路径。"},
            "content": {"type": "string", "description": "要写入的新文件 UTF-8 文本正文。"},
        }, "required": ["path", "content"]},
        "examples": ["在工作区新建文档"], "keywords": ["工作区", "新建文件"], "trace_args": ["path"],
    }

    registry["workspace_update"] = {
        "func": _workspace_update_wrapper,
        "description": "更新已授权 workspace 中的文本文件。",
        "dangerous": True, "category": "fs", "effect": "write",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "已授权工作区内已存在的文本文件路径。"},
            "content": {"type": "string", "description": "用于替换文件的 UTF-8 文本正文。"},
        }, "required": ["path", "content"]},
        "examples": ["更新工作区文档"], "keywords": ["工作区", "更新文件", "覆盖文件"], "trace_args": ["path"],
    }

    registry["workspace_delete"] = {
        "func": _workspace_delete_wrapper,
        "description": "删除已授权 workspace 中的文件。",
        "dangerous": True, "category": "fs", "effect": "write",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "已授权工作区内要删除的受支持文本文件路径。"},
        }, "required": ["path"]},
        "examples": ["删除工作区文件"], "keywords": ["工作区", "删除文件"], "trace_args": ["path"],
    }

    registry["workspace_undo"] = {
        "func": _workspace_undo_wrapper,
        "description": "撤销 workspace 文件最近一次受控变更。",
        "dangerous": True, "category": "fs", "effect": "write",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "已授权工作区内要恢复最近版本的文本文件路径。"},
        }, "required": ["path"]},
        "examples": ["撤销工作区文件修改"], "keywords": ["工作区", "撤销修改"], "trace_args": ["path"],
    }

    registry["process_run"] = {
        "func": _process_run_wrapper,
        "description": "在已授权 workspace 内运行一个短时 allowlist 程序；不接受 shell 字符串，网络关闭，输出和资源受限。仅在用户明确要求执行该工作区程序时调用。",
        "dangerous": True, "category": "system", "effect": "write",
        "parameters": {"type": "object", "properties": {
            "program": {"type": "string", "description": "workspace 内的相对程序路径，默认只允许 .py。"},
            "args": {"type": "array", "items": {"type": "string", "maxLength": 2048}, "maxItems": 64, "description": "结构化程序参数，不是 shell 命令字符串。"},
            "interpreter": {"type": "string", "enum": ["python", "python3"], "description": "已配置的解释器 allowlist 名称。"},
            "wall_seconds": {"type": "integer", "minimum": 1, "maximum": 900, "description": "进程允许运行的最长秒数，超时会以未知结果结束。"},
            "output_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576, "description": "stdout 与 stderr 合计的最大字节数，超出部分会截断。"},
        }, "required": ["program"]},
        "examples": ["运行工作区里的整理脚本"],
        "keywords": ["运行脚本", "执行程序", "整理工作区"],
        "trace_args": ["program", "interpreter"],
    }

    registry["start_agent_task"] = {
        "func": _start_agent_task_wrapper,
        "description": (
            "启动一个当前角色的有界后台工作任务，并立即返回 task_id。"
            "仅选择服务端已授权的 workspace_id；不要提供 shell、绝对路径、确认票据或身份字段。"
            "同一次工具调用重试必须复用 request_id；新的明确请求使用新的 request_id。"
        ),
        "dangerous": False,
        "category": "info",
        "effect": "write",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "goal": {"type": "string", "minLength": 1, "maxLength": 2000, "description": "后台任务的具体目标。"},
            "workspace_id": {"type": "string", "minLength": 1, "maxLength": 64, "description": "服务端配置的稳定 workspace ID。"},
            "request_id": {"type": "string", "minLength": 8, "maxLength": 128, "description": "本次调用的幂等 ID；重试时保持不变。"},
            "task_type": {"type": "string", "enum": ["coding", "inspect"], "description": "coding 可按 manifest 修改并验证；inspect 只读。"},
            "input_refs": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 1024}, "description": "workspace 内有限相对文件引用。"},
            "budget": {"type": "object", "additionalProperties": False, "properties": {
                "steps": {"type": "integer", "minimum": 1, "maximum": 32},
                "seconds": {"type": "integer", "minimum": 10, "maximum": 1800},
                "tokens": {"type": "integer", "minimum": 256, "maximum": 16000},
            }, "description": '本次任务的步骤、时间和 token 上限，不能超出管理面配置。'},
        }, "required": ["goal", "workspace_id", "request_id"]},
        "examples": ["在已授权工作区后台修改样例并运行检查", "后台检查这个项目文件"],
        "keywords": ["后台任务", "Agent task", "修改并验证", "稍后完成"],
        "trace_args": ["workspace_id", "task_type"],
    }

    registry["get_agent_task"] = {
        "func": _get_agent_task_wrapper,
        "description": "查询当前角色在当前用户范围内的后台 Agent task 进度、脱敏摘要、相对产物引用和验证状态。",
        "dangerous": False, "category": "info", "effect": "read",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "task_id": {"type": "string", "minLength": 32, "maxLength": 32, "description": '任务的32位标识，取自 start_agent_task 返回值。'},
        }, "required": ["task_id"]},
        "examples": ["查看刚才后台任务的进度"], "keywords": ["任务进度", "后台结果"],
        "trace_args": ["task_id"],
    }

    registry["cancel_agent_task"] = {
        "func": _cancel_agent_task_wrapper,
        "description": "取消当前角色在当前用户范围内的后台 Agent task；运行中的任务会在下一个有界检查点停止。",
        "dangerous": False, "category": "info", "effect": "write",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {
            "task_id": {"type": "string", "minLength": 32, "maxLength": 32, "description": '任务的32位标识，取自 start_agent_task 返回值。'},
        }, "required": ["task_id"]},
        "examples": ["取消刚才的后台任务"], "keywords": ["取消任务", "停止后台任务"],
        "trace_args": ["task_id"],
    }

    registry["browser_automation"] = {
        "func": _browser_automation_wrapper,
        "description": "在显式允许的网页域名中执行隔离浏览器操作；页面结果有界且不会暴露凭据、cookie、header 或 profile。操作按当前角色权限执行。",
        "dangerous": True,
        "category": "browser",
        "effect": "write",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "maxLength": 2048, "description": "仅限配置允许域名的 http/https 目标 URL。"},
            "operation": {"type": "string", "enum": ["navigate", "read_page", "click", "fill", "select", "login", "pay", "post", "delete", "send_email", "change_password", "upload", "download"], "description": "要执行的受限浏览器操作。"},
            "selector": {"type": "string", "maxLength": 256, "description": "页面元素选择器；仅用于需要定位元素的操作。"},
            "value": {"type": "string", "maxLength": 2000, "description": "填充或选择操作使用的值。"},
            "path": {"type": "string", "maxLength": 1024, "description": "Workspace 内文件的相对路径；仅用于上传或下载。"},
        }, "required": ["url", "operation"]},
        "examples": ["读取已允许域名的网页摘要"],
        "keywords": ["浏览网页", "网页操作", "打开网页"],
        "trace_args": ["operation"],
    }

    registry["manage_self_capability"] = {
        "func": _manage_self_capability_wrapper,
        "description": (
            "Manage only an explicitly user-granted capability for this character. "
            "Use only with a current revision and an idempotency action_id. "
            "Never use it for URLs, headers, tokens, arbitrary settings, files, or global configuration."
        ),
        "dangerous": False,
        "category": "self_management",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["enable", "disable", "set_value"],
                    "description": "要执行的受授权变更类型：启用、停用或在约束内设置值。",
                },
                "capability_id": {
                    "type": "string",
                    "maxLength": 160,
                    "description": "当前用户明确授权且允许角色修改的能力 ID。",
                },
                "value": {
                    "type": ["boolean", "integer", "string", "array", "object", "null"],
                    "description": "仅 set_value 使用的新值，必须符合该能力的约束。",
                },
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 240,
                    "description": "这次变更的简短原因，会进入有界审计记录。",
                },
                "expected_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "发起调用前看到的能力状态 revision；不一致时拒绝，避免覆盖用户新改动。",
                },
                "action_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "description": "本次变更的幂等 action ID；重试同一 ID 不会重复写入。",
                },
            },
            "required": ["action", "capability_id", "reason", "expected_revision", "action_id"],
        },
        "examples": ["temporarily disable an authorized capability", "set an authorized cooldown within its limit"],
        "keywords": ["authorized capability", "self capability", "cooldown"],
        "effect": "write",
        "self_management": True,
    }

    registry["list_self_capabilities"] = {
        "func": _list_self_capabilities_wrapper,
        "description": "List current Self Capability keys, types, values, constraints and whether this character may change each one.",
        "dangerous": False, "category": "self_management", "effect": "read",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "examples": ["What capabilities can I change?"],
        "keywords": ["self capabilities", "my settings"],
        "self_management_read": True, "echo_event_log": False,
    }

    registry["read_self_action_history"] = {
        "func": _read_self_action_history_wrapper,
        "description": "Read past tool-call receipts and Self Capability change attempts before describing past actions or failures.",
        "dangerous": False, "category": "self_management", "effect": "read",
        "parameters": {"type": "object", "properties": {
            "time_range": {"type": "string", "enum": ["24h", "7d", "30d"], "description": '动作记录的时间范围：24h、7d 或30d。'},
            "capability_id": {"type": "string", "maxLength": 160, "description": '可选能力标识，用于筛选动作记录。'},
            "tool": {"type": "string", "maxLength": 128, "description": '可选工具名称，用于筛选动作记录。'},
            "status": {"type": "string", "enum": ["success", "failed"], "description": '可选执行状态，用于筛选动作记录。'},
        }, "required": []},
        "examples": ["Which self capability changes failed yesterday?"],
        "keywords": ["self action history", "capability audit"],
        "self_management_read": True, "echo_event_log": False,
    }


INVOCATION_ADAPTERS = {
    **dict.fromkeys(('device_workspace_list', 'device_file_task_start', 'device_file_task_status', 'device_file_task_control', 'device_file_task_cancel', 'browser_session_start', 'browser_session_action', 'browser_snapshot', 'browser_screenshot', 'browser_console', 'browser_visual_review', 'browser_session_status', 'browser_session_cancel', 'device_file_list_dir', 'device_file_stat', 'device_file_find_name', 'device_file_search_text', 'device_file_read'), _invoke_device_workspace_list),
    **dict.fromkeys(('gmail_list_messages', 'gmail_search_messages', 'gmail_read_message', 'gmail_list_pending', 'gmail_mark_handled', 'gmail_reopen_pending', 'gmail_list_labels', 'gmail_send_message', 'gmail_reply_message'), _invoke_gmail_list_messages),
    **dict.fromkeys(('list_character_intents', 'propose_character_intent', 'revise_character_intent'), _invoke_list_character_intents),
    **dict.fromkeys(('self_tool_define', 'self_tool_list', 'self_tool_run'), _invoke_self_tool_define),
    **dict.fromkeys(('fs_list', 'fs_read', 'workspace_list', 'workspace_read', 'workspace_create', 'workspace_update', 'workspace_delete', 'workspace_undo', 'process_run', 'start_agent_task', 'get_agent_task', 'cancel_agent_task', 'self_db_tables', 'self_db_create_table', 'self_db_insert', 'self_db_query', 'self_db_update', 'self_db_delete', 'self_db_drop_table', 'self_list', 'self_read', 'self_create', 'self_update', 'self_move', 'self_delete', 'self_restore', 'list_reminders', 'get_reminder', 'add_reminder', 'update_reminder', 'cancel_reminder', 'restore_reminder', 'qzone_get_feeds', 'qzone_get_posts', 'qzone_get_comments', 'qzone_publish', 'qzone_comment', 'qzone_set_like', 'qzone_delete_post'), _invoke_fs_list),
}


def register_invokers(registry):
    for name, adapter in INVOCATION_ADAPTERS.items():
        registry[name]["invoke"] = adapter
