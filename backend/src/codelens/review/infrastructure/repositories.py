import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import case, delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingBatch,
    FindingDisposition,
    FindingSeverity,
    RuleReference,
    SourceLocation,
)
from codelens.review.domain.agent_run import InvalidAgentRunStateError
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import (
    MAX_RECENT_REPOSITORY_LIMIT,
    MIN_RECENT_REPOSITORY_LIMIT,
    AgentReviewCompletionStatus,
    RecentRepositoryRecord,
    ReviewEvent,
    ReviewExecutionRecord,
    ReviewRecord,
)
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.event_bus import InMemoryEventBus
from codelens.review.infrastructure.tables import (
    dag_checkpoints,
    events,
    findings,
    jobs,
    recent_repositories,
    recent_repository_settings,
    review_tasks,
    task_worktrees,
)
from codelens.workspace.domain.models import ReviewScopeType, TaskWorktree

_LOGGER = logging.getLogger("codelens.review.infrastructure.repositories")


@dataclass(frozen=True)
class JobRecord:
    """Expose durable singleton queue state without leaking SQLAlchemy rows."""

    task_id: str
    status: str


@dataclass(frozen=True)
class CheckpointRecord:
    """Expose one restart-safe DAG checkpoint."""

    task_id: str
    node_key: str
    logical_attempt_group: str
    status: str
    execution_attempts: int
    validation_attempts: int
    artifact_ref: str | None
    artifact_hash: str | None
    review_completion_status: AgentReviewCompletionStatus
    error_code: str | None


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(timestamp: datetime) -> datetime:
    """Restore UTC metadata that SQLite drops from timezone-aware columns."""

    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _resolve_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _review_scope_type(scope: dict[str, object]) -> ReviewScopeType:
    value = scope.get("type")
    if value not in {"branch", "commit", "uncommitted", "full"}:
        raise RuntimeError("review has an invalid persisted scope type")
    return cast(ReviewScopeType, value)


def _event_values(task_id: str, event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "task_id": task_id,
        "event_type": event_type,
        "payload_json": _json(payload),
        "created_at": _now(),
    }


async def _record_recent_repository(
    session: AsyncSession,
    repository_path: Path,
    reviewed_at: datetime,
    limit: int,
) -> None:
    """Touch one LRU entry and evict overflow within the Review creation transaction."""

    statement = sqlite_insert(recent_repositories).values(
        repository_path=str(repository_path),
        last_reviewed_at=reviewed_at,
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[recent_repositories.c.repository_path],
            set_={
                "last_reviewed_at": func.max(
                    recent_repositories.c.last_reviewed_at,
                    statement.excluded.last_reviewed_at,
                )
            },
        )
    )
    await _prune_recent_repositories(session, limit)


async def _get_recent_repository_limit(session: AsyncSession) -> int:
    limit = await session.scalar(
        select(recent_repository_settings.c.recent_repository_limit).where(
            recent_repository_settings.c.settings_id == 1
        )
    )
    if limit is None:
        raise RuntimeError("recent repository settings are missing")
    return int(limit)


async def _prune_recent_repositories(session: AsyncSession, limit: int) -> None:
    """Remove LRU entries beyond one validated capacity."""

    overflow = (
        select(recent_repositories.c.repository_path)
        .order_by(
            recent_repositories.c.last_reviewed_at.desc(),
            recent_repositories.c.repository_path.asc(),
        )
        .offset(limit)
    )
    await session.execute(
        delete(recent_repositories).where(recent_repositories.c.repository_path.in_(overflow))
    )


def _finding_payload(finding: Finding) -> str:
    return _json(asdict(finding))


def _finding_from_payload(payload: str) -> Finding:
    value: dict[str, Any] = json.loads(payload)
    primary = SourceLocation(**value.pop("primary_location"))
    related = tuple(SourceLocation(**item) for item in value.pop("related_locations"))
    evidence_items = tuple(Evidence(**item) for item in value.pop("evidence"))
    rules = tuple(RuleReference(**item) for item in value.pop("rule_sources"))
    severity = FindingSeverity(value.pop("severity"))
    disposition = FindingDisposition(value.pop("disposition"))
    change_origin = ChangeOrigin(value.pop("change_origin"))
    return Finding(
        **value,
        severity=severity,
        disposition=disposition,
        change_origin=change_origin,
        primary_location=primary,
        related_locations=related,
        evidence=evidence_items,
        rule_sources=rules,
    )


