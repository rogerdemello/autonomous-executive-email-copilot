"""Response security headers for every surface the app serves.

Pure-ASGI (same style as the ``/v1`` rewrite middleware in ``app.main``) so it
adds no per-request object allocation beyond the header writes, and applies to
JSON, HTML, and static responses alike.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders

from app.core.config import get_settings

# The server-rendered UI carries no JavaScript of its own beyond /static/app.js
# and uses inline style attributes in templates, hence 'unsafe-inline' for
# styles only. Everything else is same-origin.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'"
)

# Swagger UI / ReDoc load their assets from a CDN; a same-origin CSP would
# render them blank. The docs pages get every other header.
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


class SecurityHeadersMiddleware:
    """Set baseline security headers on every HTTP response.

    HSTS is emitted only in production: sending it from a plain-HTTP local
    dev server would poison the browser's HTTPS-only cache for localhost.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                if path not in _CSP_EXEMPT_PATHS:
                    headers.setdefault("Content-Security-Policy", _CSP)
                if get_settings().is_production:
                    headers.setdefault(
                        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_with_headers)
