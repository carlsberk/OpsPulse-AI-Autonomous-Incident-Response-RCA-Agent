"""SQLAlchemy models (PostgreSQL / Neon in production, SQLite in tests).

Column names are kept identical to the first released schema (`confidence_score`, `failure_reason`) so an
existing `incidents` table keeps working without a migration; the Python attribute names describe what the
values actually mean.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_repo_fingerprint", "repo_name", "fingerprint"),
        Index("ix_incidents_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stack_trace: Mapped[str] = mapped_column(Text, nullable=False, default="")
    affected_file: Mapped[str | None] = mapped_column(String(1024), nullable=True, index=True)
    root_cause_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    suggested_patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_score: Mapped[float] = mapped_column("confidence_score", Float, nullable=False, default=0.0)
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # See src/services/incident_service.py for the status lifecycle.
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="queued")
    pr_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_reason: Mapped[str | None] = mapped_column("failure_reason", Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # migration 0002
    submitted_by: Mapped[str | None] = mapped_column(String(100), nullable=True)  # migration 0003
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # migration 0007
    # Job queue (migration 0004): how often a worker claimed the incident, and until when its claim is valid.
    job_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(32), nullable=True)  # migration 0005
    # Not claimable before this time (deferred retry after a transient provider error). Migration 0006.
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class RemediationApproval(Base):
    """A proposed GitHub side effect waiting for an explicit human decision (migration 0002).

    The approval is bound to one incident AND to the exact patch (SHA-256): if the stored patch changes, the
    approval no longer matches and is rejected as stale.
    """

    __tablename__ = "remediation_approvals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("incidents.id"), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False, default="open_pull_request")
    target_file: Mapped[str] = mapped_column(String(1024), nullable=False)
    patch_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # pending -> approved -> executed | execution_failed ;  pending -> rejected | expired | superseded
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="pending")
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
