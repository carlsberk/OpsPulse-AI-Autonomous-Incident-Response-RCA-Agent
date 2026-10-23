"""Append-only, hash-chained approval log: `audit_entries`.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=True),
        sa.Column("patch_sha256", sa.String(length=64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("at", sa.String(length=40), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("incident_id", "seq", name="uq_audit_incident_seq"),
    )
    op.create_index("ix_audit_entries_incident_id", "audit_entries", ["incident_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_entries_incident_id", table_name="audit_entries")
    op.drop_table("audit_entries")
