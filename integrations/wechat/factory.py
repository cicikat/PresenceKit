"""Reference implementation selection stays outside the IM business layer."""
from integrations.wechat.padpro import PadProTransport


def create_transport(settings, token):
    if settings.transport != "wechatpadpro":
        raise ValueError("unsupported_transport")
    return PadProTransport(settings.base_url, token)
