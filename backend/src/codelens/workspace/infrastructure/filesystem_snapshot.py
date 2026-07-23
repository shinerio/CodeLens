import asyncio
import hashlib
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Literal

from codelens.instruction_policy.domain.models import ResolvedInstructionSet, StructuredSkipPort
from codelens.shared.domain.errors import InvalidRepositoryError, WorktreeMutatedError
from codelens.workspace.domain.models import (
    ExcludedPath,
    RepositoryFingerprint,
    ReviewSnapshot,
    SnapshotBuild,
    SnapshotEntry,
    SnapshotManifest,
    TaskWorktree,
)
from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.git_ignore import GitIgnoreResolver


def _normalize_path(path: str) -> str:
    candidate = PurePosixPath(path)
    if not path or candidate.is_absolute() or ".." in candidate.parts or "\0" in path:
        raise InvalidRepositoryError("invalid Snapshot path")
    return candidate.as_posix()


def _contained_symlink(path: str, target: str) -> bool:
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        return False
    parts: list[str] = []
    for part in (PurePosixPath(path).parent / target_path).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return False
            parts.pop()
            continue
        parts.append(part)
    return True


type _SnapshotOrigin = Literal["target", "context", "instruction"]


def _snapshot_entry(root: Path, path: str, origin: _SnapshotOrigin) -> SnapshotEntry | None:
    normalized = _normalize_path(path)
    absolute = root / normalized
    try:
        metadata = absolute.lstat()
    except FileNotFoundError:
        return SnapshotEntry(
            path=normalized,
            kind="deleted",
            mode=0,
            size_bytes=0,
            content_hash=hashlib.sha256(b"").hexdigest(),
            symlink_target=None,
            origin=origin,
        )

    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(absolute)
        if not _contained_symlink(normalized, target):
            raise InvalidRepositoryError("Snapshot symlink escapes worktree")
        content = target.encode("utf-8")
        kind: Literal["file", "symlink", "deleted"] = "symlink"
        symlink_target: str | None = target
    elif stat.S_ISREG(metadata.st_mode):
        resolved = absolute.resolve()
        if not resolved.is_relative_to(root):
            raise InvalidRepositoryError("Snapshot path escapes worktree")
        content = absolute.read_bytes()
        kind = "file"
        symlink_target = None
    else:
        # Skip directories (e.g., submodule gitlinks) that git ls-files --cached
        # reports as entries but have no file content to snapshot.
        return None
    return SnapshotEntry(
        path=normalized,
        kind=kind,
        mode=stat.S_IMODE(metadata.st_mode),
        size_bytes=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
        symlink_target=symlink_target,
        origin=origin,
    )


def _canonical_manifest(manifest: SnapshotManifest) -> bytes:
    payload = {
        "target_paths": manifest.target_paths,
        "context_paths": manifest.context_paths,
        "instruction_paths": manifest.instruction_paths,
        "excluded_paths": [asdict(path) for path in manifest.excluded_paths],
        "entries": [asdict(entry) for entry in manifest.entries],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FilesystemSnapshotBuilder:
    """Freeze a safe Manifest from an owned task worktree.

    Git metadata is used only to enumerate candidates and current ignore rules.
    Every recorded path is normalized, contained, hashed, and classified before
    the worktree can be exposed to a Reviewer.
    """

    def __init__(self, *, git: GitCli, ignore: GitIgnoreResolver) -> None:
        self._git = git
        self._ignore = ignore

    async def build(
        self,
        worktree: TaskWorktree,
        target_paths: tuple[str, ...],
        instructions: ResolvedInstructionSet,
        structured_skip: StructuredSkipPort,
    ) -> SnapshotBuild:
        """Build target/context/instruction partitions and their integrity hash."""

        listed = await self._git.run(
            worktree.root,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        )
        context_candidates = tuple(
            _normalize_path(raw.decode("utf-8", errors="strict"))
            for raw in listed.stdout.split(b"\0")
            if raw
        )
        instruction_paths = tuple(document.relative_path for document in instructions.documents)
        control_set = set(instruction_paths)
        candidates = tuple(sorted({*context_candidates, *target_paths} - control_set))
        ignore_resolution = await self._ignore.resolve(worktree.root, candidates)

        policy_excluded = tuple(
            ExcludedPath(path=path, reason="instruction_policy")
            for path in ignore_resolution.included
            if structured_skip.excludes(path, instructions)
        )
        policy_excluded_set = {item.path for item in policy_excluded}
        included = tuple(
            path for path in ignore_resolution.included if path not in policy_excluded_set
        )
        included_set = set(included)
        normalized_targets = tuple(
            sorted(
                path
                for path in (_normalize_path(value) for value in target_paths)
                if path in included_set and path not in control_set
            )
        )
        context_paths = tuple(
            sorted(
                path
                for path in context_candidates
                if path in included_set and path not in control_set
            )
        )
        origins: dict[str, _SnapshotOrigin] = {path: "context" for path in context_paths}
        origins.update({path: "target" for path in normalized_targets})
        origins.update({path: "instruction" for path in instruction_paths})
        entries = tuple(
            entry
            for entry in await asyncio.gather(
                *(
                    asyncio.to_thread(_snapshot_entry, worktree.root, path, origin)
                    for path, origin in sorted(origins.items())
                )
            )
            if entry is not None
        )
        manifest = SnapshotManifest(
            target_paths=normalized_targets,
            context_paths=context_paths,
            excluded_paths=tuple((*ignore_resolution.excluded, *policy_excluded)),
            instruction_paths=instruction_paths,
            entries=entries,
        )
        manifest_hash = hashlib.sha256(_canonical_manifest(manifest)).hexdigest()
        head = await self._git.run(worktree.root, "rev-parse", "HEAD")
        staged = await self._git.run(worktree.root, "diff", "--binary", "--cached", "HEAD", "--")
        return SnapshotBuild(
            manifest=manifest,
            fingerprint=RepositoryFingerprint(
                head_sha=head.stdout.decode("ascii", errors="strict").strip(),
                index_hash=hashlib.sha256(staged.stdout).hexdigest(),
                worktree_hash=manifest_hash,
            ),
            manifest_hash=manifest_hash,
        )

    async def verify(self, snapshot: ReviewSnapshot) -> None:
        """Detect any Reviewer mutation of a path frozen in the Manifest."""

        for expected in snapshot.manifest.entries:
            try:
                actual = await asyncio.to_thread(
                    _snapshot_entry,
                    snapshot.worktree.root,
                    expected.path,
                    expected.origin,
                )
            except (OSError, InvalidRepositoryError) as error:
                raise WorktreeMutatedError("review worktree content changed") from error
            if actual is None or actual != expected:
                raise WorktreeMutatedError("review worktree content changed")
