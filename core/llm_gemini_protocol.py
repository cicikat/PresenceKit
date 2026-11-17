"""Explicit Gemini generateContent REST boundary; never guesses from model names."""
from copy import deepcopy
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4
import math

from core.llm_protocol import (
    NormalizedResponse, NormalizedToolCall, _format_error, canonical_tool_history,
    internal_tool_name, portable_tool_spec,
)

PROTOCOL = "gemini_generate_content"
PARAMETERS = {"temperature": "temperature", "top_p": "topP", "top_k": "topK",
              "max_tokens": "maxOutputTokens", "stop": "stopSequences", "seed": "seed"}


def validate_preset(preset):
    if preset.get("api_protocol") != PROTOCOL:
        return
    if preset.get("provider_kind") != "gemini":
        raise ValueError("native Gemini requires provider_kind=gemini")
    endpoint(preset.get("base_url", ""), preset.get("model", ""))
    params = preset.get("params") or {}
    if not isinstance(params, dict) or set(params) - set(PARAMETERS):
        raise ValueError("unsupported native Gemini parameters")
    for key, value in params.items():
        if key == "stop":
            if not (isinstance(value, str) or isinstance(value, list) and all(isinstance(x, str) for x in value)):
                raise ValueError("Gemini stop must be text or a list of text")
        elif isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("Gemini generation parameter must be a finite number")
        elif key in {"max_tokens", "top_k", "seed"} and (not isinstance(value, int) or key != "seed" and value < 1):
            raise ValueError("Gemini token/top_k/seed parameter must be an integer in range")
        elif (key == "temperature" and not 0 <= value <= 2) or (key == "top_p" and not 0 <= value <= 1):
            raise ValueError("Gemini sampling parameter out of range")
    if preset.get("reasoning_extra_body"):
        raise ValueError("native Gemini does not accept OpenAI reasoning_extra_body")


