"""
core/coplay/game_state.py — Brief 41: per-game 进度档 + coplay_context prompt 层。

存储：data/runtime/coplay/{char_id}/games/{uid}/{game_id}/state.json（经
core.sandbox.get_paths()，game_id 里的 ':' 等非法字符在 DataPaths.coplay_game_dir()
里已消毒）。

字段：
  progress_markers — 自由文本进度标记（章节/区域/boss 名），v1 不做结构化校验，
    只按字符串相等去重，追加顺序即时间顺序。
  highlights        — 高光时刻 [{summary, ts}]，供 Brief 42 afterglow/game_log 引用。
  aliases           — 该游戏的别名表，供 Brief 42 的 tag 门控注入匹配聊天里提到
    的游戏名。

recent moments 不在这里持久化——那是 core/coplay/observer.py 里的内存态滚动
队列，session 结束前（Brief 42 收尾）才浓缩落盘。
"""

import json
import logging
from typing import Any

from core.data_paths import DEFAULT_CHAR_ID
from core.safe_write import safe_write_json
from core.sandbox import get_paths

logger = logging.getLogger(__name__)


def default_game_state(game_id: str, game_name: str = "") -> dict[str, Any]:
    return {
        "game_id": game_id,
        "game_name": game_name,
        "progress_markers": [],
        "highlights": [],
        "aliases": [],
        "last_summary": "",
        "last_closed_at": 0.0,
    }


