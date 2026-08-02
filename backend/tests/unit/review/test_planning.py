import asyncio
from datetime import UTC, datetime

import pytest

from codelens.capabilities.application.resolve import CapabilityResolver
from codelens.capabilities.domain.models import AgentExecutionLimits, FrozenAgentExecutionSpec
from codelens.capabilities.domain.skills import SkillActivationFacts
from codelens.review.application.budget_policy import (
    BudgetExceededError,
    BudgetLimits,
    BudgetPolicyCatalog,
    TaskBudgetLedger,
)
from codelens.review.application.planning import (
    CapabilityReadiness,
    InvalidReviewPlanError,
    PlannerReviewerDecision,
    PlannerSelection,
    ReviewPlanCompiler,
    ReviewPlanningService,
)
from codelens.review.domain.ports import ReviewPlanRecord
from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    BudgetProfile,
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
    return ReviewPlanCompiler(
        builtin_agent_catalog(), BudgetPolicyCatalog.version_one()
    )


def _ready() -> dict[str, CapabilityReadiness]:
    return {
        reference: CapabilityReadiness("ready", ())
        for reference, agent in builtin_agent_catalog().items()
        if agent.planner_eligible
    }


def test_fixed_compiler_builds_host_dag_without_planner() -> None:
    references = ("correctness:v2", "security:v1")
    specs = _specs(*references, "review-resolver:v1", "review-verifier:v1")

    plan = _compiler().compile(
        task_id=TASK_ID,
        selection_mode="fixed",
        reviewer_references=references,
        budget_profile=BudgetProfile.STANDARD,
        planner_selection=None,
        execution_specs=specs,
        readiness=_ready(),
    )

    assert plan.reviewer_references == tuple(sorted(references))
    assert not any(node.node_type.value == "planner" for node in plan.nodes)
    resolver = next(node for node in plan.nodes if node.node_type.value == "resolver")
    verifier = next(node for node in plan.nodes if node.node_type.value == "verifier")
    assert len(resolver.depends_on) == 2
    assert verifier.depends_on == (resolver.node_id,)


def test_adaptive_rejects_general_plus_specialists() -> None:
    selection = PlannerSelection(
        schema_version="1",
        strategy="specialist_team",
        risk_signals=(),
        reviewer_decisions=(
            PlannerReviewerDecision("general:v1", True, ("broad-risk",), ()),
            PlannerReviewerDecision("security:v1", True, ("security-risk",), ()),
        ),
    )

    with pytest.raises(InvalidReviewPlanError, match="General reviewer must run alone"):
        _compiler().compile(
            task_id=TASK_ID,
            selection_mode="adaptive",
            reviewer_references=selection.reviewer_references,
            budget_profile=BudgetProfile.STANDARD,
            planner_selection=selection,
            execution_specs=_specs(
                "review-planner:v1", "general:v1", "security:v1"
            ),
            readiness=_ready(),
        )


@pytest.mark.parametrize(
    ("profile", "max_reviewers", "max_model_nodes", "per_review_concurrency"),
    [
        (BudgetProfile.LEAN, 1, 2, 1),
        (BudgetProfile.STANDARD, 3, 6, 3),
        (BudgetProfile.DEEP, 7, 10, 4),
    ],
)
def test_budget_policy_v1_exact_limits(
    profile: BudgetProfile,
    max_reviewers: int,
    max_model_nodes: int,
    per_review_concurrency: int,
) -> None:
    limits = BudgetPolicyCatalog.version_one().limits(profile)

    assert limits.max_reviewers == max_reviewers
    assert limits.max_model_nodes == max_model_nodes
    assert limits.per_review_concurrency == per_review_concurrency


def test_budget_policy_v1_freezes_every_approved_limit() -> None:
    catalog = BudgetPolicyCatalog.version_one()

    assert catalog.limits(BudgetProfile.LEAN) == BudgetLimits(
        1, 2, 1, 100_000, 8_000, 12, 80, 300, 0
    )
    assert catalog.limits(BudgetProfile.STANDARD) == BudgetLimits(
        3, 6, 3, 400_000, 16_000, 20, 240, 900, 12
    )
    assert catalog.limits(BudgetProfile.DEEP) == BudgetLimits(
        7, 10, 4, 1_200_000, 24_000, 30, 600, 1_800, 40
    )


