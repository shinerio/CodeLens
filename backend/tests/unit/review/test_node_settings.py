"""Unit tests for the NodeSettings domain model and its filesystem store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codelens.bootstrap.node_settings import NodeSettings
from codelens.review.infrastructure.file_node_settings import FilesystemNodeSettingsStore


def test_default_node_settings_match_settings_defaults() -> None:
    settings = NodeSettings()
    assert settings.memory_limit_mb == 2048
    assert settings.memory_check_interval_seconds == 5.0
    assert settings.memory_cleanup_threshold_ratio == 0.85
    assert settings.memory_reject_threshold_ratio == 0.95
    assert settings.max_active_reviews == 4
    assert settings.max_active_agent_runs == 8
    assert settings.max_agent_runs_per_review == 4


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("memory_limit_mb", 256),
        ("memory_check_interval_seconds", 0),
        ("memory_check_interval_seconds", -1.0),
        ("memory_cleanup_threshold_ratio", 0.0),
        ("memory_reject_threshold_ratio", 1.5),
        ("max_active_reviews", 0),
        ("max_active_agent_runs", -1),
    ],
)
def test_node_settings_reject_out_of_range_values(field: str, invalid_value: int | float) -> None:
    with pytest.raises(ValueError):
        NodeSettings(**{field: invalid_value})


def test_node_settings_reject_cleanup_ge_reject() -> None:
    with pytest.raises(ValueError, match="thresholds"):
        NodeSettings(memory_cleanup_threshold_ratio=0.9, memory_reject_threshold_ratio=0.9)


def test_node_settings_reject_per_review_exceeds_global() -> None:
    with pytest.raises(ValueError, match="per-review"):
        NodeSettings(max_active_agent_runs=4, max_agent_runs_per_review=8)


def test_node_settings_are_frozen() -> None:
    settings = NodeSettings()
    with pytest.raises(AttributeError):
        settings.memory_limit_mb = 999  # type: ignore[misc]


def test_store_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    store = FilesystemNodeSettingsStore(tmp_path)
    assert store.get_node_settings() == NodeSettings()


def test_store_persists_and_reloads(tmp_path: Path) -> None:
    store = FilesystemNodeSettingsStore(tmp_path)
    custom = NodeSettings(
        memory_limit_mb=4096,
        memory_check_interval_seconds=10.0,
        memory_cleanup_threshold_ratio=0.8,
        memory_reject_threshold_ratio=0.9,
        max_active_reviews=8,
        max_active_agent_runs=16,
        max_agent_runs_per_review=6,
    )
    store.save_node_settings(custom)
    assert store.get_node_settings() == custom


def test_store_uses_custom_defaults_when_file_missing(tmp_path: Path) -> None:
    custom_defaults = NodeSettings(memory_limit_mb=8192, max_active_reviews=2)
    store = FilesystemNodeSettingsStore(tmp_path, defaults=custom_defaults)
    assert store.get_node_settings() == custom_defaults


def test_store_overwrites_previous(tmp_path: Path) -> None:
    store = FilesystemNodeSettingsStore(tmp_path)
    store.save_node_settings(NodeSettings(memory_limit_mb=4096))
    store.save_node_settings(NodeSettings(memory_limit_mb=8192))
    assert store.get_node_settings().memory_limit_mb == 8192


def test_store_creates_data_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "data"
    store = FilesystemNodeSettingsStore(nested)
    store.save_node_settings(NodeSettings())
    assert (nested / "node-settings.json").exists()


def test_store_rejects_invalid_json(tmp_path: Path) -> None:
    store = FilesystemNodeSettingsStore(tmp_path)
    (tmp_path / "node-settings.json").write_text("not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        store.get_node_settings()


def test_store_rejects_non_dict_payload(tmp_path: Path) -> None:
    store = FilesystemNodeSettingsStore(tmp_path)
    (tmp_path / "node-settings.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        store.get_node_settings()


def test_store_rejects_boolean_field(tmp_path: Path) -> None:
    store = FilesystemNodeSettingsStore(tmp_path)
    store.save_node_settings(NodeSettings())
    payload = json.loads((tmp_path / "node-settings.json").read_text(encoding="utf-8"))
    payload["max_active_reviews"] = True
    (tmp_path / "node-settings.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="max_active_reviews"):
        store.get_node_settings()


def test_store_uses_atomic_write(tmp_path: Path) -> None:
    store = FilesystemNodeSettingsStore(tmp_path)
    store.save_node_settings(NodeSettings())
    assert list(tmp_path.glob(".node-settings-*.tmp")) == []
