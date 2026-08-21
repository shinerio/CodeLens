import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import case, delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult, RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codelens.capabilities.domain.models import (
    FrozenAgentExecutionSpec,
    canonical_execution_payload,
)
from codelens.findings.domain.candidates import (
    CandidateFinding,
    CandidateFindingBatch,
    EvidenceStrength,
)
from codelens.findings.domain.clusters import FindingCluster
from codelens.findings.domain.dedup import DedupDecision, DedupOutcome
from codelens.findings.domain.existing_findings import ExistingFindingSet
from codelens.findings.domain.models import (
    ChangeOrigin,
    Evidence,
    Finding,
    FindingDisposition,
    FindingSeverity,
    RuleReference,
    SourceLocation,
)
from codelens.findings.domain.remediation import (
    RemediationDecision,
    RemediationDecisionSource,
    RemediationOutcome,
)
from codelens.findings.domain.verdict import VerdictDecision, VerdictOutcome, verdict_decision_id
from codelens.review.domain.agent_run import AgentRun, InvalidAgentRunStateError
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import (
    MAX_RECENT_REPOSITORY_LIMIT,
    MIN_RECENT_REPOSITORY_LIMIT,
    AgentExecutionSpecRecord,
    AgentReviewCompletionStatus,
    ArtifactIdentity,
    RecentRepositoryRecord,
    ReviewEvent,
    ReviewExecutionRecord,
    ReviewPlanRecord,
    ReviewRecord,
)
from codelens.review.domain.review_plan import ReviewPlan, ReviewPlanNode
from codelens.review.domain.review_profile import (
    ReviewProfile,
    ReviewProfileDefaultRequiredError,
    ReviewProfileNotFoundError,
    ReviewProfileRevisionConflictError,
)
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    FixedReviewerSelection,
    ReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.event_bus import InMemoryEventBus
from codelens.review.infrastructure.tables import (
    agent_execution_skill_artifacts,
    agent_execution_specs,
    artifacts,
    candidate_findings,
    dag_checkpoints,
    dedup_decisions,
    events,
    finding_cluster_candidates,
    finding_clusters,
    findings,
    jobs,
    recent_repositories,
    recent_repository_settings,
    remediation_decisions,
    review_file_scopes,
    review_plans,
    review_profiles,
    review_tasks,
    task_worktrees,
    verdict_decision_clusters,
    verdict_decisions,
)
from codelens.workspace.domain.models import ReviewScopeType, TaskWorktree
from codelens.workspace.domain.review_file_scope import ReviewFileScope

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
    run_id: str | None = None
    node_role: str | None = None
    agent_version: str | None = None
    pass_index: int | None = None
    shard_id: str | None = None
    capability_fingerprint: str | None = None
    result_summary: dict[str, object] | None = None


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


def _selection_json(selection: ReviewerSelection) -> str:
    if isinstance(selection, AdaptiveReviewerSelection):
        return _json({"mode": "adaptive"})
    return _json({"mode": "fixed", "reviewer_versions": list(selection.reviewer_versions)})


def _selection_from_json(payload: str) -> ReviewerSelection:
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("mode") not in {"adaptive", "fixed"}:
        raise RuntimeError("Review profile has invalid reviewer selection")
    if value["mode"] == "adaptive":
        return AdaptiveReviewerSelection()
    versions = value.get("reviewer_versions")
    if not isinstance(versions, list) or not all(isinstance(item, str) for item in versions):
        raise RuntimeError("Review profile has invalid fixed reviewer selection")
    return FixedReviewerSelection(tuple(versions))


def _review_profile_from_row(row: RowMapping) -> ReviewProfile:
    return ReviewProfile(
        profile_id=str(row["profile_id"]),
        revision=int(row["revision"]),
        name=str(row["name"]),
        is_default=bool(row["is_default"]),
        reviewer_selection=_selection_from_json(str(row["reviewer_selection_json"])),
        created_at=_as_utc(cast(datetime, row["created_at"])),
        updated_at=_as_utc(cast(datetime, row["updated_at"])),
    )