@pytest.mark.parametrize(
    ("profile", "selection_mode", "references", "is_valid"),
    (
        (BudgetProfile.LEAN, "fixed", ("general:v1",), True),
        (BudgetProfile.LEAN, "fixed", ("security:v1",), True),
        (BudgetProfile.LEAN, "fixed", ("security:v1", "performance:v1"), False),
        (BudgetProfile.LEAN, "adaptive", ("general:v1",), True),
        (
            BudgetProfile.STANDARD,
            "fixed",
            ("security:v1", "performance:v1", "architecture:v1"),
            True,
        ),
        (
            BudgetProfile.STANDARD,
            "fixed",
            (
                "security:v1",
                "performance:v1",
                "architecture:v1",
                "contract-data:v1",
            ),
            False,
        ),
        (
            BudgetProfile.DEEP,
            "fixed",
            (
                "correctness:v2",
                "security:v1",
                "reliability-concurrency:v1",
                "contract-data:v1",
                "architecture:v1",
                "performance:v1",
                "test-regression:v1",
            ),
            True,
        ),
    ),
)
def test_budget_policy_reserves_whole_dag_before_reviewer_fanout(
    profile: BudgetProfile,
    selection_mode: str,
    references: tuple[str, ...],
    is_valid: bool,
) -> None:
    policy = BudgetPolicyCatalog.version_one()

    if is_valid:
        limits = policy.validate_shape(
            profile=profile,
            selection_mode=selection_mode,
            reviewer_count=len(references),
            is_multi_specialist=len(references) > 1,
        )
        reserved_nodes = len(references) + (selection_mode == "adaptive") + (
            2 if len(references) > 1 else 0
        )
        assert reserved_nodes <= limits.max_model_nodes
    else:
        with pytest.raises(ValueError, match="budget"):
            policy.validate_shape(
                profile=profile,
                selection_mode=selection_mode,
                reviewer_count=len(references),
                is_multi_specialist=len(references) > 1,
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
            str(metadata["budget_json"]),
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
        PlannerSelection("1", "generalist", (), ())
    )
    store = _PlanStore()
    service = ReviewPlanningService(
        compiler=_compiler(), planner=planner, plan_store=store
    )

    plan = await service.plan(
        task_id=TASK_ID,
        profile=ReviewProfileSnapshot(
            FixedReviewerSelection(("security:v1",)), BudgetProfile.LEAN
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
            "generalist",
            (),
            (PlannerReviewerDecision("general:v1", True, ("broad-risk",), ()),),
        )
    )
    service = ReviewPlanningService(
        compiler=_compiler(), planner=first_planner, plan_store=store
    )
    inputs = dict(
        task_id=TASK_ID,
        profile=ReviewProfileSnapshot(
            AdaptiveReviewerSelection(), BudgetProfile.LEAN
        ),
        execution_specs=_specs("review-planner:v1", "general:v1"),
        readiness=_ready(),
        target_paths=("src/app.py",),
        catalog_version="builtin-v1",
        capability_fingerprint="d" * 64,
    )
    first = await service.plan(**inputs)
    second = await service.plan(**inputs)

    assert second == first
    assert first_planner.call_count == 1


