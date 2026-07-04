"""
LLM 客户端模块
所有 LLM 调用的唯一出口，支持多模型 preset 路由（DeepSeek / Claude / 本地）。
Preset 路由、参数合并、provider 白名单由 core.model_registry 管理。
Prompt-style 转换（narrative / xml）由 core.prompt_style 管理。
"""

import json
import logging
import re
from typing import Any

import httpx
from openai import AsyncOpenAI

from core.config_loader import get_config
from core.error_handler import log_error
from core.model_registry import ModelClient, get_model_client, reload_registry
from core.prompt_layer import sanitize_messages
from core.prompt_style import apply_prompt_style

logger = logging.getLogger(__name__)

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


def reload_client():
    """
    重置所有 LLM 客户端（代理/API Key 配置变更后调用）。
    下次调用时将按最新 config 重建。
    """
    global _vision_client
    _vision_client = None
    reload_registry()
    logger.info("[llm_client] 客户端已重置，下次请求时按最新配置重建")


async def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens_override: int | None = None,
    use_vision: bool = False,
    call_category: str = "chat",
) -> str:
    """
    调用 LLM 生成回复

    参数:
        messages: OpenAI 格式的消息列表 [{role, content}, ...]
        tools:    工具定义列表（function_calling 模式时使用）
        use_vision: 使用视觉模型处理图片
        call_category: 路由到对应 preset 的类别名

    返回:
        模型生成的文本字符串
        function_calling 模式下如果模型调用了工具，返回序列化后的工具调用 JSON
    """
    _timeout = _CALL_TIMEOUTS.get(call_category, _DEFAULT_CALL_TIMEOUT)

    # vision 模式走独立 vision client，不经过 preset 路由
    if use_vision:
        vision_client = _get_vision_client()
        if vision_client:
            vision_cfg = get_config().get("vision", {})
            # Vision branch: sanitize only (no prompt_style transform needed)
            safe_msgs = sanitize_messages(messages)
            try:
                response = await vision_client.chat.completions.create(
                    model=vision_cfg["model"],
                    messages=safe_msgs,
                    max_tokens=1000,
                    timeout=_CALL_TIMEOUTS["vision"],
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                log_error("llm_client.chat.vision", e)
                return ""

    mc: ModelClient = get_model_client(call_category)

    # Phase 2: apply prompt style BEFORE sanitize so _layer is still available
    messages = apply_prompt_style(messages, mc.prompt_style)
    messages = sanitize_messages(messages)

    model = mc.model
    client = mc.client
    mode = mc.tool_call_mode

    # Build generation kwargs from preset params; max_tokens_override wins
    _gen_kwargs: dict[str, Any] = dict(mc.params)
    if max_tokens_override is not None:
        _gen_kwargs["max_tokens"] = max_tokens_override
    _gen_kwargs["timeout"] = _timeout

    try:
        # ── function_calling 模式 ──────────────────────────────────────────
        if mode == "function_calling" and tools:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                **_gen_kwargs,
            )
            choice = response.choices[0]
            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                tool_calls = []
                for tc in choice.message.tool_calls:
                    tool_calls.append({
                        "name": tc.function.name,
                        "arguments": json.loads(tc.function.arguments),
                    })
                return "__TOOL_CALL__:" + json.dumps(tool_calls, ensure_ascii=False)
            return choice.message.content or ""

        # ── xml_fallback 模式（不支持 FC 的模型）────────────────────────────
        elif mode == "xml_fallback" and tools:
            tool_desc = _build_xml_tool_desc(tools)
            msgs = list(messages)
            injected = False
            for i, m in enumerate(msgs):
                if m["role"] == "system":
                    msgs[i] = {
                        "role": "system",
                        "content": m["content"] + "\n\n" + tool_desc,
                    }
                    injected = True
                    break
            if not injected:
                msgs.insert(0, {"role": "system", "content": tool_desc})

            response = await client.chat.completions.create(
                model=model,
                messages=msgs,
                **_gen_kwargs,
            )
            return response.choices[0].message.content or ""

        # ── 普通对话（无工具）────────────────────────────────────────────────
        else:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                **_gen_kwargs,
            )
            return response.choices[0].message.content or ""

    except Exception as e:
        log_error(f"llm_client.chat[{call_category}]", e)
        raise


