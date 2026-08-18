import json

from codelens.findings.domain.existing_findings import ExistingFinding, ExistingFindingSet
from codelens.worker.execution import add_existing_findings_context


def test_existing_findings_are_injected_as_bounded_role_context() -> None:
    base = json.dumps(
        {"review_files": [], "repository_instructions": []},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    findings = ExistingFindingSet.from_findings(
        (
            ExistingFinding(
                source_id="local",
                finding_id="finding-1",
                title="Existing issue",
                content="Do not report this issue again.",
                path="src/service.py",
                side="new",
                start_line=12,
                end_line=12,
                existing_code="return account.name",
            ),
        )
    )

    payload = add_existing_findings_context(base, findings)

    assert json.loads(payload)["role_context"]["existing_findings"] == {
        "findings": [
            {
                "content": "Do not report this issue again.",
                "finding_id": "finding-1",
                "existing_code": "return account.name",
                "end_line": 12,
                "path": "src/service.py",
                "side": "new",
                "source_id": "local",
                "start_line": 12,
                "title": "Existing issue",
            }
        ],
        "schema_version": "1",
    }


def test_empty_existing_findings_leave_payload_unchanged() -> None:
    base = b'{"repository_instructions":[],"review_files":[]}'

    assert add_existing_findings_context(base, ExistingFindingSet.empty()) == base
