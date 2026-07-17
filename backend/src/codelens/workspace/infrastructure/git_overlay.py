import asyncio
import base64
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import TypedDict

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.models import RepositoryFingerprint, TaskWorktree
from codelens.workspace.infrastructure.git_cli import GitCli

_MAX_ENTRY_BYTES = 1024 * 1024
_MAX_TOTAL_ENTRY_BYTES = 8 * 1024 * 1024


class _OverlayEntry(TypedDict):
    path: str
    mode: int
    kind: str
    content: str
    origin: str


class _OverlayPayload(TypedDict):
    schema_version: int
    tracked_patch: str
    entries: list[_OverlayEntry]


def _normalize_path(path: str) -> str:
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or ".." in candidate.parts or "\0" in path:
        raise InvalidRepositoryError("invalid overlay path")
    return candidate.as_posix()


def _control_paths(target_paths: tuple[str, ...]) -> tuple[str, ...]:
    candidates = {"AGENTS.md", "REVIEW.md"}
    for target_path in target_paths:
        target = PurePosixPath(_normalize_path(target_path))
        current = PurePosixPath()
        for part in target.parent.parts:
            current /= part
            candidates.add((current / "REVIEW.md").as_posix())
        candidates.add(f"{target.as_posix()}.review.md")
    return tuple(sorted(candidates))


def _read_entries(
    repository: Path,
    untracked_paths: tuple[str, ...],
    control_paths: tuple[str, ...],
) -> list[_OverlayEntry]:
    repository_root = repository.resolve()
    origins = {path: "untracked" for path in untracked_paths}
    origins.update({path: "control" for path in control_paths})
    entries: list[_OverlayEntry] = []
    total_bytes = 0
    for path, origin in sorted(origins.items()):
        normalized = _normalize_path(path)
        absolute = repository_root / normalized
        try:
            metadata = absolute.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(absolute)
            encoded = target.encode("utf-8")
            kind = "symlink"
        elif stat.S_ISREG(metadata.st_mode):
            resolved = absolute.resolve()
            if not resolved.is_relative_to(repository_root):
                raise InvalidRepositoryError("overlay path escapes repository")
            encoded = absolute.read_bytes()
            kind = "file"
        else:
            continue
        if len(encoded) > _MAX_ENTRY_BYTES:
            raise InvalidRepositoryError("overlay entry exceeds the configured size limit")
        total_bytes += len(encoded)
        if total_bytes > _MAX_TOTAL_ENTRY_BYTES:
            raise InvalidRepositoryError("overlay entries exceed the configured total size limit")
        entries.append(
            {
                "path": normalized,
                "mode": stat.S_IMODE(metadata.st_mode),
                "kind": kind,
                "content": base64.b64encode(encoded).decode("ascii"),
                "origin": origin,
            }
        )
    return entries


def _canonical_payload(tracked_patch: bytes, entries: list[_OverlayEntry]) -> bytes:
    payload: _OverlayPayload = {
        "schema_version": 1,
        "tracked_patch": base64.b64encode(tracked_patch).decode("ascii"),
        "entries": entries,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _materialize_entries(root: Path, entries: list[_OverlayEntry]) -> None:
    root = root.resolve()
    for entry in entries:
        relative = _normalize_path(entry["path"])
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = destination.parent.resolve()
        if not resolved_parent.is_relative_to(root):
            raise InvalidRepositoryError("overlay destination escapes worktree")
        content = base64.b64decode(entry["content"], validate=True)
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        if entry["kind"] == "symlink":
            target = content.decode("utf-8", errors="strict")
            destination.symlink_to(target)
        elif entry["kind"] == "file":
            destination.write_bytes(content)
            destination.chmod(entry["mode"])
        else:
            raise ValueError("unknown overlay entry kind")


class GitReviewInputCaptureAdapter:
    """Capture bounded dirty and control inputs from a source checkout pre-task."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def fingerprint(
        self,
        repository: Path,
        target_paths: tuple[str, ...],
    ) -> RepositoryFingerprint:
        """Hash HEAD, staged diff, and the complete canonical overlay view."""

        head = await self._git.run(repository, "rev-parse", "HEAD")
        head_oid = head.stdout.decode("ascii", errors="strict").strip()
        staged = await self._git.run(repository, "diff", "--binary", "--cached", "HEAD", "--")
        payload = await self.capture_overlay(repository, target_paths)
        return RepositoryFingerprint(
            head_sha=head_oid,
            index_hash=hashlib.sha256(staged.stdout).hexdigest(),
            worktree_hash=hashlib.sha256(payload).hexdigest(),
        )

    async def capture_overlay(self, repository: Path, target_paths: tuple[str, ...]) -> bytes:
        """Serialize tracked patch, allowed untracked files, and ignored control inputs."""

        tracked = await self._git.run(repository, "diff", "--binary", "HEAD", "--")
        untracked = await self._git.run(
            repository,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        paths = tuple(
            _normalize_path(raw.decode("utf-8", errors="strict"))
            for raw in untracked.stdout.split(b"\0")
            if raw
        )
        entries = await asyncio.to_thread(
            _read_entries,
            repository,
            paths,
            _control_paths(target_paths),
        )
        return _canonical_payload(tracked.stdout, entries)


class GitOverlayMaterializer:
    """Apply a canonical overlay to an owned worktree without source reads."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def materialize(self, worktree: TaskWorktree, payload: bytes) -> None:
        """Apply tracked binary diff then materialize verified untracked/control entries."""

        decoded = json.loads(payload)
        if not isinstance(decoded, dict) or decoded.get("schema_version") != 1:
            raise ValueError("unsupported overlay Artifact schema")
        tracked_patch = base64.b64decode(decoded.get("tracked_patch", ""), validate=True)
        entries = decoded.get("entries")
        if not isinstance(entries, list):
            raise ValueError("invalid overlay Artifact entries")
        if tracked_patch:
            await self._git.run(
                worktree.root,
                "apply",
                "--binary",
                "--whitespace=nowarn",
                "-",
                stdin=tracked_patch,
            )
        await asyncio.to_thread(_materialize_entries, worktree.root, entries)