def _review_record(row: Any, finding_count: int = 0) -> ReviewRecord:
    scope: dict[str, object] = json.loads(str(row["scope_json"]))
    selected_agents: list[str] = json.loads(str(row["selected_agent_versions_json"]))
    external_context = None
    if row["external_context_json"] is not None:
        external_context = json.loads(str(row["external_context_json"]))
    return ReviewRecord(
        task_id=str(row["task_id"]),
        repository_id=str(row["repository_id"]),
        repository_realpath_hash=str(row["repository_realpath_hash"]),
        git_common_dir_hash=str(row["git_common_dir_hash"]),
        scope_type=_review_scope_type(scope),
        base_oid=str(row["base_oid"]),
        head_oid=str(row["head_oid"]),
        selected_agent_versions=tuple(selected_agents),
        status=str(row["status"]),
        cancellation_requested=bool(row["cancellation_requested"]),
        repository_name=(
            Path(str(row["repository_path"])).name
            if row["repository_path"] is not None
            else str(row["repository_id"])[-12:]
        ),
        created_at=_as_utc(cast(datetime, row["created_at"])),
        is_deleted=row["deleted_at"] is not None,
        finding_count=finding_count,
        external_context=external_context,
    )


class SqlRecentRepositoryStore:
    """Manage the bounded repository LRU without consulting Review tombstones."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_limit(self) -> int:
        """Return the persisted list capacity."""

        async with self._database.sessions() as session:
            return await _get_recent_repository_limit(session)

    async def update_limit(self, limit: int) -> int:
        """Persist one capacity and prune older entries atomically."""

        if not MIN_RECENT_REPOSITORY_LIMIT <= limit <= MAX_RECENT_REPOSITORY_LIMIT:
            raise ValueError(
                "recent repository limit must be between "
                f"{MIN_RECENT_REPOSITORY_LIMIT} and {MAX_RECENT_REPOSITORY_LIMIT}"
            )

        async def operation(session: AsyncSession) -> int:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(recent_repository_settings)
                    .where(recent_repository_settings.c.settings_id == 1)
                    .values(recent_repository_limit=limit)
                ),
            )
            if result.rowcount != 1:
                raise RuntimeError("recent repository settings are missing")
            await _prune_recent_repositories(session, limit)
            return limit

        return await self._database.run_transaction(operation)

    async def list_recent_repositories(
        self,
        limit: int,
    ) -> tuple[RecentRepositoryRecord, ...]:
        """Return at most the persisted LRU capacity in recency order."""

        if not MIN_RECENT_REPOSITORY_LIMIT <= limit <= MAX_RECENT_REPOSITORY_LIMIT:
            raise ValueError(
                "recent repository limit must be between "
                f"{MIN_RECENT_REPOSITORY_LIMIT} and {MAX_RECENT_REPOSITORY_LIMIT}"
            )
        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(recent_repositories)
                    .order_by(
                        recent_repositories.c.last_reviewed_at.desc(),
                        recent_repositories.c.repository_path.asc(),
                    )
                    .limit(limit)
                )
            ).mappings()
        return tuple(
            RecentRepositoryRecord(
                repository_path=Path(str(row["repository_path"])),
                repository_name=Path(str(row["repository_path"])).name,
                last_reviewed_at=_as_utc(cast(datetime, row["last_reviewed_at"])),
            )
            for row in rows
        )

    async def delete_recent_repository(self, repository_path: Path) -> None:
        """Idempotently remove one shortcut without consulting or changing Reviews."""

        resolved_path = _resolve_path(str(repository_path))

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                delete(recent_repositories).where(
                    recent_repositories.c.repository_path == str(resolved_path)
                )
            )

        await self._database.run_transaction(operation)


class SqlReviewStore:
    """Persist ReviewTask commands and atomic Agent success boundaries."""

    def __init__(
        self,
        database: Database,
        *,
        completion_hook: Callable[[str], Awaitable[None]] | None = None,
        event_bus: InMemoryEventBus | None = None,
        terminal_hook: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._database = database
        self._completion_hook = completion_hook
        self._event_bus = event_bus
        self._terminal_hook = terminal_hook

    def set_terminal_hook(self, hook: Callable[[str, str], Awaitable[None]]) -> None:
        """Late-bind the post-terminal-status hook.

        The hook fires after a review reaches a terminal status
        (``completed``, ``partial``, ``failed``, ``canceled``) and after the
        originating transaction has committed. It receives ``(task_id, status)``
        and must be failure-isolated by the caller; export or notification
        failures must not break Review persistence.
        """

        self._terminal_hook = hook

    async def _fire_terminal_hook(self, task_id: str, status: str) -> None:
        """Run the terminal hook after commit; swallow exceptions to isolate failures."""

        if self._terminal_hook is None:
            return
        try:
            await self._terminal_hook(task_id, status)
        except Exception:
            _LOGGER.exception(
                "Terminal hook failed for task '%s' (status=%s)",
                task_id,
                status,
            )

    async def _publish_events(self, captured: list[ReviewEvent]) -> None:
        """Publish committed events to the in-memory bus; never raises."""

        if self._event_bus is None:
            return
        for event in captured:
            try:
                await self._event_bus.publish(event)
            except Exception:
                _LOGGER.warning(
                    "Failed to publish event to bus",
                    extra={"event_type": event.event_type, "task_id": event.task_id},
                    exc_info=True,
                )

    async def create_with_job(self, task: ReviewTask) -> None:
        """Insert task, singleton job, and review.created event in one transaction."""

        timestamp = task.created_at
        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                insert(review_tasks).values(
                    task_id=task.task_id,
                    repository_id=task.repository_id,
                    repository_path=str(task.repository_path),
                    repository_realpath_hash=task.repository_realpath_hash,
                    git_common_dir_hash=task.git_common_dir_hash,
                    scope_json=_json(asdict(task.scope)),
                    base_oid=task.target.base_oid,
                    head_oid=task.target.head_oid,
                    overlay_hash=task.target.overlay_hash,
                    overlay_artifact_ref=task.overlay_artifact_ref,
                    target_paths_json=_json(task.target_paths),
                    status=task.status.value,
                    selected_agent_versions_json=_json(task.selected_agent_versions),
                    prompt_locale=task.prompt_locale,
                    external_context_json=(
                        _json(task.external_context) if task.external_context else None
                    ),
                    worktree_id=task.worktree_id,
                    snapshot_id=task.snapshot_id,
                    cancellation_requested=task.cancellation_requested,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            await session.execute(
                insert(jobs).values(
                    task_id=task.task_id,
                    status="queued",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            event_id = await session.scalar(
                insert(events)
                .values(
                    **_event_values(
                        task.task_id,
                        "review.created",
                        {
                            "status": task.status.value,
                            "base_oid": task.target.base_oid,
                            "head_oid": task.target.head_oid,
                        },
                    )
                )
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=task.task_id,
                        event_type="review.created",
                        payload={
                            "status": task.status.value,
                            "base_oid": task.target.base_oid,
                            "head_oid": task.target.head_oid,
                        },
                    )
                )
            recent_repository_limit = await _get_recent_repository_limit(session)
            await _record_recent_repository(
                session,
                task.repository_path,
                timestamp,
                recent_repository_limit,
            )

        await self._database.run_transaction(operation)
        await self._publish_events(captured)

    async def count_tasks(self) -> int:
        """Return the number of durable ReviewTasks."""

        async with self._database.sessions() as session:
            value = await session.scalar(select(func.count()).select_from(review_tasks))
        return int(value or 0)

    async def list_input_artifact_references(self) -> frozenset[str]:
        """Return opaque input references retained by durable ReviewTasks."""

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(review_tasks.c.overlay_artifact_ref).where(
                        review_tasks.c.overlay_artifact_ref.is_not(None)
                    )
                )
            ).scalars()
        return frozenset(str(reference) for reference in rows)

    async def get_review(self, task_id: str) -> ReviewRecord | None:
        """Return one path-free persisted review summary."""

        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(review_tasks).where(review_tasks.c.task_id == task_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _review_record(row) if row is not None else None

    async def list_reviews(self) -> tuple[ReviewRecord, ...]:
        """Return non-deleted workspaces in deterministic newest-first order."""

        async with self._database.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(review_tasks)
                        .where(review_tasks.c.deleted_at.is_(None))
                        .order_by(
                            review_tasks.c.created_at.desc(),
                            review_tasks.c.task_id.desc(),
                        )
                    )
                )
                .mappings()
                .all()
            )
            if not rows:
                return ()
            task_ids = [str(row["task_id"]) for row in rows]
            count_rows = (
                await session.execute(
                    select(findings.c.task_id, func.count().label("cnt"))
                    .where(findings.c.task_id.in_(task_ids))
                    .group_by(findings.c.task_id)
                )
            ).mappings()
            finding_counts = {str(row["task_id"]): int(row["cnt"]) for row in count_rows}
        return tuple(
            _review_record(row, finding_counts.get(str(row["task_id"]), 0))
            for row in rows
        )

    async def retry_failed_review(
        self,
        source_task_id: str,
        new_task_id: str,
        created_at: datetime,
    ) -> ReviewRecord | None:
        """Atomically clone a visible failed command into a fresh queued task."""

        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> ReviewRecord | None:
            source = (
                (
                    await session.execute(
                        select(review_tasks).where(review_tasks.c.task_id == source_task_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if source is None or source["deleted_at"] is not None:
                return None
            if str(source["status"]) != "failed":
                raise InvalidAgentRunStateError("only failed reviews can retry")

            await session.execute(
                insert(review_tasks).values(
                    task_id=new_task_id,
                    repository_id=source["repository_id"],
                    repository_path=source["repository_path"],
                    repository_realpath_hash=source["repository_realpath_hash"],
                    git_common_dir_hash=source["git_common_dir_hash"],
                    scope_json=source["scope_json"],
                    base_oid=source["base_oid"],
                    head_oid=source["head_oid"],
                    overlay_hash=source["overlay_hash"],
                    overlay_artifact_ref=source["overlay_artifact_ref"],
                    target_paths_json=source["target_paths_json"],
                    status="created",
                    selected_agent_versions_json=source["selected_agent_versions_json"],
                    prompt_locale=source["prompt_locale"],
                    worktree_id=None,
                    snapshot_id=None,
                    cancellation_requested=False,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            await session.execute(
                insert(jobs).values(
                    task_id=new_task_id,
                    status="queued",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            payload: dict[str, object] = {
                "status": "created",
                "base_oid": str(source["base_oid"]),
                "head_oid": str(source["head_oid"]),
                "retried_from_task_id": source_task_id,
            }
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(new_task_id, "review.created", payload))
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=new_task_id,
                        event_type="review.created",
                        payload=payload,
                    )
                )
            repository_path = source["repository_path"]
            if repository_path is None:
                raise RuntimeError("review lacks restart-safe repository input")
            recent_repository_limit = await _get_recent_repository_limit(session)
            await _record_recent_repository(
                session,
                Path(str(repository_path)),
                created_at,
                recent_repository_limit,
            )
            created = (
                (
                    await session.execute(
                        select(review_tasks).where(review_tasks.c.task_id == new_task_id)
                    )
                )
                .mappings()
                .one()
            )
            return _review_record(created)

        record = await self._database.run_transaction(operation)
        await self._publish_events(captured)
        return record

    async def soft_delete_review(self, task_id: str) -> bool:
        """Hide one workspace and atomically request cancellation if it is active."""

        terminal_statuses = {"completed", "partial", "failed", "canceled"}
        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> bool:
            row = (
                (
                    await session.execute(
                        select(review_tasks).where(review_tasks.c.task_id == task_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return False
            if row["deleted_at"] is not None:
                return True
            is_active = str(row["status"]) not in terminal_statuses
            should_request_cancellation = is_active and not bool(row["cancellation_requested"])
            await session.execute(
                update(review_tasks)
                .where(review_tasks.c.task_id == task_id)
                .values(
                    deleted_at=_now(),
                    cancellation_requested=(
                        True if is_active else bool(row["cancellation_requested"])
                    ),
                    updated_at=_now(),
                )
            )
            if should_request_cancellation:
                event_id = await session.scalar(
                    insert(events)
                    .values(
                        **_event_values(
                            task_id,
                            "review.cancel_requested",
                            {"cancellation_requested": True},
                        )
                    )
                    .returning(events.c.event_id)
                )
                if event_id is not None:
                    captured.append(
                        ReviewEvent(
                            event_id=int(event_id),
                            task_id=task_id,
                            event_type="review.cancel_requested",
                            payload={"cancellation_requested": True},
                        )
                    )
            return True

        result = await self._database.run_transaction(operation)
        await self._publish_events(captured)
        return result

    async def get_execution(self, task_id: str) -> ReviewExecutionRecord | None:
        """Return private executable inputs only to the Worker composition boundary."""

        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(review_tasks).where(review_tasks.c.task_id == task_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        raw_path = row["repository_path"]
        raw_targets = row["target_paths_json"]
        if raw_path is None or raw_targets is None:
            raise RuntimeError("review lacks restart-safe execution inputs")
        selected: list[str] = json.loads(str(row["selected_agent_versions_json"]))
        target_paths: list[str] = json.loads(str(raw_targets))
        repository_path = await asyncio.to_thread(_resolve_path, str(raw_path))
        return ReviewExecutionRecord(
            task_id=str(row["task_id"]),
            repository_path=repository_path,
            repository_realpath_hash=str(row["repository_realpath_hash"]),
            git_common_dir_hash=str(row["git_common_dir_hash"]),
            base_oid=str(row["base_oid"]),
            head_oid=str(row["head_oid"]),
            scope_type=_review_scope_type(json.loads(str(row["scope_json"]))),
            overlay_hash=str(row["overlay_hash"]) if row["overlay_hash"] is not None else None,
            overlay_artifact_ref=(
                str(row["overlay_artifact_ref"])
                if row["overlay_artifact_ref"] is not None
                else None
            ),
            target_paths=tuple(target_paths),
            selected_agent_versions=tuple(selected),
            prompt_locale=str(row["prompt_locale"]),
            status=str(row["status"]),
            cancellation_requested=bool(row["cancellation_requested"]),
        )

    async def list_active_executions(self) -> tuple[ReviewExecutionRecord, ...]:
        """Return every non-terminal execution for startup worktree reconciliation."""

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(review_tasks).where(
                        review_tasks.c.status.not_in(("completed", "partial", "failed", "canceled"))
                    )
                )
            ).mappings().all()

        executions: list[ReviewExecutionRecord] = []
        for row in rows:
            raw_path = row["repository_path"]
            raw_targets = row["target_paths_json"]
            if raw_path is None or raw_targets is None:
                continue
            selected: list[str] = json.loads(str(row["selected_agent_versions_json"]))
            target_paths: list[str] = json.loads(str(raw_targets))
            repository_path = await asyncio.to_thread(_resolve_path, str(raw_path))
            executions.append(
                ReviewExecutionRecord(
                    task_id=str(row["task_id"]),
                    repository_path=repository_path,
                    repository_realpath_hash=str(row["repository_realpath_hash"]),
                    git_common_dir_hash=str(row["git_common_dir_hash"]),
                    base_oid=str(row["base_oid"]),
                    head_oid=str(row["head_oid"]),
                    scope_type=_review_scope_type(json.loads(str(row["scope_json"]))),
                    overlay_hash=(
                        str(row["overlay_hash"])
                        if row["overlay_hash"] is not None
                        else None
                    ),
                    overlay_artifact_ref=(
                        str(row["overlay_artifact_ref"])
                        if row["overlay_artifact_ref"] is not None
                        else None
                    ),
                    target_paths=tuple(target_paths),
                    selected_agent_versions=tuple(selected),
                    prompt_locale=str(row["prompt_locale"]),
                    status=str(row["status"]),
                    cancellation_requested=bool(row["cancellation_requested"]),
                )
            )
        return tuple(executions)

    async def get_status(self, task_id: str) -> str:
        """Return the current durable workflow state."""

        record = await self.get_review(task_id)
        if record is None:
            raise KeyError(task_id)
        return record.status

    async def cancellation_requested(self, task_id: str) -> bool:
        record = await self.get_review(task_id)
        if record is None:
            raise KeyError(task_id)
        return record.cancellation_requested

    async def transition(self, task_id: str, status: str, **values: str) -> None:
        """Move one expected workflow edge and append its event transactionally."""

        predecessors = {
            "provisioning_worktree": "created",
            "snapshotting": "provisioning_worktree",
            "preparing": "snapshotting",
            "reviewing": "preparing",
            "validating": "reviewing",
            "synthesizing": "validating",
            "completed": "synthesizing",
            "partial": "synthesizing",
        }
        expected = predecessors.get(status)
        if expected is None:
            raise InvalidAgentRunStateError("unknown review workflow transition")

        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> None:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(review_tasks)
                    .where(
                        review_tasks.c.task_id == task_id,
                        review_tasks.c.status == expected,
                    )
                    .values(status=status, updated_at=_now(), **values)
                ),
            )
            if result.rowcount != 1:
                current = await session.scalar(
                    select(review_tasks.c.status).where(review_tasks.c.task_id == task_id)
                )
                if current == status:
                    return
                raise InvalidAgentRunStateError("review transition lost its expected state")
            if status in {"completed", "partial"}:
                await session.execute(
                    update(jobs)
                    .where(jobs.c.task_id == task_id, jobs.c.status.in_(("running", "queued")))
                    .values(status=status, finished_at=_now(), updated_at=_now())
                )
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, f"review.{status}", {"status": status}))
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=task_id,
                        event_type=f"review.{status}",
                        payload={"status": status},
                    )
                )

        await self._database.run_transaction(operation)
        await self._publish_events(captured)
        if captured and status in {"completed", "partial"}:
            await self._fire_terminal_hook(task_id, status)

    async def cancel(self, task_id: str) -> None:
        await self._finish_unsuccessfully(task_id, "canceled", "review.canceled", None)

    async def fail(self, task_id: str, error_code: str) -> None:
        await self._finish_unsuccessfully(task_id, "failed", "review.failed", error_code)

    async def _finish_unsuccessfully(
        self,
        task_id: str,
        status: str,
        event_type: str,
        error_code: str | None,
    ) -> None:
        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> None:
            current = await session.scalar(
                select(review_tasks.c.status).where(review_tasks.c.task_id == task_id)
            )
            if current == status:
                return
            if current in {"completed", "partial", "failed", "canceled", None}:
                raise InvalidAgentRunStateError("terminal review cannot finish again")
            await session.execute(
                update(review_tasks)
                .where(review_tasks.c.task_id == task_id)
                .values(status=status, updated_at=_now())
            )
            await session.execute(
                update(jobs)
                .where(jobs.c.task_id == task_id)
                .values(status=status, finished_at=_now(), updated_at=_now())
            )
            event_payload: dict[str, object] = {
                "status": status,
                **({"error_code": error_code} if error_code else {}),
            }
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, event_type, event_payload))
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=task_id,
                        event_type=event_type,
                        payload=event_payload,
                    )
                )

        await self._database.run_transaction(operation)
        await self._publish_events(captured)
        if captured:
            await self._fire_terminal_hook(task_id, status)

    async def interrupt(self, task_id: str) -> None:
        """Persist active RUNNING nodes/jobs as resumable without discarding output."""

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                update(dag_checkpoints)
                .where(
                    dag_checkpoints.c.task_id == task_id,
                    dag_checkpoints.c.status == "running",
                )
                .values(status="pending", updated_at=_now())
            )
            await session.execute(
                update(jobs)
                .where(jobs.c.task_id == task_id, jobs.c.status == "running")
                .values(status="queued", started_at=None, updated_at=_now())
            )

        await self._database.run_transaction(operation)

    async def complete_job(self, task_id: str) -> None:
        """Idempotently close a job whose Review reached a successful terminal state."""

        async def operation(session: AsyncSession) -> None:
            status = await session.scalar(
                select(review_tasks.c.status).where(review_tasks.c.task_id == task_id)
            )
            if status not in {"completed", "partial"}:
                raise InvalidAgentRunStateError("job cannot complete before its review")
            await session.execute(
                update(jobs)
                .where(jobs.c.task_id == task_id, jobs.c.status != status)
                .values(status=status, finished_at=_now(), updated_at=_now())
            )

        await self._database.run_transaction(operation)

    async def request_cancellation(self, task_id: str) -> ReviewRecord | None:
        """Set cancellation intent and append its outbox event in one transaction."""

        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> ReviewRecord | None:
            row = (
                (
                    await session.execute(
                        select(review_tasks).where(review_tasks.c.task_id == task_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            if bool(row["cancellation_requested"]):
                return _review_record(row)
            if str(row["status"]) in {"completed", "partial", "failed", "canceled"}:
                raise InvalidAgentRunStateError("terminal review cannot be canceled")
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(review_tasks)
                    .where(
                        review_tasks.c.task_id == task_id,
                        review_tasks.c.cancellation_requested.is_(False),
                    )
                    .values(cancellation_requested=True, updated_at=_now())
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("review cancellation state changed concurrently")
            event_id = await session.scalar(
                insert(events)
                .values(
                    **_event_values(
                        task_id,
                        "review.cancel_requested",
                        {"cancellation_requested": True},
                    )
                )
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=task_id,
                        event_type="review.cancel_requested",
                        payload={"cancellation_requested": True},
                    )
                )
            updated = (
                (
                    await session.execute(
                        select(review_tasks).where(review_tasks.c.task_id == task_id)
                    )
                )
                .mappings()
                .one()
            )
            return _review_record(updated)

        record = await self._database.run_transaction(operation)
        await self._publish_events(captured)
        return record

    async def recover_after_singleton_restart(self) -> None:
        """Requeue only interrupted jobs/nodes while preserving saved and terminal output."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                update(jobs)
                .where(jobs.c.status == "running")
                .values(status="queued", started_at=None, updated_at=timestamp)
            )
            await session.execute(
                update(dag_checkpoints)
                .where(dag_checkpoints.c.status == "running")
                .values(status="pending", updated_at=timestamp)
            )

        await self._database.run_transaction(operation)

    async def complete_agent_run(
        self,
        task_id: str,
        node_key: str,
        batch: FindingBatch,
    ) -> None:
        """Insert Findings, mark SUCCEEDED, and append its event atomically."""

        timestamp = _now()
        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> None:
            status = await session.scalar(
                select(dag_checkpoints.c.status).where(
                    dag_checkpoints.c.task_id == task_id,
                    dag_checkpoints.c.node_key == node_key,
                )
            )
            if status not in {"output_saved", "validating"}:
                raise InvalidAgentRunStateError("AgentRun is not ready for atomic completion")
            for finding in batch.findings:
                await session.execute(
                    insert(findings).values(
                        finding_id=finding.finding_id,
                        task_id=task_id,
                        node_key=node_key,
                        fingerprint=finding.fingerprint,
                        payload_json=_finding_payload(finding),
                        severity=finding.severity.value,
                        confidence=finding.confidence,
                        path=finding.primary_location.path,
                        start_line=finding.primary_location.start_line,
                        created_at=timestamp,
                    )
                )
            if self._completion_hook is not None:
                await self._completion_hook("after_finding_insert_attempt")
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status.in_(("output_saved", "validating")),
                    )
                    .values(status="succeeded", updated_at=timestamp)
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("AgentRun completion lost its expected state")
            event_payload = {"node_key": node_key, "finding_count": len(batch.findings)}
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, "agent.succeeded", event_payload))
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=task_id,
                        event_type="agent.succeeded",
                        payload=event_payload,
                    )
                )

        await self._database.run_transaction(operation)
        await self._publish_events(captured)

    async def complete_with_findings(
        self,
        task_id: str,
        node_key: str,
        findings_batch: FindingBatch,
    ) -> None:
        """Implement the orchestrator atomic-completion Port."""

        await self.complete_agent_run(task_id, node_key, findings_batch)

    async def list_findings(self, task_id: str) -> tuple[Finding, ...]:
        """Return trusted Findings in stable severity/confidence/path order."""

        severity_order = case(
            (findings.c.severity == "critical", 0),
            (findings.c.severity == "high", 1),
            (findings.c.severity == "medium", 2),
            (findings.c.severity == "low", 3),
            else_=4,
        )
        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(findings.c.payload_json)
                    .where(findings.c.task_id == task_id)
                    .order_by(
                        severity_order,
                        findings.c.confidence.desc(),
                        findings.c.path,
                        findings.c.start_line,
                    )
                )
            ).scalars()
        return tuple(_finding_from_payload(payload) for payload in rows)


