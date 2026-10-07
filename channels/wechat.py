"""Optional WeChat text output; direct replies use their ingress address."""
from channels.base import BaseChannel


class WeChatChannel(BaseChannel):
    def __init__(self, service):
        self.service = service

    @property
    def name(self):
        return "wechat"

    @property
    def is_active(self):
        return self.service.snapshot()["proactive_effective"]

    async def send(self, content, user_id, behavior=None, *, char_id=None, sticker=None, artifacts=None):
        from core.config_loader import get_config
        if not self.is_active:
            return
        owner = str(get_config().get("scheduler", {}).get("owner_id") or "")
        if str(user_id) != owner:
            raise ValueError("wechat_recipient_not_bound")
        # Text-only first implementation: never interpret desktop actions or
        # sticker bytes as transport markup or send a local path.
        if not content.strip():
            return
        from core import response_processor
        from core.character_name_provider import get_char_name
        segments = response_processor.process(content, get_char_name(char_id))
        segments = [response_processor.strip_render_tags(s) for s in segments]
        await self.service.send_segments(self.service.settings.owner_sender_id, segments,
                                         generation=self.service._generation)
