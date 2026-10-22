"""Central, typed configuration. All values come from environment variables (.env supported).

Validation errors name the offending variable but never echo its value, so a misconfigured
secret cannot leak into logs.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

AGENT_VERSION = "1.5.0-opspulse"


class ConfigError(ValueError):
    """Raised when an environment variable has an invalid value."""


def _env(*names: str, default: str = "") -> str:
    """First non-empty value among `names` (supports legacy variable names)."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false)")


def _number(names: tuple[str, ...], default: float, cast: type, low: float, high: float) -> float:
    raw = _env(*names)
    if raw == "":
        return default
    try:
        value: float = cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{names[0]} must be a {cast.__name__}") from exc
    if not low <= value <= high:
        raise ConfigError(f"{names[0]} must be between {low} and {high}")
    return value


def _repo_list(name: str) -> frozenset[str]:
    return frozenset(item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip())


ROLES = ("reporter", "reviewer", "admin")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ApiIdentity:
    """One API client. Only the SHA-256 of its key is configured, never the key itself."""

    name: str
    role: str
    key_sha256: str


def _https_url(name: str) -> str:
    value = _env(name)
    if value and not value.lower().startswith("https://"):
        raise ConfigError(f"{name} must be an https:// URL")
    return value


def _status_set(name: str, default: str) -> frozenset[str]:
    return frozenset(item.strip() for item in (os.getenv(name) or default).split(",") if item.strip())


