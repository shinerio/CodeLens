import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from codelens.findings.domain.existing_findings import ExistingFinding
from codelens.review.domain.models import ReviewTask
from codelens.review.domain.ports import ReviewRecord, ReviewStorePort
from codelens.review.domain.review_strategy import FixedReviewerSelection, ReviewProfileSnapshot
from codelens.workspace.application.capture_overlay import ReviewInputCaptureService
from codelens.workspace.application.plan_scope import ScopePlanner
from codelens.workspace.domain.models import ReviewScope
from codelens.workspace.domain.ports import InputArtifactPort, RepositoryInfo


class PlanningContextFreezerPort(Protocol):
    """Freeze resolved Catalog and capability data without persisting trusted bodies."""

    async def freeze(
        self, profile: ReviewProfileSnapshot, prompt_locale: str
    ) -> Mapping[str, object]: ...


class ExistingFindingsProviderPort(Protocol):
    """Load structured historical issues before a triggered task is frozen."""

    async def load(self, repository_path: Path) -> tuple[ExistingFinding, ...]: ...


@dataclass(frozen=True, slots=True)
class CreateTriggeredReview:
    repository: RepositoryInfo
    scope: ReviewScope
    review_profile: ReviewProfileSnapshot
    prompt_locale: str
    supersede_policy: Literal["latest_snapshot", "preserve_all"]
    external_context: Mapping[str, object] | None
    existing_findings: tuple[ExistingFinding, ...] = ()


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_REQUIRED_CONTEXT_KEYS = {
    "schema_version",
    "catalog_snapshot",
    "capability_readiness",
    "planner_execution_spec",
    "eligible_reviewer_execution_specs",
    "artifact_ids",
}
_FORBIDDEN_BODY_KEYS = {
    "body",
    "content",
    "instruction",
    "instruction_text",
    "instructions",
    "prompt",
    "prompt_body",
    "prompt_template",
    "skill_body",
    "skill_content",
    "skill_instructions",
    "text",
}


def _reject_embedded_bodies(value: object) -> None:
    if isinstance(value, Mapping):
        forbidden = _FORBIDDEN_BODY_KEYS.intersection(value)
        if forbidden:
            raise ValueError(f"planning context contains trusted body fields: {sorted(forbidden)}")
        for nested in value.values():
            _reject_embedded_bodies(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _reject_embedded_bodies(nested)


class CreateTriggeredReviewHandler:
    """Freeze trigger input before atomically deduplicating and enqueueing it."""

    def __init__(
        self,
        planner: ScopePlanner,
        capture: ReviewInputCaptureService,
        freezer: PlanningContextFreezerPort,
        store: ReviewStorePort,
        input_artifacts: InputArtifactPort,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        existing_findings_provider: ExistingFindingsProviderPort | None = None,
    ) -> None:
        self._planner, self._capture, self._freezer = planner, capture, freezer
        self._store, self._input_artifacts = store, input_artifacts
        self._id_factory = id_factory or (lambda: f"review_{uuid.uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._existing_findings_provider = existing_findings_provider

    async def handle(self, command: CreateTriggeredReview) -> ReviewRecord:
        plan = await self._planner.plan(command.repository.path, command.scope)
        captured = await self._capture.capture(command.repository.path, plan)
        artifact = captured.overlay_artifact
        try:
            context = dict(
                await self._freezer.freeze(command.review_profile, command.prompt_locale)
            )
            missing = _REQUIRED_CONTEXT_KEYS.difference(context)
            if missing:
                raise ValueError(f"planning context is incomplete: {sorted(missing)}")
            _reject_embedded_bodies(context)
            context_json = _canonical(context)
            provided_existing_findings = (
                await self._existing_findings_provider.load(command.repository.path)
                if self._existing_findings_provider is not None
                else ()
            )
            selection = command.review_profile.reviewer_selection
            selection_policy: dict[str, object] = {"mode": selection.mode}
            if isinstance(selection, FixedReviewerSelection):
                selection_policy["reviewer_versions"] = list(selection.reviewer_versions)
            policy = {
                "repository_id": command.repository.repository_id,
                "review_profile": {
                    "selection": selection_policy,
                },
                "prompt_locale": command.prompt_locale,
                "planning_context_hash": hashlib.sha256(context_json.encode()).hexdigest(),
            }
            slot_key = hashlib.sha256(_canonical(policy).encode()).hexdigest()
            exact = dict(policy)
            exact["snapshot"] = {
                "base_oid": captured.target.base_oid,
                "head_oid": captured.target.head_oid,
                "overlay_hash": captured.target.overlay_hash,
            }
            idempotency_key = hashlib.sha256(_canonical(exact).encode()).hexdigest()
            task = ReviewTask.create(
                task_id=self._id_factory(),
                repository_id=command.repository.repository_id,
                repository_realpath_hash=command.repository.repository_realpath_hash,
                git_common_dir_hash=command.repository.git_common_dir_hash,
                scope=command.scope,
                target=captured.target,
                repository_path=command.repository.path,
                candidate_paths=plan.candidate_paths,
                selected_agent_versions=selection.reviewer_versions
                if isinstance(selection, FixedReviewerSelection)
                else (),
                review_profile=command.review_profile,
                planning_context=context,
                trigger_source="plugin",
                supersede_policy=command.supersede_policy,
                idempotency_key=idempotency_key,
                trigger_slot_key=slot_key,
                prompt_locale=command.prompt_locale,
                created_at=self._clock(),
                overlay_artifact_ref=artifact.reference if artifact else None,
                external_context=dict(command.external_context)
                if command.external_context
                else None,
                existing_findings=(*provided_existing_findings, *command.existing_findings),
            )
            record, was_created = await self._store.create_triggered_with_job(task)
            if not was_created and artifact is not None:
                await self._input_artifacts.discard(artifact.reference)
            return record
        except BaseException:
            if artifact is not None:
                await self._input_artifacts.discard(artifact.reference)
            raise
