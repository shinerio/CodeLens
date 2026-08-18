"""add explicit SSE event versions

Revision ID: 0004_version_sse_events
Revises: 0003_correct_empty_findings_hash
Create Date: 2026-08-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_version_sse_events"
down_revision: str | None = "0003_correct_empty_findings_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNVERSIONED_EVENTS = (
    "review.created",
    "review.provisioning_worktree",
    "review.snapshotting",
    "review.preparing",
    "review.planning",
    "review.reviewing",
    "review.validating",
    "review.verifying",
    "review.synthesizing",
    "review.completed",
    "review.partial",
    "review.failed",
    "review.canceled",
    "review.superseded",
    "review.plan_created",
    "review.ready",
    "review.scope_empty",
    "review.cancel_requested",
    "review.verdict_completed",
    "agent_run.started",
    "agent_run.completed",
    "agent_run.failed",
    "agent.succeeded",
    "agent_tool_call.rejected",
    "finding.published",
)


def upgrade() -> None:
    """Version persisted v2 outbox events without inventing compatibility events."""

    events = sa.table("events", sa.column("event_type", sa.String(length=128)))
    op.execute(
        events.update()
        .where(events.c.event_type.in_(_UNVERSIONED_EVENTS))
        .values(event_type=events.c.event_type + ".v2")
    )


def downgrade() -> None:
    """Remove the explicit suffix to restore the previous development state."""

    events = sa.table("events", sa.column("event_type", sa.String(length=128)))
    versioned = tuple(f"{name}.v2" for name in _UNVERSIONED_EVENTS)
    op.execute(
        events.update()
        .where(events.c.event_type.in_(versioned))
        .values(
            event_type=sa.func.substr(
                events.c.event_type, 1, sa.func.length(events.c.event_type) - 3
            )
        )
    )
