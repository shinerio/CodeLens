"""Persist the configurable recent repository limit.

Revision ID: 0003_recent_repository_limit
Revises: 0002_recent_repository_lru
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_recent_repository_limit"
down_revision: str | None = "0002_recent_repository_lru"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    settings = op.create_table(
        "recent_repository_settings",
        sa.Column("settings_id", sa.Integer(), primary_key=True),
        sa.Column("recent_repository_limit", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "recent_repository_limit BETWEEN 1 AND 20",
            name="ck_recent_repository_settings_limit",
        ),
    )
    op.bulk_insert(
        settings,
        [{"settings_id": 1, "recent_repository_limit": 10}],
    )


def downgrade() -> None:
    op.drop_table("recent_repository_settings")
