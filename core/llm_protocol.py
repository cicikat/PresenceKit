"""Protocol boundary for OpenAI and Anthropic Messages API calls.

The rest of PresenceKit consumes normalized assistant text and tool calls.  This
module is the only place that knows either wire format.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator


VALID_API_PROTOCOLS = frozenset({"chat_completions", "responses", "anthropic_messages"})
VALID_ANTHROPIC_AUTH_MODES = frozenset({"x_api_key", "bearer"})


class UpstreamResponseFormatError(RuntimeError):
    """The gateway response does not match the preset's declared protocol."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        category: str = "protocol_incompatible",
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.category = category


def _protocol(mc: Any) -> str:
    """Keep older test doubles and external callers on the legacy default."""
    value = getattr(mc, "api_protocol", "chat_completions")
    return value if isinstance(value, str) else "chat_completions"


@dataclass
class NormalizedToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class NormalizedResponse:
    assistant_text: str
    tool_calls: list[NormalizedToolCall]
    status: str
    usage: dict[str, Any] | None
    continuation_items: list[dict[str, Any]]
    raw_response: Any


def _diagnostic(mc: Any, response: Any = None) -> str:
    status = getattr(response, "status_code", None)
    suffix = f" http_status={status}" if status is not None else ""
    return (
        f"preset={mc.name!r} provider={mc.provider_kind!r} model={mc.model!r} "
        f"api_protocol={_protocol(mc)!r} response_type={type(response).__name__}{suffix}"
    )


def _http_error_category(status: int | None) -> str:
    if status == 404:
        return "upstream_not_found"
    if status in (401, 403):
        return "upstream_auth_failed"
    if status == 429:
        return "upstream_rate_limited"
    if status is not None and 400 <= status < 500:
        return "upstream_request_rejected"
    if status is not None and status >= 500:
        return "upstream_unavailable"
    return "protocol_incompatible"


def _format_error(
    mc: Any,
    message: str,
    response: Any = None,
    *,
    category: str | None = None,
) -> UpstreamResponseFormatError:
    status = getattr(response, "status_code", None)
    normalized_status = status if isinstance(status, int) else None
    return UpstreamResponseFormatError(
        f"{message}; {_diagnostic(mc, response)}",
        http_status=normalized_status,
        category=category or _http_error_category(normalized_status),
    )


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _parse_arguments(mc: Any, value: Any, response: Any = None) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        raise _format_error(mc, "tool call arguments are not a JSON string", response)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise _format_error(mc, "tool call arguments are invalid JSON", response) from exc
    if not isinstance(parsed, dict):
        raise _format_error(mc, "tool call arguments must decode to an object", response)
    return parsed


