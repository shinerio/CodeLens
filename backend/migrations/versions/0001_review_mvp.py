"""Create restart-safe review MVP storage.

Revision ID: 0001_review_mvp
Revises: None
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_review_mvp"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_tasks",
        sa.Column("task_id", sa.String(128), primary_key=True),
        sa.Column("repository_id", sa.String(128), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("base_oid", sa.String(64), nullable=False),
        sa.Column("head_oid", sa.String(64), nullable=False),
        sa.Column("overlay_hash", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("selected_agent_versions_json", sa.Text(), nullable=False),
        sa.Column("worktree_id", sa.String(128)),
        sa.Column("snapshot_id", sa.String(128)),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_tasks_repository_id", "review_tasks", ["repository_id"])
    op.create_table(
        "task_worktrees",
        sa.Column("worktree_id", sa.String(128), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("owned_path_hash", sa.String(64), nullable=False),
        sa.Column("common_dir_hash", sa.String(64), nullable=False),
        sa.Column("head_oid", sa.String(64), nullable=False),
        sa.Column("ownership_token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_worktrees_common_dir_hash", "task_worktrees", ["common_dir_hash"])
    op.create_table(
        "jobs",
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_table(
        "dag_checkpoints",
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("node_key", sa.String(256), primary_key=True),
        sa.Column("logical_attempt_group", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("execution_attempts", sa.Integer(), nullable=False),
        sa.Column("artifact_ref", sa.String(128)),
        sa.Column("artifact_hash", sa.String(64)),
        sa.Column("error_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_task_id", "events", ["task_id"])
    op.create_table(
        "artifacts",
        sa.Column("reference", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("storage_key", sa.String(128), nullable=False, unique=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_table(
        "findings",
        sa.Column("finding_id", sa.String(128), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("review_tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.String(256), nullable=False),
        sa.Column("fingerprint", sa.String(256), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "fingerprint", name="uq_findings_task_fingerprint"),
    )
    op.create_index("ix_findings_task_id", "findings", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_findings_task_id", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_events_task_id", table_name="events")
    op.drop_table("events")
    op.drop_table("dag_checkpoints")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_task_worktrees_common_dir_hash", table_name="task_worktrees")
    op.drop_table("task_worktrees")
    op.drop_index("ix_review_tasks_repository_id", table_name="review_tasks")
    op.drop_table("review_tasks")
