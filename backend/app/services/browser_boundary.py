"""Browser response protections and bounded owner login body."""
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from ..config import get_settings


class BrowserBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = get_settings()
        async def protected_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"),
                ]
                if settings.cookie_secure:
                    message["headers"].append((b"strict-transport-security", b"max-age=31536000"))
            await send(message)
        headers = dict(scope.get("headers", []))
        if settings.public_origin:
            expected = urlsplit(settings.public_origin).netloc.encode()
            if headers.get(b"host", b"") != expected:
                return await JSONResponse({"detail": "Invalid host."}, status_code=400)(scope, receive, protected_send)
        if scope["method"] == "POST" and scope["path"] == "/access":
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 4096:
                    return await JSONResponse({"detail": "Login body too large."}, status_code=413)(scope, receive, protected_send)
                if not message.get("more_body", False):
                    break
            delivered = False
            original_receive = receive

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()
            receive = replay

        return await self.app(scope, receive, protected_send)