def _usage(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    result = _as_dict(value)
    return result or None


def _chat_assistant_message(message: Any) -> dict[str, Any]:
    dump = getattr(message, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    result: dict[str, Any] = {"role": "assistant"}
    content = getattr(message, "content", None)
    if content is not None:
        result["content"] = content
    calls = getattr(message, "tool_calls", None)
    if calls:
        result["tool_calls"] = [
            {
                "id": getattr(call, "id", ""),
                "type": "function",
                "function": {
                    "name": getattr(getattr(call, "function", None), "name", ""),
                    "arguments": getattr(getattr(call, "function", None), "arguments", "{}"),
                },
            }
            for call in calls
        ]
    return result


def _normalize_chat_completion(mc: Any, response: Any) -> NormalizedResponse:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise _format_error(mc, "expected an OpenAI ChatCompletion with choices[0].message", response)
    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None or not hasattr(message, "content"):
        raise _format_error(mc, "ChatCompletion response is missing choices[0].message.content", response)

    tool_calls: list[NormalizedToolCall] = []
    if getattr(choice, "finish_reason", None) == "tool_calls" and getattr(message, "tool_calls", None):
        for call in message.tool_calls:
            function = getattr(call, "function", None)
            call_id = getattr(call, "id", None)
            name = getattr(function, "name", None)
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                raise _format_error(mc, "ChatCompletion tool call is missing id or name", response)
            tool_calls.append(
                NormalizedToolCall(
                    id=call_id,
                    name=name,
                    arguments=_parse_arguments(mc, getattr(function, "arguments", None), response),
                )
            )
    return NormalizedResponse(
        assistant_text=getattr(message, "content", None) or "",
        tool_calls=tool_calls,
        status=str(getattr(choice, "finish_reason", "") or ""),
        usage=_usage(getattr(response, "usage", None)),
        continuation_items=[_chat_assistant_message(message)],
        raw_response=response,
    )


def _message_text(content: Any) -> str:
    if not isinstance(content, str):
        raise ValueError("Responses input only supports string message content in this adapter")
    return content


def responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert the existing structured conversation without flattening its order."""
    result: list[dict[str, Any]] = []
    for message in messages:
        item_type = message.get("type")
        if item_type == "function_call":
            call_id = message.get("call_id")
            name = message.get("name")
            arguments = message.get("arguments")
            if not all(isinstance(value, str) and value for value in (call_id, name, arguments)):
                raise ValueError("Responses function-call history is missing call_id, name, or arguments")
            result.append({
                "type": "function_call", "call_id": call_id, "name": name, "arguments": arguments,
            })
            continue
        if item_type == "function_call_output":
            call_id = message.get("call_id")
            output = message.get("output")
            if not isinstance(call_id, str) or not call_id or not isinstance(output, str):
                raise ValueError("Responses function-call output history is invalid")
            result.append({"type": "function_call_output", "call_id": call_id, "output": output})
            continue
        if item_type == "message" and message.get("role") == "assistant":
            content = message.get("content")
            if not isinstance(content, list):
                raise ValueError("Responses assistant message history has invalid content")
            result.append({"type": "message", "role": "assistant", "content": list(content)})
            continue
        role = message.get("role")
        content = message.get("content")
        if role in {"system", "developer", "user"}:
            result.append({
                "type": "message",
                "role": role,
                "content": [{"type": "input_text", "text": _message_text(content)}],
            })
        elif role == "assistant":
            if content:
                result.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": _message_text(content)}],
                })
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                call_id = call.get("id")
                name = function.get("name")
                arguments = function.get("arguments")
                if not all(isinstance(value, str) and value for value in (call_id, name, arguments)):
                    raise ValueError("assistant tool history is missing id, name, or JSON arguments")
                result.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                })
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("tool history is missing tool_call_id")
            result.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": _message_text(content),
            })
        else:
            raise ValueError(f"unsupported message role for Responses API: {role!r}")
    return result


def responses_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    result: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", tool)
        name = function.get("name")
        parameters = function.get("parameters")
        if not isinstance(name, str) or not name or not isinstance(parameters, dict):
            raise ValueError("invalid function tool schema")
        item: dict[str, Any] = {"type": "function", "name": name, "parameters": parameters}
        if "description" in function:
            item["description"] = function["description"]
        if "strict" in function:
            item["strict"] = function["strict"]
        result.append(item)
    return result


def anthropic_messages_input(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert the internal chat-shaped history to Anthropic Messages input.

    Anthropic keeps system instructions in a dedicated top-level field and
    represents tool results as user content blocks.  Keeping this conversion in
    the protocol boundary means the pipeline can continue to use its existing
    Chat/Responses-neutral loop context.
    """
    system_parts: list[str] = []
    result: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        nonlocal pending_tool_results
        if pending_tool_results:
            result.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []

    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role in {"system", "developer"}:
            if not isinstance(content, str):
                raise ValueError("Anthropic system/developer message content must be text")
            system_parts.append(content)
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("Anthropic tool history is missing tool_call_id")
            if not isinstance(content, str):
                raise ValueError("Anthropic tool history content must be text")
            pending_tool_results.append({
                "type": "tool_result", "tool_use_id": call_id, "content": content,
            })
            continue

        flush_tool_results()
        if role not in {"user", "assistant"}:
            raise ValueError(f"unsupported message role for Anthropic Messages API: {role!r}")
        if not isinstance(content, (str, list)):
            raise ValueError("Anthropic user/assistant message content must be text or content blocks")
        result.append({"role": role, "content": content})

    flush_tool_results()
    if not result:
        raise ValueError("Anthropic Messages API requires at least one user or assistant message")
    return ("\n\n".join(part for part in system_parts if part), result)


def anthropic_messages_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Translate the existing OpenAI function schema to Anthropic's tool schema."""
    if not tools:
        return None
    result: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", tool)
        name = function.get("name")
        parameters = function.get("parameters")
        if not isinstance(name, str) or not name or not isinstance(parameters, dict):
            raise ValueError("invalid function tool schema")
        item: dict[str, Any] = {"name": name, "input_schema": parameters}
        if "description" in function:
            item["description"] = function["description"]
        if "strict" in function:
            item["strict"] = function["strict"]
        result.append(item)
    return result


def _anthropic_message_url(mc: Any) -> str:
    base_url = str(getattr(mc, "base_url", "") or "").rstrip("/")
    if not base_url:
        raise _format_error(mc, "Anthropic Messages API requires base_url")
    if base_url.endswith("/v1/messages"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/messages"
    return f"{base_url}/v1/messages"


def _anthropic_headers(mc: Any) -> dict[str, str]:
    key = str(getattr(mc, "api_key", "") or "")
    auth_mode = str(getattr(mc, "anthropic_auth_mode", "x_api_key") or "x_api_key")
    if auth_mode not in VALID_ANTHROPIC_AUTH_MODES:
        raise _format_error(mc, f"unknown anthropic_auth_mode {auth_mode!r}")
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if auth_mode == "bearer":
        headers["authorization"] = f"Bearer {key}"
    else:
        headers["x-api-key"] = key
    return headers


def _anthropic_tool_choice(value: str | None) -> dict[str, str] | None:
    if value is None:
        return None
    mapping = {"auto": "auto", "none": "none", "required": "any"}
    if value not in mapping:
        raise ValueError(f"unsupported Anthropic tool_choice: {value!r}")
    return {"type": mapping[value]}


def _anthropic_messages_request(
    mc: Any,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | None,
    gen_kwargs: dict[str, Any],
    stream: bool = False,
) -> tuple[str, dict[str, str], dict[str, Any], Any]:
    system, converted_messages = anthropic_messages_input(messages)
    kwargs = dict(gen_kwargs)
    timeout = kwargs.pop("timeout", None)
    max_tokens = kwargs.pop("max_tokens", 1024)
    extra_body = kwargs.pop("extra_body", None)
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError("Anthropic max_tokens must be a positive integer")
    if extra_body is not None and not isinstance(extra_body, dict):
        raise ValueError("Anthropic extra_body must be an object")

    payload: dict[str, Any] = {
        "model": mc.model,
        "max_tokens": max_tokens,
        "messages": converted_messages,
        **kwargs,
    }
    if system:
        payload["system"] = system
    converted_tools = anthropic_messages_tools(tools)
    if converted_tools:
        payload["tools"] = converted_tools
        payload["tool_choice"] = _anthropic_tool_choice(tool_choice or "auto")
    if extra_body:
        # `reasoning_extra_body` is intentionally a provider escape hatch.  For
        # native Anthropic presets it carries native Anthropic fields directly.
        payload.update(extra_body)
    if stream:
        payload["stream"] = True
    return _anthropic_message_url(mc), _anthropic_headers(mc), payload, timeout


def _normalize_anthropic_messages(mc: Any, response: Any) -> NormalizedResponse:
    if not isinstance(response, dict):
        raise _format_error(mc, "Anthropic Messages response is not a JSON object", response)
    content = response.get("content")
    status = response.get("stop_reason")
    if not isinstance(content, list):
        raise _format_error(mc, "Anthropic Messages response has invalid content", response)
    if not isinstance(status, str) or not status:
        raise _format_error(mc, "Anthropic Messages response is missing stop_reason", response)

    text_parts: list[str] = []
    tool_calls: list[NormalizedToolCall] = []
    continuation_content: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            raise _format_error(mc, "Anthropic Messages content block is invalid", response)
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise _format_error(mc, "Anthropic text block is not text", response)
            text_parts.append(text)
            continuation_content.append({"type": "text", "text": text})
        elif block_type == "tool_use":
            call_id = block.get("id")
            name = block.get("name")
            arguments = block.get("input")
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                raise _format_error(mc, "Anthropic tool_use block is missing id or name", response)
            if not isinstance(arguments, dict):
                raise _format_error(mc, "Anthropic tool_use input must be an object", response)
            tool_calls.append(NormalizedToolCall(id=call_id, name=name, arguments=dict(arguments)))
            continuation_content.append({
                "type": "tool_use", "id": call_id, "name": name, "input": dict(arguments),
            })
        elif block_type in {"thinking", "redacted_thinking"}:
            # Internal reasoning is neither rendered nor carried into the local
            # loop history. The independent archive already captured returned
            # plaintext thinking before normalization; it is not prompt memory.
            continue
        else:
            raise _format_error(mc, f"Anthropic Messages content has unknown block type {block_type!r}", response)
    if not continuation_content:
        raise _format_error(mc, "Anthropic Messages response has no text or tool call", response)
    return NormalizedResponse(
        assistant_text="".join(text_parts),
        tool_calls=tool_calls,
        status=status,
        usage=_usage(response.get("usage")),
        continuation_items=[{"role": "assistant", "content": continuation_content}],
        raw_response=response,
    )


def _responses_kwargs(gen_kwargs: dict[str, Any]) -> dict[str, Any]:
    result = dict(gen_kwargs)
    if "max_tokens" in result:
        result["max_output_tokens"] = result.pop("max_tokens")
    # These parameters are accepted by Chat Completions but not by Responses.
    result.pop("frequency_penalty", None)
    result.pop("presence_penalty", None)
    return result


def _normalize_responses(mc: Any, response: Any) -> NormalizedResponse:
    output = getattr(response, "output", None)
    status = getattr(response, "status", None)
    if status != "completed":
        if status == "incomplete":
            raise _format_error(mc, "Responses API returned an incomplete response", response)
        if status == "failed":
            raise _format_error(mc, "Responses API returned a failed response", response)
        raise _format_error(mc, "Responses API response is missing completed status", response)
    if not isinstance(output, list) or not output:
        raise _format_error(mc, "Responses API output has no consumable items", response)

    text_parts: list[str] = []
    tool_calls: list[NormalizedToolCall] = []
    continuation_items: list[dict[str, Any]] = []
    saw_consumable = False
    for item in output:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            content = getattr(item, "content", None)
            if not isinstance(content, list):
                raise _format_error(mc, "Responses message output has invalid content", response)
            output_text_parts: list[str] = []
            for part in content:
                if getattr(part, "type", None) != "output_text":
                    raise _format_error(mc, "Responses message output has an unknown content item", response)
                text = getattr(part, "text", None)
                if not isinstance(text, str):
                    raise _format_error(mc, "Responses output_text is not text", response)
                text_parts.append(text)
                output_text_parts.append(text)
            if output_text_parts:
                continuation_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text} for text in output_text_parts],
                })
                saw_consumable = True
        elif item_type == "function_call":
            call_id = getattr(item, "call_id", None)
            name = getattr(item, "name", None)
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                raise _format_error(mc, "Responses function call is missing call_id or name", response)
            arguments_raw = getattr(item, "arguments", None)
            tool_calls.append(NormalizedToolCall(
                id=call_id,
                name=name,
                arguments=_parse_arguments(mc, arguments_raw, response),
            ))
            continuation_items.append({
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments_raw,
            })
            saw_consumable = True
        elif item_type == "reasoning":
            # Reasoning is archived separately, never rendered or put in history.
            # The current loop has no
            # stateful previous_response_id contract, so it is deliberately absent
            # from continuation input as well.
            continue
        else:
            raise _format_error(mc, f"Responses API output contains unknown item type {item_type!r}", response)
    if not saw_consumable:
        raise _format_error(mc, "Responses API output has no assistant text or function call", response)
    return NormalizedResponse(
        assistant_text="".join(text_parts),
        tool_calls=tool_calls,
        status=status,
        usage=_usage(getattr(response, "usage", None)),
        continuation_items=continuation_items,
        raw_response=response,
    )


