import pytest
from pydantic import ValidationError

from codelens.findings.infrastructure.model_output import FindingBatchSchema


def _valid_finding() -> dict[str, object]:
    return {
        "reviewer_id": "correctness",
        "category": "logic",
        "title": "Guard is inverted",
        "severity": "high",
        "disposition": "blocking",
        "confidence": 0.94,
        "primary_location": {
            "path": "src/state.py",
            "start_line": 2,
            "end_line": 2,
            "side": "new",
            "excerpt_hash": "a" * 64,
            "is_deleted": False,
        },
        "related_locations": [],
        "changed_hunk_id": "hunk-1",
        "change_origin": "introduced",
        "evidence": [
            {
                "kind": "excerpt",
                "description": "The changed return negates the ready value.",
                "artifact_ref": None,
                "excerpt_hash": "a" * 64,
            }
        ],
        "impact": "Ready states are treated as not ready.",
        "explanation": "The changed boolean expression reverses the intended transition.",
        "reproduction": None,
        "recommendation": "Return value without negation.",
        "suggested_patch": None,
        "rule_sources": [{"path": "REVIEW.md", "content_hash": "b" * 64}],
    }


def test_accepts_strict_versioned_finding_batch_without_model_supplied_ids() -> None:
    batch = FindingBatchSchema.model_validate(
        {"schema_version": "1", "findings": [_valid_finding()]}
    )

    assert batch.findings[0].severity == "high"
    dumped = batch.model_dump()
    assert "id" not in dumped["findings"][0]
    assert "fingerprint" not in dumped["findings"][0]


def test_rejects_invalid_location_range() -> None:
    finding = _valid_finding()
    finding["primary_location"] = {
        **finding["primary_location"],  # type: ignore[dict-item]
        "start_line": 3,
        "end_line": 2,
    }

    with pytest.raises(ValidationError, match="end_line"):
        FindingBatchSchema.model_validate({"schema_version": "1", "findings": [finding]})


def test_requires_changed_hunk_or_data_flow_evidence() -> None:
    finding = _valid_finding()
    finding["changed_hunk_id"] = None

    with pytest.raises(ValidationError, match="change-propagation"):
        FindingBatchSchema.model_validate({"schema_version": "1", "findings": [finding]})


def test_deleted_location_must_use_old_side() -> None:
    finding = _valid_finding()
    finding["primary_location"] = {
        **finding["primary_location"],  # type: ignore[dict-item]
        "side": "new",
        "is_deleted": True,
    }

    with pytest.raises(ValidationError, match="old side"):
        FindingBatchSchema.model_validate({"schema_version": "1", "findings": [finding]})
