import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

TransactionResult = TypeVar("TransactionResult")


def _configure_sqlite(
    dbapi_connection: Any,
    _connection_record: Any,
    *,
    busy_timeout_ms: int,
) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms:d}")
    finally:
        cursor.close()


def _upgrade_database(database_url: str) -> None:
    backend_root = Path(__file__).resolve().parents[4]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


class Database:
    """Own the async SQLite engine, sessions, PRAGMAs, and Alembic lifecycle."""

    def __init__(
        self,
        database_url: str,
        *,
        busy_timeout_ms: int = 5_000,
        max_busy_retries: int = 3,
    ) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        if max_busy_retries < 0:
            raise ValueError("max_busy_retries cannot be negative")
        self.database_url = database_url
        self._max_busy_retries = max_busy_retries
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        event.listen(
            self.engine.sync_engine,
            "connect",
            partial(_configure_sqlite, busy_timeout_ms=busy_timeout_ms),
        )
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def migrate(self) -> None:
        """Apply Alembic migrations outside the event loop's blocking path."""

        await asyncio.to_thread(_upgrade_database, self.database_url)

    async def run_transaction(
        self,
        operation: Callable[[AsyncSession], Awaitable[TransactionResult]],
    ) -> TransactionResult:
        """Retry an entire transaction only when SQLite reports transient contention."""

        for attempt in range(self._max_busy_retries + 1):
            try:
                async with self.sessions.begin() as session:
                    return await operation(session)
            except OperationalError as error:
                message = str(error.orig).lower()
                is_busy = "database is locked" in message or "database is busy" in message
                if not is_busy or attempt == self._max_busy_retries:
                    raise
                await asyncio.sleep(0.025 * (attempt + 1))
        raise RuntimeError("unreachable SQLite transaction retry state")

    async def dispose(self) -> None:
        """Close all pooled database resources."""

        await self.engine.dispose()
