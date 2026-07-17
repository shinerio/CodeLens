import asyncio
import hashlib
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from codelens.review.domain.ports import RunOutputArtifact
from codelens.review.infrastructure.database import Database
from codelens.review.infrastructure.tables import artifacts

_REFERENCE_PATTERN = re.compile(r"artifact_[0-9a-f]{32}\Z")
_STORAGE_KEY_PATTERN = re.compile(r"blob_[0-9a-f]{32}\Z")


def _write_file(root: Path, storage_key: str, payload: bytes) -> None:
    root.mkdir(parents=True, exist_ok=True)
    destination = root / storage_key
    staging = root / f".{storage_key}.tmp"
    with staging.open("xb") as artifact:
        artifact.write(payload)
        artifact.flush()
        os.fsync(artifact.fileno())
    os.replace(staging, destination)


def _read_file(root: Path, storage_key: str) -> bytes:
    if _STORAGE_KEY_PATTERN.fullmatch(storage_key) is None:
        raise ValueError("invalid Artifact storage mapping")
    return (root / storage_key).read_bytes()


def _discard_file(root: Path, storage_key: str) -> None:
    (root / storage_key).unlink(missing_ok=True)


class FilesystemRunArtifactStore:
    """Persist canonical Agent output behind database-mapped opaque references."""

    def __init__(self, database: Database, root: Path) -> None:
        self._database = database
        self._root = root.expanduser().resolve()

    async def write_output(self, run_id: str, payload: bytes) -> RunOutputArtifact:
        """Fsync and atomically rename bytes before committing their metadata mapping."""

        reference = f"artifact_{uuid.uuid4().hex}"
        storage_key = f"blob_{uuid.uuid4().hex}"
        content_hash = hashlib.sha256(payload).hexdigest()
        await asyncio.to_thread(_write_file, self._root, storage_key, payload)

        async def operation(session: AsyncSession) -> None:
            await session.execute(
                insert(artifacts).values(
                    reference=reference,
                    run_id=run_id,
                    storage_key=storage_key,
                    content_hash=content_hash,
                    size_bytes=len(payload),
                    created_at=datetime.now(UTC),
                )
            )

        try:
            await self._database.run_transaction(operation)
        except BaseException:
            await asyncio.to_thread(_discard_file, self._root, storage_key)
            raise
        return RunOutputArtifact(reference, content_hash, len(payload))

    async def read_output(self, reference: str, expected_hash: str) -> bytes:
        """Resolve an opaque ID through SQLite and fail closed on size/hash mismatch."""

        if _REFERENCE_PATTERN.fullmatch(reference) is None:
            raise ValueError("invalid Artifact reference")
        async with self._database.sessions() as session:
            row = (
                (await session.execute(select(artifacts).where(artifacts.c.reference == reference)))
                .mappings()
                .one()
            )
        stored_hash = str(row["content_hash"])
        if stored_hash != expected_hash:
            raise ValueError("Artifact expected hash mismatch")
        payload = await asyncio.to_thread(_read_file, self._root, str(row["storage_key"]))
        if hashlib.sha256(payload).hexdigest() != stored_hash:
            raise ValueError("Artifact hash mismatch")
        if len(payload) != int(row["size_bytes"]):
            raise ValueError("Artifact size mismatch")
        return payload
