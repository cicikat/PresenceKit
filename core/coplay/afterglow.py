"""
core/coplay/afterglow.py — Brief 42: session 结束后的软提示回流。

复用 dream_exit_afterglow 的"模式"（时间衰减的只读软提示层），但不复用它的
存储/整合机制——dream 那套挂在 core.memory.user_hidden_state（sensitivity/
embodied_ease 数值整合），是 dream 身体状态专属的，与陪玩无关。coplay 版本是
纯只读、纯文本、fail-closed 的独立残留文件：不写 mood_state / hidden_state /
profile，读取失败或 TTL 过期一律返回空字符串（宁可不注入，不可注入残缺/过期
的提示——比 observer/watcher 的 fail-open 更保守，因为这里的内容会直接进
prompt 影响角色的话，出错代价更高）。

写：session 收尾链（core/coplay/session_close.py）在收尾成功后调用
save_afterglow()。
读：core/pipeline.py::fetch_context() 每轮调用 load_afterglow_text()，经
prompt_builder 的 coplay_afterglow_soft_hint 层注入（仅当 coplay_context
本身为空，即当前不在 active 陪玩中时才可能出现——两层互斥）。
"""

import json
import logging
import time

from core.data_paths import DEFAULT_CHAR_ID
from core.safe_write import safe_write_json
from core.sandbox import get_paths

logger = logging.getLogger(__name__)

# "刚打完游戏"的余韵比梦境模糊感褪得快，比 dream_afterglow_soft_hint 的 8h TTL 更短。
AFTERGLOW_TTL_SECONDS = 4 * 3600


def save_afterglow(uid: str | int, *, game_name: str, char_id: str = DEFAULT_CHAR_ID) -> bool:
    game_name = (game_name or "").strip()
    if not game_name:
        return False
    payload = {"game_name": game_name, "created_at": time.time()}
    path = get_paths().coplay_afterglow_path(uid, char_id=char_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return safe_write_json(path, payload)


def load_afterglow_text(uid: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> str:
    """Fail-closed：任何读取/解析/校验失败都返回 ""，绝不注入残缺提示。"""
    try:
        path = get_paths().coplay_afterglow_path(uid, char_id=char_id)
        if not path.exists():
            return ""
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ""
        game_name = str(data.get("game_name") or "").strip()
        created_at = float(data.get("created_at") or 0)
        if not game_name or created_at <= 0:
            return ""
        age = time.time() - created_at
        if age < 0 or age > AFTERGLOW_TTL_SECONDS:
            return ""
        return f"（刚陪她打完《{game_name}》，还有点意犹未尽。）"
    except Exception:
        logger.warning("[coplay_afterglow] load 失败，fail-closed 不注入", exc_info=True)
        return ""


def clear_afterglow_for_test(uid: str | int, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    path = get_paths().coplay_afterglow_path(uid, char_id=char_id)
    if path.exists():
        path.unlink()
