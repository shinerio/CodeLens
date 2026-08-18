"""add frozen existing findings to review tasks

Revision ID: 0002_add_existing_findings
Revises: 0001_codelens_v2
Create Date: 2026-08-13 23:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_add_existing_findings"
down_revision: str | None = "0001_codelens_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_FINDINGS_HASH = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


def upgrade() -> None:
    """Add hash-verified existing findings with defaults for persisted tasks."""

    op.add_column(
        "review_tasks",
        sa.Column("existing_findings_json", sa.Text(), server_default="[]", nullable=False),
    )
    op.add_column(
        "review_tasks",
        sa.Column(
            "existing_findings_hash",
            sa.String(length=64),
            server_default=_EMPTY_FINDINGS_HASH,
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove frozen existing findings from review tasks."""

    op.drop_column("review_tasks", "existing_findings_hash")
    op.drop_column("review_tasks", "existing_findings_json")