class SqlJobQueue:
    """Provide expected-state singleton queue transitions without leases."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, task_id: str) -> JobRecord:
        """Return the durable job for a task."""

        async with self._database.sessions() as session:
            row = (
                (await session.execute(select(jobs).where(jobs.c.task_id == task_id)))
                .mappings()
                .one()
            )
        return JobRecord(task_id=str(row["task_id"]), status=str(row["status"]))

    async def next_queued(self) -> JobRecord | None:
        """Atomically change the oldest queued job to running for the singleton Worker."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> JobRecord | None:
            task_id = await session.scalar(
                select(jobs.c.task_id)
                .where(jobs.c.status == "queued")
                .order_by(jobs.c.created_at, jobs.c.task_id)
                .limit(1)
            )
            if task_id is None:
                return None
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(jobs)
                    .where(jobs.c.task_id == task_id, jobs.c.status == "queued")
                    .values(status="running", started_at=timestamp, updated_at=timestamp)
                ),
            )
            if result.rowcount != 1:
                return None
            return JobRecord(task_id=str(task_id), status="running")

        return await self._database.run_transaction(operation)


class SqlCheckpointStore:
    """Persist deterministic DAG checkpoints with expected-prior-state updates."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def ensure(self, task_id: str, node_key: str, logical_attempt_group: str) -> None:
        """Create one PENDING checkpoint with a stable composite key."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                sqlite_insert(dag_checkpoints)
                .values(
                    task_id=task_id,
                    node_key=node_key,
                    logical_attempt_group=logical_attempt_group,
                    status="pending",
                    execution_attempts=0,
                    validation_attempts=0,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                .on_conflict_do_nothing(
                    index_elements=(dag_checkpoints.c.task_id, dag_checkpoints.c.node_key)
                )
            )

        await self._database.run_transaction(operation)

    async def mark_validating(self, task_id: str, node_key: str) -> None:
        """Move OUTPUT_SAVED to VALIDATING without changing its Artifact identity."""

        async def operation(session: AsyncSession) -> None:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status == "output_saved",
                    )
                    .values(
                        status="validating",
                        validation_attempts=dag_checkpoints.c.validation_attempts + 1,
                        updated_at=_now(),
                    )
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("checkpoint has no saved output")

        await self._database.run_transaction(operation)

    async def mark_running(self, task_id: str, node_key: str) -> None:
        """Move PENDING to RUNNING and increment its execution-attempt count."""

        async def operation(session: AsyncSession) -> None:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status == "pending",
                    )
                    .values(
                        status="running",
                        execution_attempts=dag_checkpoints.c.execution_attempts + 1,
                        updated_at=_now(),
                    )
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("checkpoint is not pending")

        await self._database.run_transaction(operation)

    async def mark_output_saved(
        self,
        task_id: str,
        node_key: str,
        artifact_ref: str,
        artifact_hash: str,
        review_completion_status: AgentReviewCompletionStatus = "complete",
    ) -> None:
        """Move RUNNING to OUTPUT_SAVED with an opaque hash-verified Artifact."""

        if review_completion_status not in {"complete", "incomplete"}:
            raise ValueError("review completion status is invalid")

        async def operation(session: AsyncSession) -> None:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status == "running",
                    )
                    .values(
                        status="output_saved",
                        artifact_ref=artifact_ref,
                        artifact_hash=artifact_hash,
                        review_completion_status=review_completion_status,
                        updated_at=_now(),
                    )
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("checkpoint is not running")

        await self._database.run_transaction(operation)

    async def get(self, task_id: str, node_key: str) -> CheckpointRecord:
        """Return one checkpoint by its stable task/node key."""

        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(dag_checkpoints).where(
                            dag_checkpoints.c.task_id == task_id,
                            dag_checkpoints.c.node_key == node_key,
                        )
                    )
                )
                .mappings()
                .one()
            )
        return CheckpointRecord(
            task_id=str(row["task_id"]),
            node_key=str(row["node_key"]),
            logical_attempt_group=str(row["logical_attempt_group"]),
            status=str(row["status"]),
            execution_attempts=int(row["execution_attempts"]),
            artifact_ref=str(row["artifact_ref"]) if row["artifact_ref"] is not None else None,
            artifact_hash=str(row["artifact_hash"]) if row["artifact_hash"] is not None else None,
            review_completion_status=cast(
                AgentReviewCompletionStatus,
                str(row["review_completion_status"]),
            ),
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            validation_attempts=int(row["validation_attempts"]),
        )


