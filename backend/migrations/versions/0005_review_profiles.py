"""add persistent review profiles

Revision ID: 0005_review_profiles
Revises: 0e0e42b05c24
Create Date: 2026-08-01
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0005_review_profiles"
down_revision: str | None = "0e0e42b05c24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_profiles",
        sa.Column("profile_id", sa.String(length=128), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("reviewer_selection_json", sa.Text(), nullable=False),
        sa.Column("budget_profile", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_review_profiles_positive_revision"),
        sa.CheckConstraint(
            "budget_profile IN ('lean', 'standard', 'deep')",
            name="ck_review_profiles_budget_profile",
        ),
    )
    op.create_index(
        "uq_review_profiles_single_default",
        "review_profiles",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )
    now = datetime.now(UTC)
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO review_profiles (
                profile_id, revision, name, is_default, reviewer_selection_json,
                budget_profile, created_at, updated_at
            )
            SELECT :profile_id, 1, :name, 1, :selection, :budget, :now, :now
            WHERE NOT EXISTS (SELECT 1 FROM review_profiles)
            """
        ),
        {
            "profile_id": "profile-balanced",
            "name": "Balanced Review",
            "selection": '{"mode":"adaptive"}',
            "budget": "standard",
            "now": now,
        },
    )


def downgrade() -> None:
    op.drop_index("uq_review_profiles_single_default", table_name="review_profiles")
    op.drop_table("review_profiles")
