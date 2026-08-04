import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from codelens.capabilities.domain.models import FrozenAgentExecutionSpec
from codelens.review.application.budget_policy import BudgetPolicyCatalog
from codelens.review.domain.ports import ReviewPlanStorePort
from codelens.review.domain.review_plan import (
    PlanCapabilityDegradation,
    ReviewerPlanGuidance,
    ReviewPass,
    ReviewPlan,
    ReviewPlanNode,
    ReviewPlanNodeType,
)
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    BudgetProfile,
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.reviewer_catalog.domain.models import AgentRole, AgentVersion
from codelens.shared.domain.errors import DomainError
from codelens.workspace.domain.models import ReviewSnapshot


class InvalidReviewPlanError(DomainError, ValueError):
    """Reject Planner or Fixed input that cannot form a legal frozen DAG."""

    code = "invalid_review_plan"


def _covered_line_count(ranges: list[tuple[int, int]]) -> int:
    if not ranges:
        return 0
    total = 0
    current_start, current_end = sorted(ranges)[0]
    for start, end in sorted(ranges)[1:]:
        if start <= current_end + 1:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start + 1
            current_start, current_end = start, end
    return total + current_end - current_start + 1


def _language_hint(path: str) -> str | None:
    suffixes = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".java": "java",
        ".sql": "sql",
        ".go": "go",
        ".rs": "rust",
    }
    return next((language for suffix, language in suffixes.items() if path.endswith(suffix)), None)


@dataclass(frozen=True, slots=True)
class CapabilityReadiness:
    status: Literal["ready", "degraded", "unavailable"]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlannerRiskSignal:
    code: str
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChangedFileRiskSummary:
    path: str
    change_type: str
    changed_old_lines: int
    changed_new_lines: int
    language_hint: str | None


@dataclass(frozen=True, slots=True)
class ChangeRiskSummary:
    """Contain only deterministic frozen change metadata, never source bodies."""

    file_count: int
    changed_line_count: int
    files: tuple[ChangedFileRiskSummary, ...]
    risk_signals: tuple[PlannerRiskSignal, ...]

    @classmethod
    def from_snapshot(cls, snapshot: ReviewSnapshot) -> "ChangeRiskSummary":
        hunks_by_file: dict[str, dict[str, list[tuple[int, int]]]] = {}
        for hunk in snapshot.change_index.hunks:
            hunks_by_file.setdefault(hunk.path, {"old": [], "new": []})[
                hunk.side
            ].append((hunk.start_line, hunk.end_line))
        files = tuple(
            ChangedFileRiskSummary(
                path=change.path,
                change_type=change.change_type,
                changed_old_lines=_covered_line_count(
                    hunks_by_file.get(change.path, {}).get("old", [])
                ),
                changed_new_lines=_covered_line_count(
                    hunks_by_file.get(change.path, {}).get("new", [])
                ),
                language_hint=_language_hint(change.path),
            )
            for change in sorted(snapshot.change_index.files, key=lambda item: item.path)
        )
        signals: list[PlannerRiskSignal] = []
        signal_rules = {
            "auth-boundary": ("auth", "permission", "acl", "security"),
            "concurrency-boundary": ("async", "worker", "lock", "thread", "queue"),
            "data-migration": ("migration", "alembic", "schema", "database"),
        }
        for code, fragments in signal_rules.items():
            paths = tuple(
                item.path
                for item in files
                if any(fragment in item.path.casefold() for fragment in fragments)
            )
            if paths:
                signals.append(PlannerRiskSignal(code, paths))
        return cls(
            file_count=len(files),
            changed_line_count=sum(
                item.changed_old_lines + item.changed_new_lines for item in files
            ),
            files=files,
            risk_signals=tuple(signals),
        )


@dataclass(frozen=True, slots=True)
class PlannerSelection:
    schema_version: Literal["1"]
    reviewer_references: tuple[str, ...]


class PlannerPort(Protocol):
    async def select(
        self,
        *,
        task_id: str,
        target_paths: tuple[str, ...],
        readiness: Mapping[str, CapabilityReadiness],
        budget_profile: BudgetProfile,
        risk_summary: ChangeRiskSummary | None,
    ) -> PlannerSelection: ...