class SqlEventOutbox:
    """Append and query ordered durable events for resumable SSE."""

    def __init__(
        self,
        database: Database,
        *,
        event_bus: InMemoryEventBus | None = None,
    ) -> None:
        self._database = database
        self._event_bus = event_bus

    async def append(self, task_id: str, event_type: str, payload: dict[str, object]) -> None:
        """Append one redacted event in its own transaction."""

        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> None:
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, event_type, payload))
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=task_id,
                        event_type=event_type,
                        payload=payload,
                    )
                )

        await self._database.run_transaction(operation)
        if self._event_bus is not None and captured:
            for event in captured:
                try:
                    await self._event_bus.publish(event)
                except Exception:
                    _LOGGER.warning(
                        "Failed to publish event to bus",
                        extra={"event_type": event.event_type, "task_id": event.task_id},
                        exc_info=True,
                    )

    async def list_after(self, task_id: str, *, after_event_id: int) -> tuple[ReviewEvent, ...]:
        """Return task events strictly after a supplied SSE event ID."""

        async with self._database.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(events)
                        .where(events.c.task_id == task_id, events.c.event_id > after_event_id)
                        .order_by(events.c.event_id)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            ReviewEvent(
                event_id=int(row["event_id"]),
                task_id=str(row["task_id"]),
                event_type=str(row["event_type"]),
                payload=json.loads(str(row["payload_json"])),
            )
            for row in rows
        )


