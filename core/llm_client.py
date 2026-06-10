"""
LLM 客户端模块
所有 LLM 调用的唯一出口，支持 DeepSeek / OpenAI / 本地模型
"""

import json
import logging
import re
from typing import Any

import httpx
from openai import AsyncOpenAI

from core.config_loader import get_config
from core.error_handler import log_error
from core.prompt_layer import sanitize_messages

logger = logging.getLogger(__name__)

# 全局客户端实例（延迟初始化）
_client: AsyncOpenAI | None = None
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
}
_DEFAULT_CALL_TIMEOUT: float = 90.0

def _get_proxy_url() -> str | None:
    """读取代理配置，未启用时返回 None"""
    proxy_cfg = get_config().get("proxy", {})
    if proxy_cfg.get("enabled", False):
        return proxy_cfg.get("http") or None
    return None


def _make_http_client(proxy_url: str | None) -> httpx.AsyncClient:
    """Build httpx client with base connect timeout; per-call read timeout
    is passed via each completions.create() timeout= kwarg.
    """
    base_timeout = httpx.Timeout(timeout=_DEFAULT_CALL_TIMEOUT, connect=10.0)
    if proxy_url:
        return httpx.AsyncClient(proxy=proxy_url, timeout=base_timeout)
    return httpx.AsyncClient(trust_env=False, timeout=base_timeout)


def _get_client() -> AsyncOpenAI:
    """获取 OpenAI 客户端（单例，含代理配置）"""
    global _client
    if _client is None:
        cfg = get_config()["llm"]
        proxy_url = _get_proxy_url()
        http_client = _make_http_client(proxy_url)
        _client = AsyncOpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            http_client=http_client,
        )
        logger.info(
            f"[llm_client] 客户端已初始化，代理={'已启用 ' + proxy_url if proxy_url else '未启用'}"
        )
    return _client


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
    重置 OpenAI 客户端（代理/API Key 配置变更后调用）
    下次调用 _get_client() 时会重新按最新配置创建
    """
    global _client, _vision_client
    _client = None
    _vision_client = None
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

    返回:
        模型生成的文本字符串
        function_calling 模式下如果模型调用了工具，返回序列化后的工具调用 JSON
    """
    _timeout = _CALL_TIMEOUTS.get(call_category, _DEFAULT_CALL_TIMEOUT)

    # Strip internal fields (e.g. _layer, _debug) before any message reaches the API.
    # Never mutates the caller's list.
    messages = sanitize_messages(messages)

    # vision模式用视觉客户端和模型
    if use_vision:
        vision_client = _get_vision_client()
        if vision_client:
            vision_cfg = get_config().get("vision", {})
            try:
                response = await vision_client.chat.completions.create(
                    model=vision_cfg["model"],
                    messages=messages,
                    max_tokens=1000,
                    timeout=_CALL_TIMEOUTS["vision"],
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                log_error("llm_client.chat.vision", e)
                return ""

    cfg = get_config()["llm"]
    client = _get_client()
    model = cfg["model"]
    mode = cfg.get("tool_call_mode", "function_calling")

    # 读取生成参数（每次 chat 调用都重新读，支持热重载）
    temperature       = float(cfg.get("temperature",       0.7))
    top_p             = float(cfg.get("top_p",             0.9))
    max_tokens        = max_tokens_override or int(cfg.get("max_tokens", 1000))
    frequency_penalty = float(cfg.get("frequency_penalty", 0.0))

    # 公共关键字参数，注入到每种调用模式
    _gen_kwargs = dict(
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        frequency_penalty=frequency_penalty,
        timeout=_timeout,
    )

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
            # 模型选择调用工具时，返回工具调用信息的 JSON 字符串
            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                tool_calls = []
                for tc in choice.message.tool_calls:
                    tool_calls.append({
                        "name": tc.function.name,
                        "arguments": json.loads(tc.function.arguments),
                    })
                # 用特殊前缀标记，让 tool_dispatcher 识别
                return "__TOOL_CALL__:" + json.dumps(tool_calls, ensure_ascii=False)
            return choice.message.content or ""

        # ── xml_fallback 模式（不支持 FC 的模型）────────────────────────────
        elif mode == "xml_fallback" and tools:
            # 把工具描述注入到 system 消息末尾
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


def parse_tool_call_response(response: str) -> list[dict] | None:
    """
    解析 LLM 返回值中的工具调用信息

    function_calling 模式：检测 __TOOL_CALL__: 前缀
    xml_fallback 模式：检测 <tool_call> 标签

    返回工具调用列表，无工具调用则返回 None
    """
    # function_calling 模式
    if response.startswith("__TOOL_CALL__:"):
        try:
            return json.loads(response[len("__TOOL_CALL__:"):])
        except json.JSONDecodeError:
            return None

    # xml_fallback 模式
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
# 取小值是因为中文 8 字 ≈ 一句话信息量；再低 LLM 也产不出好摘要，没必要花 token。
_SUMMARIZE_MIN_TOTAL_LEN = 8


async def summarize_turn(user_msg: str, reply: str, tags: list[str] | None = None) -> str:
    """把一轮对话压缩成 8-15 字客观陈述。失败/过短走规则 fallback。"""
    user_msg = (user_msg or "").strip()
    reply = (reply or "").strip()

    # 用户和回复合起来都很短，才不调 LLM。
    # 旧版只看 user_msg < 10，导致用户输入"（锤他胸口）"等短动作时
    # 即便 reply 很长也跳过 LLM，写入的 summary 就是用户原话，无效记忆。
    if len(user_msg) + len(reply) < _SUMMARIZE_MIN_TOTAL_LEN:
        return _rule_fallback(user_msg, reply, tags)
    try:
        cfg = get_config()["llm"]
        client = _get_client()
        response = await client.chat.completions.create(
            model=cfg["model"],
            messages=[
                {"role": "system", "content": _SUMMARIZE_SYSTEM},
                {"role": "user", "content": f"用户:{user_msg}\n回复:{reply}"},
            ],
            max_tokens=40,
            temperature=0.3,
            timeout=_CALL_TIMEOUTS["summary"],
        )
        result = (response.choices[0].message.content or "").strip()
        result = result.strip('"\'"“”‘’')
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
        cfg = get_config()["llm"]
        client = _get_client()
        response = await client.chat.completions.create(
            model=cfg["model"],
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

    def parse_tool_call_response(self, response: str) -> list | None:
        return parse_tool_call_response(response)