def build_planner_input_payload(
    base_payload: bytes,
    *,
    eligible_reviewer_references: tuple[str, ...],
    readiness: Mapping[str, CapabilityReadiness],
    risk_summary: ChangeRiskSummary,
    budget_limits: Mapping[str, object],
    reviewer_catalog: tuple[Mapping[str, object], ...],
) -> bytes:
    """Add bounded Planner metadata to an existing frozen Agent input envelope."""

    try:
        envelope = json.loads(base_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("base Planner input is not canonical JSON") from error
    if not isinstance(envelope, dict) or set(envelope) != {
        "review_files",
        "repository_instructions",
    }:
        raise ValueError("base Planner input has an invalid shape")
    unavailable = tuple(
        reference
        for reference in eligible_reviewer_references
        if readiness.get(reference, CapabilityReadiness("unavailable", ())).status
        == "unavailable"
    )
    envelope["role_context"] = {
        "budget_limits": dict(budget_limits),
        "change_risk_summary": asdict(risk_summary),
        "eligible_reviewer_references": list(eligible_reviewer_references),
        "reviewer_catalog": [dict(item) for item in reviewer_catalog],
        "unavailable_reviewer_references": list(unavailable),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()


class ReviewPlanCompiler:
    """Validate a team against the frozen Catalog, then compile its host-owned DAG."""

    def __init__(
        self,
        catalog: Mapping[str, AgentVersion],
        budget_policy: BudgetPolicyCatalog,
    ) -> None:
        self._catalog = dict(catalog)
        self._budget_policy = budget_policy

    def compile(
        self,
        *,
        task_id: str,
        selection_mode: Literal["fixed", "adaptive"],
        reviewer_references: tuple[str, ...],
        budget_profile: BudgetProfile,
        planner_selection: PlannerSelection | None,
        execution_specs: Mapping[str, FrozenAgentExecutionSpec],
        readiness: Mapping[str, CapabilityReadiness],
    ) -> ReviewPlan:
        try:
            reviewers = self._validate_team(
                reviewer_references, selection_mode, readiness
            )
            is_multi = len(reviewers) > 1
            self._budget_policy.validate_shape(
                profile=budget_profile,
                selection_mode=selection_mode,
                reviewer_count=len(reviewers),
                is_multi_specialist=is_multi,
            )
            required = list(reviewers)
            if selection_mode == "adaptive":
                required.append("review-planner:v1")
            if is_multi:
                required.extend(("review-resolver:v1", "review-verifier:v1"))
            missing = [reference for reference in required if reference not in execution_specs]
            if missing:
                raise ValueError(f"frozen execution spec is missing: {missing}")
            for reference in required:
                if execution_specs[reference].agent.reference != reference:
                    raise ValueError("execution spec reference does not match its DAG node")
            return self._build_plan(
                task_id=task_id,
                selection_mode=selection_mode,
                reviewers=reviewers,
                budget_profile=budget_profile,
                planner_selection=planner_selection,
                readiness=readiness,
            )
        except InvalidReviewPlanError:
            raise
        except ValueError as error:
            raise InvalidReviewPlanError(str(error)) from error

    def _validate_team(
        self,
        references: tuple[str, ...],
        selection_mode: Literal["fixed", "adaptive"],
        readiness: Mapping[str, CapabilityReadiness],
    ) -> tuple[str, ...]:
        if not references or len(references) != len(set(references)):
            raise ValueError("reviewer team must be non-empty and unique")
        agents: list[AgentVersion] = []
        for reference in references:
            agent = self._catalog.get(reference)
            if agent is None or agent.role is not AgentRole.REVIEWER:
                raise ValueError(f"unknown Reviewer reference: {reference}")
            if selection_mode == "adaptive" and (
                not agent.planner_eligible or not agent.is_public or agent.is_legacy
            ):
                raise ValueError(f"Reviewer is not Planner eligible: {reference}")
            if selection_mode == "fixed" and not (agent.is_public or agent.is_legacy):
                raise ValueError(f"Reviewer is not selectable: {reference}")
            state = readiness.get(reference)
            if state is not None and state.status == "unavailable":
                raise ValueError(f"Reviewer capability is unavailable: {reference}")
            agents.append(agent)
        if "general:v1" in references and references != ("general:v1",):
            raise ValueError("General reviewer must run alone")
        if "correctness:v1" in references and references != ("correctness:v1",):
            raise ValueError("correctness:v1 is legacy single-reviewer only")
        if selection_mode == "adaptive" and references != ("general:v1",):
            if len(references) < 2:
                raise ValueError("Adaptive specialist team requires at least two reviewers")
        return tuple(agent.reference for agent in agents)

    @staticmethod
    def _node(
        task_id: str,
        node_type: ReviewPlanNodeType,
        reference: str,
        review_pass: ReviewPass,
        *,
        shard_id: str = "root",
        depends_on: tuple[str, ...] = (),
    ) -> ReviewPlanNode:
        return ReviewPlanNode.create(
            task_id=task_id,
            node_type=node_type,
            agent_reference=reference,
            pass_index=review_pass,
            shard_id=shard_id,
            logical_attempt_group="primary",
            depends_on=depends_on,
        )

    def _build_plan(
        self,
        *,
        task_id: str,
        selection_mode: Literal["fixed", "adaptive"],
        reviewers: tuple[str, ...],
        budget_profile: BudgetProfile,
        planner_selection: PlannerSelection | None,
        readiness: Mapping[str, CapabilityReadiness],
    ) -> ReviewPlan:
        planner_node: ReviewPlanNode | None = None
        if selection_mode == "adaptive":
            if planner_selection is None:
                raise ValueError("Adaptive plan requires Planner output")
            if planner_selection.reviewer_references != reviewers:
                raise ValueError("host cannot add or remove Planner selections")
            planner_node = self._node(
                task_id,
                ReviewPlanNodeType.PLANNER,
                "review-planner:v1",
                ReviewPass.PLANNER,
            )
        elif planner_selection is not None:
            raise ValueError("Fixed plan cannot contain Planner output")
        reviewer_dependencies = (planner_node.node_id,) if planner_node else ()
        reviewer_nodes = tuple(
            self._node(
                task_id,
                ReviewPlanNodeType.REVIEWER,
                reference,
                ReviewPass.REVIEWER,
                depends_on=reviewer_dependencies,
            )
            for reference in reviewers
        )
        nodes: list[ReviewPlanNode] = []
        if planner_node:
            nodes.append(planner_node)
        nodes.extend(reviewer_nodes)
        if len(reviewers) > 1:
            resolver = self._node(
                task_id,
                ReviewPlanNodeType.RESOLVER,
                "review-resolver:v1",
                ReviewPass.RESOLVER,
                depends_on=tuple(node.node_id for node in reviewer_nodes),
            )
            verifier = self._node(
                task_id,
                ReviewPlanNodeType.VERIFIER,
                "review-verifier:v1",
                ReviewPass.VERIFIER,
                shard_id="batch",
                depends_on=(resolver.node_id,),
            )
            nodes.extend((resolver, verifier))
        guidance: tuple[ReviewerPlanGuidance, ...] = ()
        return ReviewPlan.create(
            task_id=task_id,
            selection_mode=selection_mode,
            budget_profile=budget_profile.value,
            reviewer_references=reviewers,
            nodes=tuple(nodes),
            planner_reason=("planner-selection:v1" if planner_selection else None),
            reviewer_guidance=guidance,
            capability_degradations=tuple(
                PlanCapabilityDegradation(reference, readiness[reference].reason_codes)
                for reference in reviewers
                if reference in readiness and readiness[reference].status == "degraded"
            ),
        )


class ReviewPlanningService:
    """Persist a Fixed host plan or one validated Adaptive Planner selection."""

    def __init__(
        self,
        *,
        compiler: ReviewPlanCompiler,
        planner: PlannerPort,
        plan_store: ReviewPlanStorePort,
        budget_policy: BudgetPolicyCatalog | None = None,
    ) -> None:
        self._compiler = compiler
        self._planner = planner
        self._plan_store = plan_store
        self._budget_policy = budget_policy or BudgetPolicyCatalog.version_one()

    async def plan(
        self,
        *,
        task_id: str,
        profile: ReviewProfileSnapshot,
        execution_specs: Mapping[str, FrozenAgentExecutionSpec],
        readiness: Mapping[str, CapabilityReadiness],
        target_paths: tuple[str, ...],
        catalog_version: str,
        capability_fingerprint: str,
        risk_summary: ChangeRiskSummary | None = None,
    ) -> ReviewPlan:
        existing = await self._plan_store.get(task_id)
        if existing is not None:
            return existing.plan
        selection = profile.reviewer_selection
        planner_selection: PlannerSelection | None = None
        if isinstance(selection, AdaptiveReviewerSelection):
            planner_selection = await self._planner.select(
                task_id=task_id,
                target_paths=target_paths,
                readiness=readiness,
                budget_profile=profile.budget_profile,
                risk_summary=risk_summary,
            )
            references = planner_selection.reviewer_references
            mode: Literal["fixed", "adaptive"] = "adaptive"
        else:
            if not isinstance(selection, FixedReviewerSelection):
                raise InvalidReviewPlanError("unknown Reviewer selection mode")
            references = selection.reviewer_versions
            mode = "fixed"
        plan = self._compiler.compile(
            task_id=task_id,
            selection_mode=mode,
            reviewer_references=references,
            budget_profile=profile.budget_profile,
            planner_selection=planner_selection,
            execution_specs=execution_specs,
            readiness=readiness,
        )
        limits = self._budget_policy.limits(profile.budget_profile)
        budget_json = json.dumps(
            {
                "limits": asdict(limits),
                "profile": profile.budget_profile.value,
                "version": self._budget_policy.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        record = await self._plan_store.save(
            plan,
            catalog_version=catalog_version,
            budget_json=budget_json,
            capability_fingerprint=capability_fingerprint,
        )
        return record.plan
