"""add external_context_json to review_tasks

Revision ID: 0e0e42b05c24
Revises: 0004_agent_review_completion_status
Create Date: 2026-07-29 16:47:23.937600
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0e0e42b05c24'
down_revision: str | None = '0004_agent_review_completion_status'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('review_tasks', sa.Column('external_context_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('review_tasks', 'external_context_json')
