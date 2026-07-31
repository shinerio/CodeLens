import pytest

from codelens.review.domain.agent_run import (
    AgentRun,
    AgentRunStatus,
    InvalidAgentRunStateError,
)


def test_run_identity_includes_pass_shard_and_attempt_group() -> None:
    root = AgentRun.create(
        task_id="review-1",
        agent_version="correctness:v1",
        pass_index=0,
        shard_id="root",
        logical_attempt_group="primary",
    )
    shard = AgentRun.create(
        task_id="review-1",
        agent_version="correctness:v1",
        pass_index=0,
        shard_id="payments",
        logical_attempt_group="primary",
    )
    second_pass = AgentRun.create(
        task_id="review-1",
        agent_version="correctness:v1",
        pass_index=1,
        shard_id="root",
        logical_attempt_group="primary",
    )

    assert len({root.run_id, shard.run_id, second_pass.run_id}) == 3


def test_agent_run_requires_output_checkpoint_before_validation() -> None:
    run = AgentRun.create(
        task_id="review-1",
        agent_version="correctness:v1",
        pass_index=0,
        shard_id="root",
        logical_attempt_group="primary",
    )

    run.start()
    run.save_output("artifact-1", "a" * 64)
    run.begin_validation()

    assert run.status is AgentRunStatus.VALIDATING
    assert run.output_artifact_ref == "artifact-1"


def test_failed_run_retries_only_within_policy() -> None:
    run = AgentRun.create(
        task_id="review-1",
        agent_version="correctness:v1",
        pass_index=0,
        shard_id="root",
        logical_attempt_group="primary",
    )
    run.start()
    run.fail("transient_model_error")
    run.retry(max_attempts=2)
    run.start()
    run.timeout()

    with pytest.raises(InvalidAgentRunStateError):
        run.retry(max_attempts=2)


def test_physical_retry_keeps_the_same_logical_run_id() -> None:
    run = AgentRun.create(
        task_id="review-1",
        agent_version="security:v1",
        pass_index=1,
        shard_id="root",
        logical_attempt_group="primary",
        node_role="reviewer",
        capability_fingerprint="a" * 64,
    )
    logical_run_id = run.run_id

    run.start()
    run.fail("provider_unavailable")
    run.retry(max_attempts=2)
    run.start()

    assert run.run_id == logical_run_id
    assert run.execution_attempts == 2
    assert run.node_role == "reviewer"
    assert run.capability_fingerprint == "a" * 64


def test_planned_node_can_be_skipped_without_a_physical_attempt() -> None:
    run = AgentRun.create(
        task_id="review-1",
        agent_version="review-verifier:v1",
        pass_index=3,
        shard_id="batch",
        logical_attempt_group="primary",
        node_role="verifier",
        capability_fingerprint="b" * 64,
    )

    run.skip("no_verification_required")

    assert run.status is AgentRunStatus.SKIPPED
    assert run.execution_attempts == 0
    assert run.error_code == "no_verification_required"
