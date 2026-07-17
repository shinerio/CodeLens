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
    assert not hasattr(run, "succeed")


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

