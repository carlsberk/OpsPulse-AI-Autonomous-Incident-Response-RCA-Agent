"""API-key authentication, role checks and per-identity rate limiting.

Keys are configured as SHA-256 digests (`API_KEYS=name:role:sha256`), so a leaked configuration does not leak
usable keys. Generate a key with `python -m scripts.make_api_key <name> <role>`.

Roles: reporter (submit and read incidents), reviewer (+ decide remediation), admin (everything).
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from src.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
# Same keys as a Bearer token, for clients that can only send `Authorization` (e.g. Alertmanager http_config).
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    name: str
    role: str


def authenticate(
    api_key: Annotated[str | None, Security(_api_key_header)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> Principal:
    """Fail closed: with no identities configured, refuse requests unless ALLOW_UNAUTHENTICATED=true."""
    provided = api_key or (bearer.credentials if bearer else None)
    if not settings.api_identities:
        if settings.allow_unauthenticated:
            return Principal("anonymous", "admin")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "API authentication is not configured (API_KEYS)")
    # SHA-256 is right here, and a slow password hash (bcrypt, argon2) would only add a cost to every request: the keys
    # are not passwords. `scripts.make_api_key` issues 256 random bits (`secrets.token_urlsafe(32)`), which cannot be
    # guessed or looked up offline even from a leaked digest. (CodeQL's weak-sensitive-data-hashing rule assumes
    # human-chosen secrets; the generator's output is pinned by a test.)
    digest = hashlib.sha256((provided or "").encode()).hexdigest()
    match = None
    for identity in settings.api_identities:  # compare against every identity: no early exit on a hit
        if hmac.compare_digest(digest, identity.key_sha256):
            match = identity
    if not provided or match is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")
    return Principal(match.name, match.role)


def require_role(*roles: str) -> Callable[[Principal], Principal]:
    allowed = set(roles) | {"admin"}

    def dependency(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role '{principal.role}' may not perform this action")
        return principal

    return dependency


class SlidingWindowLimiter:
    """In-process limiter (per identity, 60-second window). With several API replicas the effective limit is
    multiplied by the replica count; a shared store would be needed for a global limit."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, now: float | None = None) -> float | None:
        """Record one event; return None if allowed, else seconds until the next slot frees up."""
        if limit <= 0:
            return None
        now = time.monotonic() if now is None else now
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= 60:
                events.popleft()
            if len(events) >= limit:
                return 60 - (now - events[0])
            events.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


submission_limiter = SlidingWindowLimiter()
ask_limiter = SlidingWindowLimiter()  # separate budget: questions cost LLM tokens


def rate_limited_reporter(principal: Annotated[Principal, Depends(require_role("reporter", "reviewer"))]) -> Principal:
    retry_after = submission_limiter.check(principal.name, settings.rate_limit_per_minute)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate limit exceeded",
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )
    return principal


def rate_limited_asker(principal: Annotated[Principal, Depends(require_role("reporter", "reviewer"))]) -> Principal:
    retry_after = ask_limiter.check(principal.name, settings.ask_rate_limit_per_minute)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "question rate limit exceeded",
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )
    return principal
