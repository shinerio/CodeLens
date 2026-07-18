"""Track validation attempts separately from execution retries.

Revision ID: 0004_review_checkpoint_validation_attempts
Revises: 0003_review_execution_inputs
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_review_checkpoint_validation_attempts"
down_revision: str | None = "0003_review_execution_inputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a stable validation counter for schema-repair eligibility."""

    with op.batch_alter_table("dag_checkpoints") as batch:
        batch.add_column(sa.Column("validation_attempts", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE dag_checkpoints SET validation_attempts = 0"))
    with op.batch_alter_table("dag_checkpoints") as batch:
        batch.alter_column("validation_attempts", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("dag_checkpoints") as batch:
        batch.drop_column("validation_attempts")
