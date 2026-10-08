"""Hot client admission and delivery switches, compatible when absent."""
from core.config_loader import get_config


def enabled(name: str) -> bool:
    if name not in {"desktop", "mobile"}:
        return True
    return bool(get_config().get("client_channels", {}).get(name, True))


class ClientChannelGate:
    """Gate HTTP and WS before client work; retain stored queues on disable."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        name = next((n for n in ("desktop", "mobile")
                     if path.startswith(f"/{n}/") or path == f"/ws/{n}"), None)
        if name and not enabled(name):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            if scope["type"] == "http":
                from starlette.responses import JSONResponse
                await JSONResponse({"detail": "client_channel_disabled"}, status_code=503)(scope, receive, send)
                return
        await self.app(scope, receive, send)
