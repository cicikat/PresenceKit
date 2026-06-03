"""
回复后处理模块
清理模型输出：去除多余前缀、分段、过滤审查语句
"""

import re
import logging

from core.error_handler import log_error

logger = logging.getLogger(__name__)

# 最大单条消息长度（超过则分段）
MAX_LENGTH = 500

# 模型自我审查语句的关键词（命中则过滤整句）
_SELF_CENSOR_PATTERNS = [
    r"作为一个AI",
    r"作为人工智能",
    r"我是一个语言模型",
    r"我是一个AI",
    r"我无法.*真实感情",
    r"我实际上是.*程序",
    r"我只是.*模型",
    r"我不能.*真的",
]


def process(reply: str, character_name: str) -> list[str]:
    """QQ-only legacy processor: guard filters + segment split.

    Helper functions (_remove_tool_call_tags / _remove_character_prefix /
    _filter_self_censor) are also imported directly by core/reality_output_guard.py
    for non-QQ channels — keep their signatures stable.
    """
    if not reply:
        return []

    try:
        # 步骤1：移除 xml_fallback 模式的 <tool_call> 标记
        reply = _remove_tool_call_tags(reply)

        # 步骤2：去除开头的角色名前缀（如 "小花：xxx" → "xxx"）
        reply = _remove_character_prefix(reply, character_name)

        # 步骤3：过滤模型自我审查语句
        reply = _filter_self_censor(reply)

        # 步骤4：清理多余空白
        reply = reply.strip()

        if not reply:
            return []

        # 步骤5：超过 MAX_LENGTH 自动分段
        return _split_message(reply)

    except Exception as e:
        log_error("response_processor.process", e)
        return [reply] if reply else []


def _remove_tool_call_tags(text: str) -> str:
    """移除 <tool_call>...</tool_call> 标记（xml_fallback 模式残留）"""
    return re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL).strip()


def _remove_character_prefix(text: str, character_name: str) -> str:
    """
    去除回复开头的角色名前缀

    支持格式：
        "小花：xxx"
        "小花:xxx"
        "[小花] xxx"
        "小花 说：xxx"
    """
    if not character_name:
        return text

    # 转义角色名中的特殊正则字符
    escaped_name = re.escape(character_name)

    patterns = [
        rf"^{escaped_name}[：:]\s*",          # "小花：" 或 "小花:"
        rf"^\[{escaped_name}\]\s*",            # "[小花] "
        rf"^{escaped_name}\s+说[：:]\s*",      # "小花 说："
        rf"^{escaped_name}\s*[（(].*?[）)][：:]\s*",  # "小花（某情绪）："
    ]

    for pattern in patterns:
        text = re.sub(pattern, "", text, count=1)

    return text


def _filter_self_censor(text: str) -> str:
    """
    过滤模型自我审查句子

    只过滤包含审查关键词的那一句，保留其余内容
    """
    sentences = re.split(r"([。！？…\n])", text)
    filtered_parts = []

    i = 0
    while i < len(sentences):
        sentence = sentences[i]
        # 对应的句末标点
        punct = sentences[i + 1] if i + 1 < len(sentences) else ""

        should_filter = any(
            re.search(pattern, sentence) for pattern in _SELF_CENSOR_PATTERNS
        )

        if not should_filter:
            filtered_parts.append(sentence + punct)
        else:
            logger.debug(f"[response_processor] 过滤自我审查句：{sentence[:30]}")

        i += 2

    return "".join(filtered_parts).strip()


def _split_message(text: str, max_len: int = MAX_LENGTH) -> list[str]:
    """
    将超长文本拆分为多条消息

    优先在自然断句处（句号/换行）拆分
    """
    if len(text) <= max_len:
        return [text]

    parts = []
    current = ""

    # 先按换行拆分段落
    paragraphs = text.split("\n")

    for para in paragraphs:
        if not para.strip():
            continue

        if len(current) + len(para) + 1 <= max_len:
            current = (current + "\n" + para).strip() if current else para
        else:
            # 当前段落加进去会超长
            if current:
                parts.append(current)
                current = ""

            # 单个段落本身超长，按句号再拆
            if len(para) > max_len:
                sentences = re.split(r"([。！？])", para)
                i = 0
                while i < len(sentences):
                    s = sentences[i]
                    p = sentences[i + 1] if i + 1 < len(sentences) else ""
                    combined = s + p
                    if len(current) + len(combined) <= max_len:
                        current += combined
                    else:
                        if current:
                            parts.append(current)
                        current = combined
                    i += 2
            else:
                current = para

    if current:
        parts.append(current)

    return parts if parts else [text]


# Matches any XML-like open or close tag: <word> or </word>
_RENDER_TAG_RE = re.compile(r"</?[a-zA-Z]\w*>")


def strip_render_tags(text: str) -> str:
    """Strip render-only XML tags (<say>, <thought>, <narration>, etc.) from text.

    Used to clean output for QQ / mobile / memory targets that don't render NMP markup.
    Desktop channel output is intentionally left intact.
    """
    stripped = _RENDER_TAG_RE.sub("", text)
    stripped = re.sub(r" {2,}", " ", stripped)
    return stripped.strip()


class ResponseProcessor:
    """回复后处理类，封装模块级函数，供外部按类方式导入使用"""

    def process(self, reply: str, character_name: str) -> list[str]:
        return process(reply, character_name)
