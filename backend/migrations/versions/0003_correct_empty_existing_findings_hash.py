"""correct the empty existing findings hash

Revision ID: 0003_correct_empty_findings_hash
Revises: 0002_add_existing_findings
Create Date: 2026-08-14 00:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_correct_empty_findings_hash"
down_revision: str | None = "0002_add_existing_findings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INCORRECT_EMPTY_FINDINGS_HASH = (
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e4d8e80efc93a6c2e61e7e7"
)
_EMPTY_FINDINGS_HASH = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


def upgrade() -> None:
    """Repair only empty frozen sets written by the incorrect 0002 default."""

    review_tasks = sa.table(
        "review_tasks",
        sa.column("existing_findings_json", sa.Text()),
        sa.column("existing_findings_hash", sa.String(length=64)),
    )
    op.execute(
        review_tasks.update()
        .where(review_tasks.c.existing_findings_json == "[]")
        .where(review_tasks.c.existing_findings_hash == _INCORRECT_EMPTY_FINDINGS_HASH)
        .values(existing_findings_hash=_EMPTY_FINDINGS_HASH)
    )


def downgrade() -> None:
    """Restore the prior hash only for empty frozen sets."""

    review_tasks = sa.table(
        "review_tasks",
        sa.column("existing_findings_json", sa.Text()),
        sa.column("existing_findings_hash", sa.String(length=64)),
    )
    op.execute(
        review_tasks.update()
        .where(review_tasks.c.existing_findings_json == "[]")
        .where(review_tasks.c.existing_findings_hash == _EMPTY_FINDINGS_HASH)
        .values(existing_findings_hash=_INCORRECT_EMPTY_FINDINGS_HASH)
    )
