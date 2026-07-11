"""
temporal_query — 查询侧时间意图解析（Brief 48）。

用户问"上周说的那件事" / "前天聊到的事" 这类带时间意图的问题时，召回侧此前完全不看
时间，只按 strength + decay 全时段排序。本模块从用户消息文本里解析出一个粗粒度的
[since_ts, until_ts) 日历日范围，供 episodic_memory.retrieve() / event_log.search() /
vector_store 语义预取按时间窗过滤候选。

纯规则解析，无 LLM 探针往返：时间词形是封闭集，规则够用；漏网词后续按
core.recall_trace 里的 parsed_time_range 观测补规则。

保守原则：模糊表述（"之前" / "很久以前" 等）、未覆盖的相对时间词、解析异常，一律返回
None（fail-open）——宁可不过滤，也不能把不该排除的记忆误过滤掉。

复用 core.memory.fixation_pipeline 里 event_time_hint 既有的中文日期工具（星期名映射
_WEEKDAY_BY_CN、M月D日 日期正则 _DATE_MDY_RE），不重写第二份同类逻辑。两边方向相反：
event_time_hint 解析的是*未来*计划（年份缺失时前滚），这里解析的是*过去*的提及
（年份缺失时倒推到未来日期落地之前的那一年）。
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime, timedelta

from core.memory.fixation_pipeline import _DATE_MDY_RE, _WEEKDAY_BY_CN

_LAST_WEEK_WEEKDAY_RE = re.compile(r"上(?:个)?周([一二三四五六日天])")
_THIS_WEEK_WEEKDAY_RE = re.compile(r"(?<![上下])周([一二三四五六日天])")
_N_DAYS_AGO_RE = re.compile(r"(\d{1,3})\s*天前")


def parse_query_time_range(text: str, now: float) -> tuple[float, float] | None:
    """解析用户查询里的时间意图，返回 (since_ts, until_ts)；无时间意图返回 None。

    until_ts 为排他上界（"昨天" = [昨日00:00, 昨日24:00)，即含当天末尾）。
    纯规则，无 LLM。解析失败/异常一律 fail-open 返回 None，不抛出。
    """
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return _parse(text, now)
    except Exception:
        return None


def _range(start: _date, end_exclusive: _date) -> tuple[float, float]:
    since = datetime.combine(start, datetime.min.time()).timestamp()
    until = datetime.combine(end_exclusive, datetime.min.time()).timestamp()
    return since, until


def _single_day(d: _date) -> tuple[float, float]:
    return _range(d, d + timedelta(days=1))


def _parse(text: str, now: float) -> tuple[float, float] | None:
    base = datetime.fromtimestamp(now)
    today = base.date()

    # "上周末" 必须先于 "上周" 判断，否则会被 "上周" 分支先吃掉。
    if "上周末" in text:
        last_monday = today - timedelta(days=today.weekday() + 7)
        last_saturday = last_monday + timedelta(days=5)
        return _range(last_saturday, last_saturday + timedelta(days=2))

    m = _LAST_WEEK_WEEKDAY_RE.search(text)
    if m:
        target_weekday = _WEEKDAY_BY_CN[m.group(1)]
        last_monday = today - timedelta(days=today.weekday() + 7)
        return _single_day(last_monday + timedelta(days=target_weekday))

    if "上周" in text:
        # 裸"上周"（无具体星期几）用滚动 7 天窗口，不锚定到真实周一——锚定 ISO 自然周
        # 会让这条判断随"今天是周几"变化（测试在周一跑时窗口只剩 1 天），不可靠。
        # "本周"记作 today-6..today 这滚动 7 天，"上周"就是再往前的 7 天。
        this_week_start = today - timedelta(days=6)
        last_week_start = this_week_start - timedelta(days=7)
        return _range(last_week_start, this_week_start)

    if "上个月" in text or "上月" in text:
        first_this_month = today.replace(day=1)
        first_last_month = (first_this_month - timedelta(days=1)).replace(day=1)
        return _range(first_last_month, first_this_month)

    # "前天" / "N天前" 都可能命中 "2天前"；"前天" 是固定词形，优先判断更省事也更直观。
    if "前天" in text:
        return _single_day(today - timedelta(days=2))

    if "昨天" in text:
        return _single_day(today - timedelta(days=1))

    m = _N_DAYS_AGO_RE.search(text)
    if m:
        n = int(m.group(1))
        if n <= 0:
            return None
        return _single_day(today - timedelta(days=n))

    m = _DATE_MDY_RE.search(text)
    if m:
        year = int(m.group(1)) if m.group(1) else today.year
        try:
            target = _date(year, int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        if m.group(1) is None and target > today:
            # 未指定年份、按今年算出的日期还没发生过：查询侧问的是过去，回退上一年。
            target = target.replace(year=year - 1)
        return _single_day(target)

    # 裸 "周X"（无 上/下 前缀）：最近一次出现的那个星期几（今天算作距离 0）。
    m = _THIS_WEEK_WEEKDAY_RE.search(text)
    if m:
        target_weekday = _WEEKDAY_BY_CN[m.group(1)]
        delta = (today.weekday() - target_weekday) % 7
        return _single_day(today - timedelta(days=delta))

    return None