class SqlWorktreeRegistry:
    """Persist worktree ownership metadata while deriving contained paths from data_dir."""

    def __init__(self, database: Database, data_dir: Path) -> None:
        self._database = database
        self._data_dir = data_dir.expanduser().resolve()

    async def register(self, worktree: TaskWorktree) -> None:
        """Insert one authoritative worktree ownership record."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                insert(task_worktrees).values(
                    worktree_id=worktree.worktree_id,
                    task_id=worktree.task_id,
                    owned_path_hash=hashlib.sha256(str(worktree.root).encode()).hexdigest(),
                    common_dir_hash=worktree.repository_common_dir_hash,
                    head_oid=worktree.head_oid,
                    ownership_token_hash=worktree.ownership_token_hash,
                    status="active",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )

        await self._database.run_transaction(operation)

    async def get(self, task_id: str) -> TaskWorktree | None:
        """Return a record with its deterministic contained checkout path."""

        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(task_worktrees).where(task_worktrees.c.task_id == task_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        return self._to_worktree(row) if row is not None else None

    async def remove(self, task_id: str) -> None:
        """Delete one ownership record after scoped Git removal succeeds."""

        from sqlalchemy import delete

        async def operation(session: AsyncSession) -> None:
            await session.execute(delete(task_worktrees).where(task_worktrees.c.task_id == task_id))

        await self._database.run_transaction(operation)

    async def list_all(self) -> tuple[TaskWorktree, ...]:
        """Return all durable ownership records for startup reconciliation."""

        async with self._database.sessions() as session:
            rows = (await session.execute(select(task_worktrees))).mappings().all()
        return tuple(self._to_worktree(row) for row in rows)

    def _to_worktree(self, row: Any) -> TaskWorktree:
        task_id = str(row["task_id"])
        root = self._data_dir / "worktrees" / task_id / "checkout"
        expected_hash = hashlib.sha256(str(root).encode()).hexdigest()
        if expected_hash != str(row["owned_path_hash"]):
            raise ValueError("durable worktree path hash mismatch")
        return TaskWorktree(
            worktree_id=str(row["worktree_id"]),
            task_id=task_id,
            repository_common_dir_hash=str(row["common_dir_hash"]),
            root=root,
            head_oid=str(row["head_oid"]),
            ownership_token_hash=str(row["ownership_token_hash"]),
        )
