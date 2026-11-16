"""Tool outcome."""

from core.agent_runtime import computer_use as _computer_use
from core.tools import device_file_tools as _device_file_tools
from core.tools import gmail_tools as _gmail_tools
import json
import logging
logger = logging.getLogger("core.tool_dispatcher")

_MCP_SERVER_ERROR_PREFIX = "MCP 工具返回错误: "

_TOOL_FALLBACKS = {
    "weather": "天气信息暂时获取不到",
    "web_search": "网络暂时有些不稳定，没搜到",
    "get_time": "时间获取出了点问题",
    "add_reminder": "备忘录暂时写不进去，稍后再试",
    "list_reminders": "备忘录暂时读不到",
    "get_reminder": "备忘录暂时读不到",
    "update_reminder": "备忘录暂时改不了，稍后再试",
    "cancel_reminder": "备忘录暂时取消不了，稍后再试",
    "restore_reminder": "备忘录暂时恢复不了，稍后再试",
    "read_diary": "日记暂时读不到",
    "read_watch": "身体数据暂时读取不到",
    "read_hds_heart_rate": "HDS 心率状态暂时读取不到",
    "search_documents": "资料暂时检索不到",
    "read_document": "资料暂时读取不到",
    "search_character_notes": "角色手记暂时检索不到",
}


from dataclasses import dataclass
def _log_execute_failure_context(tool_name: str, tool_args: dict, origin: str) -> None:
    """Content-free diagnostics, including tools with private arguments."""
    keys = sorted(str(k)[:64] for k in tool_args) if isinstance(tool_args, dict) else []
    logger.error("[tool_dispatcher.execute] failure context: tool=%s origin=%s argument_count=%d",
                 tool_name, origin, len(keys))


def log_execution_error(tool_name, origin, error):
    logger.error("[tool_dispatcher.execute] tool=%s origin=%s error_type=%s",
                 tool_name, origin, type(error).__name__)


def _mcp_server_reported_error(e: Exception) -> str | None:
    """从异常里抠出 MCP 服务器自己给出的具体错误文案（Brief 122）。

    只认 core/mcp_client.py::_format_result() 在 result.isError 时抛出的
    ``RuntimeError(f"MCP 工具返回错误: {text}")`` 这一种——这是服务器业务逻辑主动
    说清楚"缺了什么/为什么拒绝"（如"command 参数必填"），模型看到原话大概率能在
    下一步自己纠正重试。_call_tool() 里连接失败/超时/重连未恢复包出来的其他
    RuntimeError（"MCP 工具调用失败: ..."/"MCP server 未连接" 等）是我们这边的
    基础设施问题，不代表服务器业务层给出了可操作的具体原因，继续走通用兜底文案，
    不把内部异常细节暴露给模型。
    """
    if isinstance(e, RuntimeError) and str(e).startswith(_MCP_SERVER_ERROR_PREFIX):
        return str(e)[len(_MCP_SERVER_ERROR_PREFIX):].strip() or None
    return None


@dataclass(frozen=True)
class ToolExecutionOutcome:
    """Structured execution result used by the pre-tool routing contract."""

    status: str
    result: str | None = None
    confirmation_request: str | None = None
    missing_parameters: tuple[str, ...] = ()


def _execution_outcome(
    status: str,
    result: str | None = None,
    confirmation_request: str | None = None,
    *,
    missing_parameters: list[str] | tuple[str, ...] = (),
) -> ToolExecutionOutcome:
    return ToolExecutionOutcome(
        status=status,
        result=result,
        confirmation_request=confirmation_request,
        missing_parameters=tuple(missing_parameters),
    )


from core.tools import character_intent_tools as _intent_tools


def private_receipt(tool_name):
    return tool_name in _gmail_tools.NAMES | _intent_tools.NAMES | _device_file_tools.NAMES


def scoped_event_tool(tool_name):
    return tool_name in {"search_events", "expand_event_window", "get_related_events"}


@dataclass(frozen=True)
class ResultDisposition:
    status: str
    trace_status: str | None
    observer_status: str | None


def classify_result(tool_name, result, tool_result):
    from core.tools.tool_result import ToolResult
    from core.tools.qzone_tools import _QZONE_TOOL_NAMES
    if private_receipt(tool_name) or (tool_name == "web_search" and isinstance(result, ToolResult)):
        validity = tool_result.meta.get("validity")
        if validity == "current_turn":
            return ResultDisposition("tool_executed", "ok", "finished")
        if validity == "outcome_unknown":
            return ResultDisposition("outcome_unknown", None, "outcome_unknown")
        return ResultDisposition("tool_failed", "failed", "failed")
    if tool_name in _QZONE_TOOL_NAMES and tool_result.meta.get("validity") != "current_turn":
        status = "outcome_unknown" if tool_result.meta.get("validity") == "outcome_unknown" else "tool_failed"
        return ResultDisposition(status, "failed", None)
    return None


def failure_hint(tool_name, error, char_id):
    from core import tool_result_pins
    from core.character_image_reference import ReferenceError
    from core.image_gen import ImagePresetError, discovery_note
    if isinstance(error, tool_result_pins.PinError):
        return error.hint
    if tool_name in {"generate_image", "pin_character_image_reference"} and isinstance(error, ReferenceError):
        return str(error)
    if tool_name == "generate_image" and isinstance(error, ImagePresetError):
        return "无法使用指定的生图连接。" + discovery_note(char_id)
    if tool_name == "generate_image" and getattr(error, "error_category", "") == "runware_thirdPartyInsufficientCredits":
        return "Runware 的第三方模型付费额度不足。不要重试同一连接；可使用工具定义中另一个确切的连接名。"
    return _mcp_server_reported_error(error)
