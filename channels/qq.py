"""
channels/qq — QQ通道，通过NapCat发消息。
通道始终注册；QQ supervisor 根据开关、standalone 和连接状态热启停。
"""

from channels.base import BaseChannel
import logging

logger = logging.getLogger(__name__)


class QQChannel(BaseChannel):
    def __init__(self, user_id: str):
        self._user_id = user_id
        self._active = True

    @property
    def name(self) -> str:
        return "qq"

    @property
    def is_active(self) -> bool:
        from core.qq_service import operational_state
        return self._active and operational_state()['connected']

    def set_active(self, active: bool) -> None:
        self._active = active

    async def send(
        self,
        content: str,
        user_id: str,
        behavior: dict | None = None,
        *,
        char_id: str | None = None,
        target_id: str | None = None,
        is_group: bool = False,
    ) -> None:
        """发送 QQ 消息（分段发送）。

        从 turn_sink._fanout 调用时，user_id 为 owner UID（私聊）；target_id /
        is_group 不传，默认走私聊路由（行为正确）。群聊路由由 main.py
        _qq_reality_reply_adapter 直接调用 text_output.send(target_id, ...,
        is_group) 处理，不经过本方法。

        触发器路径（scheduler/watch 等）经 _fanout 到达此处时，content 是整段
        原文；通过 response_processor.process 切段后用 text_output.send 逐条发
        送，与正常对话回复路径保持一致（分条 + 段间停顿）。
        """
        try:
            from core import response_processor
            from core.output import text_output

            _target = target_id if target_id is not None else user_id

            _char_name = ""
            try:
                from core.pipeline_registry import get as _get_pipeline
                pl = _get_pipeline()
                if pl is not None:
                    char = getattr(pl, "character", None)
                    if char is not None:
                        _char_name = getattr(char, "name", "") or ""
            except Exception:
                pass

            segments = response_processor.process(content, _char_name)
            if segments:
                await text_output.send(_target, segments, is_group)
        except Exception as e:
            logger.warning(f"[qq_channel] 发送失败: {e}")
