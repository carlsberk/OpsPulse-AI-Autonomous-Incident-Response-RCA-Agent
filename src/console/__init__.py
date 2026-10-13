"""Static review console (src/console/static) and security headers.

The console is plain HTML/JS served from this application, so it can use a strict Content-Security-Policy:
no inline scripts or styles, no third-party origins. API keys stay in the browser tab's sessionStorage.
"""

from __future__ import annotations

import pathlib
from collections.abc import Awaitable, Callable
from html import escape as html_escape

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = pathlib.Path(__file__).with_name("static")
CONSOLE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/console"):
        response.headers["Content-Security-Policy"] = CONSOLE_CSP
        response.headers["Cache-Control"] = "no-store"
    return response


class SafeStaticFiles(StaticFiles):
    """A path the operating system cannot even stat (invalid characters on Windows, over-long names) is "not found",
    not a server error: the base class lets the OSError escape, which becomes an HTTP 500."""

    def lookup_path(self, path: str):  # type: ignore[no-untyped-def]
        try:
            return super().lookup_path(path)
        except (OSError, ValueError):
            return "", None


def mount_console(app: FastAPI) -> None:
    app.mount("/console/static", SafeStaticFiles(directory=STATIC_DIR), name="console-static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:  # browsers ask for this path on their own, whatever the page declares
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    @app.get("/console", include_in_schema=False)
    def console() -> Response:
        from src.config import settings

        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        if settings.console_demo_key:  # local demo only (scripts.demo_console); empty by default
            meta = f'<meta name="opspulse-demo-key" content="{html_escape(settings.console_demo_key, quote=True)}">'
            html = html.replace("</head>", f"  {meta}\n</head>", 1)
        return HTMLResponse(html)
