"""Pin analysis to the deployed commit: `incidents.commit_sha` (optional, reported by the caller).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incidents") as batch:
        batch.add_column(sa.Column("commit_sha", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("incidents") as batch:
        batch.drop_column("commit_sha")
