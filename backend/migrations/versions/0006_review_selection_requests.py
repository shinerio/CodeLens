"""persist immutable review selection requests

Revision ID: 0006_review_selection_requests
Revises: 0005_review_profiles
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_review_selection_requests"
down_revision: str | None = "0005_review_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column("selection_request_json", sa.Text()),
        sa.Column("budget_profile", sa.String(16)),
        sa.Column("profile_source_id", sa.String(128)),
        sa.Column("profile_source_revision", sa.Integer()),
        sa.Column("trigger_source", sa.String(16)),
        sa.Column("supersede_policy", sa.String(32)),
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("trigger_slot_key", sa.String(64)),
        sa.Column("planning_context_json", sa.Text()),
        sa.Column("planning_context_hash", sa.String(64)),
    )
    for column in columns:
        op.add_column("review_tasks", column)
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT task_id, selected_agent_versions_json FROM review_tasks")
    )
    for task_id, selected_json in rows:
        selection = {"mode": "fixed", "reviewer_versions": json.loads(selected_json)}
        connection.execute(
            sa.text(
                "UPDATE review_tasks SET selection_request_json=:selection, "
                "budget_profile='standard' WHERE task_id=:task_id"
            ),
            {"selection": json.dumps(selection, separators=(",", ":")), "task_id": task_id},
        )
    op.create_index(
        "uq_review_tasks_idempotency_key",
        "review_tasks",
        ["idempotency_key"],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_review_tasks_trigger_slot",
        "review_tasks",
        ["trigger_slot_key", "status", "created_at"],
        sqlite_where=sa.text("trigger_slot_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_review_tasks_trigger_slot", table_name="review_tasks")
    op.drop_index("uq_review_tasks_idempotency_key", table_name="review_tasks")
    for name in reversed(
        (
            "selection_request_json",
            "budget_profile",
            "profile_source_id",
            "profile_source_revision",
            "trigger_source",
            "supersede_policy",
            "idempotency_key",
            "trigger_slot_key",
            "planning_context_json",
            "planning_context_hash",
        )
    ):
        op.drop_column("review_tasks", name)