def endpoint(base_url, model, *, stream=False):
    base = str(base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment or not parsed.path.endswith(("/v1beta", "/v1"))):
        raise ValueError("Gemini base_url must be an explicit native /v1beta or /v1 root without credentials or query")
    name = str(model).removeprefix("models/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("invalid Gemini model resource")
    return base + "/models/" + name + (":streamGenerateContent?alt=sse" if stream else ":generateContent")


def request(mc, messages, tools, tool_choice, gen_kwargs, *, stream=False):
    url = endpoint(mc.base_url, mc.model, stream=stream)
    kwargs = dict(gen_kwargs)
    timeout = kwargs.pop("timeout", getattr(mc, "request_timeout_s", 90))
    if kwargs.pop("extra_body", {}):
        raise ValueError("Gemini native protocol does not accept OpenAI extra_body")
    unknown = set(kwargs) - set(PARAMETERS)
    if unknown:
        raise ValueError("unsupported Gemini generation parameters: " + ", ".join(sorted(unknown)))
    validate_preset({"api_protocol": PROTOCOL, "provider_kind": "gemini", "base_url": mc.base_url,
                     "model": mc.model, "params": kwargs})
    history = canonical_tool_history(messages)
    contents, system, calls = [], [], {}
    for message in history:
        role = message.get("role")
        if role in {"system", "developer"}:
            if not isinstance(message.get("content"), str):
                raise ValueError("Gemini system instruction must be text")
            system.append({"text": message["content"]})
            continue
        if role == "tool":
            call_id = message["tool_call_id"]
            name, native_id = calls.pop(call_id)
            result = {"name": name, "response": {"output": message["content"]}}
            if native_id:
                result["id"] = native_id
            parts = [{"functionResponse": result}]
            wire_role = "user"
        elif role in {"user", "assistant"}:
            text = message.get("content") or ""
            if not isinstance(text, str):
                raise ValueError("Gemini native text protocol accepts text messages only")
            generic_calls = message.get("tool_calls") or []
            native = message.get("gemini_content")
            parts = deepcopy(native["parts"]) if native is not None else ([{"text": text}] if text else [])
            native_calls = [part["functionCall"] for part in parts if "functionCall" in part]
            if native is not None and (native.get("role") != "model" or len(native_calls) != len(generic_calls)):
                raise ValueError("Gemini continuation call identity mismatch")
            for index, call in enumerate(generic_calls):
                function = call["function"]
                args = json.loads(function["arguments"]) if isinstance(function["arguments"], str) else function["arguments"]
                if native is not None:
                    raw_call = native_calls[index]
                    if raw_call.get("name") != function["name"] or raw_call.get("args", {}) != args:
                        raise ValueError("Gemini continuation call mismatch")
                    if raw_call.get("id") and raw_call["id"] != call["id"]:
                        raise ValueError("Gemini continuation call ID mismatch")
                else:
                    raw_call = {"name": function["name"], "args": args, "id": call["id"]}
                    parts.append({"functionCall": raw_call})
                calls[call["id"]] = (function["name"], raw_call.get("id"))
            wire_role = "model" if role == "assistant" else "user"
        else:
            raise ValueError("unsupported Gemini message role")
        if parts:
            if contents and contents[-1]["role"] == wire_role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": wire_role, "parts": parts})
    payload = {"contents": contents, "generationConfig": {PARAMETERS[key]: value for key, value in kwargs.items()}}
    if isinstance(payload["generationConfig"].get("stopSequences"), str):
        payload["generationConfig"]["stopSequences"] = [payload["generationConfig"]["stopSequences"]]
    if system:
        payload["systemInstruction"] = {"parts": system}
    declarations = []
    for tool in tools or []:
        function = tool.get("function", tool)
        name, description, params = portable_tool_spec(function["name"], function.get("description"), function.get("parameters"))
        declarations.append({"name": name, "description": description, "parametersJsonSchema": params})
    if tool_choice not in {None, "auto", "none", "required"}:
        raise ValueError("unsupported Gemini tool choice")
    if declarations:
        payload["tools"] = [{"functionDeclarations": declarations}]
        payload["toolConfig"] = {"functionCallingConfig": {"mode": {None: "AUTO", "auto": "AUTO", "none": "NONE", "required": "ANY"}[tool_choice]}}
    return url, {"x-goog-api-key": mc.api_key, "Content-Type": "application/json"}, payload, timeout


def _packet(mc, packet, capture):
    if not isinstance(packet, dict) or packet.get("error"):
        raise _format_error(mc, "Gemini invalid response envelope")
    if (packet.get("promptFeedback") or {}).get("blockReason"):
        raise _format_error(mc, "Gemini prompt blocked", category="upstream_safety_blocked")
    usage = packet.get("usageMetadata")
    if usage is not None:
        if not isinstance(usage, dict):
            raise _format_error(mc, "Gemini invalid usage metadata")
        capture.usage = {"prompt_tokens": usage.get("promptTokenCount", 0),
                         "completion_tokens": usage.get("candidatesTokenCount", 0),
                         "total_tokens": usage.get("totalTokenCount", 0)}
    candidates = packet.get("candidates") or []
    if not candidates:
        return [], None
    if len(candidates) != 1 or not isinstance(candidates[0], dict):
        raise _format_error(mc, "Gemini expected exactly one candidate")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish and finish != "STOP":
        blocked = finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}
        raise _format_error(mc, "Gemini rejected or incomplete candidate",
                            category="upstream_safety_blocked" if blocked else "protocol_incompatible")
    content = candidate.get("content") or {}
    if not isinstance(content, dict) or content.get("role", "model") != "model":
        raise _format_error(mc, "Gemini invalid model content")
    parts = content.get("parts") or []
    if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
        raise _format_error(mc, "Gemini invalid candidate parts")
    native_parts = []
    for part in parts:
        if part.get("thought") is True:
            if "functionCall" in part:
                raise _format_error(mc,"Gemini function call marked as internal thought")
            capture.add("gemini_thought", part.get("text"))
            # Hidden from display, but signed native parts must survive the
            # next function response exactly as provided by the native API.
            native_parts.append(part)
        elif "functionCall" in part or isinstance(part.get("text"), str):
            native_parts.append(part)
        else:
            raise _format_error(mc, "Gemini unsupported non-text part")
    return native_parts, finish


