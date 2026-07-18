"""Add soft deletion for persistent Review workspaces.

Revision ID: 0005_review_workspace_soft_delete
Revises: 0004_review_checkpoint_validation_attempts
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_review_workspace_soft_delete"
down_revision: str | None = "0004_review_checkpoint_validation_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a nullable tombstone without disturbing active Worker execution."""

    with op.batch_alter_table("review_tasks") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("review_tasks") as batch:
        batch.drop_column("deleted_at")
