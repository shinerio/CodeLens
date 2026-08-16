from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from codelens.review.infrastructure.tables import metadata


def _alembic_config(database_path: Path) -> Config:
    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_v2_database_keeps_one_initial_revision_and_linear_upgrades(tmp_path: Path) -> None:
    config = _alembic_config(tmp_path / "unused.sqlite3")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0004_version_sse_events"]
    baseline = scripts.get_revision("0001_codelens_v2")
    assert baseline is not None
    assert baseline.down_revision is None
    assert [item.revision for item in scripts.walk_revisions()] == [
        "0004_version_sse_events",
        "0003_correct_empty_findings_hash",
        "0002_add_existing_findings",
        "0001_codelens_v2",
    ]


async def test_v2_database_initializes_complete_metadata_from_empty_file(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "review.sqlite3"
    config = _alembic_config(database_path)

    await asyncio.to_thread(command.upgrade, config, "head")

    with sqlite3.connect(database_path) as connection:
        actual_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if row[0] not in {"alembic_version", "sqlite_sequence"}
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        task_columns = {row[1] for row in connection.execute("PRAGMA table_info(review_tasks)")}
        finding_columns = {row[1] for row in connection.execute("PRAGMA table_info(findings)")}
        decision_cluster_indexes = {
            row[1]: bool(row[2])
            for row in connection.execute("PRAGMA index_list(verdict_decision_clusters)")
        }

    assert actual_tables == set(metadata.tables)
    assert revision == ("0004_version_sse_events",)
    assert "candidate_paths_json" in task_columns
    assert "target_paths_json" not in task_columns
    assert "verdict_decision_id" in finding_columns
    assert "confidence" not in finding_columns
    assert "verdict_decisions" in actual_tables
    assert "verdict_decision_clusters" in actual_tables
    assert any(decision_cluster_indexes.values())


async def test_sse_event_migration_versions_existing_outbox_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "review.sqlite3"
    config = _alembic_config(database_path)
    await asyncio.to_thread(command.upgrade, config, "0003_correct_empty_findings_hash")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO events (task_id, event_type, payload_json, created_at) "
            "VALUES ('task', 'review.created', '{}', '2026-01-01 00:00:00+00:00')"
        )

    await asyncio.to_thread(command.upgrade, config, "head")

    with sqlite3.connect(database_path) as connection:
        event_type = connection.execute("SELECT event_type FROM events").fetchone()

    assert event_type == ("review.created.v2",)


async def test_v2_database_downgrades_only_to_empty_base(tmp_path: Path) -> None:
    database_path = tmp_path / "review.sqlite3"
    config = _alembic_config(database_path)
    await asyncio.to_thread(command.upgrade, config, "head")

    await asyncio.to_thread(command.downgrade, config, "base")

    with sqlite3.connect(database_path) as connection:
        remaining_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if row[0] not in {"alembic_version", "sqlite_sequence"}
        }
    assert remaining_tables == set()
