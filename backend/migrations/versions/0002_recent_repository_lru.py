"""Create the independent recent repository LRU.

Revision ID: 0002_recent_repository_lru
Revises: 0001_review_mvp
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_recent_repository_lru"
down_revision: str | None = "0001_review_mvp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recent_repositories",
        sa.Column("repository_path", sa.Text(), primary_key=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_recent_repositories_last_reviewed_at",
        "recent_repositories",
        ["last_reviewed_at"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO recent_repositories (repository_path, last_reviewed_at)
            SELECT repository_path, MAX(created_at)
            FROM review_tasks
            WHERE repository_path IS NOT NULL
            GROUP BY repository_path
            ORDER BY MAX(created_at) DESC, repository_path ASC
            LIMIT 10
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recent_repositories_last_reviewed_at",
        table_name="recent_repositories",
    )
    op.drop_table("recent_repositories")
