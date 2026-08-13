import json

import pytest

from codelens.findings.domain.existing_findings import (
    ExistingFinding,
    ExistingFindingSet,
)


def test_existing_finding_set_serializes_stably_and_deduplicates_by_source_identity() -> None:
    duplicate = ExistingFinding(
        source_id="github",
        finding_id="discussion-42",
        title="Null dereference",
        content="This branch can dereference user after lookup fails.",
        path="src/users.py",
        side="new",
        start_line=40,
        end_line=42,
        existing_code="if user is None:\n    return user.name",
        fingerprint="f" * 64,
    )

    findings = ExistingFindingSet.from_findings((duplicate, duplicate))

    assert len(findings.items) == 1
    assert json.loads(findings.canonical_json) == [
        {
            "content": "This branch can dereference user after lookup fails.",
            "end_line": 42,
            "existing_code": "if user is None:\n    return user.name",
            "finding_id": "discussion-42",
            "fingerprint": "f" * 64,
            "path": "src/users.py",
            "side": "new",
            "source_id": "github",
            "start_line": 40,
            "title": "Null dereference",
        }
    ]
    assert len(findings.content_hash) == 64


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {
                "path": "../secret.py",
                "side": "new",
                "start_line": 1,
                "end_line": 1,
                "existing_code": "secret = read()",
            },
            "normalized repository-relative",
        ),
        ({"path": "src/users.py"}, "location fields must be provided together"),
        (
            {
                "path": "src/users.py",
                "side": "new",
                "start_line": 2,
                "end_line": 1,
                "existing_code": "return user",
            },
            "line range is invalid",
        ),
        (
            {"path": "src/users.py", "side": "new", "start_line": 2, "end_line": 2},
            "location fields must be provided together",
        ),
    ],
)
def test_existing_finding_rejects_unsafe_or_incomplete_locations(
    changes: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "source_id": "github",
        "finding_id": "discussion-42",
        "title": "Null dereference",
        "content": "Details",
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        ExistingFinding(**values)  # type: ignore[arg-type]
