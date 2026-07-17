"""Persist private executable review inputs.

Revision ID: 0003_review_execution_inputs
Revises: 0002_review_repository_identity
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_review_execution_inputs"
down_revision: str | None = "0002_review_repository_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable columns because legacy hashes cannot safely recover source paths."""

    with op.batch_alter_table("review_tasks") as batch:
        batch.add_column(sa.Column("repository_path", sa.Text(), nullable=True))
        batch.add_column(sa.Column("target_paths_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("review_tasks") as batch:
        batch.drop_column("target_paths_json")
        batch.drop_column("repository_path")
