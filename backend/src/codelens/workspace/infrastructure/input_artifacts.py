import asyncio
import hashlib
import os
import re
import uuid
from pathlib import Path

from codelens.workspace.domain.models import OpaqueArtifact

_REFERENCE_PATTERN = re.compile(r"input_[0-9a-f]{32}\Z")
_STAGING_PATTERN = re.compile(r"\.input_[0-9a-f]{32}\.tmp\Z")


def _write_artifact(root: Path, payload: bytes) -> OpaqueArtifact:
    root.mkdir(parents=True, exist_ok=True)
    reference = f"input_{uuid.uuid4().hex}"
    destination = root / reference
    staging = root / f".{reference}.tmp"
    content_hash = hashlib.sha256(payload).hexdigest()
    with staging.open("xb") as artifact:
        artifact.write(payload)
        artifact.flush()
        os.fsync(artifact.fileno())
    os.replace(staging, destination)
    return OpaqueArtifact(reference, content_hash, len(payload))


def _read_artifact(root: Path, reference: str, expected_hash: str) -> bytes:
    if _REFERENCE_PATTERN.fullmatch(reference) is None:
        raise ValueError("invalid input Artifact reference")
    payload = (root / reference).read_bytes()
    actual_hash = hashlib.sha256(payload).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("input Artifact hash mismatch")
    return payload


def _discard_artifact(root: Path, reference: str) -> None:
    if _REFERENCE_PATTERN.fullmatch(reference) is None:
        raise ValueError("invalid input Artifact reference")
    (root / reference).unlink(missing_ok=True)


def _prune_orphans(root: Path, referenced: frozenset[str]) -> None:
    if not root.exists():
        return
    for candidate in root.iterdir():
        name = candidate.name
        is_orphan = _REFERENCE_PATTERN.fullmatch(name) is not None and name not in referenced
        is_staging = _STAGING_PATTERN.fullmatch(name) is not None
        if (is_orphan or is_staging) and (candidate.is_file() or candidate.is_symlink()):
            candidate.unlink()


class FilesystemInputArtifactStore:
    """Store input bytes atomically under an app-data-contained opaque namespace."""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    async def write_bytes(self, payload: bytes) -> OpaqueArtifact:
        """Persist bytes using fsync and atomic rename before returning a reference."""

        return await asyncio.to_thread(_write_artifact, self._root, payload)

    async def read_bytes(self, reference: str, expected_hash: str) -> bytes:
        """Read a contained Artifact only when its SHA-256 identity matches."""

        return await asyncio.to_thread(_read_artifact, self._root, reference, expected_hash)

    async def discard(self, reference: str) -> None:
        """Remove one unreferenced input Artifact by opaque ID."""

        await asyncio.to_thread(_discard_artifact, self._root, reference)

    async def prune_orphans(self, referenced: frozenset[str]) -> None:
        """Remove only verified unreferenced files from the contained input namespace."""

        await asyncio.to_thread(_prune_orphans, self._root, referenced)
