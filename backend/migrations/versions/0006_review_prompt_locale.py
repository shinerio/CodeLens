"""Persist the UI locale used to select reviewer prompts."""

import sqlalchemy as sa
from alembic import op

revision = "0006_review_prompt_locale"
down_revision = "0005_review_workspace_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("review_tasks") as batch:
        batch.add_column(
            sa.Column("prompt_locale", sa.String(length=8), nullable=False, server_default="en")
        )


def downgrade() -> None:
    with op.batch_alter_table("review_tasks") as batch:
        batch.drop_column("prompt_locale")
