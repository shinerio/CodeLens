import json

import pytest

from codelens.review.domain.tool_results import (
    ToolDiagnostic,
    ToolResult,
    ToolResultError,
    ToolResultStatus,
    classify_tool_result,
    parse_tool_result,
)


@pytest.mark.parametrize("status", list(ToolResultStatus))
def test_tool_result_round_trips_every_status(status: ToolResultStatus) -> None:
    result = ToolResult(
        tool="grep",
        status=status,
        data={"路径": "src/中文.py"},
        diagnostics=(
            ToolDiagnostic(
                code="no_content_matches",
                message="没有内容匹配。",
                field="pattern",
                retryable=True,
                suggested_arguments={
                    "pattern": "run_id",
                    "mode": "literal",
                    "path": "src",
                    "file_pattern": "*.py",
                },
            ),
        ),
    )

    serialized = result.to_json()

    assert serialized == result.to_json()
    assert "中文" in serialized
    assert parse_tool_result(serialized) == result


def test_diagnostic_omits_optional_fields_instead_of_serializing_null() -> None:
    payload = json.loads(
        ToolResult(
            tool="read_file",
            status=ToolResultStatus.FAILED,
            data={},
            diagnostics=(ToolDiagnostic("read_failed", "读取失败。", False),),
        ).to_json()
    )

    assert payload["diagnostics"] == [
        {"code": "read_failed", "message": "读取失败。", "retryable": False}
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "2", "tool": "grep", "status": "unknown", "data": {}, "diagnostics": []},
        {"schema_version": "2", "tool": "grep", "status": "success", "data": [], "diagnostics": []},
        {
            "schema_version": "2",
            "tool": "grep",
            "status": "success",
            "data": {},
            "diagnostics": [],
            "extra": True,
        },
        {
            "schema_version": "2",
            "tool": "grep",
            "status": "success",
            "data": {},
            "diagnostics": [{"code": "", "message": "bad", "retryable": False}],
        },
    ],
)
def test_parser_rejects_invalid_envelopes(payload: dict[str, object]) -> None:
    with pytest.raises(ToolResultError):
        parse_tool_result(json.dumps(payload))


def test_constructor_rejects_non_json_serializable_data() -> None:
    with pytest.raises(ToolResultError):
        ToolResult("grep", ToolResultStatus.SUCCESS, {"bad": object()})


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (ToolResultStatus.SUCCESS, "accepted"),
        (ToolResultStatus.PARTIAL, "accepted"),
        (ToolResultStatus.NEEDS_ACTION, "rejected"),
        (ToolResultStatus.REJECTED, "rejected"),
        (ToolResultStatus.FAILED, "rejected"),
    ],
)
def test_host_outcome_depends_only_on_status(
    status: ToolResultStatus, outcome: str
) -> None:
    result = ToolResult("comment", status, {"accepted": status is ToolResultStatus.REJECTED})

    assert classify_tool_result(result.to_json()).outcome == outcome


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"accepted":true}',
        '{"schema_version":"2","tool":"grep","status":"mystery","data":{},"diagnostics":[]}',
    ],
)
def test_unknown_or_legacy_results_are_unclassified(content: str) -> None:
    assert classify_tool_result(content).outcome == "unclassified"
