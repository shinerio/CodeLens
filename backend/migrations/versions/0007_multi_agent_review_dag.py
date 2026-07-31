"""persist multi-agent review DAG and finding audit state

Revision ID: 0007_multi_agent_review_dag
Revises: 0006_review_selection_requests
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_multi_agent_review_dag"
down_revision: str | None = "0006_review_selection_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "review_tasks",
        sa.Column(
            "has_partial_coverage",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    for column in (
        sa.Column("run_id", sa.String(128)),
        sa.Column("node_role", sa.String(32)),
        sa.Column("agent_version", sa.String(128)),
        sa.Column("pass_index", sa.Integer()),
        sa.Column("shard_id", sa.String(128)),
        sa.Column("capability_fingerprint", sa.String(64)),
        sa.Column("result_summary_json", sa.Text()),
    ):
        op.add_column("dag_checkpoints", column)
    op.create_index(
        "uq_dag_checkpoints_run_id",
        "dag_checkpoints",
        ["run_id"],
        unique=True,
        sqlite_where=sa.text("run_id IS NOT NULL"),
    )

    op.create_table(
        "review_plans",
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("catalog_version", sa.String(128), nullable=False),
        sa.Column("budget_json", sa.Text(), nullable=False),
        sa.Column("capability_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_execution_specs",
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("logical_node_id", sa.String(128), primary_key=True),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "prompt_artifact_ref",
            sa.String(128),
            sa.ForeignKey("artifacts.reference"),
            nullable=False,
        ),
        sa.Column("prompt_artifact_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_execution_skill_artifacts",
        sa.Column("task_id", sa.String(128), primary_key=True),
        sa.Column("logical_node_id", sa.String(128), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column(
            "artifact_ref",
            sa.String(128),
            sa.ForeignKey("artifacts.reference"),
            nullable=False,
        ),
        sa.Column("artifact_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id", "logical_node_id"],
            ["agent_execution_specs.task_id", "agent_execution_specs.logical_node_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_table(
        "candidate_findings",
        sa.Column("candidate_id", sa.String(128), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("snapshot_id", sa.String(128), nullable=False),
        sa.Column("reviewer_reference", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(256), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id", "node_key"],
            ["dag_checkpoints.task_id", "dag_checkpoints.node_key"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_candidate_findings_task_id", "candidate_findings", ["task_id"])
    op.create_index("ix_candidate_findings_run_id", "candidate_findings", ["run_id"])
    op.create_table(
        "finding_clusters",
        sa.Column("cluster_id", sa.String(128), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.String(128), nullable=False),
        sa.Column("cluster_key", sa.String(256), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "cluster_key", name="uq_finding_clusters_task_key"),
    )
    op.create_index("ix_finding_clusters_task_id", "finding_clusters", ["task_id"])
    op.create_table(
        "finding_cluster_candidates",
        sa.Column(
            "cluster_id",
            sa.String(128),
            sa.ForeignKey("finding_clusters.cluster_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "candidate_id",
            sa.String(128),
            sa.ForeignKey("candidate_findings.candidate_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint("candidate_id", name="uq_cluster_candidate_membership"),
        sa.UniqueConstraint("cluster_id", "ordinal", name="uq_cluster_candidate_ordinal"),
    )
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
        sa.Column("resolver_run_id", sa.String(128)),
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
    op.create_index("ix_resolution_decisions_task_id", "resolution_decisions", ["task_id"])
    op.create_table(
        "verification_decisions",
        sa.Column("verification_decision_id", sa.String(128), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "resolution_decision_id",
            sa.String(128),
            sa.ForeignKey("resolution_decisions.decision_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("verifier_run_id", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_verification_decisions_task_id", "verification_decisions", ["task_id"])

    with op.batch_alter_table("findings") as batch:
        batch.alter_column("confidence", existing_type=sa.Float(), nullable=True)
        batch.add_column(sa.Column("verification_status", sa.String(32)))


def downgrade() -> None:
    with op.batch_alter_table("findings") as batch:
        batch.drop_column("verification_status")
        batch.alter_column("confidence", existing_type=sa.Float(), nullable=False)
    op.drop_index("ix_verification_decisions_task_id", table_name="verification_decisions")
    op.drop_table("verification_decisions")
    op.drop_index("ix_resolution_decisions_task_id", table_name="resolution_decisions")
    op.drop_table("resolution_decisions")
    op.drop_table("finding_cluster_candidates")
    op.drop_index("ix_finding_clusters_task_id", table_name="finding_clusters")
    op.drop_table("finding_clusters")
    op.drop_index("ix_candidate_findings_run_id", table_name="candidate_findings")
    op.drop_index("ix_candidate_findings_task_id", table_name="candidate_findings")
    op.drop_table("candidate_findings")
    op.drop_table("agent_execution_skill_artifacts")
    op.drop_table("agent_execution_specs")
    op.drop_table("review_plans")
    op.drop_index("uq_dag_checkpoints_run_id", table_name="dag_checkpoints")
    for name in reversed(
        (
            "run_id",
            "node_role",
            "agent_version",
            "pass_index",
            "shard_id",
            "capability_fingerprint",
            "result_summary_json",
        )
    ):
        op.drop_column("dag_checkpoints", name)
    op.drop_column("review_tasks", "has_partial_coverage")