def read_game_state(uid: str | int, game_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    path = get_paths().coplay_game_state_path(uid, game_id, char_id=char_id)
    if not path.exists():
        return default_game_state(game_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[coplay_game_state] read failed uid=%s game_id=%s: %s", uid, game_id, e)
        return default_game_state(game_id)
    if not isinstance(data, dict):
        return default_game_state(game_id)
    merged = default_game_state(game_id)
    merged.update(data)
    return merged


def write_game_state(uid: str | int, game_id: str, state: dict[str, Any], *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    path = get_paths().coplay_game_state_path(uid, game_id, char_id=char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return safe_write_json(path, state)


def add_progress_marker(uid: str | int, game_id: str, marker: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    marker = (marker or "").strip()
    state = read_game_state(uid, game_id, char_id=char_id)
    if marker and marker not in state["progress_markers"]:
        state["progress_markers"].append(marker)
        write_game_state(uid, game_id, state, char_id=char_id)
    return state


def add_highlight(uid: str | int, game_id: str, summary: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    import time
    summary = (summary or "").strip()
    state = read_game_state(uid, game_id, char_id=char_id)
    if summary:
        state["highlights"].append({"summary": summary, "ts": time.time()})
        write_game_state(uid, game_id, state, char_id=char_id)
    return state


def set_aliases(uid: str | int, game_id: str, aliases: list[str], *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    state = read_game_state(uid, game_id, char_id=char_id)
    state["aliases"] = [str(a).strip() for a in aliases if str(a).strip()]
    write_game_state(uid, game_id, state, char_id=char_id)
    return state


def set_last_summary(uid: str | int, game_id: str, summary: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any]:
    import time
    state = read_game_state(uid, game_id, char_id=char_id)
    state["last_summary"] = (summary or "").strip()
    state["last_closed_at"] = time.time()
    write_game_state(uid, game_id, state, char_id=char_id)
    return state


# ═══════════════════════════════════════════════════════════════════════════
# game_log 桶（Brief 42：session 收尾追加式 markdown 日志）
# ═══════════════════════════════════════════════════════════════════════════

def append_game_log_entry(uid: str | int, game_id: str, entry_text: str, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """追加一条 session 收尾摘要到 log.md。追加式，永不覆盖/删除旧内容。"""
    path = get_paths().coplay_game_log_path(uid, game_id, char_id=char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = entry_text if entry_text.endswith("\n") else entry_text + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)
    return True


def read_game_log_text(uid: str | int, game_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    path = get_paths().coplay_game_log_path(uid, game_id, char_id=char_id)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        logger.warning("[coplay_game_state] read_game_log_text 失败 uid=%s game_id=%s", uid, game_id)
        return ""


def list_games_for_user(uid: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict[str, Any]]:
    """列出该 uid/char_id 下所有曾经玩过（有 state.json）的游戏 game_state。"""
    games_root = get_paths().coplay_games_root(uid, char_id=char_id)
    if not games_root.exists():
        return []
    results = []
    for child in games_root.iterdir():
        if not child.is_dir():
            continue
        state_path = child / "state.json"
        if not state_path.exists():
            continue
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("game_id"):
            results.append(data)
    return results


def match_game_by_text(uid: str | int, text: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict[str, Any] | None:
    """在该 uid/char_id 已玩过的游戏里，找 game_name/aliases 命中 text 的第一个。

    简单子串匹配（同 core/tag_rules.py 的 get_tags() 风格，不是正则）。
    """
    text = text or ""
    if not text:
        return None
    for game in list_games_for_user(uid, char_id=char_id):
        candidates = [game.get("game_name") or ""] + list(game.get("aliases") or [])
        if any(c and c in text for c in candidates):
            return game
    return None


def build_game_log_recall_text(uid: str | int, text: str, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    """tag 门控（关键词命中）注入：聊天里提到某个玩过的游戏名/别名时，回忆起
    上次玩的摘要。fail-open：任何异常都返回 ""。

    只在当前不处于 active 陪玩中时才有意义（active 时 coplay_context 已经
    覆盖了"正在玩的这局"，两层不需要同时出现——由 fetch_context() 里的调用方
    保证互斥，本函数自身不检查 session 状态）。
    """
    try:
        game = match_game_by_text(uid, text, char_id=char_id)
        if not game or not game.get("last_summary"):
            return ""
        game_name = game.get("game_name") or "那个游戏"
        return (
            "<陪玩回忆>\n"
            f"你之前陪她玩过《{game_name}》。上次的印象：{game['last_summary']}\n"
            "</陪玩回忆>"
        )
    except Exception:
        logger.exception("[coplay_game_state] build_game_log_recall_text 失败（fail-open）")
        return ""


# ═══════════════════════════════════════════════════════════════════════════
# coplay_context prompt 层文本（Brief 41 §四·2）
# ═══════════════════════════════════════════════════════════════════════════

_ANTI_SPOILER_CONSTRAINT = (
    "你和她是第一次一起玩这个游戏，只知道到目前为止发生的事——"
    "禁止预测后续剧情，禁止暗示接下来会发生什么，哪怕你「觉得」知道这个游戏的后续内容也绝对不能说。"
)

_MAX_RECENT_MOMENTS = 3


def build_coplay_context_text(uid: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    """active 状态才返回非空文本。fail-open：任何读取异常都返回 ""，不影响正常对话。

    core/pipeline.py::fetch_context() 里调用，结果经 context 字典透传给
    core/prompt_builder.py::build() 的 coplay_context_text 参数。
    """
    try:
        from core.coplay import session, observer

        state = session.read_state(uid, char_id=char_id)
        if state.get("status") != session.CoplayStatus.ACTIVE.value:
            return ""

        game_id = state.get("game_id") or ""
        game_name = state.get("game_name") or "这个游戏"
        if not game_id:
            return ""

        game_state = read_game_state(uid, game_id, char_id=char_id)
        markers = game_state.get("progress_markers") or []
        progress_line = "、".join(markers[-3:]) if markers else "刚开始玩"

        moments = observer.peek_moments(str(uid))[-_MAX_RECENT_MOMENTS:]
        moment_line = "；".join(m.summary for m in moments if m.summary) or "还没有什么特别的动静"

        lines = [
            "<陪玩状态>",
            f"你正在陪她玩《{game_name}》。目前进度：{progress_line}。",
            f"最近发生的事：{moment_line}。",
            _ANTI_SPOILER_CONSTRAINT,
            "</陪玩状态>",
        ]
        return "\n".join(lines)
    except Exception:
        logger.exception("[coplay_game_state] build_coplay_context_text 失败（fail-open）")
        return ""
