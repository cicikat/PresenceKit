"""Truth boundaries for tool-required turns.

This module is intentionally text-only and fail-open.  It gives prompt and
output paths the same vocabulary for a required tool call, a successful result,
and a failed/unknown result.
"""

from __future__ import annotations

import re


GROUNDING_LAYER = "11_tool_grounding"

# ── 工具 meta 文本泄漏闸门（工单 C）─────────────────────────────────────────
#
# 不是系统把工具执行结果当回复发出——是模型复述了它在 system 消息里看到的
# 工具目录描述（core/tool_discovery.py 的分类折叠 schema，或 xml_fallback
# 注入的工具说明文本）。用具体短语组合识别，不用单个宽泛关键词，避免误杀
# 正常对话里提到"工具""不执行"这类词。见 docs/work-orders/
# stt-tool-leak-and-video-diff.md「零、结论 3」。

_TOOL_META_PHRASES = (
    "只发现工具，不执行任何业务操作",
    "下一轮才能调用具体工具",
    "的工具定义。",
    "该分类已加载。请使用当前提供的具体工具",
)
_DISCOVERY_PREFIX = "load_tools_"
_DENSE_TOOL_NAME_THRESHOLD = 3


def detect_tool_meta_leak(text: str, tool_names: "set[str] | frozenset[str]" = frozenset()) -> bool:
    """True if ``text`` looks like it is reciting tool/discovery metadata to the user.

    Any one of three independent signals is sufficient:
      1. a fixed phrase lifted straight from ``core.tool_discovery`` 的折叠描述
         or load() 回执;
      2. the ``load_tools_`` discovery-tool name prefix appearing in prose;
      3. three or more registered tool names enumerated in the same text —
         a human writing about "the tools" doesn't list call signatures.
    """
    if not text:
        return False
    if any(phrase in text for phrase in _TOOL_META_PHRASES):
        return True
    if _DISCOVERY_PREFIX in text:
        return True
    if tool_names:
        hits = sum(1 for name in tool_names if name and name in text)
        if hits >= _DENSE_TOOL_NAME_THRESHOLD:
            return True
    return False

_COMPLETION_CLAIM_RE = re.compile(
    r"(?:已经|已|刚刚|刚才|现在已经|已经帮你|已帮你)"
    r"(?:查到|查过|搜到|搜过|看过|读到|读过|控制|操作|完成|打开|关闭|发送|发出|"
    r"播放|暂停|浇过|浇了|写入|更新|删除|清空|执行)"
    r"|(?:查到了|搜到了|读到了|控制好了|操作完成了|完成了|打开了|"
    r"关闭了|发送了|发出去了|正在播放)",
)

# 句级排除：疑问、否定、主语是「你」、引用用户内容的句子不算角色自己的完成声明。
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_NEGATION_BEFORE_RE = re.compile(r"(?:没|没有|未|尚未|还没|并没|不|别|无法|不能|没能)\s*$")
_QUESTION_RE = re.compile(r"[吗呢么]\s*$|是不是|有没有|是否|能不能|可不可以|要不要")
_USER_SUBJECT_RE = re.compile(r"(?:你|您|用户)[^，,、]{0,6}$")
_USER_CONTENT_RE = re.compile(r"你(?:发|说|写|给|传|的|刚)|你们")
_QUOTE_SPAN_RE = re.compile(r"[「『“\"][^」』”\"]*[」』”\"]")


def find_unverified_claim(reply: str) -> str | None:
    """Return the first sentence fragment that reads as the speaker's own completion claim."""
    if not reply:
        return None
    for sentence in _SENTENCE_SPLIT_RE.split(reply):
        sentence = sentence.strip()
        if not sentence:
            continue
        # 句末是问号的已被切分符吞掉，这里再按疑问词收尾/疑问短语排除。
        if _QUESTION_RE.search(sentence):
            continue
        if _USER_CONTENT_RE.search(sentence):
            continue
        unquoted = _QUOTE_SPAN_RE.sub("", sentence)
        for m in _COMPLETION_CLAIM_RE.finditer(unquoted):
            before = unquoted[:m.start()]
            if _NEGATION_BEFORE_RE.search(before):
                continue
            # 紧邻主语是「你」：「你完成了」「你已经发送」
            if _USER_SUBJECT_RE.search(before):
                continue
            return sentence[:80]
    return None


