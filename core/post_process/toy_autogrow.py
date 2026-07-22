"""
toy_autogrow.py — 角色自主写入思考笔记（沙盒自生长雏形）

慢队列 handler：每轮回复后用轻量 LLM 判断是否值得记录一句话，
命中则 rollover append 到目标玩具文件。
直接操作文件，绕开探针与 desktop 模式限制（自主写入是系统行为）。
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


def _rollover_append(file_key: str, note: str) -> None:
    """Append note to the toy file; roll over (trim from head) if it would exceed the char cap."""
    from core.tools.toybox import _TOYBOX_FILES, _TOY_FILE_CHAR_CAP, _assert_within

    if file_key not in _TOYBOX_FILES:
        raise ValueError(f"未知玩具文件键: {file_key}")

    root = get_paths().very_formal_project_dir()
    target = root / _TOYBOX_FILES[file_key]
    _assert_within(root, target)
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = ""
    if target.exists() and target.is_file():
        try:
            existing = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            existing = ""

    sep = "" if (not existing or existing.endswith("\n")) else "\n"
    combined = existing + sep + note

    if len(combined) > _TOY_FILE_CHAR_CAP:
        # Trim from head, align to next line boundary to avoid mid-line cuts
        overflow = len(combined) - _TOY_FILE_CHAR_CAP
        cut = combined[overflow:]
        nl = cut.find("\n")
        if 0 <= nl < len(cut) - 1:
            cut = cut[nl + 1:]
        combined = cut

    if not safe_write_text(target, combined):
        raise OSError("玩具文件自主写入失败")


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
        # This is a low-frequency personal note, so use the chat/persona route
        # instead of the terse summary route that naturally produces minutes.
        mc = get_model_client("chat")
        response = await mc.client.chat.completions.create(
            model=mc.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0.9,
            timeout=30.0,
        )
        result = (response.choices[0].message.content or "").strip()
        if not result or result.upper().startswith("SKIP"):
            return ""
        return result
    except Exception as e:
        log_error("toy_autogrow._judge_turn", e)
        return ""


async def handler_toy_autogrow(payload: dict) -> None:
    """慢队列 handler：判断并自主写入玩具文件。"""
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
        from core.character_loader import get_active_char_name
        char_name = get_active_char_name()
    except Exception:
        char_name = char_id

    note = await _judge_turn(user_content, reply, char_name)
    if not note:
        logger.debug("[toy_autogrow] 判定无值得记录内容，跳过 uid=%s", uid)
        return

    import datetime
    ts = datetime.datetime.now().strftime("%m-%d %H:%M")
    line = f"[{ts}] {note}"

    _rollover_append(file_key, line)
    _mark_written(char_id, uid)
    logger.info("[toy_autogrow] 自主写入成功 uid=%s char=%s: %s", uid, char_id, line[:60])
