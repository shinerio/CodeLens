import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import case, func, insert, select, update
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
from codelens.review.domain.ports import ReviewEvent, ReviewExecutionRecord, ReviewRecord
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.tables import (
    dag_checkpoints,
    events,
    findings,
    jobs,
    review_tasks,
    task_worktrees,
)
from codelens.workspace.domain.models import TaskWorktree


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
    error_code: str | None


def _now() -> datetime:
    return datetime.now(UTC)


def _resolve_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _event_values(task_id: str, event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "task_id": task_id,
        "event_type": event_type,
        "payload_json": _json(payload),
        "created_at": _now(),
    }


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


def _review_record(row: Any) -> ReviewRecord:
    scope: dict[str, object] = json.loads(str(row["scope_json"]))
    selected_agents: list[str] = json.loads(str(row["selected_agent_versions_json"]))
    return ReviewRecord(
        task_id=str(row["task_id"]),
        repository_id=str(row["repository_id"]),
        repository_realpath_hash=str(row["repository_realpath_hash"]),
        git_common_dir_hash=str(row["git_common_dir_hash"]),
        scope_type=str(scope["type"]),
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
        created_at=cast(datetime, row["created_at"]),
        is_deleted=row["deleted_at"] is not None,
    )


class SqlReviewStore:
    """Persist ReviewTask commands and atomic Agent success boundaries."""

    def __init__(
        self,
        database: Database,
        *,
        completion_hook: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._database = database
        self._completion_hook = completion_hook

    async def create_with_job(self, task: ReviewTask) -> None:
        """Insert task, singleton job, and review.created event in one transaction."""

        timestamp = task.created_at

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
            await session.execute(
                insert(events).values(
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
            )

        await self._database.run_transaction(operation)

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
        return tuple(_review_record(row) for row in rows)

    async def soft_delete_review(self, task_id: str) -> bool:
        """Hide one workspace and atomically request cancellation if it is active."""

        terminal_statuses = {"completed", "partial", "failed", "canceled"}

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
            should_request_cancellation = is_active and not bool(
                row["cancellation_requested"]
            )
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
                await session.execute(
                    insert(events).values(
                        **_event_values(
                            task_id,
                            "review.cancel_requested",
                            {"cancellation_requested": True},
                        )
                    )
                )
            return True

        return await self._database.run_transaction(operation)

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
            raise RuntimeError("legacy review lacks restart-safe execution inputs")
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
            overlay_hash=str(row["overlay_hash"]) if row["overlay_hash"] is not None else None,
            overlay_artifact_ref=(
                str(row["overlay_artifact_ref"])
                if row["overlay_artifact_ref"] is not None
                else None
            ),
            target_paths=tuple(target_paths),
            selected_agent_versions=tuple(selected),
            status=str(row["status"]),
            cancellation_requested=bool(row["cancellation_requested"]),
        )

    async def list_active_executions(self) -> tuple[ReviewExecutionRecord, ...]:
        """Return every non-terminal execution for startup worktree reconciliation."""

        async with self._database.sessions() as session:
            task_ids = (
                await session.execute(
                    select(review_tasks.c.task_id).where(
                        review_tasks.c.status.not_in(
                            ("completed", "partial", "failed", "canceled")
                        )
                    )
                )
            ).scalars()
        executions: list[ReviewExecutionRecord] = []
        for task_id in task_ids:
            record = await self.get_execution(str(task_id))
            if record is not None:
                executions.append(record)
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
        }
        expected = predecessors.get(status)
        if expected is None:
            raise InvalidAgentRunStateError("unknown review workflow transition")

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
            if status == "completed":
                await session.execute(
                    update(jobs)
                    .where(jobs.c.task_id == task_id, jobs.c.status.in_(("running", "queued")))
                    .values(status="completed", finished_at=_now(), updated_at=_now())
                )
            await session.execute(
                insert(events).values(
                    **_event_values(task_id, f"review.{status}", {"status": status})
                )
            )

        await self._database.run_transaction(operation)

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
            await session.execute(
                insert(events).values(
                    **_event_values(
                        task_id,
                        event_type,
                        {"status": status, **({"error_code": error_code} if error_code else {})},
                    )
                )
            )

        await self._database.run_transaction(operation)

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
        """Idempotently close a job whose task transition already completed atomically."""

        async def operation(session: AsyncSession) -> None:
            status = await session.scalar(
                select(review_tasks.c.status).where(review_tasks.c.task_id == task_id)
            )
            if status != "completed":
                raise InvalidAgentRunStateError("job cannot complete before its review")
            await session.execute(
                update(jobs)
                .where(jobs.c.task_id == task_id, jobs.c.status != "completed")
                .values(status="completed", finished_at=_now(), updated_at=_now())
            )

        await self._database.run_transaction(operation)

    async def request_cancellation(self, task_id: str) -> ReviewRecord | None:
        """Set cancellation intent and append its outbox event in one transaction."""

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
            await session.execute(
                insert(events).values(
                    **_event_values(
                        task_id,
                        "review.cancel_requested",
                        {"cancellation_requested": True},
                    )
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

        return await self._database.run_transaction(operation)

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
            await session.execute(
                insert(events).values(
                    **_event_values(
                        task_id,
                        "agent.succeeded",
                        {"node_key": node_key, "finding_count": len(batch.findings)},
                    )
                )
            )

        await self._database.run_transaction(operation)

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
                sqlite_insert(dag_checkpoints).values(
                    task_id=task_id,
                    node_key=node_key,
                    logical_attempt_group=logical_attempt_group,
                    status="pending",
                    execution_attempts=0,
                    validation_attempts=0,
                    created_at=timestamp,
                    updated_at=timestamp,
                ).on_conflict_do_nothing(
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

    async def mark_repair_pending(self, task_id: str, node_key: str) -> None:
        """Schedule one repair attempt while retaining the first Artifact reference."""

        async def operation(session: AsyncSession) -> None:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status == "validating",
                        dag_checkpoints.c.validation_attempts == 1,
                    )
                    .values(
                        status="pending",
                        error_code="finding_validation_failed",
                        updated_at=_now(),
                    )
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("checkpoint cannot schedule schema repair")

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
    ) -> None:
        """Move RUNNING to OUTPUT_SAVED with an opaque hash-verified Artifact."""

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
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
            validation_attempts=int(row["validation_attempts"]),
        )


class SqlEventOutbox:
    """Append and query ordered durable events for resumable SSE."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def append(self, task_id: str, event_type: str, payload: dict[str, object]) -> None:
        """Append one redacted event in its own transaction."""

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                insert(events).values(**_event_values(task_id, event_type, payload))
            )

        await self._database.run_transaction(operation)

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