async def _create(
    mc: Any,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    tool_choice: str | None,
    gen_kwargs: dict[str, Any],
    capture,
) -> NormalizedResponse:
    """Call the declared wire protocol and normalize its response."""
    protocol = _protocol(mc)
    if protocol == "chat_completions":
        kwargs = dict(gen_kwargs)
        if tools:
            kwargs.update(tools=tools, tool_choice=tool_choice or "auto")
        response = await mc.client.chat.completions.create(model=mc.model, messages=messages, **kwargs)
        capture.response(response)
        return _normalize_chat_completion(mc, response)
    if protocol == "anthropic_messages":
        url, headers, payload, timeout = _anthropic_messages_request(
            mc, messages, tools=tools, tool_choice=tool_choice, gen_kwargs=gen_kwargs,
        )
        response = await mc.client.post(url, headers=headers, json=payload, timeout=timeout)
        try:
            response.raise_for_status()
        except Exception as exc:
            raise _format_error(mc, "Anthropic Messages API returned an HTTP error", response) from exc
        try:
            payload = response.json()
        except Exception as exc:
            raise _format_error(mc, "Anthropic Messages API returned invalid JSON", response) from exc
        capture.response(payload)
        return _normalize_anthropic_messages(mc, payload)
    if protocol != "responses":
        raise _format_error(mc, "unknown api_protocol")
    responses = getattr(mc.client, "responses", None)
    if responses is None or not callable(getattr(responses, "create", None)):
        raise _format_error(mc, "installed OpenAI SDK does not support client.responses.create")
    kwargs = _responses_kwargs(gen_kwargs)
    kwargs.update(
        input=responses_input(messages),
        store=False,
    )
    converted_tools = responses_tools(tools)
    if converted_tools:
        kwargs["tools"] = converted_tools
        kwargs["tool_choice"] = tool_choice or "auto"
    response = await responses.create(model=mc.model, **kwargs)
    capture.response(response)
    return _normalize_responses(mc, response)


