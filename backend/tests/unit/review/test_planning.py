from datetime import UTC, datetime

import pytest

from codelens.capabilities.application.resolve import CapabilityResolver
from codelens.capabilities.domain.models import AgentExecutionLimits, FrozenAgentExecutionSpec
from codelens.capabilities.domain.skills import SkillActivationFacts
from codelens.review.application.planning import (
    CapabilityReadiness,
    InvalidReviewPlanError,
    PlannerSelection,
    ReviewPlanCompiler,
    ReviewPlanningService,
)
from codelens.review.domain.ports import ReviewPlanRecord
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog

TASK_ID = "review_" + "a" * 32


def _specs(*references: str) -> dict[str, FrozenAgentExecutionSpec]:
    catalog = builtin_agent_catalog()
    resolver = CapabilityResolver.testing()
    return {
        reference: resolver.resolve(
            agent=catalog[reference],
            prompt_content_hash="a" * 64,
            facts=SkillActivationFacts.empty(),
            execution_limits=AgentExecutionLimits.legacy_default(),
        )
        for reference in references
    }


def _compiler() -> ReviewPlanCompiler:
    return ReviewPlanCompiler(builtin_agent_catalog())


def _ready() -> dict[str, CapabilityReadiness]:
    return {
        reference: CapabilityReadiness("ready", ())
        for reference, agent in builtin_agent_catalog().items()
        if agent.planner_eligible
    }


def test_fixed_compiler_builds_host_dag_without_planner() -> None:
    references = ("correctness:v2", "security:v1")
    specs = _specs(*references, "review-verifier:v1")

    plan = _compiler().compile(
        task_id=TASK_ID,
        selection_mode="fixed",
        reviewer_references=references,
        planner_selection=None,
        execution_specs=specs,
        readiness=_ready(),
    )

    assert plan.reviewer_references == tuple(sorted(references))
    assert not any(node.node_type.value == "planner" for node in plan.nodes)
    verifier = next(node for node in plan.nodes if node.node_type.value == "verifier")
    assert verifier.shard_id == "batch"
    assert verifier.depends_on == tuple(
        sorted(node.node_id for node in plan.nodes if node.node_type.value == "reviewer")
    )


def test_adaptive_rejects_general_plus_specialists() -> None:
    selection = PlannerSelection(
        schema_version="1",
        reviewer_references=("general:v1", "security:v1"),
    )

    with pytest.raises(InvalidReviewPlanError, match="not Planner eligible"):
        _compiler().compile(
            task_id=TASK_ID,
            selection_mode="adaptive",
            reviewer_references=selection.reviewer_references,
            planner_selection=selection,
            execution_specs=_specs(
                "review-planner:v1", "general:v1", "security:v1"
            ),
            readiness=_ready(),
        )


class _PlanStore:
    def __init__(self) -> None:
        self.record: ReviewPlanRecord | None = None

    async def get(self, _task_id: str) -> ReviewPlanRecord | None:
        return self.record

    async def save(self, plan: object, **metadata: object) -> ReviewPlanRecord:
        from codelens.review.domain.review_plan import ReviewPlan

        assert isinstance(plan, ReviewPlan)
        self.record = ReviewPlanRecord(
            plan,
            str(metadata["catalog_version"]),
            str(metadata["capability_fingerprint"]),
            datetime(2026, 8, 2, tzinfo=UTC),
        )
        return self.record


class _Planner:
    def __init__(self, selection: PlannerSelection) -> None:
        self.selection = selection
        self.call_count = 0

    async def select(self, **_inputs: object) -> PlannerSelection:
        self.call_count += 1
        return self.selection


async def test_fixed_service_never_invokes_planner_and_persists_before_return() -> None:
    planner = _Planner(
        PlannerSelection("1", ("general:v1",))
    )
    store = _PlanStore()
    service = ReviewPlanningService(
        compiler=_compiler(), planner=planner, plan_store=store
    )

    plan = await service.plan(
        task_id=TASK_ID,
        profile=ReviewProfileSnapshot(
            FixedReviewerSelection(("security:v1",))
        ),
        execution_specs=_specs("security:v1"),
        readiness=_ready(),
        target_paths=("src/app.py",),
        catalog_version="builtin-v1",
        capability_fingerprint="c" * 64,
    )

    assert planner.call_count == 0
    assert store.record is not None and store.record.plan == plan


