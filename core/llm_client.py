"""
LLM 客户端模块
所有 LLM 调用的唯一出口，支持多模型 preset 路由（DeepSeek / Claude / 本地）。
Preset 路由、参数合并、provider 白名单由 core.model_registry 管理。
Prompt-style 转换（narrative / xml）由 core.prompt_style 管理。
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI

from core import thinking
from core.config_loader import get_config
from core.error_handler import log_error
from core.model_registry import ModelClient, get_model_client, reload_registry
from core.llm_protocol import UpstreamResponseFormatError, create as create_protocol_response, stream_text
from core.prompt_layer import sanitize_messages
from core.prompt_style import apply_prompt_style

logger = logging.getLogger(__name__)


def _log_empty_completion(mc, normalized, content: str) -> None:
    """Diagnose empty output without logging response bodies or reasoning."""
    if content.strip() or normalized.tool_calls:
        return
    raw_text = normalized.assistant_text
    status = normalized.status
    known_statuses = {
        "stop", "length", "tool_calls", "content_filter", "end_turn",
        "max_tokens", "stop_sequence", "tool_use", "completed", "incomplete",
    }
    choices = getattr(normalized.raw_response, "choices", None)
    message = getattr(choices[0], "message", None) if isinstance(choices, list) and choices else None
    reasoning = getattr(message, "reasoning_content", None)
    raw_calls = getattr(message, "tool_calls", None)
    logger.warning(
        "[llm_client.empty_completion] protocol=%s status=%s "
        "raw_text_chars=%d cleaned_text_chars=%d reasoning_chars=%d "
        "raw_tool_calls=%d normalized_tool_calls=%d",
        getattr(mc, "api_protocol", "chat_completions"),
        status if status in known_statuses else "unknown",
        len(raw_text) if isinstance(raw_text, str) else 0,
        len(content),
        len(reasoning) if isinstance(reasoning, str) else 0,
        len(raw_calls) if isinstance(raw_calls, list) else 0,
        len(normalized.tool_calls),
    )

# Logging contract: request URLs (and therefore query strings) stay at DEBUG;
# INFO emits exactly one completed-call summary with model, purpose and latency.
# The OpenAI SDK uses the ``httpx`` logger for per-request URL lines, so keep it
# quiet unless an operator explicitly enables debug logging.
logging.getLogger("httpx").setLevel(logging.WARNING)


def _record_api_call(
    *,
    provider: str,
    model: str,
    purpose: str,
    started_at: float,
    ok: bool,
    output_hint: str = "",
) -> None:
    from core.api_call_log import append

    append(
        caller="llm_client",
        purpose=purpose,
        provider=provider,
        model=model,
        duration_ms=int((time.perf_counter() - started_at) * 1000),
        ok=ok,
        output_hint=output_hint,
    )


def _record_debug_request(
    *,
    provider: str,
    model: str,
    purpose: str,
    messages: list[dict],
    tools: list[dict] | None,
    request_kwargs: dict[str, Any],
) -> None:
    """Persist an opt-in semantic request snapshot without affecting the call."""
    from core.llm_debug_requests import append

    append(
        provider=provider,
        model=model,
        purpose=purpose,
        messages=messages,
        tools=tools,
        request_kwargs=request_kwargs,
    )


def _log_completed_call(*, provider: str, model: str, purpose: str, started_at: float) -> None:
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info(
        "[llm_client] 对话 API 调用 model=%s purpose=%s duration_ms=%d",
        model,
        purpose,
        duration_ms,
    )
    _record_api_call(
        provider=provider,
        model=model,
        purpose=purpose,
        started_at=started_at,
        ok=True,
    )

# Vision client is kept as a separate singleton; it does not participate in
# preset routing (as specified — vision stays on its own `vision:` block).
_vision_client: AsyncOpenAI | None = None


# -- Call-category timeouts (seconds) ----------------------------------------
# probe/intent/detect_emotion: lightweight, 10 s; summary/consolidation: 30 s
# chat: main turn 90 s; vision: 30 s
_CALL_TIMEOUTS: dict[str, float] = {
    "probe":          10.0,
    "intent":         10.0,
    "detect_emotion": 10.0,
    "summary":        30.0,
    "consolidation":  30.0,
    "chat":           90.0,
    "vision":         30.0,
    "perform":        10.0,
    "monologue":      10.0,
    "scenario_reconcile": 8.0,
    "event_edge_proposer": 30.0,
    "rpg_kp":        30.0,
}
_DEFAULT_CALL_TIMEOUT: float = 90.0


def _get_proxy_url() -> str | None:
    """读取代理配置，未启用时返回 None（vision client 专用；preset clients 在 model_registry 中建）"""
    proxy_cfg = get_config().get("proxy", {})
    if proxy_cfg.get("enabled", False):
        return proxy_cfg.get("http") or None
    return None


def _make_http_client(proxy_url: str | None) -> httpx.AsyncClient:
    base_timeout = httpx.Timeout(timeout=_DEFAULT_CALL_TIMEOUT, connect=10.0)
    if proxy_url:
        return httpx.AsyncClient(proxy=proxy_url, timeout=base_timeout)
    return httpx.AsyncClient(trust_env=False, timeout=base_timeout)


def _get_client() -> AsyncOpenAI:
    """薄封装：返回 chat preset 的 AsyncOpenAI 实例。
    保留此函数使外部少数直接调用者（和旧测试）不需要改动。
    """
    return get_model_client("chat").client


def _get_vision_client() -> AsyncOpenAI | None:
    """获取视觉模型客户端，未配置时返回None"""
    global _vision_client
    cfg = get_config().get("vision", {})
    if not cfg.get("enabled", False):
        return None
    if _vision_client is None:
        proxy_url = _get_proxy_url()
        http_client = _make_http_client(proxy_url)
        _vision_client = AsyncOpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            http_client=http_client,
        )
        logger.info(f"[llm_client] Vision客户端已初始化: {cfg.get('model')}")
    return _vision_client


async def reload_client() -> None:
    """
    重置所有 LLM 客户端（代理/API Key 配置变更后调用）。
    下次调用时将按最新 config 重建。
    """
    global _vision_client
    retired_vision = _vision_client
    _vision_client = None
    retired_models = reload_registry()

    retired_clients = [mc.client for mc in retired_models]
    if retired_vision is not None:
        retired_clients.append(retired_vision)
    seen: set[int] = set()
    closed = 0
    for client in retired_clients:
        identity = id(client)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            close = getattr(client, "close", None) or getattr(client, "aclose", None)
            if not callable(close):
                raise TypeError(f"client {type(client).__name__} has no async close method")
            await close()
            closed += 1
        except Exception as exc:
            logger.warning("[llm_client] 关闭旧客户端失败: %s", exc)
    logger.info(
        "[llm_client] 客户端已重置并关闭 %d 个旧连接池，下次请求按最新配置重建",
        closed,
    )


def _first_chat_choice(
    response: Any,
    *,
    operation: str,
    require_tool_fields: bool = False,
    require_serializable_message: bool = False,
):
    """Validate the minimum response contract before any `.choices` access."""
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or not choices:
        raise UpstreamResponseFormatError(
            f"{operation} received {type(response).__name__}, expected an OpenAI "
            "ChatCompletion with choices[0].message"
        )

    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None or not hasattr(message, "content"):
        raise UpstreamResponseFormatError(
            f"{operation} response is missing choices[0].message.content"
        )
    if require_tool_fields and (
        not hasattr(choice, "finish_reason") or not hasattr(message, "tool_calls")
    ):
        raise UpstreamResponseFormatError(
            f"{operation} response is missing function-calling fields"
        )
    if require_serializable_message and not callable(getattr(message, "model_dump", None)):
        raise UpstreamResponseFormatError(
            f"{operation} response message cannot be serialized for a tool loop"
        )
    return choice


async def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens_override: int | None = None,
    use_vision: bool = False,
    call_category: str = "chat",
    *,
    char_id: str | None = None,
    preset_name: str | None = None,
    is_proactive: bool = False,
) -> str:
    """
    调用 LLM 生成回复

    参数:
        messages: OpenAI 格式的消息列表 [{role, content}, ...]
        tools:    工具定义列表（function_calling 模式时使用）
        use_vision: 使用视觉模型处理图片
        call_category: 路由到对应 preset 的类别名
        char_id:  显式指定"替谁说话"时传（Brief 30）；None（默认）按活跃角色解析，与现状一致
        preset_name: 直接选择 preset；给定时不经过 routing profile，未知名字明确失败
        is_proactive: 本次是否 scheduler 主动消息（Brief 32 · thinking.apply_to_proactive 用）

    返回:
        模型生成的文本字符串
        function_calling 模式下如果模型调用了工具，返回序列化后的工具调用 JSON
    """
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("llm")
    _timeout = _CALL_TIMEOUTS.get(call_category, _DEFAULT_CALL_TIMEOUT)

    # vision 模式走独立 vision client，不经过 preset 路由
    if use_vision:
        vision_client = _get_vision_client()
        if vision_client:
            vision_cfg = get_config().get("vision", {})
            # Vision branch: sanitize only (no prompt_style transform needed)
            safe_msgs = sanitize_messages(messages)
            started_at = time.perf_counter()
            try:
                _record_debug_request(
                    provider=str(vision_cfg.get("provider") or "vision"),
                    model=str(vision_cfg.get("model") or ""),
                    purpose="vision",
                    messages=safe_msgs,
                    tools=None,
                    request_kwargs={"max_tokens": 1000, "timeout": _CALL_TIMEOUTS["vision"]},
                )
                response = await vision_client.chat.completions.create(
                    model=vision_cfg["model"],
                    messages=safe_msgs,
                    max_tokens=1000,
                    timeout=_CALL_TIMEOUTS["vision"],
                )
                choice = _first_chat_choice(response, operation="chat[vision]")
                _log_completed_call(
                    provider=str(vision_cfg.get("provider") or "vision"),
                    model=str(vision_cfg.get("model") or ""),
                    purpose="vision",
                    started_at=started_at,
                )
                return choice.message.content or ""
            except Exception as e:
                _record_api_call(
                    provider=str(vision_cfg.get("provider") or "vision"),
                    model=str(vision_cfg.get("model") or ""),
                    purpose="vision",
                    started_at=started_at,
                    ok=False,
                    output_hint=type(e).__name__,
                )
                log_error("llm_client.chat.vision", e)
                return ""

    if preset_name is None:
        mc: ModelClient = get_model_client(call_category, char_id=char_id)
    else:
        mc = get_model_client(call_category, char_id=char_id, preset_name=preset_name)

    # Brief 32：monologue 路线在 prompt_style 转换前注入（作为普通 system 消息一并转换）；
    # native 路线不改 messages，只影响下面的 extra_body。
    messages = await thinking.maybe_apply(
        messages, call_category=call_category, char_id=char_id, is_proactive=is_proactive, mc=mc,
    )

    # Phase 2: apply prompt style BEFORE sanitize so _layer is still available
    messages = apply_prompt_style(messages, mc.prompt_style)
    messages = sanitize_messages(messages)

    model = mc.model
    mode = mc.tool_call_mode
    started_at = time.perf_counter()

    # Build generation kwargs from preset params; max_tokens_override wins
    _gen_kwargs: dict[str, Any] = dict(mc.params)
    if max_tokens_override is not None:
        _gen_kwargs["max_tokens"] = max_tokens_override
    _gen_kwargs["timeout"] = _timeout
    _gen_kwargs.update(
        thinking.build_reasoning_kwargs(mc, call_category=call_category, is_proactive=is_proactive)
    )

    try:
        request_messages = messages
        request_tools = tools if mode == "function_calling" and tools else None
        request_debug: dict[str, Any] = dict(_gen_kwargs)
        if request_tools:
            request_debug["tool_choice"] = "auto"
        # ── xml_fallback 模式（不支持 FC 的模型）────────────────────────────
        if mode == "xml_fallback" and tools:
            tool_desc = _build_xml_tool_desc(tools)
            request_messages = list(messages)
            injected = False
            for i, m in enumerate(request_messages):
                if m["role"] == "system":
                    request_messages[i] = {
                        "role": "system",
                        "content": m["content"] + "\n\n" + tool_desc,
                    }
                    injected = True
                    break
            if not injected:
                request_messages.insert(0, {"role": "system", "content": tool_desc})
            request_debug["tool_encoding"] = "xml_fallback"

        _record_debug_request(
            provider=mc.provider_kind, model=mc.model, purpose=call_category,
            messages=request_messages, tools=request_tools or (tools if mode == "xml_fallback" else None),
            request_kwargs={"api_protocol": getattr(mc, "api_protocol", "chat_completions"), **request_debug},
        )
        normalized = await create_protocol_response(
            mc,
            request_messages,
            tools=request_tools,
            tool_choice="auto" if request_tools else None,
            gen_kwargs=_gen_kwargs,
        )
        if request_tools and normalized.tool_calls:
            tool_calls = [
                {"name": call.name, "arguments": call.arguments}
                for call in normalized.tool_calls
            ]
            _log_completed_call(provider=mc.provider_kind, model=mc.model, purpose=call_category, started_at=started_at)
            return "__TOOL_CALL__:" + json.dumps(tool_calls, ensure_ascii=False)
        _log_completed_call(provider=mc.provider_kind, model=mc.model, purpose=call_category, started_at=started_at)
        content = thinking.strip_think_tags(normalized.assistant_text) or ""
        _log_empty_completion(mc, normalized, content)
        return content

    except Exception as e:
        _record_api_call(
            provider=mc.provider_kind,
            model=model,
            purpose=call_category,
            started_at=started_at,
            ok=False,
            output_hint=type(e).__name__,
        )
        log_error(f"llm_client.chat[{call_category}]", e)
        raise


def _prepare_call(
    messages: list[dict],
    call_category: str,
    max_tokens_override: int | None,
    char_id: str | None = None,
    is_proactive: bool = False,
) -> tuple[ModelClient, list[dict], dict[str, Any]]:
    """路由/参数合并/超时/prompt_style/sanitize 前处理，供 chat_turn() 复用。

    与 chat() 内联的同一套前处理逻辑保持一致；chat() 本身不改动。

    monologue 注入不在此处做：chat_turn() 被 tool loop 逐步调用，_prepare_call
    每步都会拿到一份新的临时消息列表（不会把注入结果写回调用方持有的 loop_msgs），
    在这里注入会导致"每步都独白一次"。monologue 由调用方（pipeline.run_agentic_loop）
    在进入循环前对 messages 做一次性注入，之后原样带过每一步。
    """
    mc: ModelClient = get_model_client(call_category, char_id=char_id)
    prepared = apply_prompt_style(messages, mc.prompt_style)
    prepared = sanitize_messages(prepared)

    gen_kwargs: dict[str, Any] = dict(mc.params)
    if max_tokens_override is not None:
        gen_kwargs["max_tokens"] = max_tokens_override
    gen_kwargs["timeout"] = _CALL_TIMEOUTS.get(call_category, _DEFAULT_CALL_TIMEOUT)
    gen_kwargs.update(
        thinking.build_reasoning_kwargs(mc, call_category=call_category, is_proactive=is_proactive)
    )
    return mc, prepared, gen_kwargs


@dataclass
class ChatTurn:
    """一次 function_calling 主生成的结构化结果，供多步 tool loop 使用。"""

    content: str                # 文本回复（""表示纯工具轮）
    tool_calls: list[dict]      # [{id, name, arguments}]，空表示自然终止
    assistant_message: dict     # Chat-compatible continuation for legacy callers
    continuation_items: list[dict] | None = None  # protocol-neutral loop context


# 全角竖线"｜"——DeepSeek 等模型的工具调用内部 special token（如
# <｜tool▁calls▁begin｜>）用这个字符包裹分隔符，正常对话文本几乎不会用到。
_LEAKED_TOOL_TOKEN_CHAR = "｜"
_LEAKED_TOOL_TOKEN_MIN_COUNT = 2


def _looks_like_leaked_tool_call_markup(text: str) -> bool:
    """探测 content 里是否混进了模型自己的工具调用内部 special token，没被网关
    正确解析进结构化 tool_calls 字段、原样漏进了普通文本（Brief 122 排查
    cedar_toy 时发现的新故障：`finish_reason` 没标 tool_calls，`content` 里却是一段
    以 `<｜...｜>` 形式包裹的内部 token）。

    刻意不针对某一个具体模型/版本的具体 token 名字做匹配——不同模型内部命名
    不同（这次是 deepseek-v4-pro，以后可能是别的、名字也可能变），只认这类
    token 的共同特征：用全角竖线"｜"包裹分隔符。这个字符在正常中文/英文对话
    文本里几乎不会出现，出现两次以上就宁可判过、丢弃这段 content，也不放过
    一次泄漏——展示给用户比误判更糟。
    """
    return bool(text) and text.count(_LEAKED_TOOL_TOKEN_CHAR) >= _LEAKED_TOOL_TOKEN_MIN_COUNT


# chat_stream() 逐 chunk 到达，判不出"这一段已经安全"就不能立刻 yield；留这么多个
# 字符在尾部缓冲区里滚动扫描，覆盖典型 special token 全长（真实样本 <｜tool▁calls▁
# begin｜> 一类在 20～30 字符量级），避免 token 跨多个 chunk 到达时漏判。
_LEAK_SCAN_WINDOW = 64


async def chat_turn(
    messages: list[dict],
    tools: list[dict],
    *,
    call_category: str = "chat",
    max_tokens_override: int | None = None,
    char_id: str | None = None,
    is_proactive: bool = False,
) -> ChatTurn:
    """function_calling 模式下的单步调用，保留 tool_call id，供多步 tool loop 回填。

    仅支持 function_calling 模式；preset 不是该模式时抛 ValueError（调用方保证不会发生）。
    探针等既有 chat(tools=) 调用方继续用哨兵串，不迁移到这个 API。
    char_id: 显式指定"替谁说话"时传（Brief 30）；None（默认）按活跃角色解析。
    is_proactive: 本次是否 scheduler 主动消息（Brief 32）。
    """
    mc, prepared, gen_kwargs = _prepare_call(
        messages, call_category, max_tokens_override, char_id=char_id, is_proactive=is_proactive,
    )
    if mc.tool_call_mode != "function_calling":
        raise ValueError(
            f"[llm_client.chat_turn] preset '{mc.name}' tool_call_mode="
            f"{mc.tool_call_mode!r}，chat_turn 仅支持 function_calling"
        )

    started_at = time.perf_counter()
    try:
        _record_debug_request(
            provider=mc.provider_kind,
            model=mc.model,
            purpose=call_category,
            messages=prepared,
            tools=tools,
            request_kwargs={"api_protocol": getattr(mc, "api_protocol", "chat_completions"), "tool_choice": "auto", **gen_kwargs},
        )
        normalized = await create_protocol_response(
            mc, prepared, tools=tools, tool_choice="auto", gen_kwargs=gen_kwargs,
        )
    except Exception as e:
        _record_api_call(
            provider=mc.provider_kind,
            model=mc.model,
            purpose=call_category,
            started_at=started_at,
            ok=False,
            output_hint=type(e).__name__,
        )
        log_error(f"llm_client.chat_turn[{call_category}]", e)
        raise

    _log_completed_call(
        provider=mc.provider_kind,
        model=mc.model,
        purpose=call_category,
        started_at=started_at,
    )

    continuation_items = list(normalized.continuation_items)
    # 铁律防线：思考内容不得经 assistant_message 混入 loop_msgs / 历史。
    # reasoning_content 字段（部分网关的原生 reasoning 扩展）整个丢弃；
    # content 里内联的 <think>/<thinking> 标签剥除。
    for item in continuation_items:
        item.pop("reasoning_content", None)
        item.pop("reasoning", None)
        if isinstance(item.get("content"), str) and item["content"]:
            item["content"] = thinking.strip_think_tags(item["content"])
        elif isinstance(item.get("content"), list):
            item["content"] = [
                {**block, "text": thinking.strip_think_tags(block["text"])}
                if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
                and isinstance(block.get("text"), str) else block
                for block in item["content"]
            ]

    tool_calls = [
        {"id": call.id, "name": call.name, "arguments": call.arguments}
        for call in normalized.tool_calls
    ]

    content = thinking.strip_think_tags(normalized.assistant_text) or ""
    _log_empty_completion(mc, normalized, content)
    if not tool_calls and _looks_like_leaked_tool_call_markup(content):
        # 网关这一步没能把模型自己的工具调用内部 token 解析成结构化 tool_calls，
        # 原始 token 直接漏进了 content——不能把这个当成真的自然语言回复展示给
        # 用户。丢弃并按空内容处理，复用 run_agentic_loop 里已有的"空回复→不带
        # tools 强制重新生成"兜底（该分支已覆盖"网关偶发返回既无 content 也无
        # tool_calls"的情况，这次泄漏是同一类问题的另一种表现，不需要在
        # pipeline.py 里另开分支）。
        logger.warning(
            "[llm_client.chat_turn] content 疑似混进模型工具调用内部 token（网关"
            "未解析成 tool_calls），丢弃并按空内容处理: %r",
            content[:200],
        )
        content = ""
        for item in continuation_items:
            item.pop("content", None)

    return ChatTurn(
        content=content,
        tool_calls=tool_calls,
        assistant_message=(
            continuation_items[0]
            if len(continuation_items) == 1
            else {"role": "assistant"}
        ),
        continuation_items=continuation_items,
    )


async def chat_stream(
    messages: list[dict],
    max_tokens_override: int | None = None,
    call_category: str = "chat",
    *,
    char_id: str | None = None,
    is_proactive: bool = False,
):
    """流式生成，逐 token yield 文本增量（async generator）。

    仅用于无工具的主生成（主生成步骤本身无 tools 参数）。
    失败时抛异常，调用方（run_llm_stream）负责降级。
    char_id: 显式指定"替谁说话"时传（Brief 30）；None（默认）按活跃角色解析。
    is_proactive: 本次是否 scheduler 主动消息（Brief 32）。

    native reasoning 防线：
      - 协议边界默认独立保存 reasoning；此处只消费正文。
      - 内联标签按跨 chunk 状态机剥离，未闭合内容也只留独立 archive，不展示。

    Brief 122 补：chat_turn()（工具决策步）已经会挡掉模型自己泄漏的工具调用内部
    special token（如 <｜tool▁calls▁begin｜>），但那次修复漏了这条纯文本的流式
    出口——同一类泄漏一样可能出现在无 tools 的最终生成里，且是逐 chunk 到达，
    不是一次性拿到完整 content，不能直接复用 chat_turn() 那个"整段判完再决定"
    的判断方式。这里维护一个尾部滚动缓冲区（_LEAK_SCAN_WINDOW 个字符），只有
    确认不含泄漏特征的部分才真正 yield 给调用方（进而推给前端）；一旦在缓冲区里
    扫到泄漏特征，判定这条流已经脏了，本次剩余内容全部丢弃、不再展示。
    """
    _timeout = _CALL_TIMEOUTS.get(call_category, _DEFAULT_CALL_TIMEOUT)

    mc: ModelClient = get_model_client(call_category, char_id=char_id)

    messages = await thinking.maybe_apply(
        messages, call_category=call_category, char_id=char_id, is_proactive=is_proactive, mc=mc,
    )

    messages = apply_prompt_style(messages, mc.prompt_style)
    messages = sanitize_messages(messages)

    _gen_kwargs: dict[str, Any] = dict(mc.params)
    if max_tokens_override is not None:
        _gen_kwargs["max_tokens"] = max_tokens_override
    _gen_kwargs["timeout"] = _timeout
    _gen_kwargs.update(
        thinking.build_reasoning_kwargs(mc, call_category=call_category, is_proactive=is_proactive)
    )

    _record_debug_request(
        provider=mc.provider_kind,
        model=mc.model,
        purpose=call_category,
        messages=messages,
        tools=None,
        request_kwargs={"api_protocol": getattr(mc, "api_protocol", "chat_completions"), "stream": True, **_gen_kwargs},
    )

    think_filter = thinking.ThinkTextFilter()

    leak_buf = ""
    leaked = False

    def _leak_scan(text: str) -> str | None:
        """滚动扫描泄漏特征，返回这一步真正可以安全 yield 的部分（可能是 None）。

        命中泄漏时只丢弃从第一个可疑字符开始的部分——之前已经攒在缓冲区里的
        干净文本（比如泄漏 token 前面正常的角色台词）仍然放行，不能因为后面
        出现了泄漏就连前面已经确认干净的内容一起吞掉。
        """
        nonlocal leak_buf, leaked
        if leaked:
            return None
        leak_buf += text
        if _looks_like_leaked_tool_call_markup(leak_buf):
            cut = leak_buf.find(_LEAKED_TOOL_TOKEN_CHAR)
            safe_prefix = leak_buf[:cut] if cut > 0 else ""
            logger.warning(
                "[llm_client.chat_stream] 疑似模型工具调用内部 token 泄漏进流式"
                "输出，丢弃本次剩余内容: %r",
                leak_buf[:200],
            )
            leaked = True
            leak_buf = ""
            return safe_prefix or None
        if len(leak_buf) > _LEAK_SCAN_WINDOW:
            cut = len(leak_buf) - _LEAK_SCAN_WINDOW
            safe, leak_buf = leak_buf[:cut], leak_buf[cut:]
            return safe or None
        return None

    def _leak_flush() -> str | None:
        """流正常结束时把尾部缓冲区里剩下的（已确认安全的）内容吐出。"""
        nonlocal leak_buf
        if leaked:
            return None
        out, leak_buf = leak_buf, ""
        return out or None

    from contextlib import aclosing
    async with aclosing(stream_text(mc, messages, gen_kwargs=_gen_kwargs)) as source:
        async for piece in source:
            safe = _leak_scan(think_filter.feed(piece))
            if safe:
                yield safe

    safe = _leak_scan(think_filter.finish())
    if safe:
        yield safe

    tail = _leak_flush()
    if tail:
        yield tail


@dataclass(frozen=True)
class ProbeParseResult:
    """Strict, protocol-neutral result of decoding an isolated probe response."""

    status: str
    tool_calls: list[dict]
    encoding: str | None


def parse_probe_response(
    response: object,
    *,
    allowed_tool_names: set[str] | frozenset[str] | None = None,
) -> ProbeParseResult:
    """Decode a function-calling sentinel or XML probe encoding fail-closed.

    Probe output is control data, never chat content.  A malformed payload,
    unknown tool, or non-object arguments invalidates the entire response so a
    partially decoded call can never execute.
    """
    if not isinstance(response, str) or not response.strip():
        return ProbeParseResult("no_tool_selected", [], None)

    raw_calls: object
    encoding: str
    if response.startswith("__TOOL_CALL__:"):
        encoding = "function_calling"
        try:
            raw_calls = json.loads(response[len("__TOOL_CALL__:"):])
        except json.JSONDecodeError:
            return ProbeParseResult("probe_parse_failed", [], encoding)
    elif "<tool_call" in response or "</tool_call>" in response:
        encoding = "xml"
        matches = re.findall(r"<tool_call>(.*?)</tool_call>", response, re.DOTALL)
        if not matches:
            return ProbeParseResult("probe_parse_failed", [], encoding)
        decoded: list[object] = []
        for payload in matches:
            try:
                decoded.append(json.loads(payload.strip()))
            except json.JSONDecodeError:
                return ProbeParseResult("probe_parse_failed", [], encoding)
        raw_calls = decoded
    else:
        # A probe is required to emit either an empty response or a supported
        # control encoding.  Prose must not fall through into the chat path.
        return ProbeParseResult("probe_parse_failed", [], None)

    if not isinstance(raw_calls, list) or not raw_calls:
        return ProbeParseResult("probe_parse_failed", [], encoding)

    calls: list[dict] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            return ProbeParseResult("probe_parse_failed", [], encoding)
        name = item.get("name")
        arguments = item.get("arguments", {})
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            return ProbeParseResult("arguments_invalid", [], encoding)
        if allowed_tool_names is not None and name not in allowed_tool_names:
            return ProbeParseResult("tool_unknown", [], encoding)
        calls.append({"name": name, "arguments": arguments})

    return ProbeParseResult("tool_selected", calls, encoding)


def parse_tool_call_response(response: str) -> list[dict] | None:
    """Compatibility wrapper for callers that do not supply an exposure set."""
    parsed = parse_probe_response(response)
    return parsed.tool_calls or None


def _build_xml_tool_desc(tools: list[dict]) -> str:
    """为 xml_fallback 模式构建工具说明，注入到 system 消息"""
    lines = [
        "你可以使用以下工具。需要调用工具时，用如下格式输出（只输出 JSON，不要多余文字）：",
        "<tool_call>",
        '{"name": "工具名", "arguments": {"参数名": "参数值"}}',
        "</tool_call>",
        "",
        "可用工具：",
    ]
    for tool in tools:
        func = tool.get("function", tool)
        name = func.get("name", "")
        desc = func.get("description", "")
        params = func.get("parameters", {}).get("properties", {})
        param_str = ", ".join(
            f'{k}({v.get("type","any")})' for k, v in params.items()
        )
        lines.append(f"- {name}({param_str}): {desc}")
    return "\n".join(lines)


_VALID_EMOTIONS = frozenset({"neutral", "happy", "sad", "gentle", "surprised", "angry", "thinking", "sleepy"})

_SUMMARIZE_SYSTEM = (
    "把下面这轮对话压缩成 8-15 字的客观陈述句，主语用「用户」，只描述发生了什么，"
    "不要情感修饰，不要加引号。直接输出陈述句，不要任何前缀。"
)

# Brief 97 §3：trigger 轮的 user_msg 是 scheduler/sensor 的种子旁白，不是真实用户发言——
# 沿用 _SUMMARIZE_SYSTEM 会把旁白当"用户做了什么"概括进 mid_term，冷启动首轮典型产出
# "她收到日记分析提醒并回复了近况"这类凭空记忆。旁白只是触发角色开口的由头。
_SUMMARIZE_SYSTEM_TRIGGER = (
    "把下面这轮对话压缩成 8-15 字的客观陈述句。「场景旁白」是系统写的开场设定，"
    "不是用户说的话，也不代表真实发生过的用户行为，只是触发角色开口的由头——"
    "不要把旁白内容当成已发生的事实写进陈述句。只客观描述角色在回复里实际说了/"
    "表达了什么，主语用角色。不要情感修饰，不要加引号。直接输出陈述句，不要任何前缀。"
)


def _truncate(s: str, n: int) -> str:
    """切到 n 字以内，截断时补省略号；空串返空串。"""
    s = (s or "").strip()
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + "…"


def _rule_fallback(
    user_msg: str, reply: str = "", tags: list[str] | None = None, *, is_trigger_turn: bool = False
) -> str:
    """
    LLM 不可用 / 太琐碎不值得调 LLM 时的兜底摘要。
    必须同时利用 user_msg 和 reply，否则写进 mid_term 的全是用户原话，等于没记忆。

    is_trigger_turn=True 时 user_msg 是触发器种子旁白，不是用户说的话——不能标成"用户：..."，
    否则和 LLM 压缩路径一样会把旁白当成真实用户行为写进 mid_term（Brief 97）。
    """
    user_head = _truncate(user_msg, 18)
    reply_head = _truncate(reply, 18)

    from core.config_loader import _char_name
    char_name = _char_name()
    if is_trigger_turn:
        base = f"{char_name}主动开口：{reply_head}" if reply_head else f"{char_name}主动开口"
    elif user_head and reply_head:
        base = f"用户：{user_head}；{char_name}：{reply_head}"
    elif user_head:
        base = f"用户：{user_head}"
    elif reply_head:
        base = f"{char_name}：{reply_head}"
    else:
        base = "一轮简短对话"

    if tags:
        return f"{base} [{','.join(tags[:2])}]"
    return base


# 用户和回复合起来低于这个长度才走 fallback；高于则进 LLM 压缩。
_SUMMARIZE_MIN_TOTAL_LEN = 8


async def summarize_turn(
    user_msg: str, reply: str, tags: list[str] | None = None, *, is_trigger_turn: bool = False
) -> str:
    """把一轮对话压缩成 8-15 字客观陈述。失败/过短走规则 fallback。

    is_trigger_turn=True：user_msg 是 scheduler/sensor 触发轮的种子旁白，不是真实用户
    发言，用专门的系统 prompt + 消息框定，避免旁白被当成"已发生的事"概括（Brief 97）。
    """
    user_msg = (user_msg or "").strip()
    reply = (reply or "").strip()

    if len(user_msg) + len(reply) < _SUMMARIZE_MIN_TOTAL_LEN:
        return _rule_fallback(user_msg, reply, tags, is_trigger_turn=is_trigger_turn)
    try:
        is_group_projection = "group_chat" in (tags or [])
        system_prompt = _SUMMARIZE_SYSTEM_TRIGGER if is_trigger_turn else _SUMMARIZE_SYSTEM
        if is_group_projection:
            system_prompt += (
                "\n这是群聊投影：必须用第三人称并保留名字归属，例如“甲说了…，乙回应…”。"
                "不得把不同说话人的内容合并成无主语陈述。"
            )
        mc = get_model_client("summary")
        user_content = (
            f"场景旁白（非用户发言，不代表已发生的事）:{user_msg}\n角色回复:{reply}"
            if is_trigger_turn
            else f"用户:{user_msg}\n回复:{reply}"
        )
        response = await create_protocol_response(
            mc,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            tools=None,
            tool_choice=None,
            gen_kwargs={
                "max_tokens": 80 if is_group_projection else 40,
                "temperature": 0.3,
                "timeout": _CALL_TIMEOUTS["summary"],
            },
        )
        result = response.assistant_text.strip()
        result = result.strip('"\'"""''')
        result = result[:60 if is_group_projection else 30]
        if not result:
            return _rule_fallback(user_msg, reply, tags, is_trigger_turn=is_trigger_turn)
        return result
    except Exception as e:
        logger.warning(f"[llm_client.summarize_turn] 压缩失败，走 fallback: {e}")
        return _rule_fallback(user_msg, reply, tags, is_trigger_turn=is_trigger_turn)


async def detect_emotion(text: str) -> str:
    """
    轻量 LLM 调用，判断回复文本的情绪。
    只消耗约 10 个 token，异步非阻塞。
    返回值：neutral / happy / sad / gentle / surprised / angry / thinking / sleepy
    失败时返回 "neutral"。
    """
    prompt = (
        "判断以下文本的情绪，只返回一个词：\n"
        "neutral/happy/sad/gentle/surprised/angry/thinking/sleepy\n"
        f"文本：{text}"
    )
    try:
        mc = get_model_client("detect_emotion")
        response = await create_protocol_response(
            mc,
            [{"role": "user", "content": prompt}],
            tools=None,
            tool_choice=None,
            gen_kwargs={
                "max_tokens": 10,
                "temperature": 0.0,
                "timeout": _CALL_TIMEOUTS["detect_emotion"],
            },
        )
        result = response.assistant_text.strip().lower()
        if result in _VALID_EMOTIONS:
            return result
        # 小模型有时不严格按"只返回一个词"的指令走（夹带标点/多余文字/中文），
        # 严格 == 匹配会把这些一律静默判成 neutral 且不留任何日志，下游 sticker/TTS
        # 因此永远拿不到非 neutral 的 emotion 却查不到原因。这里先做一次宽松匹配
        # （原样输出中包含某个合法标签即可），仍失败才降级 neutral，但两种情况都留痕。
        for label in _VALID_EMOTIONS:
            if label in result:
                logger.debug(
                    "[detect_emotion] 严格匹配未命中，宽松匹配到 %r（原始输出=%r）",
                    label, result,
                )
                return label
        from core.runtime_signal_observability import record_counts

        reason = "empty" if not result else "unrecognized"
        _, _, preset_count = record_counts(
            category="model_quality",
            code="emotion_output_invalid",
            status="attention",
            context={"purpose": "detect_emotion", "reason": reason, "model": mc.name},
        )
        if preset_count == 1 or preset_count % 20 == 0:
            logger.warning("[detect_emotion] 输出无法解析为合法情绪，降级 neutral: reason=%s", reason)
        else:
            logger.debug("[detect_emotion] repeated invalid output, downgraded neutral: reason=%s", reason)
        return "neutral"
    except Exception as e:
        from core.runtime_signal_observability import record_counts

        preset = getattr(locals().get("mc"), "name", "unresolved")
        _, _, preset_count = record_counts(
            category="model_quality",
            code="emotion_output_invalid",
            status="attention",
            context={"purpose": "detect_emotion", "reason": "request_error", "model": preset},
        )
        if preset_count == 1 or preset_count % 20 == 0:
            logger.warning(
                "[detect_emotion] request failed; downgraded neutral: preset=%s error_type=%s count=%s",
                preset, type(e).__name__, preset_count,
            )
        return "neutral"


async def detect_affection(text: str) -> bool:
    """判断这条回复是否在【表达爱意/喜欢/亲昵】（表白、撒娇、比心、想念、深情）。
    轻量调用，失败返回 False。"""
    prompt = (
        "下面是角色对用户说的话。判断她是否在直接向用户表达"
        "爱意/喜欢/亲昵（如表白、撒娇、比心、想你、深情告白）。"
        "只回一个词：yes 或 no。\n"
        f"文本：{text}"
    )
    try:
        mc = get_model_client("detect_emotion")   # 复用轻量档，无需新模型
        resp = await create_protocol_response(
            mc,
            [{"role": "user", "content": prompt}],
            tools=None,
            tool_choice=None,
            gen_kwargs={
                "max_tokens": 3,
                "temperature": 0.0,
                "timeout": _CALL_TIMEOUTS["detect_emotion"],
            },
        )
        return resp.assistant_text.strip().lower().startswith("y")
    except Exception as e:
        log_error("llm_client.detect_affection", e)
        return False


class LLMClient:
    """LLM 客户端类，封装模块级函数，供外部按类方式导入使用"""

    async def chat(
        self,
        messages: list,
        tools: list | None = None,
        max_tokens_override: int | None = None,
        call_category: str = "chat",
    ) -> str:
        return await chat(messages, tools, max_tokens_override=max_tokens_override, call_category=call_category)

    async def chat_vision(self, messages: list) -> str:
        return await chat(messages, use_vision=True, call_category="vision")

    async def detect_emotion(self, text: str) -> str:
        return await detect_emotion(text)

    async def detect_affection(self, text: str) -> bool:
        return await detect_affection(text)

    def parse_tool_call_response(self, response: str) -> list | None:
        return parse_tool_call_response(response)
