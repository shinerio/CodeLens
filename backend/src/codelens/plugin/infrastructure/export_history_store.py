"""SQLite-backed export history store.

Persists export attempts so the UI can display historical results.
"""

import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from codelens.plugin.domain.models import ExportHistoryEntry


class SqliteExportHistoryStore:
    """Store export history in the existing SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_table(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS export_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plugin_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    output_path TEXT,
                    error TEXT,
                    exported_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_export_history_task_id
                ON export_history(task_id)
            """)
            conn.commit()

    def _save_sync(self, entry: ExportHistoryEntry) -> None:
        """Synchronous save implementation."""
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO export_history
                        (plugin_id, task_id, success, output_path, error, exported_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.plugin_id,
                        entry.task_id,
                        1 if entry.success else 0,
                        entry.output_path,
                        entry.error,
                        entry.exported_at.isoformat(),
                    ),
                )

    def _list_by_task_sync(self, task_id: str) -> list[ExportHistoryEntry]:
        """Synchronous list_by_task implementation."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                SELECT plugin_id, task_id, success, output_path, error, exported_at
                FROM export_history
                WHERE task_id = ?
                ORDER BY id DESC
                """,
                (task_id,),
            )
            rows = cursor.fetchall()

        return [
            ExportHistoryEntry(
                plugin_id=row["plugin_id"],
                task_id=row["task_id"],
                success=bool(row["success"]),
                output_path=row["output_path"],
                error=row["error"],
                exported_at=datetime.fromisoformat(row["exported_at"]),
            )
            for row in rows
        ]

    async def save(self, entry: ExportHistoryEntry) -> None:
        """Persist one export history entry."""
        await asyncio.to_thread(self._save_sync, entry)

    async def list_by_task(self, task_id: str) -> list[ExportHistoryEntry]:
        """Return all export history entries for a task, newest first."""
        return await asyncio.to_thread(self._list_by_task_sync, task_id)