def _review_profile_values(profile: ReviewProfile) -> dict[str, object]:
    return {
        "profile_id": profile.profile_id,
        "revision": profile.revision,
        "name": profile.name,
        "is_default": profile.is_default,
        "reviewer_selection_json": _selection_json(profile.reviewer_selection),
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _review_scope_type(scope: dict[str, object]) -> ReviewScopeType:
    value = scope.get("type")
    if value not in {"branch", "commit", "uncommitted", "full"}:
        raise RuntimeError("review has an invalid persisted scope type")
    return value


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
    source_reviewer_references = tuple(value.pop("source_reviewer_references"))
    return Finding(
        **value,
        severity=severity,
        disposition=disposition,
        change_origin=change_origin,
        primary_location=primary,
        related_locations=related,
        evidence=evidence_items,
        rule_sources=rules,
        source_reviewer_references=source_reviewer_references,
    )


def _candidate_payload(candidate: CandidateFinding) -> str:
    return _json(asdict(candidate))


def _candidate_from_payload(payload: str) -> CandidateFinding:
    value: dict[str, Any] = json.loads(payload)
    primary = SourceLocation(**value.pop("primary_location"))
    related = tuple(SourceLocation(**item) for item in value.pop("related_locations"))
    severity = FindingSeverity(value.pop("severity"))
    evidence_strength = EvidenceStrength(value.pop("evidence_strength"))
    evidence_hashes = tuple(value.pop("evidence_hashes"))
    return CandidateFinding(
        **value,
        severity=severity,
        evidence_strength=evidence_strength,
        primary_location=primary,
        related_locations=related,
        evidence_hashes=evidence_hashes,
    )


def _verdict_payload(decision: VerdictDecision) -> str:
    """Canonicalize a Verdict decision for durable, conflict-detected storage."""

    return _json(
        {
            "cluster_ids": list(decision.cluster_ids),
            "outcome": decision.outcome.value,
            "path": decision.path,
            "side": decision.side,
            "existing_code": decision.existing_code,
            "title": decision.title,
            "content": decision.content,
            "recommendation": decision.recommendation,
            "category": decision.category,
            "severity": decision.severity.value if decision.severity is not None else None,
            "primary_dimension": decision.primary_dimension,
            "evidence_strength": (
                decision.evidence_strength.value if decision.evidence_strength is not None else None
            ),
            "primary_location": (
                asdict(decision.primary_location) if decision.primary_location is not None else None
            ),
            "changed_hunk_id": decision.changed_hunk_id,
        }
    )


def _review_scope_refs(scope: dict[str, object]) -> tuple[str | None, str | None]:
    scope_type = scope.get("type")
    if scope_type == "branch":
        return str(scope.get("base_ref", "")) or None, str(scope.get("target_ref", "")) or None
    if scope_type == "commit":
        return None, str(scope.get("target_ref", "")) or None
    if scope_type == "full":
        return None, str(scope.get("target_ref", "")) or None
    return None, None


def _review_record(row: Any, finding_count: int = 0) -> ReviewRecord:
    scope: dict[str, object] = json.loads(str(row["scope_json"]))
    selected_agents: list[str] = json.loads(str(row["selected_agent_versions_json"]))
    selection_value = json.loads(str(row["selection_request_json"]))
    selection: ReviewerSelection = (
        AdaptiveReviewerSelection()
        if selection_value["mode"] == "adaptive"
        else FixedReviewerSelection(tuple(selection_value["reviewer_versions"]))
    )
    profile = ReviewProfileSnapshot(
        selection,
        str(row["profile_source_id"]) if row["profile_source_id"] is not None else None,
        int(row["profile_source_revision"]) if row["profile_source_revision"] is not None else None,
    )
    external_context = None
    if row["external_context_json"] is not None:
        external_context = json.loads(str(row["external_context_json"]))
    existing_findings_json = str(row["existing_findings_json"])
    existing_findings_hash = str(row["existing_findings_hash"])
    existing_findings = ExistingFindingSet.from_json(existing_findings_json, existing_findings_hash)
    base_ref, target_ref = _review_scope_refs(scope)
    return ReviewRecord(
        task_id=str(row["task_id"]),
        repository_id=str(row["repository_id"]),
        repository_realpath_hash=str(row["repository_realpath_hash"]),
        git_common_dir_hash=str(row["git_common_dir_hash"]),
        scope_type=_review_scope_type(scope),
        base_oid=str(row["base_oid"]),
        head_oid=str(row["head_oid"]),
        base_ref=base_ref,
        target_ref=target_ref,
        selected_agent_versions=tuple(selected_agents),
        review_profile=profile,
        planning_context_json=str(row["planning_context_json"])
        if row["planning_context_json"] is not None
        else None,
        planning_context_hash=str(row["planning_context_hash"])
        if row["planning_context_hash"] is not None
        else None,
        trigger_source=str(row["trigger_source"]) if row["trigger_source"] is not None else None,
        supersede_policy=str(row["supersede_policy"])
        if row["supersede_policy"] is not None
        else None,
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
        has_partial_coverage=bool(row["has_partial_coverage"]),
        existing_finding_count=len(existing_findings.items),
    )


class SqlReviewProfileRepository:
    """Persist Review profiles and enforce exactly one default per transaction."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_review_profiles(self) -> tuple[ReviewProfile, ...]:
        """Return the default first, then stable profile identities."""

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(review_profiles).order_by(
                        review_profiles.c.is_default.desc(),
                        review_profiles.c.profile_id.asc(),
                    )
                )
            ).mappings()
            return tuple(_review_profile_from_row(row) for row in rows)

    async def create_review_profile(self, profile: ReviewProfile) -> ReviewProfile:
        """Insert a profile, replacing a prior default before the transaction commits."""

        async def operation(session: AsyncSession) -> ReviewProfile:
            default_count = await session.scalar(
                select(func.count())
                .select_from(review_profiles)
                .where(review_profiles.c.is_default.is_(True))
            )
            if profile.is_default:
                await session.execute(
                    update(review_profiles)
                    .where(review_profiles.c.is_default.is_(True))
                    .values(
                        is_default=False,
                        revision=review_profiles.c.revision + 1,
                        updated_at=profile.updated_at,
                    )
                )
            elif not default_count:
                raise ReviewProfileDefaultRequiredError(
                    "a default profile must exist before creating a non-default profile"
                )
            await session.execute(insert(review_profiles).values(**_review_profile_values(profile)))
            return profile

        return await self._database.run_transaction(operation)

    async def _load_for_update(self, session: AsyncSession, profile_id: str) -> ReviewProfile:
        row = (
            (
                await session.execute(
                    select(review_profiles).where(review_profiles.c.profile_id == profile_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ReviewProfileNotFoundError("review profile does not exist")
        return _review_profile_from_row(row)

    async def update_review_profile(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        name: str,
        is_default: bool,
        reviewer_selection: ReviewerSelection,
        updated_at: datetime,
    ) -> ReviewProfile:
        """Replace a profile and its default membership in one optimistic transaction."""

        async def operation(session: AsyncSession) -> ReviewProfile:
            current = await self._load_for_update(session, profile_id)
            updated = current.update(
                expected_revision=expected_revision,
                name=name,
                is_default=is_default,
                reviewer_selection=reviewer_selection,
                updated_at=updated_at,
            )
            if current.is_default and not updated.is_default:
                raise ReviewProfileDefaultRequiredError("the default profile cannot be unset")
            if updated.is_default and not current.is_default:
                await session.execute(
                    update(review_profiles)
                    .where(review_profiles.c.is_default.is_(True))
                    .values(
                        is_default=False,
                        revision=review_profiles.c.revision + 1,
                        updated_at=updated_at,
                    )
                )
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(review_profiles)
                    .where(
                        review_profiles.c.profile_id == profile_id,
                        review_profiles.c.revision == expected_revision,
                    )
                    .values(**_review_profile_values(updated))
                ),
            )
            if result.rowcount != 1:
                raise ReviewProfileRevisionConflictError("review profile revision conflict")
            return updated

        return await self._database.run_transaction(operation)

    async def copy_review_profile(
        self,
        profile_id: str,
        *,
        new_profile_id: str,
        name: str,
        created_at: datetime,
    ) -> ReviewProfile:
        """Copy only strategy values into an independent non-default aggregate."""

        async def operation(session: AsyncSession) -> ReviewProfile:
            source = await self._load_for_update(session, profile_id)
            copied = ReviewProfile.create(
                profile_id=new_profile_id,
                name=name,
                is_default=False,
                reviewer_selection=source.reviewer_selection,
                created_at=created_at,
            )
            await session.execute(insert(review_profiles).values(**_review_profile_values(copied)))
            return copied

        return await self._database.run_transaction(operation)

    async def delete_review_profile(self, profile_id: str) -> None:
        """Delete a non-default profile while retaining the current default."""

        async def operation(session: AsyncSession) -> None:
            profile = await self._load_for_update(session, profile_id)
            if profile.is_default:
                raise ReviewProfileDefaultRequiredError("the default profile cannot be deleted")
            await session.execute(
                delete(review_profiles).where(review_profiles.c.profile_id == profile_id)
            )

        await self._database.run_transaction(operation)

    async def set_default_review_profile(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        updated_at: datetime,
    ) -> ReviewProfile:
        """Promote one profile and demote the old default atomically."""

        async def operation(session: AsyncSession) -> ReviewProfile:
            current = await self._load_for_update(session, profile_id)
            updated = current.update(
                expected_revision=expected_revision,
                name=current.name,
                is_default=True,
                reviewer_selection=current.reviewer_selection,
                updated_at=updated_at,
            )
            await session.execute(
                update(review_profiles)
                .where(
                    review_profiles.c.is_default.is_(True),
                    review_profiles.c.profile_id != profile_id,
                )
                .values(
                    is_default=False,
                    revision=review_profiles.c.revision + 1,
                    updated_at=updated_at,
                )
            )
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(review_profiles)
                    .where(
                        review_profiles.c.profile_id == profile_id,
                        review_profiles.c.revision == expected_revision,
                    )
                    .values(**_review_profile_values(updated))
                ),
            )
            if result.rowcount != 1:
                raise ReviewProfileRevisionConflictError("review profile revision conflict")
            return updated

        return await self._database.run_transaction(operation)


class SqlReviewPlanStore:
    """Persist one canonical, create-once Review Plan per task."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(
        self,
        plan: ReviewPlan,
        *,
        catalog_version: str,
        capability_fingerprint: str,
    ) -> ReviewPlanRecord:
        """Idempotently freeze a plan, rejecting identity-preserving mutation."""

        if len(capability_fingerprint) != 64:
            raise ValueError("Capability fingerprint must be SHA-256")
        timestamp = _now()

        async def operation(session: AsyncSession) -> None:
            existing = await session.scalar(
                select(review_plans.c.task_id).where(review_plans.c.task_id == plan.task_id)
            )
            await session.execute(
                sqlite_insert(review_plans)
                .values(
                    task_id=plan.task_id,
                    plan_json=plan.canonical_json(),
                    plan_hash=plan.plan_hash,
                    catalog_version=catalog_version,
                    capability_fingerprint=capability_fingerprint,
                    created_at=timestamp,
                )
                .on_conflict_do_nothing(index_elements=(review_plans.c.task_id,))
            )
            if existing is None:
                await session.execute(
                    insert(events).values(
                        **_event_values(
                            plan.task_id,
                            "review.plan_created.v2",
                            {"plan_hash": plan.plan_hash},
                        )
                    )
                )

        await self._database.run_transaction(operation)
        record = await self.get(plan.task_id)
        if record is None:
            raise RuntimeError("persisted Review Plan could not be reloaded")
        if (
            record.plan != plan
            or record.catalog_version != catalog_version
            or record.capability_fingerprint != capability_fingerprint
        ):
            raise ValueError("Review Plan already exists with different frozen inputs")
        return record

    async def get(self, task_id: str) -> ReviewPlanRecord | None:
        """Load a plan only after recomputing its canonical hash."""

        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(review_plans).where(review_plans.c.task_id == task_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        plan = ReviewPlan.from_json(str(row["plan_json"]), str(row["plan_hash"]))
        return ReviewPlanRecord(
            plan=plan,
            catalog_version=str(row["catalog_version"]),
            capability_fingerprint=str(row["capability_fingerprint"]),
            created_at=_as_utc(cast(datetime, row["created_at"])),
        )


class SqlAgentExecutionSpecStore:
    """Persist safe execution metadata and verify every referenced Artifact hash."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(
        self,
        *,
        task_id: str,
        logical_node_id: str,
        execution_spec: FrozenAgentExecutionSpec,
        prompt_artifact_ref: str,
        prompt_artifact_hash: str,
        skill_artifacts: tuple[ArtifactIdentity, ...],
    ) -> AgentExecutionSpecRecord:
        """Create one immutable spec without copying prompt or Skill bodies into SQLite."""

        spec_json = canonical_execution_payload(
            execution_spec.agent,
            execution_spec.capability_profile,
            execution_spec.skill_policy,
            execution_spec.prompt_content_hash,
            execution_spec.skills,
            execution_spec.execution_limits,
        ).decode("utf-8")
        if hashlib.sha256(spec_json.encode()).hexdigest() != execution_spec.fingerprint:
            raise ValueError("execution spec fingerprint mismatch")
        if prompt_artifact_hash != execution_spec.prompt_content_hash:
            raise ValueError("prompt Artifact hash does not match the frozen spec")
        if tuple(item.content_hash for item in skill_artifacts) != tuple(
            skill.content_hash for skill in execution_spec.skills
        ):
            raise ValueError("Skill Artifact hashes do not match the frozen spec")
        timestamp = _now()
        expected_identity = (
            spec_json,
            execution_spec.fingerprint,
            prompt_artifact_ref,
            prompt_artifact_hash,
            skill_artifacts,
        )

        async def operation(session: AsyncSession) -> None:
            await self._verify_artifacts(
                session,
                (ArtifactIdentity(prompt_artifact_ref, prompt_artifact_hash), *skill_artifacts),
            )
            insert_result = cast(
                CursorResult[Any],
                await session.execute(
                    sqlite_insert(agent_execution_specs)
                    .values(
                        task_id=task_id,
                        logical_node_id=logical_node_id,
                        spec_json=spec_json,
                        fingerprint=execution_spec.fingerprint,
                        prompt_artifact_ref=prompt_artifact_ref,
                        prompt_artifact_hash=prompt_artifact_hash,
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(
                            agent_execution_specs.c.task_id,
                            agent_execution_specs.c.logical_node_id,
                        )
                    )
                ),
            )
            if insert_result.rowcount == 0:
                existing_row = (
                    (
                        await session.execute(
                            select(agent_execution_specs).where(
                                agent_execution_specs.c.task_id == task_id,
                                agent_execution_specs.c.logical_node_id == logical_node_id,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                existing = await self._record(session, existing_row)
                actual_identity = (
                    existing.spec_json,
                    existing.fingerprint,
                    existing.prompt_artifact_ref,
                    existing.prompt_artifact_hash,
                    existing.skill_artifacts,
                )
                if actual_identity != expected_identity:
                    raise ValueError("execution spec already exists with different frozen inputs")
                return
            for ordinal, artifact in enumerate(skill_artifacts):
                await session.execute(
                    sqlite_insert(agent_execution_skill_artifacts)
                    .values(
                        task_id=task_id,
                        logical_node_id=logical_node_id,
                        ordinal=ordinal,
                        artifact_ref=artifact.reference,
                        artifact_hash=artifact.content_hash,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(
                            agent_execution_skill_artifacts.c.task_id,
                            agent_execution_skill_artifacts.c.logical_node_id,
                            agent_execution_skill_artifacts.c.ordinal,
                        )
                    )
                )

        await self._database.run_transaction(operation)
        record = await self.get(task_id, logical_node_id)
        if record is None:
            raise RuntimeError("persisted execution spec could not be reloaded")
        actual = (
            record.spec_json,
            record.fingerprint,
            record.prompt_artifact_ref,
            record.prompt_artifact_hash,
            record.skill_artifacts,
        )
        if actual != expected_identity:
            raise ValueError("execution spec already exists with different frozen inputs")
        return record

    async def get(self, task_id: str, logical_node_id: str) -> AgentExecutionSpecRecord | None:
        """Load safe metadata and fail closed if any Artifact identity changed."""

        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(agent_execution_specs).where(
                            agent_execution_specs.c.task_id == task_id,
                            agent_execution_specs.c.logical_node_id == logical_node_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            return await self._record(session, row)

    async def list_for_task(self, task_id: str) -> tuple[AgentExecutionSpecRecord, ...]:
        """Return a task's immutable node specs in logical-node order."""

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(agent_execution_specs)
                    .where(agent_execution_specs.c.task_id == task_id)
                    .order_by(agent_execution_specs.c.logical_node_id)
                )
            ).mappings()
            return tuple([await self._record(session, row) for row in rows])

    @staticmethod
    async def _verify_artifacts(
        session: AsyncSession, expected: tuple[ArtifactIdentity, ...]
    ) -> None:
        if not expected:
            return
        references = tuple(item.reference for item in expected)
        if len(references) != len(set(references)):
            raise ValueError("execution spec contains duplicate Artifact references")
        rows = (
            await session.execute(select(artifacts).where(artifacts.c.reference.in_(references)))
        ).mappings()
        actual = {str(row["reference"]): str(row["content_hash"]) for row in rows}
        if actual != {item.reference: item.content_hash for item in expected}:
            raise ValueError("execution spec Artifact hash mismatch")

    async def _record(self, session: AsyncSession, row: RowMapping) -> AgentExecutionSpecRecord:
        spec_json = str(row["spec_json"])
        fingerprint = str(row["fingerprint"])
        if hashlib.sha256(spec_json.encode()).hexdigest() != fingerprint:
            raise ValueError("persisted execution spec fingerprint mismatch")
        skill_rows = (
            await session.execute(
                select(agent_execution_skill_artifacts)
                .where(
                    agent_execution_skill_artifacts.c.task_id == row["task_id"],
                    agent_execution_skill_artifacts.c.logical_node_id == row["logical_node_id"],
                )
                .order_by(agent_execution_skill_artifacts.c.ordinal)
            )
        ).mappings()
        skills = tuple(
            ArtifactIdentity(str(item["artifact_ref"]), str(item["artifact_hash"]))
            for item in skill_rows
        )
        prompt = ArtifactIdentity(str(row["prompt_artifact_ref"]), str(row["prompt_artifact_hash"]))
        await self._verify_artifacts(session, (prompt, *skills))
        payload = json.loads(spec_json)
        if payload.get("prompt_content_hash") != prompt.content_hash:
            raise ValueError("persisted prompt Artifact does not match execution spec")
        expected_skill_hashes = tuple(
            str(item["content_hash"]) for item in payload.get("skills", ())
        )
        if expected_skill_hashes != tuple(item.content_hash for item in skills):
            raise ValueError("persisted Skill Artifacts do not match execution spec")
        return AgentExecutionSpecRecord(
            task_id=str(row["task_id"]),
            logical_node_id=str(row["logical_node_id"]),
            spec_json=spec_json,
            fingerprint=fingerprint,
            prompt_artifact_ref=prompt.reference,
            prompt_artifact_hash=prompt.content_hash,
            skill_artifacts=skills,
            created_at=_as_utc(cast(datetime, row["created_at"])),
        )


