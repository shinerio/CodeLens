"""merge resolution and verification decisions into single verdict table

Revision ID: 0009_merge_verdict_decisions
Revises: 0008_remove_budget_columns
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_merge_verdict_decisions"
down_revision: str | None = "0008_remove_budget_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the FK column that linked verification_decisions to resolution_decisions.
    # SQLite batch mode recreates the table, so the unnamed FK constraint is
    # removed automatically when the column is dropped.
    with op.batch_alter_table("verification_decisions", schema=None) as batch_op:
        batch_op.drop_column("resolution_decision_id")

    # The resolution_decisions table is no longer used; verdicts are stored
    # directly in verification_decisions.
    op.drop_index(
        "ix_resolution_decisions_task_id", table_name="resolution_decisions"
    )
    op.drop_table("resolution_decisions")


def downgrade() -> None:
    op.create_table(
        "resolution_decisions",
        sa.Column("decision_id", sa.String(128), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cluster_id",
            sa.String(128),
            sa.ForeignKey("finding_clusters.cluster_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verifier_run_id", sa.String(128)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column(
            "canonical_candidate_id",
            sa.String(128),
            sa.ForeignKey("candidate_findings.candidate_id"),
        ),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.String(32)),
        sa.Column(
            "publication_status",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "published_finding_id",
            sa.String(128),
            sa.ForeignKey("findings.finding_id"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "task_id", "cluster_id", name="uq_resolution_decisions_task_cluster"
        ),
    )
    op.create_index(
        "ix_resolution_decisions_task_id", "resolution_decisions", ["task_id"]
    )

    with op.batch_alter_table("verification_decisions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "resolution_decision_id",
                sa.String(128),
                sa.ForeignKey(
                    "resolution_decisions.decision_id",
                    ondelete="CASCADE",
                    name="fk_verification_decisions_resolution_decision_id",
                ),
                nullable=False,
            )
        )
        batch_op.create_unique_constraint(
            "uq_verification_decisions_resolution_decision_id",
            ["resolution_decision_id"],
        )
