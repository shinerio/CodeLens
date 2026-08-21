"""add dedup_decisions table

Revision ID: 0005_add_dedup_decisions
Revises: 0004_version_sse_events
Create Date: 2026-08-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_add_dedup_decisions"
down_revision: str | None = "0004_version_sse_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the dedup_decisions table for the Deduplicator gate."""

    op.create_table(
        "dedup_decisions",
        sa.Column(
            "verdict_decision_id",
            sa.String(length=128),
            sa.ForeignKey("verdict_decisions.verdict_decision_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "task_id",
            sa.String(length=128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("decision_source", sa.String(length=16), nullable=False),
        sa.Column("deduplicator_run_id", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_dedup_decisions_task_id",
        "dedup_decisions",
        ["task_id"],
    )


def downgrade() -> None:
    """Drop the dedup_decisions table."""

    op.drop_index("ix_dedup_decisions_task_id", table_name="dedup_decisions")
    op.drop_table("dedup_decisions")