class SqlCandidateFindingStore:
    """Read validated Candidate audit records without exposing storage rows."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_for_task(self, task_id: str) -> tuple[CandidateFinding, ...]:
        """Return Candidates in deterministic identity order."""

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(candidate_findings.c.payload_json)
                    .where(candidate_findings.c.task_id == task_id)
                    .order_by(candidate_findings.c.candidate_id)
                )
            ).scalars()
            return tuple(_candidate_from_payload(str(payload)) for payload in rows)


class SqlVerdictStore:
    """Persist normalized cluster membership and immutable Final Verifier decisions."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def save_clusters(
        self,
        task_id: str,
        snapshot_id: str,
        clusters: tuple[FindingCluster, ...],
    ) -> None:
        """Persist a deterministic Candidate partition with normalized membership."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> None:
            for cluster in clusters:
                payload = _json(
                    {
                        "candidate_ids": list(cluster.candidate_ids),
                        "canonical_candidate_id": cluster.canonical_candidate_id,
                        "title": cluster.title,
                        "category": cluster.category,
                        "severity": cluster.severity.value,
                        "content": cluster.content,
                        "recommendation": cluster.recommendation,
                        "primary_dimension": cluster.primary_dimension,
                        "evidence_strength": cluster.evidence_strength.value,
                    }
                )
                await session.execute(
                    sqlite_insert(finding_clusters)
                    .values(
                        cluster_id=cluster.cluster_id,
                        task_id=task_id,
                        snapshot_id=snapshot_id,
                        cluster_key=cluster.cluster_id,
                        payload_json=payload,
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(index_elements=(finding_clusters.c.cluster_id,))
                )
                stored = (
                    (
                        await session.execute(
                            select(finding_clusters).where(
                                finding_clusters.c.cluster_id == cluster.cluster_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                if (
                    str(stored["task_id"]) != task_id
                    or str(stored["snapshot_id"]) != snapshot_id
                    or str(stored["payload_json"]) != payload
                ):
                    raise ValueError("Finding cluster identity conflicts with persisted content")
                candidate_rows = (
                    await session.execute(
                        select(
                            candidate_findings.c.candidate_id,
                            candidate_findings.c.task_id,
                        ).where(candidate_findings.c.candidate_id.in_(cluster.candidate_ids))
                    )
                ).all()
                if {str(row.candidate_id) for row in candidate_rows} != set(
                    cluster.candidate_ids
                ) or any(str(row.task_id) != task_id for row in candidate_rows):
                    raise ValueError("Finding cluster references an unknown Candidate")
                existing_memberships = (
                    await session.execute(
                        select(
                            finding_cluster_candidates.c.candidate_id,
                            finding_cluster_candidates.c.cluster_id,
                        ).where(
                            finding_cluster_candidates.c.candidate_id.in_(cluster.candidate_ids)
                        )
                    )
                ).all()
                if any(str(row.cluster_id) != cluster.cluster_id for row in existing_memberships):
                    raise ValueError("Candidate already belongs to another cluster")
                for ordinal, candidate_id in enumerate(cluster.candidate_ids):
                    await session.execute(
                        sqlite_insert(finding_cluster_candidates)
                        .values(
                            cluster_id=cluster.cluster_id,
                            candidate_id=candidate_id,
                            ordinal=ordinal,
                        )
                        .on_conflict_do_nothing(
                            index_elements=(
                                finding_cluster_candidates.c.cluster_id,
                                finding_cluster_candidates.c.candidate_id,
                            )
                        )
                    )

        await self._database.run_transaction(operation)

    async def list_clusters(self, task_id: str) -> tuple[FindingCluster, ...]:
        """Rehydrate clusters from normalized membership order."""

        from codelens.findings.domain.candidates import EvidenceStrength

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        finding_clusters.c.cluster_id,
                        finding_clusters.c.payload_json,
                    )
                    .where(finding_clusters.c.task_id == task_id)
                    .order_by(finding_clusters.c.cluster_id)
                )
            ).all()
            result: list[FindingCluster] = []
            for row in rows:
                members = (
                    await session.execute(
                        select(finding_cluster_candidates.c.candidate_id)
                        .where(finding_cluster_candidates.c.cluster_id == row.cluster_id)
                        .order_by(finding_cluster_candidates.c.ordinal)
                    )
                ).scalars()
                value = json.loads(str(row.payload_json))
                result.append(
                    FindingCluster(
                        cluster_id=str(row.cluster_id),
                        candidate_ids=tuple(str(member) for member in members),
                        canonical_candidate_id=str(value["canonical_candidate_id"]),
                        title=str(value["title"]),
                        category=str(value["category"]),
                        severity=FindingSeverity(str(value["severity"])),
                        content=str(value["content"]),
                        recommendation=str(value["recommendation"]),
                        primary_dimension=str(value["primary_dimension"]),
                        evidence_strength=EvidenceStrength(str(value["evidence_strength"])),
                    )
                )
            return tuple(result)

    async def save_decisions(
        self,
        task_id: str,
        decisions: tuple[VerdictDecision, ...],
    ) -> None:
        """Persist one immutable verdict decision per cluster group."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> None:
            for decision in decisions:
                payload = _verdict_payload(decision)
                decision_id = verdict_decision_id(task_id, decision.cluster_ids)
                await session.execute(
                    sqlite_insert(verdict_decisions)
                    .values(
                        verdict_decision_id=decision_id,
                        task_id=task_id,
                        verifier_run_id="",
                        outcome=decision.outcome.value,
                        payload_json=payload,
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(verdict_decisions.c.verdict_decision_id,)
                    )
                )
                for ordinal, cluster_id in enumerate(decision.cluster_ids):
                    await session.execute(
                        sqlite_insert(verdict_decision_clusters)
                        .values(
                            verdict_decision_id=decision_id,
                            cluster_id=cluster_id,
                            ordinal=ordinal,
                        )
                        .on_conflict_do_nothing(
                            index_elements=(
                                verdict_decision_clusters.c.verdict_decision_id,
                                verdict_decision_clusters.c.cluster_id,
                            )
                        )
                    )
                stored = await session.scalar(
                    select(verdict_decisions.c.payload_json).where(
                        verdict_decisions.c.verdict_decision_id == decision_id
                    )
                )
                if str(stored) != payload:
                    raise ValueError("Verdict decision conflicts with persisted content")

        await self._database.run_transaction(operation)

    async def list_decisions(self, task_id: str) -> tuple[VerdictDecision, ...]:
        """Return persisted verdict decisions in stable order."""

        from codelens.findings.domain.candidates import EvidenceStrength

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(verdict_decisions.c.payload_json)
                    .where(verdict_decisions.c.task_id == task_id)
                    .order_by(verdict_decisions.c.verdict_decision_id)
                )
            ).scalars()
            result: list[VerdictDecision] = []
            for payload in rows:
                value = json.loads(str(payload))
                result.append(
                    VerdictDecision(
                        cluster_ids=tuple(str(item) for item in value["cluster_ids"]),
                        outcome=VerdictOutcome(str(value["outcome"])),
                        path=value.get("path"),
                        side=value.get("side"),
                        existing_code=value.get("existing_code"),
                        title=value.get("title"),
                        content=value.get("content"),
                        recommendation=value.get("recommendation"),
                        category=value.get("category"),
                        severity=(
                            FindingSeverity(str(value["severity"]))
                            if value.get("severity") is not None
                            else None
                        ),
                        primary_dimension=value.get("primary_dimension"),
                        evidence_strength=(
                            EvidenceStrength(str(value["evidence_strength"]))
                            if value.get("evidence_strength") is not None
                            else None
                        ),
                        primary_location=(
                            SourceLocation(**value["primary_location"])
                            if value.get("primary_location") is not None
                            else None
                        ),
                        changed_hunk_id=value.get("changed_hunk_id"),
                    )
                )
            return tuple(result)


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

    async def record_empty_review_scope(self, task_id: str) -> None:
        """Append the durable empty-scope fact once across retries and recovery."""

        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> None:
            existing = await session.scalar(
                select(events.c.event_id).where(
                    events.c.task_id == task_id,
                    events.c.event_type == "review.scope_empty.v2",
                )
            )
            if existing is not None:
                return
            payload: dict[str, object] = {"reason_code": "review_scope_empty"}
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, "review.scope_empty.v2", payload))
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=task_id,
                        event_type="review.scope_empty.v2",
                        payload=payload,
                    )
                )

        await self._database.run_transaction(operation)
        await self._publish_events(captured)

    async def get_review_file_scope(self, task_id: str) -> ReviewFileScope | None:
        """Load and hash-verify the immutable scope resolved for one task."""

        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(review_file_scopes).where(review_file_scopes.c.task_id == task_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return ReviewFileScope.from_json(str(row["scope_json"]), str(row["scope_hash"]))

    async def save_review_file_scope(self, task_id: str, scope: ReviewFileScope) -> None:
        """Idempotently persist the first canonical scope and reject divergence."""

        async with self._database.sessions.begin() as session:
            await session.execute(
                sqlite_insert(review_file_scopes)
                .values(
                    task_id=task_id,
                    scope_json=scope.canonical_json(),
                    scope_hash=scope.scope_hash,
                    created_at=_now(),
                )
                .on_conflict_do_nothing(index_elements=[review_file_scopes.c.task_id])
            )
        persisted = await self.get_review_file_scope(task_id)
        if persisted != scope:
            raise ValueError("Review file scope already exists with different content")

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
            selection = task.review_profile.reviewer_selection
            selection_payload: dict[str, object] = {"mode": selection.mode}
            if isinstance(selection, FixedReviewerSelection):
                selection_payload["reviewer_versions"] = list(selection.reviewer_versions)
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
                    candidate_paths_json=_json(task.candidate_paths),
                    file_exclusion_policy_json=task.file_exclusion_policy_json,
                    file_exclusion_policy_hash=task.file_exclusion_policy_hash,
                    status=task.status.value,
                    selected_agent_versions_json=_json(task.selected_agent_versions),
                    selection_request_json=_json(selection_payload),
                    profile_source_id=task.review_profile.source_profile_id,
                    profile_source_revision=task.review_profile.source_profile_revision,
                    trigger_source=task.trigger_source,
                    supersede_policy=task.supersede_policy,
                    idempotency_key=task.idempotency_key,
                    trigger_slot_key=task.trigger_slot_key,
                    planning_context_json=task.planning_context_json,
                    planning_context_hash=task.planning_context_hash,
                    prompt_locale=task.prompt_locale,
                    external_context_json=(
                        _json(task.external_context) if task.external_context else None
                    ),
                    existing_findings_json=task.existing_findings_json,
                    existing_findings_hash=task.existing_findings_hash,
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
                        "review.created.v2",
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
                        event_type="review.created.v2",
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

    async def create_triggered_with_job(self, task: ReviewTask) -> tuple[ReviewRecord, bool]:
        """Commit idempotency, slot superseding/cancel intent, job, and outbox atomically."""

        if task.idempotency_key is None or task.trigger_slot_key is None:
            raise ValueError("triggered reviews require durable identity keys")
        captured: list[ReviewEvent] = []

        async def operation(session: AsyncSession) -> tuple[ReviewRecord, bool]:
            # Database.run_transaction may replay this callback after SQLITE_BUSY.
            # Keep only events produced by the attempt that eventually commits.
            captured.clear()
            duplicate = (
                (
                    await session.execute(
                        select(review_tasks).where(
                            review_tasks.c.idempotency_key == task.idempotency_key
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if duplicate is not None:
                return _review_record(duplicate), False
            if task.supersede_policy == "latest_snapshot":
                active = [
                    "created",
                    "provisioning_worktree",
                    "snapshotting",
                    "preparing",
                    "planning",
                    "reviewing",
                    "resolving",
                    "verifying",
                    "validating",
                    "synthesizing",
                ]
                older = (
                    (
                        await session.execute(
                            select(
                                review_tasks.c.task_id,
                                review_tasks.c.status,
                                jobs.c.status.label("job_status"),
                            )
                            .join(jobs, jobs.c.task_id == review_tasks.c.task_id)
                            .where(
                                review_tasks.c.trigger_slot_key == task.trigger_slot_key,
                                review_tasks.c.deleted_at.is_(None),
                                review_tasks.c.status.in_(active),
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                for row in older:
                    older_id = str(row["task_id"])
                    is_queued = (
                        str(row["status"]) == "created" and str(row["job_status"]) == "queued"
                    )
                    if is_queued:
                        await session.execute(
                            update(review_tasks)
                            .where(review_tasks.c.task_id == older_id)
                            .values(status="superseded", updated_at=task.created_at)
                        )
                        await session.execute(
                            update(jobs)
                            .where(jobs.c.task_id == older_id)
                            .values(
                                status="superseded",
                                finished_at=task.created_at,
                                updated_at=task.created_at,
                            )
                        )
                        event_type = "review.superseded.v2"
                    else:
                        await session.execute(
                            update(review_tasks)
                            .where(review_tasks.c.task_id == older_id)
                            .values(cancellation_requested=True, updated_at=task.created_at)
                        )
                        event_type = "review.cancel_requested.v2"
                    supersede_payload: dict[str, object] = {"superseded_by_task_id": task.task_id}
                    supersede_event_id = await session.scalar(
                        insert(events)
                        .values(**_event_values(older_id, event_type, supersede_payload))
                        .returning(events.c.event_id)
                    )
                    if supersede_event_id is not None:
                        captured.append(
                            ReviewEvent(
                                int(supersede_event_id),
                                older_id,
                                event_type,
                                supersede_payload,
                            )
                        )
            selection = task.review_profile.reviewer_selection
            selection_payload: dict[str, object] = {"mode": selection.mode}
            if isinstance(selection, FixedReviewerSelection):
                selection_payload["reviewer_versions"] = list(selection.reviewer_versions)
            values = dict(
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
                candidate_paths_json=_json(task.candidate_paths),
                file_exclusion_policy_json=task.file_exclusion_policy_json,
                file_exclusion_policy_hash=task.file_exclusion_policy_hash,
                status=task.status.value,
                selected_agent_versions_json=_json(task.selected_agent_versions),
                selection_request_json=_json(selection_payload),
                profile_source_id=task.review_profile.source_profile_id,
                profile_source_revision=task.review_profile.source_profile_revision,
                trigger_source=task.trigger_source,
                supersede_policy=task.supersede_policy,
                idempotency_key=task.idempotency_key,
                trigger_slot_key=task.trigger_slot_key,
                planning_context_json=task.planning_context_json,
                planning_context_hash=task.planning_context_hash,
                prompt_locale=task.prompt_locale,
                external_context_json=_json(task.external_context)
                if task.external_context
                else None,
                existing_findings_json=task.existing_findings_json,
                existing_findings_hash=task.existing_findings_hash,
                worktree_id=None,
                snapshot_id=None,
                cancellation_requested=False,
                created_at=task.created_at,
                updated_at=task.created_at,
            )
            await session.execute(insert(review_tasks).values(**values))
            await session.execute(
                insert(jobs).values(
                    task_id=task.task_id,
                    status="queued",
                    created_at=task.created_at,
                    updated_at=task.created_at,
                )
            )
            payload: dict[str, object] = {
                "status": "created",
                "base_oid": task.target.base_oid,
                "head_oid": task.target.head_oid,
            }
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task.task_id, "review.created.v2", payload))
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(int(event_id), task.task_id, "review.created.v2", payload)
                )
            await _record_recent_repository(
                session,
                task.repository_path,
                task.created_at,
                await _get_recent_repository_limit(session),
            )
            row = (
                (
                    await session.execute(
                        select(review_tasks).where(review_tasks.c.task_id == task.task_id)
                    )
                )
                .mappings()
                .one()
            )
            return _review_record(row), True

        try:
            result = await self._database.run_transaction(operation)
        except IntegrityError:
            captured.clear()
            async with self._database.sessions() as session:
                duplicate = (
                    (
                        await session.execute(
                            select(review_tasks).where(
                                review_tasks.c.idempotency_key == task.idempotency_key
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if duplicate is None:
                raise
            return _review_record(duplicate), False
        await self._publish_events(captured)
        return result

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

    async def find_duplicate_review(
        self,
        repository_id: str,
        base_oid: str,
        head_oid: str,
    ) -> ReviewRecord | None:
        """Return the newest viable Review matching the repository commit range."""

        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(review_tasks)
                        .where(
                            review_tasks.c.repository_id == repository_id,
                            review_tasks.c.base_oid == base_oid,
                            review_tasks.c.head_oid == head_oid,
                            review_tasks.c.deleted_at.is_(None),
                            review_tasks.c.status.not_in(["failed", "canceled"]),
                        )
                        .order_by(review_tasks.c.created_at.desc())
                        .limit(1)
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
            _review_record(row, finding_counts.get(str(row["task_id"]), 0)) for row in rows
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
                    candidate_paths_json=source["candidate_paths_json"],
                    file_exclusion_policy_json=source["file_exclusion_policy_json"],
                    file_exclusion_policy_hash=source["file_exclusion_policy_hash"],
                    status="created",
                    selected_agent_versions_json=source["selected_agent_versions_json"],
                    selection_request_json=source["selection_request_json"],
                    profile_source_id=source["profile_source_id"],
                    profile_source_revision=source["profile_source_revision"],
                    trigger_source="manual",
                    supersede_policy=None,
                    idempotency_key=None,
                    trigger_slot_key=None,
                    planning_context_json=source["planning_context_json"],
                    planning_context_hash=source["planning_context_hash"],
                    prompt_locale=source["prompt_locale"],
                    external_context_json=source["external_context_json"],
                    existing_findings_json=source["existing_findings_json"],
                    existing_findings_hash=source["existing_findings_hash"],
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
                .values(**_event_values(new_task_id, "review.created.v2", payload))
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=new_task_id,
                        event_type="review.created.v2",
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

        terminal_statuses = {"completed", "partial", "failed", "canceled", "superseded"}
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
                            "review.cancel_requested.v2",
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
                            event_type="review.cancel_requested.v2",
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
                        select(review_tasks).where(
                            review_tasks.c.task_id == task_id,
                            review_tasks.c.deleted_at.is_(None),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        raw_path = row["repository_path"]
        raw_targets = row["candidate_paths_json"]
        if raw_path is None or raw_targets is None:
            raise RuntimeError("review lacks restart-safe execution inputs")
        selected: list[str] = json.loads(str(row["selected_agent_versions_json"]))
        candidate_paths: list[str] = json.loads(str(raw_targets))
        policy_json = str(row["file_exclusion_policy_json"])
        policy_hash = str(row["file_exclusion_policy_hash"])
        if hashlib.sha256(policy_json.encode()).hexdigest() != policy_hash:
            raise ValueError("frozen file exclusion policy hash mismatch")
        repository_path = await asyncio.to_thread(_resolve_path, str(raw_path))
        summary = _review_record(row)
        if summary.planning_context_json is not None:
            actual_hash = hashlib.sha256(summary.planning_context_json.encode()).hexdigest()
            if actual_hash != summary.planning_context_hash:
                raise ValueError("frozen planning context hash mismatch")
        return ReviewExecutionRecord(
            task_id=str(row["task_id"]),
            repository_path=repository_path,
            repository_realpath_hash=str(row["repository_realpath_hash"]),
            git_common_dir_hash=str(row["git_common_dir_hash"]),
            base_oid=str(row["base_oid"]),
            head_oid=str(row["head_oid"]),
            scope_type=_review_scope_type(json.loads(str(row["scope_json"]))),
            base_ref=_review_scope_refs(json.loads(str(row["scope_json"])))[0],
            target_ref=_review_scope_refs(json.loads(str(row["scope_json"])))[1],
            overlay_hash=str(row["overlay_hash"]) if row["overlay_hash"] is not None else None,
            overlay_artifact_ref=(
                str(row["overlay_artifact_ref"])
                if row["overlay_artifact_ref"] is not None
                else None
            ),
            candidate_paths=tuple(candidate_paths),
            file_exclusion_policy_json=policy_json,
            file_exclusion_policy_hash=policy_hash,
            selected_agent_versions=tuple(selected),
            review_profile=summary.review_profile,
            planning_context_json=summary.planning_context_json,
            planning_context_hash=summary.planning_context_hash,
            prompt_locale=str(row["prompt_locale"]),
            status=str(row["status"]),
            cancellation_requested=bool(row["cancellation_requested"]),
            has_partial_coverage=summary.has_partial_coverage,
            existing_findings_json=str(row["existing_findings_json"]),
            existing_findings_hash=str(row["existing_findings_hash"]),
        )

    async def list_active_executions(self) -> tuple[ReviewExecutionRecord, ...]:
        """Return every non-terminal execution for startup worktree reconciliation."""

        async with self._database.sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(review_tasks).where(
                            review_tasks.c.status.not_in(
                                ("completed", "partial", "failed", "canceled", "superseded")
                            )
                        )
                    )
                )
                .mappings()
                .all()
            )

        executions: list[ReviewExecutionRecord] = []
        for row in rows:
            raw_path = row["repository_path"]
            raw_targets = row["candidate_paths_json"]
            if raw_path is None or raw_targets is None:
                continue
            selected: list[str] = json.loads(str(row["selected_agent_versions_json"]))
            candidate_paths: list[str] = json.loads(str(raw_targets))
            policy_json = str(row["file_exclusion_policy_json"])
            policy_hash = str(row["file_exclusion_policy_hash"])
            if hashlib.sha256(policy_json.encode()).hexdigest() != policy_hash:
                raise ValueError("frozen file exclusion policy hash mismatch")
            repository_path = await asyncio.to_thread(_resolve_path, str(raw_path))
            summary = _review_record(row)
            executions.append(
                ReviewExecutionRecord(
                    task_id=str(row["task_id"]),
                    repository_path=repository_path,
                    repository_realpath_hash=str(row["repository_realpath_hash"]),
                    git_common_dir_hash=str(row["git_common_dir_hash"]),
                    base_oid=str(row["base_oid"]),
                    head_oid=str(row["head_oid"]),
                    scope_type=_review_scope_type(json.loads(str(row["scope_json"]))),
                    base_ref=_review_scope_refs(json.loads(str(row["scope_json"])))[0],
                    target_ref=_review_scope_refs(json.loads(str(row["scope_json"])))[1],
                    overlay_hash=(
                        str(row["overlay_hash"]) if row["overlay_hash"] is not None else None
                    ),
                    overlay_artifact_ref=(
                        str(row["overlay_artifact_ref"])
                        if row["overlay_artifact_ref"] is not None
                        else None
                    ),
                    candidate_paths=tuple(candidate_paths),
                    file_exclusion_policy_json=policy_json,
                    file_exclusion_policy_hash=policy_hash,
                    selected_agent_versions=tuple(selected),
                    review_profile=summary.review_profile,
                    planning_context_json=summary.planning_context_json,
                    planning_context_hash=summary.planning_context_hash,
                    prompt_locale=str(row["prompt_locale"]),
                    status=str(row["status"]),
                    cancellation_requested=bool(row["cancellation_requested"]),
                    has_partial_coverage=summary.has_partial_coverage,
                    existing_findings_json=str(row["existing_findings_json"]),
                    existing_findings_hash=str(row["existing_findings_hash"]),
                )
            )
        return tuple(executions)

    async def get_status(self, task_id: str) -> str:
        """Return the current durable workflow state."""

        record = await self.get_review(task_id)
        if record is None:
            raise KeyError(task_id)
        return record.status

    async def mark_partial_coverage(self, task_id: str) -> None:
        """Set the sticky partial marker independently from phase transitions."""

        async def operation(session: AsyncSession) -> None:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(review_tasks)
                    .where(review_tasks.c.task_id == task_id)
                    .values(has_partial_coverage=True, updated_at=_now())
                ),
            )
            if result.rowcount != 1:
                raise KeyError(task_id)

        await self._database.run_transaction(operation)

    async def has_partial_coverage(self, task_id: str) -> bool:
        """Return the sticky partial marker independently from the current phase."""

        async with self._database.sessions() as session:
            value = await session.scalar(
                select(review_tasks.c.has_partial_coverage).where(review_tasks.c.task_id == task_id)
            )
        if value is None:
            raise KeyError(task_id)
        return bool(value)

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
            "planning": "preparing",
            "reviewing": ("preparing", "planning"),
            "verifying": "reviewing",
            "validating": "reviewing",
            "synthesizing": "validating",
            "completed": ("reviewing", "verifying", "synthesizing"),
            "partial": ("reviewing", "verifying", "synthesizing"),
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
                        review_tasks.c.status.in_(
                            expected if isinstance(expected, tuple) else (expected,)
                        ),
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
                .values(**_event_values(task_id, f"review.{status}.v2", {"status": status}))
                .returning(events.c.event_id)
            )
            if event_id is not None:
                captured.append(
                    ReviewEvent(
                        event_id=int(event_id),
                        task_id=task_id,
                        event_type=f"review.{status}.v2",
                        payload={"status": status},
                    )
                )

        await self._database.run_transaction(operation)
        await self._publish_events(captured)
        if captured and status in {"completed", "partial"}:
            await self._fire_terminal_hook(task_id, status)

    async def cancel(self, task_id: str) -> None:
        await self._finish_unsuccessfully(task_id, "canceled", "review.canceled.v2", None)

    async def fail(self, task_id: str, error_code: str) -> None:
        await self._finish_unsuccessfully(task_id, "failed", "review.failed.v2", error_code)

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
            if current in {
                "completed",
                "partial",
                "failed",
                "canceled",
                "superseded",
                None,
            }:
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
            if str(row["status"]) in {
                "completed",
                "partial",
                "failed",
                "canceled",
                "superseded",
            }:
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
                        "review.cancel_requested.v2",
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
                        event_type="review.cancel_requested.v2",
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

    async def complete_agent_run_with_candidates(
        self,
        task_id: str,
        node_key: str,
        batch: CandidateFindingBatch,
        *,
        result_summary: dict[str, object] | None = None,
    ) -> None:
        """Atomically persist v2 Candidates, node success, and its durable event."""

        timestamp = _now()
        summary_json = _json(result_summary) if result_summary is not None else None

        async def operation(session: AsyncSession) -> ReviewEvent | None:
            checkpoint = (
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
            if checkpoint["status"] == "succeeded":
                stored_payloads = tuple(
                    (
                        await session.execute(
                            select(candidate_findings.c.payload_json)
                            .where(
                                candidate_findings.c.task_id == task_id,
                                candidate_findings.c.node_key == node_key,
                            )
                            .order_by(candidate_findings.c.candidate_id)
                        )
                    ).scalars()
                )
                expected_payloads = tuple(
                    _candidate_payload(candidate)
                    for candidate in sorted(batch.candidates, key=lambda item: item.candidate_id)
                )
                if tuple(str(payload) for payload in stored_payloads) != expected_payloads:
                    raise ValueError("completed AgentRun Candidates do not match replay")
                if checkpoint["result_summary_json"] != summary_json:
                    raise ValueError("completed AgentRun summary does not match replay")
                return None
            if checkpoint["status"] not in {"output_saved", "validating"}:
                raise InvalidAgentRunStateError("AgentRun is not ready for Candidate completion")
            run_id = checkpoint["run_id"]
            if run_id is None:
                raise InvalidAgentRunStateError("Candidate AgentRun lacks a stable run ID")
            for candidate in batch.candidates:
                if candidate.task_id != task_id or candidate.run_id != str(run_id):
                    raise ValueError("Candidate provenance does not match the AgentRun")
                payload = _candidate_payload(candidate)
                await session.execute(
                    sqlite_insert(candidate_findings)
                    .values(
                        candidate_id=candidate.candidate_id,
                        task_id=task_id,
                        node_key=node_key,
                        run_id=candidate.run_id,
                        snapshot_id=candidate.snapshot_id,
                        reviewer_reference=candidate.reviewer_reference,
                        fingerprint=candidate.fingerprint,
                        payload_json=payload,
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(index_elements=(candidate_findings.c.candidate_id,))
                )
                stored = await session.scalar(
                    select(candidate_findings.c.payload_json).where(
                        candidate_findings.c.candidate_id == candidate.candidate_id
                    )
                )
                if str(stored) != payload:
                    raise ValueError("Candidate identity conflicts with persisted content")
            if self._completion_hook is not None:
                await self._completion_hook("after_candidate_insert_attempt")
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status.in_(("output_saved", "validating")),
                    )
                    .values(
                        status="succeeded",
                        result_summary_json=summary_json,
                        updated_at=timestamp,
                    )
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("Candidate completion lost its expected state")
            event_payload = {
                "node_key": node_key,
                "candidate_count": len(batch.candidates),
            }
            await session.execute(
                insert(events).values(
                    **_event_values(task_id, "agent_run.completed.v2", event_payload)
                )
            )
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, "agent.succeeded.v2", event_payload))
                .returning(events.c.event_id)
            )
            if event_id is None:
                raise RuntimeError("Candidate completion event was not persisted")
            return ReviewEvent(
                event_id=int(event_id),
                task_id=task_id,
                event_type="agent.succeeded.v2",
                payload=event_payload,
            )

        event = await self._database.run_transaction(operation)
        if event is not None:
            await self._publish_events([event])

    async def complete_planner_run(
        self,
        task_id: str,
        node_key: str,
        reviewer_references: tuple[str, ...],
    ) -> None:
        """Atomically accept one Planner selection and expose the actual reviewer team."""

        if not reviewer_references:
            raise ValueError("Planner completion requires a non-empty reviewer team")
        timestamp = _now()
        event_payload: dict[str, object] = {
            "node_key": node_key,
            "reviewer_references": list(reviewer_references),
        }

        async def operation(session: AsyncSession) -> ReviewEvent:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status == "validating",
                    )
                    .values(
                        status="succeeded",
                        result_summary_json=_json(event_payload),
                        updated_at=timestamp,
                    )
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("Planner completion lost its expected state")
            await session.execute(
                update(review_tasks)
                .where(review_tasks.c.task_id == task_id)
                .values(
                    selected_agent_versions_json=_json(reviewer_references),
                    updated_at=timestamp,
                )
            )
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, "agent.succeeded.v2", event_payload))
                .returning(events.c.event_id)
            )
            if event_id is None:
                raise RuntimeError("Planner completion event was not persisted")
            return ReviewEvent(int(event_id), task_id, "agent.succeeded.v2", event_payload)

        event = await self._database.run_transaction(operation)
        await self._publish_events([event])

    async def complete_with_candidates(
        self,
        task_id: str,
        node_key: str,
        candidates: CandidateFindingBatch,
        *,
        result_summary: dict[str, object] | None = None,
    ) -> None:
        """Implement the v2 Candidate atomic-completion Port."""

        await self.complete_agent_run_with_candidates(
            task_id,
            node_key,
            candidates,
            result_summary=result_summary,
        )

    async def persist_partial_candidates(
        self,
        task_id: str,
        node_key: str,
        candidates: CandidateFindingBatch,
    ) -> None:
        """Persist partial candidates from a failed Agent Run without completing it."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> None:
            checkpoint = (
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
            run_id = checkpoint["run_id"]
            if run_id is None:
                raise InvalidAgentRunStateError("AgentRun lacks a stable run ID")
            for candidate in candidates.candidates:
                if candidate.task_id != task_id or candidate.run_id != str(run_id):
                    raise ValueError("Candidate provenance does not match the AgentRun")
                payload = _candidate_payload(candidate)
                await session.execute(
                    sqlite_insert(candidate_findings)
                    .values(
                        candidate_id=candidate.candidate_id,
                        task_id=task_id,
                        node_key=node_key,
                        run_id=candidate.run_id,
                        snapshot_id=candidate.snapshot_id,
                        reviewer_reference=candidate.reviewer_reference,
                        fingerprint=candidate.fingerprint,
                        payload_json=payload,
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(index_elements=(candidate_findings.c.candidate_id,))
                )

        await self._database.run_transaction(operation)

    async def complete_with_verdicts(
        self,
        task_id: str,
        node_key: str,
        decisions: tuple[VerdictDecision, ...],
    ) -> None:
        """Atomically persist Final Verifier decisions and complete the Verifier run."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> ReviewEvent | None:
            checkpoint_status = await session.scalar(
                select(dag_checkpoints.c.status).where(
                    dag_checkpoints.c.task_id == task_id,
                    dag_checkpoints.c.node_key == node_key,
                )
            )
            if checkpoint_status == "succeeded":
                return None
            if checkpoint_status not in {"output_saved", "validating"}:
                raise InvalidAgentRunStateError("Verifier AgentRun is not ready for completion")
            for decision in decisions:
                payload = _verdict_payload(decision)
                decision_id = verdict_decision_id(task_id, decision.cluster_ids)
                await session.execute(
                    sqlite_insert(verdict_decisions)
                    .values(
                        verdict_decision_id=decision_id,
                        task_id=task_id,
                        verifier_run_id="",
                        outcome=decision.outcome.value,
                        payload_json=payload,
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(verdict_decisions.c.verdict_decision_id,)
                    )
                )
                for ordinal, cluster_id in enumerate(decision.cluster_ids):
                    await session.execute(
                        sqlite_insert(verdict_decision_clusters)
                        .values(
                            verdict_decision_id=decision_id,
                            cluster_id=cluster_id,
                            ordinal=ordinal,
                        )
                        .on_conflict_do_nothing(
                            index_elements=(
                                verdict_decision_clusters.c.verdict_decision_id,
                                verdict_decision_clusters.c.cluster_id,
                            )
                        )
                    )
                stored = await session.scalar(
                    select(verdict_decisions.c.payload_json).where(
                        verdict_decisions.c.verdict_decision_id == decision_id
                    )
                )
                if str(stored) != payload:
                    raise ValueError("Verdict decision conflicts with persisted content")
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status.in_(("output_saved", "validating")),
                    )
                    .values(
                        status="succeeded",
                        result_summary_json=_json({"verdict_count": len(decisions)}),
                        updated_at=timestamp,
                    )
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("Verifier completion lost its expected state")
            event_payload = {
                "node_key": node_key,
                "verdict_count": len(decisions),
            }
            await session.execute(
                insert(events).values(
                    **_event_values(task_id, "agent_run.completed.v2", event_payload)
                )
            )
            await session.execute(
                insert(events).values(
                    **_event_values(
                        task_id,
                        "review.verdict_completed.v2",
                        {"verdict_count": len(decisions)},
                    )
                )
            )
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, "agent.succeeded.v2", event_payload))
                .returning(events.c.event_id)
            )
            if event_id is None:
                raise RuntimeError("Verifier completion event was not persisted")
            return ReviewEvent(
                event_id=int(event_id),
                task_id=task_id,
                event_type="agent.succeeded.v2",
                payload=event_payload,
            )

        event = await self._database.run_transaction(operation)
        if event is not None:
            await self._publish_events([event])

    async def save_dedup_decisions(
        self,
        task_id: str,
        decisions: tuple[DedupDecision, ...],
        *,
        run_id: str | None = None,
    ) -> None:
        """Upsert dedup decisions (idempotent per verdict_decision_id)."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> None:
            for decision in decisions:
                await session.execute(
                    sqlite_insert(dedup_decisions)
                    .values(
                        verdict_decision_id=decision.verdict_decision_id,
                        task_id=task_id,
                        outcome=decision.outcome.value,
                        decision_source=decision.decision_source.value,
                        deduplicator_run_id=run_id,
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(dedup_decisions.c.verdict_decision_id,)
                    )
                )

        await self._database.run_transaction(operation)

    async def list_denied_verdict_ids(self, task_id: str) -> frozenset[str]:
        """Return verdict_decision_ids that were denied (suppressed)."""

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(dedup_decisions.c.verdict_decision_id).where(
                        dedup_decisions.c.task_id == task_id,
                        dedup_decisions.c.outcome == DedupOutcome.DENY.value,
                    )
                )
            ).scalars()
            return frozenset(str(row) for row in rows)

    async def complete_with_dedup(
        self,
        task_id: str,
        node_key: str,
        decisions: tuple[DedupDecision, ...],
    ) -> None:
        """Atomically persist Deduplicator decisions and complete the Deduplicator run."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> ReviewEvent | None:
            checkpoint_status = await session.scalar(
                select(dag_checkpoints.c.status).where(
                    dag_checkpoints.c.task_id == task_id,
                    dag_checkpoints.c.node_key == node_key,
                )
            )
            if checkpoint_status == "succeeded":
                return None
            if checkpoint_status not in {"output_saved", "validating"}:
                raise InvalidAgentRunStateError("Deduplicator AgentRun is not ready for completion")
            for decision in decisions:
                await session.execute(
                    sqlite_insert(dedup_decisions)
                    .values(
                        verdict_decision_id=decision.verdict_decision_id,
                        task_id=task_id,
                        outcome=decision.outcome.value,
                        decision_source=decision.decision_source.value,
                        deduplicator_run_id="",
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(dedup_decisions.c.verdict_decision_id,)
                    )
                )
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status.in_(("output_saved", "validating")),
                    )
                    .values(
                        status="succeeded",
                        result_summary_json=_json({"dedup_count": len(decisions)}),
                        updated_at=timestamp,
                    )
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("Deduplicator completion lost its expected state")
            event_payload = {
                "node_key": node_key,
                "dedup_count": len(decisions),
            }
            await session.execute(
                insert(events).values(
                    **_event_values(task_id, "agent_run.completed.v2", event_payload)
                )
            )
            await session.execute(
                insert(events).values(
                    **_event_values(
                        task_id,
                        "review.dedup_completed.v2",
                        {"dedup_count": len(decisions)},
                    )
                )
            )
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, "agent.succeeded.v2", event_payload))
                .returning(events.c.event_id)
            )
            if event_id is None:
                raise RuntimeError("Deduplicator completion event was not persisted")
            return ReviewEvent(
                event_id=int(event_id),
                task_id=task_id,
                event_type="agent.succeeded.v2",
                payload=event_payload,
            )

        event = await self._database.run_transaction(operation)
        if event is not None:
            await self._publish_events([event])

    async def save_remediation_decisions(
        self,
        task_id: str,
        decisions: tuple[RemediationDecision, ...],
        *,
        run_id: str | None = None,
    ) -> None:
        """Upsert remediation decisions (idempotent per task_id+source_id+finding_id)."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> None:
            for decision in decisions:
                await session.execute(
                    sqlite_insert(remediation_decisions)
                    .values(
                        task_id=task_id,
                        source_id=decision.source_id,
                        finding_id=decision.finding_id,
                        outcome=decision.outcome.value,
                        evidence_summary=decision.evidence_summary,
                        decision_source=decision.decision_source.value,
                        remediator_run_id=run_id,
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(
                            remediation_decisions.c.task_id,
                            remediation_decisions.c.source_id,
                            remediation_decisions.c.finding_id,
                        ),
                    )
                )

        await self._database.run_transaction(operation)

    async def list_remediation_decisions(
        self, task_id: str
    ) -> tuple[RemediationDecision, ...]:
        """Return all remediation decisions for one task."""

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(
                        remediation_decisions.c.source_id,
                        remediation_decisions.c.finding_id,
                        remediation_decisions.c.outcome,
                        remediation_decisions.c.evidence_summary,
                        remediation_decisions.c.decision_source,
                    ).where(remediation_decisions.c.task_id == task_id)
                )
            ).all()
            return tuple(
                RemediationDecision(
                    source_id=str(row.source_id),
                    finding_id=str(row.finding_id),
                    outcome=RemediationOutcome(row.outcome),
                    evidence_summary=str(row.evidence_summary),
                    decision_source=RemediationDecisionSource(row.decision_source),
                )
                for row in rows
            )

    async def complete_with_remediation(
        self,
        task_id: str,
        node_key: str,
        decisions: tuple[RemediationDecision, ...],
    ) -> None:
        """Atomically persist Remediator decisions and complete the Remediator run."""

        timestamp = _now()

        async def operation(session: AsyncSession) -> ReviewEvent | None:
            checkpoint_status = await session.scalar(
                select(dag_checkpoints.c.status).where(
                    dag_checkpoints.c.task_id == task_id,
                    dag_checkpoints.c.node_key == node_key,
                )
            )
            if checkpoint_status == "succeeded":
                return None
            if checkpoint_status not in {"output_saved", "validating"}:
                raise InvalidAgentRunStateError("Remediator AgentRun is not ready for completion")
            for decision in decisions:
                await session.execute(
                    sqlite_insert(remediation_decisions)
                    .values(
                        task_id=task_id,
                        source_id=decision.source_id,
                        finding_id=decision.finding_id,
                        outcome=decision.outcome.value,
                        evidence_summary=decision.evidence_summary,
                        decision_source=decision.decision_source.value,
                        remediator_run_id="",
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(
                            remediation_decisions.c.task_id,
                            remediation_decisions.c.source_id,
                            remediation_decisions.c.finding_id,
                        ),
                    )
                )
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status.in_(("output_saved", "validating")),
                    )
                    .values(
                        status="succeeded",
                        result_summary_json=_json({"remediation_count": len(decisions)}),
                        updated_at=timestamp,
                    )
                ),
            )
            if result.rowcount != 1:
                raise InvalidAgentRunStateError("Remediator completion lost its expected state")
            event_payload = {
                "node_key": node_key,
                "remediation_count": len(decisions),
            }
            await session.execute(
                insert(events).values(
                    **_event_values(task_id, "agent_run.completed.v2", event_payload)
                )
            )
            await session.execute(
                insert(events).values(
                    **_event_values(
                        task_id,
                        "review.remediation_completed.v2",
                        {"remediation_count": len(decisions)},
                    )
                )
            )
            event_id = await session.scalar(
                insert(events)
                .values(**_event_values(task_id, "agent.succeeded.v2", event_payload))
                .returning(events.c.event_id)
            )
            if event_id is None:
                raise RuntimeError("Remediator completion event was not persisted")
            return ReviewEvent(
                event_id=int(event_id),
                task_id=task_id,
                event_type="agent.succeeded.v2",
                payload=event_payload,
            )

        event = await self._database.run_transaction(operation)
        if event is not None:
            await self._publish_events([event])

    async def publish_verdict_findings(
        self,
        task_id: str,
        verdicts: tuple[VerdictDecision, ...],
        publications: tuple[tuple[str, Finding], ...],
    ) -> None:
        """Publish ACCEPT/MERGE verdict Findings and mark DENY verdicts suppressed."""

        timestamp = _now()
        finding_by_cluster: dict[str, Finding] = {}
        for cluster_key, finding in publications:
            finding_by_cluster[cluster_key] = finding

        async def operation(session: AsyncSession) -> list[ReviewEvent]:
            emitted: list[ReviewEvent] = []
            for verdict in verdicts:
                primary_cluster = verdict.cluster_ids[0]
                if not verdict.is_publishable:
                    continue
                finding = finding_by_cluster.get(primary_cluster)
                if finding is None:
                    continue
                payload = _finding_payload(finding)
                decision_id = verdict_decision_id(task_id, verdict.cluster_ids)
                # Idempotency: skip event emission when the Finding already
                # exists from a previous publication attempt. This keeps
                # replay calls from duplicating finding.published events while
                # still verifying the persisted payload matches.
                existing_payload = await session.scalar(
                    select(findings.c.payload_json).where(
                        findings.c.task_id == task_id,
                        findings.c.fingerprint == finding.fingerprint,
                    )
                )
                if existing_payload is not None:
                    if str(existing_payload) != payload:
                        raise ValueError("Published Finding conflicts with persisted content")
                    continue
                await session.execute(
                    sqlite_insert(findings)
                    .values(
                        finding_id=finding.finding_id,
                        task_id=task_id,
                        node_key="publication:verdict",
                        fingerprint=finding.fingerprint,
                        payload_json=payload,
                        severity=finding.severity.value,
                        verdict_decision_id=decision_id,
                        verification_status="confirmed",
                        path=finding.primary_location.path,
                        start_line=finding.primary_location.start_line,
                        created_at=timestamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(findings.c.task_id, findings.c.fingerprint)
                    )
                )
                stored = await session.scalar(
                    select(findings.c.payload_json).where(
                        findings.c.task_id == task_id,
                        findings.c.fingerprint == finding.fingerprint,
                    )
                )
                if str(stored) != payload:
                    raise ValueError("Published Finding conflicts with persisted content")
                event_payload: dict[str, object] = {
                    "finding_id": finding.finding_id,
                    "cluster_id": primary_cluster,
                    "verdict": verdict.outcome.value,
                }
                event_id = await session.scalar(
                    insert(events)
                    .values(**_event_values(task_id, "finding.published.v2", event_payload))
                    .returning(events.c.event_id)
                )
                if event_id is not None:
                    emitted.append(
                        ReviewEvent(int(event_id), task_id, "finding.published.v2", event_payload)
                    )
            return emitted

        emitted = await self._database.run_transaction(operation)
        if emitted:
            await self._publish_events(emitted)

    async def list_findings(self, task_id: str) -> tuple[Finding, ...]:
        """Return trusted Findings in stable severity and source order."""

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
                        findings.c.path,
                        findings.c.start_line,
                        findings.c.finding_id,
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
        parts = node_key.rsplit(":", 2)
        agent_version = parts[0] if len(parts) == 3 else node_key
        pass_index = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else 0
        shard_id = parts[2] if len(parts) == 3 else "root"
        run = AgentRun.create(
            task_id=task_id,
            agent_version=agent_version,
            pass_index=pass_index,
            shard_id=shard_id,
            logical_attempt_group=logical_attempt_group,
            node_role="reviewer",
        )

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
                    run_id=run.run_id,
                    node_role="reviewer",
                    agent_version=agent_version,
                    pass_index=pass_index,
                    shard_id=shard_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                .on_conflict_do_nothing(
                    index_elements=(dag_checkpoints.c.task_id, dag_checkpoints.c.node_key)
                )
            )

        await self._database.run_transaction(operation)

    async def ensure_plan_nodes(
        self,
        plan: ReviewPlan,
        *,
        capability_fingerprints: dict[str, str] | None = None,
    ) -> None:
        """Create every frozen logical node, including the optional batch Verifier."""

        timestamp = _now()
        fingerprints = capability_fingerprints or {}

        async def operation(session: AsyncSession) -> None:
            for node in plan.nodes:
                fingerprint = fingerprints.get(node.node_id)
                run = AgentRun.create(
                    task_id=plan.task_id,
                    agent_version=node.agent_reference,
                    pass_index=node.pass_index.value,
                    shard_id=node.shard_id,
                    logical_attempt_group=node.logical_attempt_group,
                    node_role=cast(Any, node.node_type.value),
                    capability_fingerprint=fingerprint,
                )
                await session.execute(
                    sqlite_insert(dag_checkpoints)
                    .values(
                        task_id=plan.task_id,
                        node_key=node.node_id,
                        logical_attempt_group=node.logical_attempt_group,
                        status="pending",
                        execution_attempts=0,
                        validation_attempts=0,
                        run_id=run.run_id,
                        node_role=node.node_type.value,
                        agent_version=node.agent_reference,
                        pass_index=node.pass_index.value,
                        shard_id=node.shard_id,
                        capability_fingerprint=fingerprint,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=(dag_checkpoints.c.task_id, dag_checkpoints.c.node_key)
                    )
                )
                stored = (
                    (
                        await session.execute(
                            select(dag_checkpoints).where(
                                dag_checkpoints.c.task_id == plan.task_id,
                                dag_checkpoints.c.node_key == node.node_id,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                expected = (
                    run.run_id,
                    node.node_type.value,
                    node.agent_reference,
                    node.pass_index.value,
                    node.shard_id,
                    fingerprint,
                )
                actual = tuple(
                    stored[name]
                    for name in (
                        "run_id",
                        "node_role",
                        "agent_version",
                        "pass_index",
                        "shard_id",
                        "capability_fingerprint",
                    )
                )
                if actual != expected:
                    raise ValueError("checkpoint conflicts with the frozen Review Plan")

        await self._database.run_transaction(operation)

    async def ensure_plan_node(
        self,
        node: ReviewPlanNode,
        *,
        capability_fingerprint: str | None = None,
    ) -> None:
        """Create one frozen Plan node before the complete adaptive Plan is known."""

        timestamp = _now()
        run = AgentRun.create(
            task_id=node.task_id,
            agent_version=node.agent_reference,
            pass_index=node.pass_index.value,
            shard_id=node.shard_id,
            logical_attempt_group=node.logical_attempt_group,
            node_role=cast(Any, node.node_type.value),
            capability_fingerprint=capability_fingerprint,
        )

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                sqlite_insert(dag_checkpoints)
                .values(
                    task_id=node.task_id,
                    node_key=node.node_id,
                    logical_attempt_group=node.logical_attempt_group,
                    status="pending",
                    execution_attempts=0,
                    validation_attempts=0,
                    run_id=run.run_id,
                    node_role=node.node_type.value,
                    agent_version=node.agent_reference,
                    pass_index=node.pass_index.value,
                    shard_id=node.shard_id,
                    capability_fingerprint=capability_fingerprint,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                .on_conflict_do_nothing(
                    index_elements=(dag_checkpoints.c.task_id, dag_checkpoints.c.node_key)
                )
            )
            stored = (
                (
                    await session.execute(
                        select(dag_checkpoints).where(
                            dag_checkpoints.c.task_id == node.task_id,
                            dag_checkpoints.c.node_key == node.node_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
            expected = (
                run.run_id,
                node.logical_attempt_group,
                node.node_type.value,
                node.agent_reference,
                node.pass_index.value,
                node.shard_id,
                capability_fingerprint,
            )
            actual = tuple(
                stored[name]
                for name in (
                    "run_id",
                    "logical_attempt_group",
                    "node_role",
                    "agent_version",
                    "pass_index",
                    "shard_id",
                    "capability_fingerprint",
                )
            )
            if actual != expected:
                raise ValueError("checkpoint conflicts with the frozen Review Plan node")

        await self._database.run_transaction(operation)

    async def list_for_task(self, task_id: str) -> tuple[CheckpointRecord, ...]:
        """Return every persisted logical node in stable pass and identity order."""

        async with self._database.sessions() as session:
            rows = (
                await session.execute(
                    select(dag_checkpoints)
                    .where(dag_checkpoints.c.task_id == task_id)
                    .order_by(
                        dag_checkpoints.c.pass_index,
                        dag_checkpoints.c.node_key,
                    )
                )
            ).mappings()
            return tuple(self._record(row) for row in rows)

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
            await session.execute(
                insert(events).values(
                    **_event_values(
                        task_id,
                        "agent_run.started.v2",
                        {"node_key": node_key},
                    )
                )
            )

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

    async def mark_failed(
        self,
        task_id: str,
        node_key: str,
        error_code: str,
        *,
        is_timeout: bool = False,
        failure_metadata: Mapping[str, str] | None = None,
    ) -> None:
        """Terminally record one isolated node failure from an expected active state."""

        if not error_code:
            raise ValueError("failed checkpoint requires an error code")
        target = "timed_out" if is_timeout else "failed"

        event_payload: dict[str, object] = {"node_key": node_key, "error_code": error_code}
        if failure_metadata:
            event_payload.update(failure_metadata)

        async def operation(session: AsyncSession) -> None:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(dag_checkpoints)
                    .where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                        dag_checkpoints.c.status.in_(("running", "validating")),
                    )
                    .values(status=target, error_code=error_code, updated_at=_now())
                ),
            )
            if result.rowcount != 1:
                current = await session.scalar(
                    select(dag_checkpoints.c.status).where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                    )
                )
                if current == target:
                    return
                raise InvalidAgentRunStateError("checkpoint is not active")
            await session.execute(
                insert(events).values(
                    **_event_values(task_id, "agent_run.failed.v2", event_payload)
                )
            )

        await self._database.run_transaction(operation)

    async def mark_skipped(self, task_id: str, node_key: str, reason_code: str) -> None:
        """Terminally omit one prebuilt conditional node from PENDING."""

        if not reason_code:
            raise ValueError("skipped checkpoint requires a reason code")

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
                    .values(status="skipped", error_code=reason_code, updated_at=_now())
                ),
            )
            if result.rowcount != 1:
                current = await session.scalar(
                    select(dag_checkpoints.c.status).where(
                        dag_checkpoints.c.task_id == task_id,
                        dag_checkpoints.c.node_key == node_key,
                    )
                )
                if current == "skipped":
                    return
                raise InvalidAgentRunStateError("checkpoint is not pending")

        await self._database.run_transaction(operation)

    async def cancel_non_terminal(self, task_id: str) -> None:
        """Propagate task cancellation to every node that has not reached a terminal state."""

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                update(dag_checkpoints)
                .where(
                    dag_checkpoints.c.task_id == task_id,
                    dag_checkpoints.c.status.in_(
                        ("pending", "running", "output_saved", "validating")
                    ),
                )
                .values(status="canceled", error_code="task_canceled", updated_at=_now())
            )

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
        return self._record(row)

    @staticmethod
    def _record(row: RowMapping) -> CheckpointRecord:
        summary = (
            json.loads(str(row["result_summary_json"]))
            if row["result_summary_json"] is not None
            else None
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
            run_id=str(row["run_id"]) if row["run_id"] is not None else None,
            node_role=str(row["node_role"]) if row["node_role"] is not None else None,
            agent_version=(str(row["agent_version"]) if row["agent_version"] is not None else None),
            pass_index=int(row["pass_index"]) if row["pass_index"] is not None else None,
            shard_id=str(row["shard_id"]) if row["shard_id"] is not None else None,
            capability_fingerprint=(
                str(row["capability_fingerprint"])
                if row["capability_fingerprint"] is not None
                else None
            ),
            result_summary=summary,
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
