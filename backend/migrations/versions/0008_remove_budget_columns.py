"""remove budget control columns and constraints

Revision ID: 0008_remove_budget_columns
Revises: 0007_multi_agent_review_dag
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_remove_budget_columns"
down_revision: str | None = "0007_multi_agent_review_dag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite requires batch mode for dropping constraints and columns
    with op.batch_alter_table("review_profiles") as batch_op:
        batch_op.drop_constraint("ck_review_profiles_budget_profile", type_="check")
        batch_op.drop_column("budget_profile")

    with op.batch_alter_table("review_tasks") as batch_op:
        batch_op.drop_column("budget_profile")

    with op.batch_alter_table("review_plans") as batch_op:
        batch_op.drop_column("budget_json")


def downgrade() -> None:
    import sqlalchemy as sa

    # SQLite requires batch mode for adding constraints and columns
    with op.batch_alter_table("review_plans") as batch_op:
        batch_op.add_column(
            sa.Column("budget_json", sa.Text(), nullable=False, server_default="{}"),
        )

    with op.batch_alter_table("review_tasks") as batch_op:
        batch_op.add_column(
            sa.Column("budget_profile", sa.String(16), nullable=True),
        )

    with op.batch_alter_table("review_profiles") as batch_op:
        batch_op.add_column(
            sa.Column("budget_profile", sa.String(32), nullable=False, server_default="standard"),
        )
        batch_op.create_check_constraint(
            "ck_review_profiles_budget_profile",
            "budget_profile IN ('lean', 'standard', 'deep')",
        )
