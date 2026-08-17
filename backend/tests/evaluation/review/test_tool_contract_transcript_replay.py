"""Freeze the redacted call-pattern baseline that motivated Tool Contract v2.

The tests deliberately inspect only tool names, argument shapes, result shapes, and
bounded counters. They never include transcript source, prompts, or model output in
assertion messages or test logs.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from codelens.review.domain.tool_results import parse_tool_result
from codelens.review.infrastructure.model_paths import match_model_glob, parse_model_glob
from codelens.testing.correctness_fixture import (
    deterministic_reviewer_tool_scenario,
    load_simple_branch_comments,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_TRANSCRIPT_PATH = (
    _REPOSITORY_ROOT / "data/artifacts/transcripts/review_7aae3df27e6a4416a766339dbc422e7f.json"
)


def _records() -> list[dict[str, Any]]:
    if not _TRANSCRIPT_PATH.exists():
        pytest.skip(f"transcript fixture missing: {_TRANSCRIPT_PATH}")
    decoded = json.loads(_TRANSCRIPT_PATH.read_text(encoding="utf-8"))
    assert isinstance(decoded, list)
    return [record for record in decoded if isinstance(record, dict)]


def _decode_json_string(value: object) -> object:
    if not isinstance(value, str):
        return value
    return json.loads(value)


def _tool_arguments(record: dict[str, Any]) -> dict[str, Any]:
    item = _decode_json_string(record["content"])
    assert isinstance(item, dict)
    arguments = _decode_json_string(item["arguments"])
    assert isinstance(arguments, dict)
    return arguments


def _tool_result(record: dict[str, Any]) -> object:
    outer = _decode_json_string(record["content"])
    return _decode_json_string(outer)


def test_transcript_freezes_tool_call_distribution_without_replaying_model_content() -> None:
    records = _records()

    distribution = Counter(
        str(record["metadata"]["tool_name"])
        for record in records
        if record.get("kind") == "tool_call"
    )

    assert len(records) == 491
    assert distribution == {
        "find_files": 5,
        "grep": 36,
        "read_file": 52,
        "get_diff": 37,
        "comment": 1,
        "task_done": 4,
    }


def test_directory_basename_glob_baseline_contains_the_empty_result_mismatch() -> None:
    records = _records()
    calls = {
        str(record["metadata"]["tool_call_id"]): _tool_arguments(record)
        for record in records
        if record.get("kind") == "tool_call"
        and record.get("metadata", {}).get("tool_name") == "grep"
    }
    empty_python_glob_results = 0
    total_empty_grep_results = 0
    for record in records:
        if (
            record.get("kind") != "tool_result"
            or record.get("metadata", {}).get("tool_name") != "grep"
        ):
            continue
        result = _tool_result(record)
        assert isinstance(result, dict)
        matches = result.get("matches")
        if matches != []:
            continue
        total_empty_grep_results += 1
        call_id = str(record["metadata"]["tool_call_id"])
        if calls[call_id].get("file_pattern") == "*.py":
            empty_python_glob_results += 1

    assert total_empty_grep_results == 18
    assert empty_python_glob_results == 17


def test_repeated_call_warning_baseline_contains_non_json_inner_tool_result() -> None:
    warning_results = []
    for record in _records():
        if (
            record.get("kind") != "tool_result"
            or record.get("metadata", {}).get("tool_name") != "get_diff"
        ):
            continue
        inner = _decode_json_string(record["content"])
        if isinstance(inner, str) and "\n\n[" in inner:
            warning_results.append(inner)

    assert len(warning_results) == 1
    try:
        json.loads(warning_results[0])
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("legacy warning unexpectedly remained valid JSON")


def test_baseline_has_no_structural_comment_retraction() -> None:
    records = _records()
    tool_names = {
        str(record["metadata"]["tool_name"])
        for record in records
        if record.get("kind") == "tool_call"
    }
    comment_results = [
        _tool_result(record)
        for record in records
        if record.get("kind") == "tool_result"
        and record.get("metadata", {}).get("tool_name") == "comment"
    ]

    assert "retract_comment" not in tool_names
    assert comment_results == [
        {
            "accepted": True,
            "accepted_count": 1,
            "comment_count": 1,
            "rejected_comments": [],
            "rejected_count": 0,
        }
    ]


def test_legacy_directory_python_globs_replay_as_recursive_basename_patterns() -> None:
    calls = [
        _tool_arguments(record)
        for record in _records()
        if record.get("kind") == "tool_call"
        and record.get("metadata", {}).get("tool_name") == "grep"
        and _tool_arguments(record).get("file_pattern") == "*.py"
    ]
    parsed = parse_model_glob("*.py")

    assert len(calls) > 0
    assert parsed.pattern_scope == "recursive_basename"
    assert all(match_model_glob("nested/deep/compiler_plan.py", parsed) for _call in calls)


def test_deterministic_fake_model_scenario_is_complete_json_and_retracts_candidate() -> None:
    comments = load_simple_branch_comments()
    active_candidate_ids = tuple(
        f"candidate_fixture_active_{index}" for index in range(len(comments))
    )
    events = deterministic_reviewer_tool_scenario(comments, active_candidate_ids)
    calls = [event for event in events if event.kind == "tool_call"]
    results = [event for event in events if event.kind == "tool_result"]

    assert [event.metadata["tool_name"] for event in calls] == [
        "find_files",
        "grep",
        "read_file",
        "read_file",
        "get_diff",
        "get_diff",
        "comment",
        "retract_comment",
        "task_done",
    ]
    assert len(calls) == len(results)
    parsed_results = [parse_tool_result(event.content) for event in results]
    assert [result.status.value for result in parsed_results] == [
        "success",
        "success",
        "partial",
        "success",
        "partial",
        "success",
        "success",
        "success",
        "success",
    ]
    retraction_arguments = json.loads(calls[-2].content)
    retraction_result = parsed_results[-2]
    transient_id = retraction_arguments["candidate_ids"][0]
    assert transient_id not in active_candidate_ids
    assert retraction_result.data["results"] == [
        {"candidate_id": transient_id, "status": "retracted"}
    ]
    assert parsed_results[-1].data["active_comment_count"] == len(active_candidate_ids)
