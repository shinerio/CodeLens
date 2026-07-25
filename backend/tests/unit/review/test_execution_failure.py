from codelens.review.domain.errors import TransientAgentRuntimeError
from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.worker.execution import _failure_diagnostic


def test_failure_diagnostic_preserves_safe_agent_runtime_details() -> None:
    diagnostic = _failure_diagnostic(
        TransientAgentRuntimeError(
            "provider response body must stay private",
            phase="investigation",
            reason_code="provider_timeout",
            retryable=True,
        )
    )

    assert diagnostic.metadata == {
        "error_code": "transient_agent_runtime_error",
        "error_type": "TransientAgentRuntimeError",
        "phase": "investigation",
        "reason_code": "provider_timeout",
        "retryable": "true",
    }
    assert "provider response body" not in diagnostic.content


def test_failure_diagnostic_classifies_repository_boundary_failures() -> None:
    diagnostic = _failure_diagnostic(
        InvalidRepositoryError("private repository path must stay private")
    )

    assert diagnostic.metadata == {
        "error_code": "invalid_repository",
        "error_type": "InvalidRepositoryError",
        "reason_code": "repository_validation_failed",
        "retryable": "false",
    }
    assert "private repository path" not in diagnostic.content


def test_failure_diagnostic_classifies_unexpected_internal_failures() -> None:
    diagnostic = _failure_diagnostic(TypeError("cannot pickle private runtime state"))

    assert diagnostic.metadata == {
        "error_code": "unexpected_review_error",
        "error_type": "TypeError",
        "reason_code": "internal_review_error",
        "retryable": "false",
    }
    assert "cannot pickle" not in diagnostic.content
