"""Remember which HTTP request submitted an incident, so a run can be traced from its X-Request-ID.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("incidents") as batch:
        batch.add_column(sa.Column("request_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("incidents") as batch:
        batch.drop_column("request_id")