async def chat_stream(
    messages: list[dict],
    max_tokens_override: int | None = None,
    call_category: str = "chat",
):
    """流式生成，逐 token yield 文本增量（async generator）。

    仅用于无工具的主生成（主生成步骤本身无 tools 参数）。
    失败时抛异常，调用方（run_llm_stream）负责降级。
    """
    _timeout = _CALL_TIMEOUTS.get(call_category, _DEFAULT_CALL_TIMEOUT)

    mc: ModelClient = get_model_client(call_category)

    messages = apply_prompt_style(messages, mc.prompt_style)
    messages = sanitize_messages(messages)

    _gen_kwargs: dict[str, Any] = dict(mc.params)
    if max_tokens_override is not None:
        _gen_kwargs["max_tokens"] = max_tokens_override
    _gen_kwargs["timeout"] = _timeout

    stream = await mc.client.chat.completions.create(
        model=mc.model,
        messages=messages,
        stream=True,
        **_gen_kwargs,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        piece = getattr(delta, "content", None)
        if piece:
            yield piece


def parse_tool_call_response(response: str) -> list[dict] | None:
    """
    解析 LLM 返回值中的工具调用信息

    function_calling 模式：检测 __TOOL_CALL__: 前缀
    xml_fallback 模式：检测 <tool_call> 标签

    返回工具调用列表，无工具调用则返回 None
    """
    if response.startswith("__TOOL_CALL__:"):
        try:
            return json.loads(response[len("__TOOL_CALL__:"):])
        except json.JSONDecodeError:
            return None

    pattern = r"<tool_call>(.*?)</tool_call>"
    matches = re.findall(pattern, response, re.DOTALL)
    if matches:
        tool_calls = []
        for m in matches:
            try:
                data = json.loads(m.strip())
                tool_calls.append(data)
            except json.JSONDecodeError:
                pass
        return tool_calls if tool_calls else None

    return None


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


def _truncate(s: str, n: int) -> str:
    """切到 n 字以内，截断时补省略号；空串返空串。"""
    s = (s or "").strip()
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + "…"


def _rule_fallback(user_msg: str, reply: str = "", tags: list[str] | None = None) -> str:
    """
    LLM 不可用 / 太琐碎不值得调 LLM 时的兜底摘要。
    必须同时利用 user_msg 和 reply，否则写进 mid_term 的全是用户原话，等于没记忆。
    """
    user_head = _truncate(user_msg, 18)
    reply_head = _truncate(reply, 18)

    from core.config_loader import _char_name
    char_name = _char_name()
    if user_head and reply_head:
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


async def summarize_turn(user_msg: str, reply: str, tags: list[str] | None = None) -> str:
    """把一轮对话压缩成 8-15 字客观陈述。失败/过短走规则 fallback。"""
    user_msg = (user_msg or "").strip()
    reply = (reply or "").strip()

    if len(user_msg) + len(reply) < _SUMMARIZE_MIN_TOTAL_LEN:
        return _rule_fallback(user_msg, reply, tags)
    try:
        mc = get_model_client("summary")
        response = await mc.client.chat.completions.create(
            model=mc.model,
            messages=[
                {"role": "system", "content": _SUMMARIZE_SYSTEM},
                {"role": "user", "content": f"用户:{user_msg}\n回复:{reply}"},
            ],
            max_tokens=40,
            temperature=0.3,
            timeout=_CALL_TIMEOUTS["summary"],
        )
        result = (response.choices[0].message.content or "").strip()
        result = result.strip('"\'"""''')
        result = result[:30]
        if not result:
            return _rule_fallback(user_msg, reply, tags)
        return result
    except Exception as e:
        logger.warning(f"[llm_client.summarize_turn] 压缩失败，走 fallback: {e}")
        return _rule_fallback(user_msg, reply, tags)


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
        response = await mc.client.chat.completions.create(
            model=mc.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.0,
            timeout=_CALL_TIMEOUTS["detect_emotion"],
        )
        result = (response.choices[0].message.content or "").strip().lower()
        return result if result in _VALID_EMOTIONS else "neutral"
    except Exception as e:
        log_error("llm_client.detect_emotion", e)
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
        resp = await mc.client.chat.completions.create(
            model=mc.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3, temperature=0.0,
            timeout=_CALL_TIMEOUTS["detect_emotion"],
        )
        return (resp.choices[0].message.content or "").strip().lower().startswith("y")
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
