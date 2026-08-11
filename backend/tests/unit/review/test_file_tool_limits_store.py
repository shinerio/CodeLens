"""Unit tests for the FilesystemToolLimitsStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codelens.review.domain.tool_limits import ToolLimits
from codelens.review.infrastructure.file_tool_limits import FilesystemToolLimitsStore


def test_store_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    store = FilesystemToolLimitsStore(tmp_path)
    limits = store.get_tool_limits()
    assert limits == ToolLimits()


def test_store_persists_and_reloads_limits(tmp_path: Path) -> None:
    store = FilesystemToolLimitsStore(tmp_path)
    custom = ToolLimits(
        max_results=500,
        max_read_bytes=128 * 1024,
        max_scan_bytes=2 * 1024 * 1024,
        max_source_bytes=2 * 1024 * 1024,
        max_lines=1000,
        max_path_chars=2048,
        max_pattern_chars=1024,
        regex_timeout_seconds=60.0,
        comment_batch_size=50,
        short_text_max=480,
        long_text_max=16000,
        task_summary_max=16000,
        context_compaction_enabled=False,
        context_compaction_trigger_bytes=262_144,
        context_compaction_target_bytes=131_072,
        context_compaction_keep_recent_evidence_results=4,
    )
    store.save_tool_limits(custom)
    reloaded = store.get_tool_limits()
    assert reloaded == custom


def test_store_loads_legacy_document_with_context_compaction_defaults(tmp_path: Path) -> None:
    store = FilesystemToolLimitsStore(tmp_path)
    store.save_tool_limits(ToolLimits())
    path = tmp_path / "tool-limits.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for field in tuple(payload):
        if field.startswith("context_compaction_"):
            del payload[field]
    path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = store.get_tool_limits()

    assert reloaded.context_compaction_enabled is True
    assert reloaded.context_compaction_target_bytes < reloaded.context_compaction_trigger_bytes


def test_store_overwrites_previous_limits(tmp_path: Path) -> None:
    store = FilesystemToolLimitsStore(tmp_path)
    first = ToolLimits(max_results=100)
    second = ToolLimits(max_results=300)
    store.save_tool_limits(first)
    store.save_tool_limits(second)
    assert store.get_tool_limits().max_results == 300


def test_store_creates_data_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "data"
    store = FilesystemToolLimitsStore(nested)
    store.save_tool_limits(ToolLimits())
    assert (nested / "tool-limits.json").exists()


def test_store_rejects_invalid_json(tmp_path: Path) -> None:
    store = FilesystemToolLimitsStore(tmp_path)
    store.save_tool_limits(ToolLimits())
    (tmp_path / "tool-limits.json").write_text("not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        store.get_tool_limits()


def test_store_rejects_non_dict_payload(tmp_path: Path) -> None:
    store = FilesystemToolLimitsStore(tmp_path)
    (tmp_path / "tool-limits.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        store.get_tool_limits()


def test_store_rejects_boolean_field(tmp_path: Path) -> None:
    store = FilesystemToolLimitsStore(tmp_path)
    store.save_tool_limits(ToolLimits())
    payload = json.loads((tmp_path / "tool-limits.json").read_text(encoding="utf-8"))
    payload["max_results"] = True
    (tmp_path / "tool-limits.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="max_results"):
        store.get_tool_limits()


def test_store_uses_atomic_write(tmp_path: Path) -> None:
    store = FilesystemToolLimitsStore(tmp_path)
    store.save_tool_limits(ToolLimits())
    # Verify no temp files remain
    temp_files = list(tmp_path.glob(".tool-limits-*.tmp"))
    assert len(temp_files) == 0
