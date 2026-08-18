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
    ReviewFileChange,
    ReviewSnapshot,
    SnapshotBuild,
    SnapshotEntry,
    SnapshotManifest,
    TaskWorktree,
)
from codelens.workspace.domain.review_file_scope import (
    ReviewFileExclusionPolicy,
    ReviewFileScope,
    ReviewFileScopeResolver,
)
from codelens.workspace.infrastructure.binary_file_classifier import BinaryFileClassifier
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
_HASH_CHUNK_BYTES = 64 * 1024


def _hash_regular_file(path: Path) -> tuple[int, str]:
    """Hash one regular file incrementally without retaining its contents."""

    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as source:
        while chunk := source.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            size_bytes += len(chunk)
    return size_bytes, digest.hexdigest()


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
        size_bytes = len(content)
        content_hash = hashlib.sha256(content).hexdigest()
        kind: Literal["file", "symlink", "deleted"] = "symlink"
        symlink_target: str | None = target
    elif stat.S_ISREG(metadata.st_mode):
        resolved = absolute.resolve()
        if not resolved.is_relative_to(root):
            raise InvalidRepositoryError("Snapshot path escapes worktree")
        size_bytes, content_hash = _hash_regular_file(absolute)
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
        size_bytes=size_bytes,
        content_hash=content_hash,
        symlink_target=symlink_target,
        origin=origin,
    )


def _canonical_manifest(manifest: SnapshotManifest) -> bytes:
    payload = {
        "review_scope": json.loads(manifest.review_scope.canonical_json()),
        "scope_hash": manifest.review_scope.scope_hash,
        "instruction_paths": manifest.instruction_paths,
        "entries": [asdict(entry) for entry in manifest.entries],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FilesystemSnapshotBuilder:
    """Freeze a safe Manifest from an owned task worktree.

    Git metadata is used only to enumerate candidates and current ignore rules.
    Every recorded path is normalized, contained, hashed, and classified before
    the worktree can be exposed to a Reviewer.
    """

    def __init__(
        self,
        *,
        git: GitCli,
        ignore: GitIgnoreResolver,
        binary: BinaryFileClassifier | None = None,
        scope_resolver: ReviewFileScopeResolver | None = None,
    ) -> None:
        self._git = git
        self._ignore = ignore
        self._binary = binary or BinaryFileClassifier(git)
        self._scope_resolver = scope_resolver or ReviewFileScopeResolver()

    async def build(
        self,
        worktree: TaskWorktree,
        candidate_paths: tuple[str, ...],
        base_oid: str,
        policy: ReviewFileExclusionPolicy,
        resolved_scope: ReviewFileScope | None,
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
        all_instruction_paths = tuple(document.relative_path for document in instructions.documents)
        control_set = set(all_instruction_paths)
        candidates = tuple(sorted({*context_candidates, *candidate_paths} - control_set))
        ignore_resolution = await self._ignore.resolve(worktree.root, candidates)

        policy_excluded = tuple(
            ExcludedPath(path=path, reason="instruction_policy")
            for path in ignore_resolution.included
            if structured_skip.excludes(path, instructions)
        )
        normalized_candidates = tuple(
            sorted(
                path
                for path in (_normalize_path(value) for value in candidate_paths)
                if path not in control_set
            )
        )
        context_candidates = tuple(
            sorted(path for path in context_candidates if path not in control_set)
        )
        if resolved_scope is None:
            binary_review_paths = await self._binary.classify(
                worktree.root,
                base_oid,
                tuple(ReviewFileChange(path, "modified") for path in normalized_candidates),
            )
            binary_context_paths = await self._binary.classify_current(
                worktree.root, context_candidates
            )
            review_scope = self._scope_resolver.resolve(
                candidate_review_paths=normalized_candidates,
                candidate_context_paths=context_candidates,
                policy=policy,
                git_ignored_paths=tuple(item.path for item in ignore_resolution.excluded),
                instruction_excluded_paths=tuple(item.path for item in policy_excluded),
                binary_paths=tuple((*binary_review_paths, *binary_context_paths)),
            )
        else:
            if resolved_scope.policy_hash != policy.policy_hash:
                raise ValueError("persisted Review file scope policy does not match task")
            review_scope = resolved_scope
        normalized_targets = review_scope.review_paths
        context_paths = review_scope.context_paths
        active_targets = set(normalized_targets)
        active_instruction_path_set = {
            rule_path
            for chain in instructions.chains
            if chain.target_path in active_targets
            for rule_path in chain.rule_paths
        }
        instruction_paths = tuple(
            sorted(
                document.relative_path
                for document in instructions.documents
                if document.relative_path in active_instruction_path_set
            )
        )
        origins: dict[str, _SnapshotOrigin] = {path: "context" for path in context_paths}
        origins.update({path: "target" for path in normalized_targets})
        origins.update({path: "instruction" for path in instruction_paths})
        raw_entries = await asyncio.gather(
            *(
                asyncio.to_thread(_snapshot_entry, worktree.root, path, origin)
                for path, origin in sorted(origins.items())
            )
        )
        entries = tuple(entry for entry in raw_entries if entry is not None)
        # Filter paths whose entries were skipped (e.g., submodule directories)
        entry_paths = {entry.path for entry in entries}
        manifest_scope = review_scope.with_visible_paths(
            tuple(p for p in normalized_targets if p in entry_paths),
            tuple(p for p in context_paths if p in entry_paths),
        )
        manifest = SnapshotManifest(
            review_scope=manifest_scope,
            instruction_paths=tuple(p for p in instruction_paths if p in entry_paths),
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