def _sentry_projects() -> dict[str, str]:
    from src.integrations.sentry import parse_project_map

    try:
        return parse_project_map(os.getenv("SENTRY_PROJECT_REPOS", ""))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _identities() -> tuple[ApiIdentity, ...]:
    """API_KEYS="name:role:sha256hex,..." plus the legacy single API_KEY (admin identity "default")."""
    identities = []
    for entry in (item.strip() for item in os.getenv("API_KEYS", "").split(",")):
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3 or not parts[0] or parts[1] not in ROLES or not _SHA256_HEX.match(parts[2]):
            raise ConfigError("API_KEYS entries must look like name:reporter|reviewer|admin:<sha256 hex>")
        identities.append(ApiIdentity(parts[0], parts[1], parts[2]))
    legacy = _env("API_KEY")
    if legacy:
        identities.append(ApiIdentity("default", "admin", hashlib.sha256(legacy.encode()).hexdigest()))
    names = [i.name for i in identities]
    if len(names) != len(set(names)):
        raise ConfigError("API_KEYS contains duplicate identity names")
    return tuple(identities)


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the environment, created once at import time."""

    # --- Local demo only: when set, GET /console embeds this key so the page connects by itself.
    # Never set it on a server that holds real incidents (the key is then readable by anyone who can load the page).
    console_demo_key: str = field(default_factory=lambda: _env("CONSOLE_DEMO_KEY"))

    # --- "Ask about this incident" (grounded Q&A over the stored record) ---
    ask_enabled: bool = field(default_factory=lambda: _env("ASK_ENABLED", default="true").lower() != "false")
    ask_rate_limit_per_minute: int = field(
        default_factory=lambda: int(_number(("ASK_RATE_LIMIT_PER_MINUTE",), 10, int, 0, 600))
    )

    # --- Alertmanager webhook: the alert label that names the GitHub repository ---
    alertmanager_repo_label: str = field(default_factory=lambda: _env("ALERTMANAGER_REPO_LABEL", default="repository"))

    # --- LLM (Groq) ---
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    model_name: str = field(default_factory=lambda: _env("MODEL_NAME", "GROQ_MODEL", default="openai/gpt-oss-120b"))
    llm_timeout_seconds: float = field(default_factory=lambda: _number(("LLM_TIMEOUT_SECONDS",), 60.0, float, 5, 300))
    llm_max_retries: int = field(default_factory=lambda: int(_number(("LLM_MAX_RETRIES",), 2, int, 0, 5)))
    # Upper bound on one reply. Some provider tiers cap output tokens per minute (e.g. 1000), and a request whose
    # expected output exceeds the cap is refused outright, so this must stay below such a cap to use the model.
    llm_max_output_tokens: int = field(
        default_factory=lambda: int(_number(("LLM_MAX_OUTPUT_TOKENS",), 4096, int, 256, 32768))
    )
    # Optional USD prices per million tokens, used only for cost estimates in evaluation reports.
    llm_cost_input_per_mtok: float = field(
        default_factory=lambda: _number(("LLM_COST_INPUT_PER_MTOK",), 0.0, float, 0, 1000)
    )
    llm_cost_output_per_mtok: float = field(
        default_factory=lambda: _number(("LLM_COST_OUTPUT_PER_MTOK",), 0.0, float, 0, 1000)
    )

    # --- Database ---
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "NEON_DATABASE_URL"))

    # --- API security ---
    api_identities: tuple[ApiIdentity, ...] = field(default_factory=_identities)
    allow_unauthenticated: bool = field(default_factory=lambda: _bool("ALLOW_UNAUTHENTICATED", False))
    # Four-eyes rule: the identity that submitted an incident may not approve its remediation.
    allow_self_approval: bool = field(default_factory=lambda: _bool("ALLOW_SELF_APPROVAL", False))
    # Per-identity incident submissions per minute (in-process limiter; 0 disables).
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(_number(("RATE_LIMIT_PER_MINUTE",), 30, int, 0, 10_000))
    )
    # Job queue. Workers renew their claim every lease/3, so the lease only bounds crash-recovery time.
    embedded_worker: bool = field(default_factory=lambda: _bool("EMBEDDED_WORKER", True))
    worker_poll_seconds: float = field(default_factory=lambda: _number(("WORKER_POLL_SECONDS",), 1.0, float, 0.1, 60))
    job_lease_seconds: int = field(default_factory=lambda: int(_number(("JOB_LEASE_SECONDS",), 300, int, 30, 86_400)))
    # Touched by the worker loop and lease heartbeat; `python -m src.worker --healthcheck` checks its age.
    worker_liveness_file: str = field(default_factory=lambda: _env("WORKER_LIVENESS_FILE"))
    max_job_attempts: int = field(default_factory=lambda: int(_number(("MAX_JOB_ATTEMPTS",), 2, int, 1, 10)))
    # Claims allowed for incidents that keep hitting transient provider errors (rate limit, timeout, outage).
    max_transient_retries: int = field(default_factory=lambda: int(_number(("MAX_TRANSIENT_RETRIES",), 3, int, 0, 10)))
    allowed_repositories: frozenset[str] = field(default_factory=lambda: _repo_list("ALLOWED_REPOSITORIES"))

    # --- GitHub ---
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    enable_github_remediation: bool = field(default_factory=lambda: _bool("ENABLE_GITHUB_REMEDIATION", False))
    github_pr_draft: bool = field(default_factory=lambda: _bool("GITHUB_PR_DRAFT", True))
    # A pending approval must be explicitly approved via the API before any GitHub write happens.
    require_remediation_approval: bool = field(default_factory=lambda: _bool("REQUIRE_REMEDIATION_APPROVAL", True))
    approval_ttl_hours: int = field(default_factory=lambda: int(_number(("APPROVAL_TTL_HOURS",), 72, int, 1, 720)))
    context_radius: int = field(default_factory=lambda: int(_number(("CODE_CONTEXT_RADIUS",), 50, int, 5, 200)))
    # Calling application frames shown to the model (0 disables), each as +/- CALLER_CONTEXT_RADIUS lines.
    caller_frames: int = field(default_factory=lambda: int(_number(("CALLER_CONTEXT_FRAMES",), 2, int, 0, 5)))
    caller_radius: int = field(default_factory=lambda: int(_number(("CALLER_CONTEXT_RADIUS",), 8, int, 2, 50)))
    max_patch_changed_lines: int = field(
        default_factory=lambda: int(_number(("MAX_PATCH_CHANGED_LINES",), 40, int, 1, 400))
    )

    # --- Sandboxed test run (optional, off by default) ---
    # Runs the target repository's tests in a throwaway Docker container (no network, read-only root, capped
    # memory/CPU/processes) before and after the patch, and shows the result to the reviewer.
    sandbox_enabled: bool = field(default_factory=lambda: _bool("SANDBOX_ENABLED", False))
    sandbox_image: str = field(default_factory=lambda: _env("SANDBOX_IMAGE", default="python:3.11-slim"))
    sandbox_command: str = field(
        default_factory=lambda: _env("SANDBOX_COMMAND", default="python -m pytest -q -x -p no:cacheprovider")
    )
    sandbox_timeout_seconds: int = field(
        default_factory=lambda: int(_number(("SANDBOX_TIMEOUT_SECONDS",), 120, int, 10, 900))
    )
    sandbox_memory_mb: int = field(default_factory=lambda: int(_number(("SANDBOX_MEMORY_MB",), 512, int, 128, 8192)))
    sandbox_max_repo_mb: int = field(default_factory=lambda: int(_number(("SANDBOX_MAX_REPO_MB",), 50, int, 1, 500)))

    # --- Inbound integrations ---
    sentry_client_secret: str = field(default_factory=lambda: _env("SENTRY_CLIENT_SECRET"))
    sentry_project_repos: dict[str, str] = field(default_factory=_sentry_projects)

    # --- Notifications ---
    notify_webhook_url: str = field(default_factory=lambda: _https_url("NOTIFY_WEBHOOK_URL"))
    notify_on_statuses: frozenset[str] = field(
        default_factory=lambda: _status_set("NOTIFY_ON_STATUSES", "awaiting_approval,failed")
    )
    # Public URL of this service, used only to put console links into notifications.
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL"))

    # --- Workflow ---
    quality_threshold: float = field(
        default_factory=lambda: _number(("QUALITY_THRESHOLD", "CONFIDENCE_THRESHOLD"), 0.85, float, 0.0, 1.0)
    )
    max_analysis_iterations: int = field(
        default_factory=lambda: int(_number(("MAX_ANALYSIS_ITERATIONS", "MAX_ITERATIONS"), 3, int, 1, 5))
    )

    # Wall-clock budget for the model calls of one incident: once the attempts so far have used it, no further
    # attempt is started (an attempt already running is not interrupted; its own timeout bounds it).
    max_incident_seconds: float = field(
        default_factory=lambda: _number(("MAX_INCIDENT_SECONDS",), 300.0, float, 30, 3600)
    )

    # --- Observability ---
    langfuse_public_key: str = field(default_factory=lambda: _env("LANGFUSE_PUBLIC_KEY"))
    langfuse_secret_key: str = field(default_factory=lambda: _env("LANGFUSE_SECRET_KEY"))
    langfuse_host: str = field(default_factory=lambda: _env("LANGFUSE_HOST", default="https://cloud.langfuse.com"))
    langfuse_capture_content: bool = field(default_factory=lambda: _bool("LANGFUSE_CAPTURE_CONTENT", False))
    console_enabled: bool = field(default_factory=lambda: _bool("CONSOLE_ENABLED", True))
    metrics_enabled: bool = field(default_factory=lambda: _bool("METRICS_ENABLED", True))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", default="INFO").upper())

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token)

    def is_repository_allowed(self, repo_name: str) -> bool:
        return repo_name.strip().lower() in self.allowed_repositories


settings = Settings()
