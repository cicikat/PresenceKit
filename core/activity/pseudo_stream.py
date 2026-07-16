"""core/activity/pseudo_stream — coplay/活动对话正文伪流式打字机回放（Brief 84）。

chess/gomoku/reading 的 *_chat 和 *_comment 端点都是纯 HTTP 响应（无 WS 推送）；
这里只补一层可选的伪流式动画，不改变原有 HTTP 返回契约（额外加一个可空的
msg_id 字段，供前端与本次 HTTP 响应去重关联）。棋步/状态类 payload 不经此路径。
"""
from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


async def push_companion_reply(text: str | None, *, char_id: str) -> str | None:
    """推送伪流式动画帧，返回本轮 msg_id；text 为空（如本步不评论）时不推送，返回 None。

    fail-open：底层 pseudo_stream_push 本身已不抛异常，这里再兜一层，保证动画
    失败绝不影响调用方已经拿到的 reply/comment HTTP 响应。
    """
    if not text:
        return None
    msg_id = uuid.uuid4().hex
    try:
        from channels import ui_push

        await ui_push.pseudo_stream_push(text, msg_id=msg_id, char_id=char_id)
    except Exception:
        logger.debug("[activity.pseudo_stream] push_companion_reply failed", exc_info=True)
    return msg_id
