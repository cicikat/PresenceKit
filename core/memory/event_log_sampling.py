"""event_log 文本按时段分段取样（工单 F）。

日记素材超过预算时，旧实现 `text[-N:]` 只保留尾部，早上和下午整段丢失。
这里把当天块按（日期，时段）切段，各段均摊预算；某段用不满的份额让给更大的段。
段内超额时保留头尾、略去中间，并留一行省略标记。

- 先按 `event_log_source.block_is_recallable` 剔除 dream / web / coplay 等隔离来源块；
- 同一日期头只输出一次（`split_blocks` 会给每个块各带一份，纯属预算浪费）；
- meta 行（`> emotion:… speaker:…`）与 `**用户**：` 前缀原样保留，只精简重复的日期头；
- 纯函数，不做 IO，不依赖 LLM。
"""
from __future__ import annotations

import re

from core.memory.event_log_source import block_is_recallable, split_blocks

_DATE_HEADER_RE = re.compile(r"^# \d{4}-\d{2}-\d{2}\s*$")
_TIME_RE = re.compile(r"^## (\d{1,2}):\d{2}")

# (起始小时含, 标签)，按小时升序；< 5 点归入前一档之外的「凌晨夜间」
_PERIODS: tuple[tuple[int, str], ...] = (
    (0, "夜间"), (5, "上午"), (12, "下午"), (18, "傍晚"), (22, "夜间"),
)
_MIN_PIECE = 40


def _period_of(hour: int) -> str:
    label = _PERIODS[0][1]
    for start, name in _PERIODS:
        if hour >= start:
            label = name
    return label


def _marker(label: str) -> str:
    return f"…（{label}时段中间部分略）…"


class _Segment:
    __slots__ = ("date", "label", "pieces")

    def __init__(self, date: str, label: str) -> None:
        self.date = date
        self.label = label
        self.pieces: list[str] = []

    @property
    def size(self) -> int:
        return sum(len(p) + 1 for p in self.pieces)


def _take_head(pieces: list[str], limit: int) -> tuple[list[str], int, bool]:
    out: list[str] = []
    used = 0
    for i, piece in enumerate(pieces):
        room = limit - used
        if len(piece) + 1 <= room:
            out.append(piece)
            used += len(piece) + 1
            continue
        if room - 1 >= _MIN_PIECE or not out:
            cut = piece[: max(room - 1, 0)]
            if cut:
                out.append(cut)
        return out, i, True
    return out, len(pieces), False


def _take_tail(pieces: list[str], limit: int, floor: int) -> list[str]:
    out: list[str] = []
    used = 0
    for i in range(len(pieces) - 1, floor - 1, -1):
        piece = pieces[i]
        room = limit - used
        if len(piece) + 1 <= room:
            out.append(piece)
            used += len(piece) + 1
            continue
        if room - 1 >= _MIN_PIECE or not out:
            cut = piece[len(piece) - max(room - 1, 0):] if room - 1 > 0 else ""
            if cut:
                out.append(cut)
        break
    out.reverse()
    return out


def _shrink(seg: _Segment, quota: int) -> list[str]:
    """段内超额：留头尾，中间换成一行省略标记。"""
    marker = _marker(seg.label)
    avail = max(quota - (len(marker) + 1), 0)
    if len(seg.pieces) == 1:
        piece = seg.pieces[0]
        half = max(avail // 2 - 1, 0)
        if half <= 0:
            return [marker]
        return [piece[:half], marker, piece[len(piece) - half:]]
    head, head_end, partial = _take_head(seg.pieces, avail // 2)
    tail_floor = head_end + 1 if partial else head_end
    tail = _take_tail(seg.pieces, avail - avail // 2, tail_floor)
    return head + [marker] + tail


def sample_event_log_by_period(text: str, budget: int) -> str:
    """把 event_log 文本取样到不超过 `budget` 字符，全天各时段都有代表。

    未超预算时返回完整内容（仅剔除隔离来源块、合并重复日期头）。
    `budget <= 0` 返回空串。
    """
    if budget <= 0 or not text:
        return ""

    segments: list[_Segment] = []
    for block in split_blocks(text):
        if not block_is_recallable(block):
            continue
        date = ""
        lines = list(block)
        if lines and _DATE_HEADER_RE.fullmatch(lines[0]):
            date = lines[0]
            lines = lines[1:]
        if not lines:
            continue
        body = "\n".join(lines)
        m = _TIME_RE.match(lines[0])
        if m:
            label = _period_of(int(m.group(1)))
        elif segments and segments[-1].date == date:
            label = segments[-1].label
        else:
            label = _PERIODS[0][1]
        if not (segments and segments[-1].date == date and segments[-1].label == label):
            segments.append(_Segment(date, label))
        segments[-1].pieces.append(body)

    if not segments:
        return ""

    dates = list(dict.fromkeys(s.date for s in segments if s.date))
    header_cost = sum(len(d) + 1 for d in dates)
    total = sum(s.size for s in segments) + header_cost

    if total <= budget:
        kept = {id(s): s.pieces for s in segments}
    else:
        # 水位填充：小段全取，剩余预算均摊给大段
        remaining = max(budget - header_cost, 0)
        quotas: dict[int, int] = {}
        pending = sorted(segments, key=lambda s: s.size)
        for idx, seg in enumerate(pending):
            share = remaining // (len(pending) - idx)
            quota = min(seg.size, share)
            quotas[id(seg)] = quota
            remaining -= quota
        kept = {
            id(s): s.pieces if quotas[id(s)] >= s.size else _shrink(s, quotas[id(s)])
            for s in segments
        }

    out: list[str] = []
    last_date = None
    for seg in segments:
        if seg.date and seg.date != last_date:
            out.append(seg.date)
            last_date = seg.date
        out.extend(kept[id(seg)])
    result = "\n".join(out)
    if len(result) > budget:  # 极端小预算兜底，保证上界合同
        result = result[:budget]
    return result
