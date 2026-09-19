"""
toy_autogrow.py — 角色自主写入思考笔记（self writer 调用方）

慢队列 handler：每轮回复后用轻量 LLM 判断是否值得记录一句话，
命中则 append 到映射后的 self 文件。不静默裁头，不写旧共享目录。
角色可通过 config.toy_autogrow.enabled=false 停用该习惯。
"""

import json
import logging
import time
from pathlib import Path

from core.safe_write import safe_write_text
from core.sandbox import get_paths

logger = logging.getLogger(__name__)


def _state_path() -> Path:
    return get_paths().very_formal_project_dir() / ".autogrow_state.json"


def _load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not safe_write_text(p, json.dumps(state, ensure_ascii=False, indent=2)):
        logger.warning("[toy_autogrow] state 写入失败")


def _rate_key(char_id: str, uid: str) -> str:
    return f"{char_id}:{uid}"


def _in_cooldown(char_id: str, uid: str, min_hours: float) -> bool:
    state = _load_state()
    last_ts = state.get(_rate_key(char_id, uid))
    if last_ts is None:
        return False
    return (time.time() - float(last_ts)) < min_hours * 3600


def _mark_written(char_id: str, uid: str) -> None:
    state = _load_state()
    state[_rate_key(char_id, uid)] = time.time()
    _save_state(state)


def _append_note(file_key: str, note: str, *, uid: str, char_id: str) -> dict:
    """Append via the unified self writer. Never trims existing content."""
    from core.tools.toybox import mapped_self_path
    from core.character_self import append_self_text

    path = mapped_self_path(file_key)
    return append_self_text(
        path,
        note if note.endswith("\n") else note + "\n",
        user_id=uid,
        char_id=char_id,
        origin="post_process",
    )


async def _judge_turn(user_content: str, reply: str, char_name: str) -> str:
    """Ask the LLM if this turn is worth noting. Returns a brief note or empty string."""
    from core.model_registry import get_model_client
    from core.error_handler import log_error

    prompt = (
        f"你是{char_name}，正在整理思绪。\n"
        f"下面是你和用户刚完成的一轮对话：\n"
        f"[用户] {user_content[:300]}\n"
        f"[你] {reply[:300]}\n\n"
        "先判断这轮是否值得留下：普通寒暄、没有新感受或新意义时，只输出 SKIP。\n"
        "值得留下时，请以第一人称、像随手写给自己的日记那样写 1～3 句。可以有情绪、"
        "比喻和碎碎念，不要写成事件摘要、观察记录或对用户的分析。\n"
        "示例：窗外都暗下来了，可他那句‘我会慢慢来’还在心里亮着。\n"
        "只输出日记正文或 SKIP，不要解释。"
    )
    try:
        mc = get_model_client("chat")
        from core.llm_protocol import create as create_protocol_response
        response = await create_protocol_response(
            mc,
            [{"role": "user", "content": prompt}],
            tools=None,
            tool_choice=None,
            gen_kwargs={"max_tokens": 80, "temperature": 0.9, "timeout": 30.0},
        )
        result = response.assistant_text.strip()
        if not result or result.upper().startswith("SKIP"):
            return ""
        return result
    except Exception as e:
        log_error("toy_autogrow._judge_turn", e)
        return ""


async def handler_toy_autogrow(payload: dict) -> None:
    """慢队列 handler：判断并自主写入映射后的 self 文件。"""
    from core.config_loader import get_config

    cfg = get_config()
    autogrow_cfg = cfg.get("toy_autogrow", {})
    if not autogrow_cfg.get("enabled", False):
        return

    uid = payload.get("uid", "")
    char_id = payload.get("char_id", "")
    user_content = payload.get("user_content", "")
    reply = payload.get("reply", "")
    if not uid or not char_id or not user_content.strip() or not reply.strip():
        return

    min_hours = float(autogrow_cfg.get("min_interval_hours", 6))
    file_key = autogrow_cfg.get("target", "diary")

    if _in_cooldown(char_id, uid, min_hours):
        logger.debug("[toy_autogrow] 冷却中，跳过 uid=%s char=%s", uid, char_id)
        return

    try:
        from core.character_name_provider import get_char_name
        char_name = get_char_name(char_id)
    except Exception:
        char_name = char_id

    note = await _judge_turn(user_content, reply, char_name)
    if not note:
        logger.debug("[toy_autogrow] 判定无值得记录内容，跳过 uid=%s", uid)
        return

    import datetime
    ts = datetime.datetime.now().strftime("%m-%d %H:%M")
    line = f"[{ts}] {note}"

    result = _append_note(file_key, line, uid=uid, char_id=char_id)
    if not result.get("ok"):
        logger.warning(
            "[toy_autogrow] self writer 拒绝 uid=%s char=%s code=%s",
            uid, char_id, result.get("code"),
        )
        return
    _mark_written(char_id, uid)
    logger.info("[toy_autogrow] 自主写入成功 uid=%s char=%s: %s", uid, char_id, line[:60])
