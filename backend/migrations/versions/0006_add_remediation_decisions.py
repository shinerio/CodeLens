"""add remediation_decisions table

Revision ID: 0006_add_remediation_decisions
Revises: 0005_add_dedup_decisions
Create Date: 2026-08-21 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_add_remediation_decisions"
down_revision: str | None = "0005_add_dedup_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the remediation_decisions table for the Remediator gate."""

    op.create_table(
        "remediation_decisions",
        sa.Column(
            "id",
            sa.Integer,
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "task_id",
            sa.String(length=128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("finding_id", sa.String(length=256), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("evidence_summary", sa.Text(), nullable=False),
        sa.Column("decision_source", sa.String(length=16), nullable=False),
        sa.Column("remediator_run_id", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "task_id",
            "source_id",
            "finding_id",
            name="uq_remediation_decisions",
        ),
    )
    op.create_index(
        "ix_remediation_decisions_task_id",
        "remediation_decisions",
        ["task_id"],
    )


def downgrade() -> None:
    """Drop the remediation_decisions table."""

    op.drop_index("ix_remediation_decisions_task_id", table_name="remediation_decisions")
    op.drop_table("remediation_decisions")