async def _stream_text(
    mc: Any,
    messages: list[dict[str, Any]],
    *,
    gen_kwargs: dict[str, Any],
    capture,
) -> AsyncIterator[str]:
    """Yield text deltas while validating the declared protocol's stream state."""
    if _protocol(mc) == "chat_completions":
        stream = await mc.client.chat.completions.create(
            model=mc.model, messages=messages, stream=True, **gen_kwargs,
        )
        async for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if not isinstance(choices, list) or not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            for key in ("reasoning_content", "reasoning"):
                capture.add(key, getattr(delta, key, None))
            text = getattr(delta, "content", None)
            if text:
                yield text
        return
    if _protocol(mc) == "anthropic_messages":
        url, headers, payload, timeout = _anthropic_messages_request(
            mc, messages, tools=None, tool_choice=None, gen_kwargs=gen_kwargs, stream=True,
        )
        completed = False
        async with mc.client.stream("POST", url, headers=headers, json=payload, timeout=timeout) as response:
            try:
                response.raise_for_status()
            except Exception as exc:
                raise _format_error(mc, "Anthropic Messages stream returned an HTTP error", response) from exc
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw_data = line[5:].strip()
                if not raw_data:
                    continue
                if raw_data == "[DONE]":
                    completed = True
                    continue
                try:
                    event = json.loads(raw_data)
                except json.JSONDecodeError as exc:
                    raise _format_error(mc, "Anthropic Messages stream contains invalid JSON", response) from exc
                event_type = event.get("type") if isinstance(event, dict) else None
                if event_type == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "thinking":
                        capture.add("thinking", block.get("thinking"))
                if event_type == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "thinking_delta":
                        capture.add("thinking", delta.get("thinking"))
                    if delta.get("type") == "text_delta":
                        text = delta.get("text")
                        if not isinstance(text, str):
                            raise _format_error(mc, "Anthropic text stream delta is not text", response)
                        if text:
                            yield text
                elif event_type == "message_stop":
                    completed = True
                elif event_type == "error":
                    raise _format_error(mc, "Anthropic Messages stream terminated with error", response)
                # message_start/content_block_start/message_delta/content_block_stop
                # carry lifecycle or non-rendered thinking data only.
        if not completed:
            raise _format_error(mc, "Anthropic Messages stream ended before message_stop")
        return
    if _protocol(mc) != "responses":
        raise _format_error(mc, "unknown api_protocol")
    responses = getattr(mc.client, "responses", None)
    if responses is None or not callable(getattr(responses, "create", None)):
        raise _format_error(mc, "installed OpenAI SDK does not support client.responses.create")

    stream = await responses.create(
        model=mc.model,
        input=responses_input(messages),
        stream=True,
        store=False,
        **_responses_kwargs(gen_kwargs),
    )
    completed = False
    emitted = ""
    function_argument_deltas: dict[str, str] = {}
    async for event in stream:
        event_type = getattr(event, "type", None)
        if event_type in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
            source = "reasoning_summary" if "summary" in event_type else "reasoning_content"
            capture.add(source, getattr(event, "delta", None))
        if event_type == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if not isinstance(delta, str):
                raise _format_error(mc, "Responses text delta is not text", event)
            emitted += delta
            yield delta
        elif event_type == "response.function_call_arguments.delta":
            item_id = str(getattr(event, "item_id", ""))
            delta = getattr(event, "delta", None)
            if not isinstance(delta, str):
                raise _format_error(mc, "Responses function argument delta is not text", event)
            function_argument_deltas[item_id] = function_argument_deltas.get(item_id, "") + delta
        elif event_type in {"response.function_call_arguments.done", "response.output_item.done"}:
            # These events establish tool/item completion. The final completed
            # response below remains the authoritative normalized result.
            continue
        elif event_type == "response.completed":
            # The completed response is authoritative; avoid storing both its
            # reasoning and the same streamed deltas.
            from core.llm_reasoning_store import Capture
            final_capture = Capture(mc)
            final_capture.response(getattr(event, "response", None))
            if final_capture.parts:
                capture.parts = final_capture.parts
            normalized = _normalize_responses(mc, getattr(event, "response", None))
            if normalized.tool_calls:
                raise _format_error(mc, "Responses text stream unexpectedly returned function calls", event)
            if normalized.assistant_text and not emitted:
                emitted = normalized.assistant_text
                yield normalized.assistant_text
            completed = True
        elif event_type in {"response.failed", "response.incomplete", "error"}:
            raise _format_error(mc, f"Responses stream terminated with {event_type}", event)
        # Other lifecycle events (created, queued, in_progress, content part) do
        # not carry user-visible text and are intentionally ignored.
    if not completed:
        raise _format_error(mc, "Responses stream ended before response.completed")


async def create(mc, messages, *, tools=None, tool_choice=None, gen_kwargs):
    """Archive returned reasoning independently, including rejected completions."""
    from core.llm_reasoning_store import Capture
    capture = Capture(mc)
    try:
        result = await _create(mc, messages, tools=tools, tool_choice=tool_choice,
                               gen_kwargs=gen_kwargs, capture=capture)
        capture.status = "completed"
        return result
    finally:
        await capture.save()


async def stream_text(mc, messages, *, gen_kwargs):
    """Archive reasoning deltas and inline tags, including interrupted streams."""
    from core.llm_reasoning_store import Capture
    capture = Capture(mc)
    source = _stream_text(mc, messages, gen_kwargs=gen_kwargs, capture=capture)
    try:
        async for text in source:
            capture.text.append(text)
            yield text
        capture.status = "completed"
    finally:
        try:
            await source.aclose()
        finally:
            await capture.save()