def test_adaptive_compiler_accepts_general_or_two_specialists_only() -> None:
    general_selection = PlannerSelection(
        "1",
        "generalist",
        (),
        (PlannerReviewerDecision("general:v1", True, ("broad-risk",), ()),),
    )
    general = _compiler().compile(
        task_id=TASK_ID,
        selection_mode="adaptive",
        reviewer_references=("general:v1",),
        budget_profile=BudgetProfile.LEAN,
        planner_selection=general_selection,
        execution_specs=_specs("review-planner:v1", "general:v1"),
        readiness=_ready(),
    )
    assert general.reviewer_references == ("general:v1",)

    specialist_selection = PlannerSelection(
        "1",
        "specialist_team",
        (),
        (
            PlannerReviewerDecision("security:v1", True, ("security-risk",), ()),
            PlannerReviewerDecision("performance:v1", True, ("performance-risk",), ()),
        ),
    )
    specialists = _compiler().compile(
        task_id=TASK_ID,
        selection_mode="adaptive",
        reviewer_references=specialist_selection.reviewer_references,
        budget_profile=BudgetProfile.STANDARD,
        planner_selection=specialist_selection,
        execution_specs=_specs(
            "review-planner:v1",
            "security:v1",
            "performance:v1",
            "review-resolver:v1",
            "review-verifier:v1",
        ),
        readiness=_ready(),
    )
    assert specialists.reviewer_references == ("performance:v1", "security:v1")

    one_specialist = PlannerSelection(
        "1",
        "specialist_team",
        (),
        (PlannerReviewerDecision("security:v1", True, ("security-risk",), ()),),
    )
    with pytest.raises(InvalidReviewPlanError, match="at least two"):
        _compiler().compile(
            task_id=TASK_ID,
            selection_mode="adaptive",
            reviewer_references=("security:v1",),
            budget_profile=BudgetProfile.STANDARD,
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
            budget_profile=BudgetProfile.LEAN,
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
        budget_profile=BudgetProfile.LEAN,
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
                AdaptiveReviewerSelection(), BudgetProfile.LEAN
            ),
            execution_specs=_specs("review-planner:v1", "general:v1"),
            readiness=_ready(),
            target_paths=("src/app.py",),
            catalog_version="builtin-v1",
            capability_fingerprint="e" * 64,
        )


class _Estimator:
    estimator_version = "estimator-v1"
    model_version = "neutral-v1"

    def __init__(self, estimate: int) -> None:
        self._estimate = estimate

    def estimate(self, _payload: bytes, _model_profile_id: str) -> int:
        return self._estimate


async def test_task_budget_ledger_atomically_prevents_oversubscription() -> None:
    limits = BudgetLimits(2, 2, 2, 100, 40, 12, 80, 300, 0)
    ledger = TaskBudgetLedger(limits)
    spec = _specs("security:v1")["security:v1"]
    spec = FrozenAgentExecutionSpec.create(
        agent=spec.agent,
        capability_profile=spec.capability_profile,
        skill_policy=spec.skill_policy,
        prompt_content_hash=spec.prompt_content_hash,
        skills=spec.skills,
        execution_limits=AgentExecutionLimits(12, 20, 60, 40, 30.0, 1024),
    )

    results = await asyncio.gather(
        *(
            ledger.reserve(
                node_id=f"node-{index}",
                input_payload=b"{}",
                execution_spec=spec,
                estimator=_Estimator(20),
            )
            for index in range(2)
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    error = next(result for result in results if isinstance(result, Exception))
    assert isinstance(error, BudgetExceededError)
    assert error.reason_code == "task_token_capacity_exceeded"


async def test_task_budget_ledger_reconciles_provider_usage_and_releases_capacity() -> None:
    limits = BudgetLimits(2, 2, 2, 100, 40, 12, 80, 300, 0)
    ledger = TaskBudgetLedger(limits)
    base = _specs("security:v1")["security:v1"]
    spec = FrozenAgentExecutionSpec.create(
        agent=base.agent,
        capability_profile=base.capability_profile,
        skill_policy=base.skill_policy,
        prompt_content_hash=base.prompt_content_hash,
        skills=base.skills,
        execution_limits=AgentExecutionLimits(12, 20, 60, 40, 30.0, 1024),
    )
    reservation = await ledger.reserve(
        node_id="node-1",
        input_payload=b"{}",
        execution_spec=spec,
        estimator=_Estimator(20),
    )
    assert reservation.estimator_version == "estimator-v1"
    await ledger.reconcile("node-1", input_tokens=10, output_tokens=5, tool_calls=2)
    await ledger.reserve(
        node_id="node-2",
        input_payload=b"{}",
        execution_spec=spec,
        estimator=_Estimator(20),
    )

    with pytest.raises(BudgetExceededError) as raised:
        await TaskBudgetLedger(limits).reserve(
            node_id="too-large",
            input_payload=b"{}",
            execution_spec=spec,
            estimator=_Estimator(61),
        )
    assert raised.value.reason_code == "estimated_input_tokens_exceeded"
