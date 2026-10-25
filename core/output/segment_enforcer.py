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
_STRUCTURED_LINE_RE = re.compile(r"^(?:[ \t]{4}|\t|\s{0,3}(?:[#>|]|[-+*]\s|\d+[.)、]\s*))")
_LABEL_LINE_RE = re.compile(r"^\s*[^\s:：<>]{1,16}[:：]")
_BLOCK_TAG_RE = re.compile(r"</?(?:say|thought|narration|action|do|feel|env)\b", re.I)


def _visible_line(line: str) -> str:
    return re.sub(r"<[^>]*>", "", line).strip()


def _structured_line(line: str) -> bool:
    return bool(_STRUCTURED_LINE_RE.match(line) or _LABEL_LINE_RE.match(line)
                or _BLOCK_TAG_RE.search(line) or line.lstrip().startswith(("```", "~~~")))


def _han(char: str) -> bool:
    return bool(char) and "\u3400" <= char <= "\u9fff"


class _SoftLineBreakFilter:
    """Repair Han-to-Han single wraps, retaining explicit paragraph/Markdown structure.

    Only the uncertain newline and up to 32 following characters are held, so
    chunk boundaries cannot turn a list marker or a speaker label into prose.
    Raw output is owned by the caller; this filter only edits the visible copy.
    """

    def __init__(self):
        self._line = ""
        self._pending = False
        self._head = ""
        self._fence = ""
        self._cr = False

    def _keep_previous(self) -> bool:
        visible = _visible_line(self._line)
        return (bool(self._fence) or _structured_line(self._line)
                or self._line.endswith(("  ", "\\"))
                or not visible or not _han(visible[-1]))

    def _resolve(self, output: list[str]) -> None:
        following = _visible_line(self._head)
        join = (not self._keep_previous() and following and _han(following[0])
                and not _structured_line(self._head))
        output.extend(("" if join else "\n", self._head))
        self._line = self._head
        self._head = ""
        self._pending = False

    def _newline(self, output: list[str]) -> None:
        if self._pending:
            if not self._head.strip():
                output.extend(("\n", self._head, "\n"))
                self._line = self._head = ""
                self._pending = False
                return
            self._resolve(output)
        stripped = self._line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not self._fence:
                self._fence = marker
            elif marker == self._fence:
                self._fence = ""
        if self._fence:
            output.append("\n")
            self._line = ""
        else:
            self._pending = True

    def _char(self, char: str, output: list[str]) -> None:
        if char == "\n":
            self._newline(output)
        elif self._pending:
            self._head += char
            if self._keep_previous() or len(self._head) >= 32:
                self._resolve(output)
        else:
            self._line += char
            output.append(char)

    def feed(self, chunk: str) -> str:
        output: list[str] = []
        for char in chunk:
            if self._cr:
                self._char("\n", output)
                self._cr = False
                if char == "\n":
                    continue
            if char == "\r":
                self._cr = True
            else:
                self._char(char, output)
        return "".join(output)

    def finish(self) -> str:
        output: list[str] = []
        if self._cr:
            self._char("\n", output)
            self._cr = False
        if self._pending:
            self._resolve(output)
        return "".join(output)


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
    Han-to-Han soft line wraps are repaired first, with bounded lookahead.
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
        self._soft_wrap = _SoftLineBreakFilter()

    def feed(self, chunk: str) -> str:
        """Transform one delta; failure disables further transformation."""
        if self._failed or not chunk:
            return chunk
        if not isinstance(chunk, str):
            self._failed = True
            return chunk

        return self._feed_visible(self._soft_wrap.feed(chunk))

    def finish(self) -> str:
        """Flush bounded newline lookahead after a normally completed stream."""
        if self._failed:
            return ""
        return self._feed_visible(self._soft_wrap.finish())

    def _feed_visible(self, chunk: str) -> str:

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
    先保守合并中文句中单换行，保留空行、结构化内容和硬换行，再做句末分段；
    不改写标点、不增删字词；任何异常均返回原文。
    """
    original = text
    try:
        if not isinstance(text, str):
            return original
        threshold = max(1, int(min_len))
        enforcer = ParagraphStreamEnforcer(threshold)
        return enforcer.feed(text) + enforcer.finish()
    except Exception as exc:
        logger.warning("[segment_enforcer] 分段兜底失败，返回原文: %s", exc)
        return original
