"""Reference implementation selection stays outside the IM business layer."""
from integrations.wechat.openclaw_weixin import OpenClawWeixinTransport


def create_transport(settings, token):
    if settings.transport != "openclaw_weixin":
        raise ValueError("unsupported_transport")
    return OpenClawWeixinTransport(settings.base_url, token)