def _normalize(mc, parts, capture):
    text, calls, generic, ids = [], [], [], set()
    for part in parts:
        if part.get("thought") is True:
            continue
        if "text" in part:
            text.append(part["text"])
        if "functionCall" in part:
            call = part["functionCall"]
            if (not isinstance(call, dict) or not isinstance(call.get("name"), str)
                    or not call["name"] or not isinstance(call.get("args", {}), dict)):
                raise _format_error(mc, "Gemini invalid function call")
            call_id = call.get("id") or "gemini_" + uuid4().hex
            if not isinstance(call_id, str) or call_id in ids:
                raise _format_error(mc, "Gemini duplicate or invalid function call ID")
            ids.add(call_id)
            calls.append(NormalizedToolCall(call_id, internal_tool_name(call["name"]), call.get("args", {})))
            generic.append({"id": call_id, "type": "function", "function": {"name": call["name"], "arguments": json.dumps(call.get("args", {}), ensure_ascii=False)}})
    if not calls and not "".join(text):
        raise _format_error(mc, "Gemini empty candidate")
    continuation = {"role": "assistant", "content": "".join(text),
                    "gemini_content": {"role": "model", "parts": deepcopy(parts)}}
    if generic:
        continuation["tool_calls"] = generic
    return NormalizedResponse("".join(text), calls, "completed", capture.usage, [continuation], {})


async def _events(mc, url, headers, payload, timeout):
    async with mc.client.stream("POST", url, headers=headers, json=payload, timeout=timeout, follow_redirects=False) as response:
        if response.status_code >= 300:
            raise _format_error(mc, "Gemini HTTP error", response)
        data = []
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line and data:
                try:
                    yield json.loads("\n".join(data))
                except (ValueError, TypeError) as exc:
                    raise _format_error(mc, "Gemini invalid SSE JSON") from exc
                data = []
        if data:
            raise _format_error(mc, "Gemini incomplete SSE event")


async def generate(mc, messages, *, tools, tool_choice, gen_kwargs, capture):
    stream = getattr(mc, "force_stream", False)
    try:
        url, headers, payload, timeout = request(mc, messages, tools, tool_choice, gen_kwargs, stream=stream)
    except (ValueError, KeyError, TypeError) as exc:
        raise _format_error(mc, "Gemini request validation failed") from exc
    if not stream:
        response = await mc.client.post(url, headers=headers, json=payload, timeout=timeout, follow_redirects=False)
        if response.status_code >= 300:
            raise _format_error(mc, "Gemini HTTP error", response)
        try:
            packet = response.json()
        except ValueError as exc:
            raise _format_error(mc, "Gemini invalid JSON") from exc
        parts, finish = _packet(mc, packet, capture)
    else:
        parts, finish, seen_calls = [], None, {}
        async for packet in _events(mc, url, headers, payload, timeout):
            chunk, ending = _packet(mc, packet, capture)
            fresh = []
            for part in chunk:
                call = part.get("functionCall")
                call_id = call.get("id") if isinstance(call, dict) else None
                if call_id and call_id in seen_calls:
                    if seen_calls[call_id] != part:
                        raise _format_error(mc, "Gemini conflicting repeated function call")
                    continue
                if call_id:
                    seen_calls[call_id] = part
                fresh.append(part)
            chunk = fresh
            if finish and chunk:
                raise _format_error(mc, "Gemini content after stream finish")
            parts.extend(chunk)
            finish = ending or finish
    if finish != "STOP":
        raise _format_error(mc, "Gemini response ended without STOP")
    return _normalize(mc, parts, capture)


async def stream_text(mc, messages, *, gen_kwargs, capture):
    try:
        url, headers, payload, timeout = request(mc, messages, None, None, gen_kwargs, stream=True)
    except (ValueError, KeyError, TypeError) as exc:
        raise _format_error(mc, "Gemini request validation failed") from exc
    finish, emitted = None, False
    async for packet in _events(mc, url, headers, payload, timeout):
        parts, ending = _packet(mc, packet, capture)
        if finish and parts:
            raise _format_error(mc, "Gemini content after stream finish")
        for part in parts:
            if part.get("thought") is True:
                continue
            if "functionCall" in part:
                raise _format_error(mc, "unexpected Gemini tool in text stream")
            if part.get("text"):
                emitted = True
                yield part["text"]
        finish = ending or finish
    if finish != "STOP" or not emitted:
        raise _format_error(mc, "Gemini stream ended without complete text")
