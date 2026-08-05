import pytest

from codelens.review.domain.review_plan import (
    CoverageStatus,
    ReviewPass,
    ReviewPlan,
    ReviewPlanNode,
    ReviewPlanNodeType,
)

TASK_ID = "review_" + "a" * 32


def _node(
    reference: str,
    node_type: ReviewPlanNodeType,
    review_pass: ReviewPass,
    *,
    depends_on: tuple[str, ...] = (),
) -> ReviewPlanNode:
    return ReviewPlanNode.create(
        task_id=TASK_ID,
        node_type=node_type,
        agent_reference=reference,
        pass_index=review_pass,
        shard_id="root",
        logical_attempt_group="primary",
        depends_on=depends_on,
    )


def reviewer_nodes_only() -> tuple[ReviewPlanNode, ...]:
    return tuple(
        _node(reference, ReviewPlanNodeType.REVIEWER, ReviewPass.REVIEWER)
        for reference in ("correctness:v2", "security:v1")
    )


def test_multi_specialist_plan_requires_resolver() -> None:
    with pytest.raises(ValueError, match="multi-specialist plan requires one batched verifier"):
        ReviewPlan.create(
            task_id=TASK_ID,
            selection_mode="fixed",
            reviewer_references=("correctness:v2", "security:v1"),
            nodes=reviewer_nodes_only(),
            planner_reason=None,
        )


def test_multi_specialist_plan_requires_one_batched_verifier() -> None:
    reviewers = reviewer_nodes_only()
    resolver = _node(
        "review-verifier:v1",
        ReviewPlanNodeType.VERIFIER,
        ReviewPass.VERIFIER,
        depends_on=tuple(node.node_id for node in reviewers),
    )

    with pytest.raises(ValueError, match="batched verifier"):
        ReviewPlan.create(
            task_id=TASK_ID,
            selection_mode="fixed",
            reviewer_references=("correctness:v2", "security:v1"),
            nodes=(*reviewers, resolver),
            planner_reason=None,
        )


@pytest.mark.parametrize("reviewer_reference", ["general:v1", "security:v1"])
def test_single_reviewer_plan_does_not_require_resolver(reviewer_reference: str) -> None:
    plan = ReviewPlan.create(
        task_id=TASK_ID,
        selection_mode="fixed",
        reviewer_references=(reviewer_reference,),
        nodes=(_node(reviewer_reference, ReviewPlanNodeType.REVIEWER, ReviewPass.REVIEWER),),
        planner_reason=None,
    )

    assert plan.reviewer_references == (reviewer_reference,)


def test_adaptive_plan_requires_a_planner_reason() -> None:
    with pytest.raises(ValueError, match="Adaptive plan requires a planner reason"):
        ReviewPlan.create(
            task_id=TASK_ID,
            selection_mode="adaptive",
            reviewer_references=("general:v1",),
            nodes=(_node("general:v1", ReviewPlanNodeType.REVIEWER, ReviewPass.REVIEWER),),
            planner_reason=None,
        )


def test_plan_hash_is_independent_of_reviewer_and_node_input_order() -> None:
    reviewers = reviewer_nodes_only()
    verifier = ReviewPlanNode.create(
        task_id=TASK_ID,
        node_type=ReviewPlanNodeType.VERIFIER,
        agent_reference="review-verifier:v1",
        pass_index=ReviewPass.VERIFIER,
        shard_id="batch",
        logical_attempt_group="primary",
        depends_on=tuple(sorted(node.node_id for node in reviewers)),
    )
    first = ReviewPlan.create(
        task_id=TASK_ID,
        selection_mode="fixed",
        reviewer_references=("security:v1", "correctness:v2"),
        nodes=(*reviewers, verifier),
        planner_reason=None,
    )
    second = ReviewPlan.create(
        task_id=TASK_ID,
        selection_mode="fixed",
        reviewer_references=("correctness:v2", "security:v1"),
        nodes=(verifier, *reversed(reviewers)),
        planner_reason=None,
    )

    assert first.plan_hash == second.plan_hash
    assert first.reviewer_references == tuple(sorted(first.reviewer_references))
    assert first.nodes == tuple(sorted(first.nodes, key=lambda node: node.node_id))


def test_node_identity_includes_the_logical_attempt_group() -> None:
    first = _node("security:v1", ReviewPlanNodeType.REVIEWER, ReviewPass.REVIEWER)
    second = ReviewPlanNode.create(
        task_id=TASK_ID,
        node_type=ReviewPlanNodeType.REVIEWER,
        agent_reference="security:v1",
        pass_index=ReviewPass.REVIEWER,
        shard_id="root",
        logical_attempt_group="repair",
        depends_on=(),
    )

    assert first.node_id != second.node_id


def test_coverage_status_values_are_stable() -> None:
    assert tuple(CoverageStatus) == (
        CoverageStatus.PLANNED,
        CoverageStatus.COMPLETED,
        CoverageStatus.FAILED,
        CoverageStatus.OMITTED,
    )