def classify_tool_outcome(status: str | None, confirmation_request: str | None = None) -> str:
    """Map a ToolExecutionOutcome.status to ok / failed / pending_confirm / info."""
    if confirmation_request or status == "confirmation_required":
        return "pending_confirm"
    if status == "tool_executed":
        return "ok"
    if status in {"discovery", "info", "missing_parameters"}:
        return "info"
    return "failed"


def grounding_validity_for_status(status: str | None, has_result: bool) -> str:
    """Prompt-layer validity for a pre-tool result; info/pending statuses are not failures."""
    if status is None:
        return "current_turn" if has_result else "none"
    kind = classify_tool_outcome(status)
    if kind == "ok":
        return "current_turn" if has_result else "none"
    if status == "outcome_unknown":
        return "outcome_unknown"
    if kind in {"info", "pending_confirm"}:
        return "none"
    return "execution_failed"


def grounding_message(
    *,
    required: bool,
    tool_names: list[str] | set[str] | tuple[str, ...] = (),
    result_validity: str = "none",
) -> dict | None:
    if not required:
        return None
    names = sorted({str(name) for name in tool_names if str(name)})
    name_text = "、".join(names) if names else "可用工具"
    if result_validity == "current_turn":
        content = (
            "【本轮工具事实】本轮已收到成功的工具结果；只依据边界内结果回答，"
            "不要把历史操作当成本轮结果。"
        )
    elif result_validity in {"execution_failed", "outcome_unknown"}:
        content = (
            f"【本轮工具事实】用户明确要求本轮使用{name_text}。当前调用没有得到可确认的成功结果，"
            "不得声称已经查到、控制、完成或执行；请如实说明未完成或结果不明。"
        )
    else:
        content = (
            f"【本轮工具事实】用户明确要求本轮使用{name_text}。必须先实际调用可用工具并取得成功结果，"
            "再回答事实；只有口头承诺、历史痕迹或模型自述都不算调用。调用失败/未暴露时，"
            "不得声称已经查到、控制、完成或执行。"
        )
    return {
        "role": "system",
        "content": content,
        "_layer": GROUNDING_LAYER,
        "_tool_grounding": {
            "required": True,
            "tool_names": names,
            "result_validity": result_validity,
        },
    }


def required_from_messages(messages: list[dict]) -> dict | None:
    for message in messages:
        if message.get("_layer") == GROUNDING_LAYER:
            data = message.get("_tool_grounding")
            if isinstance(data, dict) and data.get("required"):
                return data
    return None


def has_successful_tool_message(messages: list[dict]) -> bool:
    """Recognize only dispatcher success frames, never arbitrary model text.

    Covers both Path C ``role=tool`` messages and the non-loop ``10_tool_result``
    system layer (whose frame carries the current_turn validity text).
    """
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "")
        if "<<<TOOL_DATA_START>>>" not in content:
            continue
        if role == "tool" and "本轮刚生成" in content:
            return True
        if role == "system" and message.get("_layer") == "10_tool_result" and "本轮刚生成" in content:
            return True
    return False


def check_unverified_claim(
    reply: str,
    messages: list[dict],
    *,
    successful_tool_call: bool | None = None,
) -> dict | None:
    """Detect an unverified completion claim; never rewrites the reply.

    Returns ``{"snippet", "basis"}`` when a required tool call did not succeed
    and the reply still reads as a completion claim; otherwise ``None``.
    """
    if not reply:
        return None
    grounding = required_from_messages(messages)
    if not grounding:
        return None
    validity = str(grounding.get("result_validity") or "none")
    succeeded = successful_tool_call
    if succeeded is None:
        succeeded = validity == "current_turn" or has_successful_tool_message(messages)
    if succeeded:
        return None
    snippet = find_unverified_claim(reply)
    if not snippet:
        return None
    return {"snippet": snippet, "basis": f"required_tool_not_succeeded:{validity}"}


def build_claim_nudge(char_name: str, tool_names: list[str] | None = None) -> str:
    names = "、".join(tool_names or []) or "所需工具"
    return (
        f"【系统提示】你刚才的回复声称事情已经办成，但本轮 {names} 并没有得到可确认的成功结果。"
        f"请以{char_name}自己的口吻重写这条回复：如实、自然地表达还没办成或结果不确定，"
        "不要声称已经查到、完成或执行，也不要提及系统提示或工具机制。"
    )

