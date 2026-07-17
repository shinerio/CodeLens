"""Persist immutable repository identity hashes.

Revision ID: 0002_review_repository_identity
Revises: 0001_review_mvp
Create Date: 2026-07-17
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_review_repository_identity"
down_revision: str | None = "0001_review_mvp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("review_tasks") as batch:
        batch.add_column(sa.Column("repository_realpath_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("git_common_dir_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("overlay_artifact_ref", sa.String(128), nullable=True))
        batch.create_index("ix_review_tasks_git_common_dir_hash", ["git_common_dir_hash"])
    connection = op.get_bind()
    legacy_rows = connection.execute(
        sa.text("SELECT task_id, repository_id FROM review_tasks")
    ).mappings()
    for row in legacy_rows:
        repository_id = str(row["repository_id"])
        realpath_hash = hashlib.sha256(f"legacy-realpath:{repository_id}".encode()).hexdigest()
        common_dir_hash = connection.scalar(
            sa.text("SELECT common_dir_hash FROM task_worktrees WHERE task_id = :task_id LIMIT 1"),
            {"task_id": str(row["task_id"])},
        )
        if common_dir_hash is None:
            common_dir_hash = hashlib.sha256(
                f"legacy-common-dir:{repository_id}".encode()
            ).hexdigest()
        connection.execute(
            sa.text(
                "UPDATE review_tasks SET repository_realpath_hash = :realpath_hash, "
                "git_common_dir_hash = :common_dir_hash WHERE task_id = :task_id"
            ),
            {
                "realpath_hash": realpath_hash,
                "common_dir_hash": str(common_dir_hash),
                "task_id": str(row["task_id"]),
            },
        )
    with op.batch_alter_table("review_tasks") as batch:
        batch.alter_column("repository_realpath_hash", nullable=False)
        batch.alter_column("git_common_dir_hash", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("review_tasks") as batch:
        batch.drop_index("ix_review_tasks_git_common_dir_hash")
        batch.drop_column("overlay_artifact_ref")
        batch.drop_column("git_common_dir_hash")
        batch.drop_column("repository_realpath_hash")
