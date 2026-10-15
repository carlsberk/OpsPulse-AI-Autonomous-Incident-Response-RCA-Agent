"""FastAPI application: wiring, lifespan and consistent error responses."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.integrations import router as integrations_router
from src.api.routes import router
from src.config import AGENT_VERSION, settings
from src.console import mount_console, security_headers
from src.db.client import ping_database
from src.integrations.observability import flush_traces
from src.logging_config import configure_logging
from src.worker import Worker

configure_logging(settings.log_level)
logger = logging.getLogger("opspulse")

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "rate_limited",
    413: "payload_too_large",
    422: "validation_error",
    503: "service_unavailable",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # A missing DATABASE_URL is a configuration error -> fail fast (ConfigError propagates).
    # An unreachable database is an outage -> start anyway; /readyz and endpoints report 503.
    # The schema is managed by Alembic (`alembic upgrade head`), never created implicitly here.
    try:
        ping_database()
    except SQLAlchemyError as exc:
        logger.error("database not reachable at startup (%s); continuing, readiness will fail", type(exc).__name__)
    if not settings.api_identities and not settings.allow_unauthenticated:
        logger.error("no API keys configured (API_KEYS / API_KEY): incident endpoints will refuse requests (503)")
    worker = Worker() if settings.embedded_worker else None
    if worker:
        worker.start_in_thread()
    if not settings.allowed_repositories:
        logger.warning("ALLOWED_REPOSITORIES is empty: every incident will be rejected (403)")
    logger.info(
        "OpsPulse AI %s ready (model=%s, remediation=%s, embedded worker=%s)",
        AGENT_VERSION,
        settings.model_name,
        "enabled" if settings.enable_github_remediation else "disabled",
        settings.embedded_worker,
    )
    yield
    if worker:
        worker.stop()
    flush_traces()


app = FastAPI(
    title="OpsPulse AI",
    version=AGENT_VERSION,
    description="Incident investigation and root-cause analysis assistant with human-reviewed remediation.",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(integrations_router)
app.middleware("http")(security_headers)
if settings.console_enabled:
    mount_console(app)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    response = _error(exc.status_code, _HTTP_CODES.get(exc.status_code, "http_error"), str(exc.detail))
    response.headers.update(exc.headers or {})  # e.g. Retry-After on 429
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Report where and why, but never echo the submitted values back.
    problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10])
    return _error(422, "validation_error", problems)


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error", exc_info=exc)
    return _error(500, "internal_error", "internal server error")
