"""Persist Agent Review completion coverage in DAG checkpoints.

Revision ID: 0004_agent_review_completion_status
Revises: 0003_recent_repository_limit
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_agent_review_completion_status"
down_revision: str | None = "0003_recent_repository_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dag_checkpoints",
        sa.Column(
            "review_completion_status",
            sa.String(length=32),
            nullable=False,
            server_default="complete",
        ),
    )


def downgrade() -> None:
    op.drop_column("dag_checkpoints", "review_completion_status")
