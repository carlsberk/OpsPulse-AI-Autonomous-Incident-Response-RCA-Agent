"""Request body size limit that also holds for chunked uploads.

Checking `Content-Length` is not enough: a client may omit it (chunked transfer) and stream an arbitrarily large body,
which `await request.body()` would buffer in memory before any schema check runs. This ASGI middleware counts the
bytes as they arrive and answers 413 as soon as the budget is exceeded.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_MAX_BODY_BYTES = 1_000_000  # the largest accepted payload (incident: ~120 KB of text; webhooks: 1 MB)


class _BodyTooLargeError(BaseException):
    """Raised from `receive` so that nothing between here and the middleware can swallow it."""


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLargeError:
            if started:  # too late to change the status; the connection is simply cut
                raise
            await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps({"error": {"code": "payload_too_large", "message": "payload too large"}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