async def test_existing_plan_prevents_adaptive_planner_reinvocation() -> None:
    store = _PlanStore()
    first_planner = _Planner(
        PlannerSelection(
            "1",
            ("security:v1", "performance:v1"),
        )
    )
    service = ReviewPlanningService(
        compiler=_compiler(), planner=first_planner, plan_store=store
    )
    inputs = dict(
        task_id=TASK_ID,
        profile=ReviewProfileSnapshot(
            AdaptiveReviewerSelection()
        ),
        execution_specs=_specs(
            "review-planner:v1",
            "security:v1",
            "performance:v1",
            "review-verifier:v1",
        ),
        readiness=_ready(),
        target_paths=("src/app.py",),
        catalog_version="builtin-v1",
        capability_fingerprint="d" * 64,
    )
    first = await service.plan(**inputs)
    second = await service.plan(**inputs)

    assert second == first
    assert first_planner.call_count == 1


def test_adaptive_compiler_accepts_two_or_more_specialists_only() -> None:
    specialist_selection = PlannerSelection(
        "1",
        ("security:v1", "performance:v1"),
    )
    specialists = _compiler().compile(
        task_id=TASK_ID,
        selection_mode="adaptive",
        reviewer_references=specialist_selection.reviewer_references,
        planner_selection=specialist_selection,
        execution_specs=_specs(
            "review-planner:v1",
            "security:v1",
            "performance:v1",
            "review-verifier:v1",
        ),
        readiness=_ready(),
    )
    assert specialists.reviewer_references == ("performance:v1", "security:v1")

    one_specialist = PlannerSelection(
        "1",
        ("security:v1",),
    )
    with pytest.raises(InvalidReviewPlanError, match="at least two"):
        _compiler().compile(
            task_id=TASK_ID,
            selection_mode="adaptive",
            reviewer_references=("security:v1",),
            planner_selection=one_specialist,
            execution_specs=_specs("review-planner:v1", "security:v1"),
            readiness=_ready(),
        )


def test_fixed_rejects_unavailable_reviewer_and_records_optional_degradation() -> None:
    readiness = _ready()
    readiness["security:v1"] = CapabilityReadiness("unavailable", ("mcp-required",))
    with pytest.raises(InvalidReviewPlanError, match="unavailable"):
        _compiler().compile(
            task_id=TASK_ID,
            selection_mode="fixed",
            reviewer_references=("security:v1",),
            planner_selection=None,
            execution_specs=_specs("security:v1"),
            readiness=readiness,
        )

    readiness["security:v1"] = CapabilityReadiness(
        "degraded", ("optional-mcp-unavailable", "optional-skill-unavailable")
    )
    plan = _compiler().compile(
        task_id=TASK_ID,
        selection_mode="fixed",
        reviewer_references=("security:v1",),
        planner_selection=None,
        execution_specs=_specs("security:v1"),
        readiness=readiness,
    )
    assert plan.capability_degradations[0].reason_codes == (
        "optional-mcp-unavailable",
        "optional-skill-unavailable",
    )


class _FailingPlanner:
    async def select(self, **_inputs: object) -> PlannerSelection:
        raise RuntimeError("planner failed")


async def test_adaptive_planner_failure_has_no_host_fallback() -> None:
    service = ReviewPlanningService(
        compiler=_compiler(), planner=_FailingPlanner(), plan_store=_PlanStore()
    )

    with pytest.raises(RuntimeError, match="planner failed"):
        await service.plan(
            task_id=TASK_ID,
            profile=ReviewProfileSnapshot(
                AdaptiveReviewerSelection()
            ),
            execution_specs=_specs(
                "review-planner:v1",
                "security:v1",
                "performance:v1",
                "review-verifier:v1",
            ),
            readiness=_ready(),
            target_paths=("src/app.py",),
            catalog_version="builtin-v1",
            capability_fingerprint="e" * 64,
        )
