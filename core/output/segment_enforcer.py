"""发送前段落兜底（Brief 72）。

这里只处理即将发送给用户的文本副本。调用方不得把结果写回 short_term，
否则会污染分段坍缩信号对模型原始输出的观测。
"""

from __future__ import annotations

import logging
import re

from core.config_loader import get_config
from core.memory.short_term import DEFAULT_SEGMENT_MIN_LEN

logger = logging.getLogger(__name__)

_SENTENCE_END_CHARS = frozenset("。！？…")
_CLOSING_CHARS = frozenset("”’\"'」』】）》〉")
_TAG_TOKEN_RE = re.compile(
    r"^<\s*(/?)\s*([A-Za-z][\w-]*)\b[^>]*?(/?)\s*>$",
)
_VOID_TAGS = frozenset({"br", "hr"})


def get_segment_enforce_settings() -> tuple[bool, int]:
    """返回运行时开关与有效阈值；读取异常时关闭兜底（fail-open）。"""
    try:
        config = get_config()
        output_config = config.get("output", {})
        segment_config = output_config.get("segment_enforce", {})
        anti_collapse_config = config.get("anti_collapse", {})
        min_len = segment_config.get(
            "min_len",
            anti_collapse_config.get("segment_min_len", DEFAULT_SEGMENT_MIN_LEN),
        )
        return bool(segment_config.get("enabled", False)), max(1, int(min_len))
    except Exception as exc:
        logger.warning("[segment_enforcer] 读取配置失败，跳过分段兜底: %s", exc)
        return False, DEFAULT_SEGMENT_MIN_LEN


class ParagraphStreamEnforcer:
    """Incrementally add safe paragraph breaks to an outgoing text stream.

    A break becomes eligible after the current paragraph exceeds ``min_len``
    and reaches ``。！？…``.  It is emitted immediately before the next visible
    sentence, which leaves room for closing quotes and an existing newline.
    XML/NMP tags are tracked so a synthetic break never splits one tag across
    two client bubbles.  The input chunks themselves are never mutated or
    stored; callers keep the raw model output separately for memory.
    """

    def __init__(self, min_len: int):
        try:
            self._min_len = max(1, int(min_len))
            self._failed = False
        except Exception:
            self._min_len = DEFAULT_SEGMENT_MIN_LEN
            self._failed = True
        self._paragraph_len = 0
        self._pending_break = False
        self._in_tag = False
        self._tag_buffer: list[str] = []
        self._open_tags: list[tuple[str, str]] = []

    def feed(self, chunk: str) -> str:
        """Transform one delta; failure disables further transformation."""
        if self._failed or not chunk:
            return chunk
        if not isinstance(chunk, str):
            self._failed = True
            return chunk

        try:
            output: list[str] = []
            for char in chunk:
                if self._in_tag:
                    output.append(char)
                    self._tag_buffer.append(char)
                    if char == ">":
                        self._finish_tag()
                    continue

                if char == "<":
                    # With no open tag, this token starts the next visible
                    # unit. Put the paragraph break before the tag so the tag
                    # itself remains intact inside the new bubble.
                    if self._pending_break and not self._open_tags:
                        output.append("\n\n")
                        self._paragraph_len = 0
                        self._pending_break = False
                    self._in_tag = True
                    self._tag_buffer = [char]
                    output.append(char)
                    continue

                if char in "\r\n":
                    output.append(char)
                    self._paragraph_len = 0
                    self._pending_break = False
                    continue

                if self._pending_break:
                    if (
                        char not in _SENTENCE_END_CHARS
                        and char not in _CLOSING_CHARS
                        and not char.isspace()
                    ):
                        output.append(self._break_markup())
                        self._paragraph_len = 0
                        self._pending_break = False

                output.append(char)
                self._paragraph_len += 1
                if (
                    char in _SENTENCE_END_CHARS
                    and self._paragraph_len > self._min_len
                ):
                    self._pending_break = True
            return "".join(output)
        except Exception as exc:
            logger.warning("[segment_enforcer] 流式分段失败，后续原样透传: %s", exc)
            self._failed = True
            return chunk

    def _finish_tag(self) -> None:
        token = "".join(self._tag_buffer)
        self._tag_buffer = []
        self._in_tag = False
        match = _TAG_TOKEN_RE.match(token)
        if not match:
            return
        is_close = bool(match.group(1))
        name = match.group(2).lower()
        is_self_closing = bool(match.group(3)) or name in _VOID_TAGS
        if is_close:
            for index in range(len(self._open_tags) - 1, -1, -1):
                if self._open_tags[index][0] == name:
                    del self._open_tags[index:]
                    break
        elif not is_self_closing:
            self._open_tags.append((name, token))

    def _break_markup(self) -> str:
        """Return a break that keeps every currently open tag bubble-local."""
        if not self._open_tags:
            return "\n\n"
        closing = "".join(
            f"</{name}>" for name, _token in reversed(self._open_tags)
        )
        reopening = "".join(token for _name, token in self._open_tags)
        return f"{closing}\n\n{reopening}"


def enforce_paragraph_breaks(text: str, *, min_len: int) -> str:
    """在长回复达到阈值后的句末插入段落空行。

    与 :class:`ParagraphStreamEnforcer` 使用同一增量规则，因此流式临时
    气泡与最终 canonical 文本会收敛到相同段落。已有换行会重置段长；
    函数只插入换行，不改写标点，也不增删字词；任何异常均返回原文。
    """
    original = text
    try:
        if not isinstance(text, str):
            return original
        threshold = max(1, int(min_len))
        if len(text) <= threshold:
            return text
        return ParagraphStreamEnforcer(threshold).feed(text)
    except Exception as exc:
        logger.warning("[segment_enforcer] 分段兜底失败，返回原文: %s", exc)
        return original
