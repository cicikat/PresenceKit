"""Tool outcome; called through the stable dispatcher facade."""

from dataclasses import dataclass
def _log_execute_failure_context(tool_name: str, tool_args: dict, origin: str) -> None:
    """execute() 失败时把完整调用现场记进 error.log（Brief 120 事后排查补的）。

    故意不复用 action_trace.build_args_digest() 的白名单——那个白名单是为了不让
    diary/memory 这类隐私内容经 action_trace 回流进模型自己的下轮 prompt（层 10.5），
    error.log 只给人看、从不喂回模型，此前唯一记录的信息只有异常本身，连工具名/
    参数/origin 都没有，出故障后完全没法反推真正发给 MCP 的是什么（Brief 120 排查
    cedartoy「command 参数必填」时吃过这个亏）。这里记完整 tool_args。
    """
    from core import tool_dispatcher as _runtime
    if tool_name in _runtime._computer_use.OWNER_TOOL_NAMES | _runtime._gmail_tools.NAMES | _runtime._device_file_tools.NAMES:
        return
    try:
        args_repr = _runtime.json.dumps(tool_args, ensure_ascii=False, default=str)
    except Exception:
        args_repr = repr(tool_args)
    _runtime.logger.error(
        "[tool_dispatcher.execute] 失败上下文: tool=%s origin=%s args=%s",
        tool_name, origin, args_repr,
    )


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
    from core import tool_dispatcher as _runtime
    if isinstance(e, RuntimeError) and str(e).startswith(_runtime._MCP_SERVER_ERROR_PREFIX):
        return str(e)[len(_runtime._MCP_SERVER_ERROR_PREFIX):].strip() or None
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
    from core import tool_dispatcher as _runtime
    return _runtime.ToolExecutionOutcome(
        status=status,
        result=result,
        confirmation_request=confirmation_request,
        missing_parameters=tuple(missing_parameters),
    )
