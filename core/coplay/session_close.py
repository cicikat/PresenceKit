"""
core/coplay/session_close.py — Brief 42: session 收尾链。

closing 状态触发（core/scheduler/triggers/coplay_watch.py 检测到 CLOSING 时
直调，不需要单独的 scheduler trigger/cooldown——这不是"要不要说话"的决策，
是状态机自身的收尾步骤）：

  1. summarizer：一次 LLM 调用产出"大概经过"（1-2 句叙述）。"3-5 条清晰词句"
     **不**经 LLM 二次转述——直接取自 game_state 已记录的 highlights +
     observer 收尾时排空的剩余 moment 队列，这些本来就是观察层的客观描述。
     对应 docs/briefs-36-37-and-outlook-20260710.md §2「风格坍缩」：重复
     LLM 摘要会把内容磨成通用"总结语气"，原始观测文本反而更贴近"真的发生过"。
  2. 写入 game_log（`game_state.append_game_log_entry`，追加式 markdown，
     含日期 + 进度标记），并把 gist 缓存进 `game_state.last_summary`
     供 tag 门控回忆（`build_game_log_recall_text`）低成本复用，不必每次
     重新解析 markdown。
  3. `provenance_log.append()`（硬规则 6）。
  4. 写 afterglow 残留（`core.coplay.afterglow.save_afterglow`）。
  5. `session.close_session()`（closing → armed）。

fail-open 边界：summarizer LLM 调用失败 → 退化为"没有大概经过，只有清晰
词句"，不阻塞 game_log 写入和状态转换——宁可摘要简陋，也不能让陪玩状态机
卡在 closing 出不来。provenance/afterglow 各自独立 try/except，任一失败都
不影响其余步骤和最终的 close_session()。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

_MAX_QUOTE_LINES = 5
_MIN_QUOTE_LINES = 3


def _collect_quote_lines(game_state_data: dict, remaining_moments: list) -> list[str]:
    """"清晰词句"来源：highlights（已持久化的高光）+ 收尾时剩余的 moment 队列。

    去重、保持时间顺序、最多 5 条；不足 3 条也不硬凑（v1 允许摘要单薄）。
    """
    seen: set[str] = set()
    lines: list[str] = []

    for h in game_state_data.get("highlights") or []:
        summary = str(h.get("summary") or "").strip()
        if summary and summary not in seen:
            seen.add(summary)
            lines.append(summary)

    for m in remaining_moments:
        summary = (getattr(m, "summary", "") or "").strip()
        if summary and summary not in seen:
            seen.add(summary)
            lines.append(summary)

    return lines[:_MAX_QUOTE_LINES]


async def _summarize_session(game_name: str, quote_lines: list[str]) -> str:
    """一次 LLM 调用产出"大概经过"。失败 fail-open 返回 ""。"""
    if not quote_lines:
        return ""
    try:
        from core import llm_client

        bullets = "\n".join(f"- {q}" for q in quote_lines)
        prompt = (
            f"这是一局《{game_name}》陪玩过程中记录下的客观片段（截图差分/OCR识别，"
            f"不是完整剧情）：\n{bullets}\n\n"
            "用1-2句话客观概括这次游玩大概发生了什么，不要编造片段以外的信息，"
            "不要评价好坏，不要煽情，只说事实梗概。"
        )
        messages = [{"role": "user", "content": prompt}]
        reply = await llm_client.chat(messages, call_category="summary")
        return (reply or "").strip()
    except Exception:
        logger.exception("[coplay_session_close] summarizer LLM 调用失败（fail-open）")
        return ""


def _format_log_entry(game_name: str, progress_markers: list[str], gist: str, quote_lines: list[str]) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    progress = "、".join(progress_markers[-3:]) if progress_markers else "（无进度标记）"
    lines = [f"## {date_str} — {game_name}", "", f"进度：{progress}", ""]
    if gist:
        lines += [f"大概经过：{gist}", ""]
    if quote_lines:
        lines.append("清晰词句：")
        lines += [f"- {q}" for q in quote_lines]
        lines.append("")
    return "\n".join(lines)


async def run_session_close(uid: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    """执行一次完整的 session 收尾链。防御性早退：非 closing 状态直接返回。"""
    from core.coplay import session, game_state, observer, afterglow

    state = session.read_state(uid, char_id=char_id)
    if state.get("status") != session.CoplayStatus.CLOSING.value:
        return

    game_id = state.get("game_id") or ""
    game_name = state.get("game_name") or "这个游戏"
    if not game_id:
        # 没有 game_id 没法写 game_log，直接放行回 armed，不卡状态机。
        logger.warning("[coplay_session_close] closing 状态缺少 game_id uid=%s，跳过收尾直接回 armed", uid)
        session.close_session(uid, char_id=char_id)
        return

    game_st = game_state.read_game_state(uid, game_id, char_id=char_id)
    remaining_moments = observer.drain_moments(str(uid))
    quote_lines = _collect_quote_lines(game_st, remaining_moments)
    gist = await _summarize_session(game_name, quote_lines)

    entry_text = _format_log_entry(game_name, game_st.get("progress_markers") or [], gist, quote_lines)
    game_state.append_game_log_entry(uid, game_id, entry_text, char_id=char_id)
    game_state.set_last_summary(uid, game_id, gist or "；".join(quote_lines), char_id=char_id)

    try:
        from core.memory.provenance_log import append as _prov_append
        _prov_append(
            str(uid), char_id,
            artifact="coplay_game_log",
            field=game_id,
            after_gist=(gist or "; ".join(quote_lines) or "(no summary)")[:200],
            trigger_signal="coplay_session_close",
        )
    except Exception:
        logger.exception("[coplay_session_close] provenance_log.append 失败（不阻塞收尾）")

    try:
        afterglow.save_afterglow(uid, game_name=game_name, char_id=char_id)
    except Exception:
        logger.exception("[coplay_session_close] afterglow 写入失败（不阻塞收尾）")

    session.close_session(uid, char_id=char_id)
    logger.info("[coplay_session_close] 收尾完成 uid=%s game_id=%s", uid, game_id)
