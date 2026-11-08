"""Compress only the public admin shell/assets, leaving API streams untouched."""
from starlette.middleware.gzip import GZipMiddleware


class AdminStaticCompression:
    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1024, compresslevel=5)

    async def __call__(self, scope, receive, send):
        static = scope.get("type") == "http" and (scope.get("path") == "/" or scope.get("path", "").startswith("/static/"))
        await (self.compressed if static else self.app)(scope, receive, send)
