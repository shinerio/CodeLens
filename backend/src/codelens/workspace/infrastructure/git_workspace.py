import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.models import (
    BranchScope,
    CommitScope,
    FullRepositoryScope,
    ReviewScope,
    UncommittedScope,
)
from codelens.workspace.domain.ports import ScopePlan
from codelens.workspace.infrastructure.git_cli import GitCli

_FULL_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_NON_ANCESTOR_WARNING = "base commit is not an ancestor of target; using direct diff"


@dataclass(frozen=True)
class _TreeEntry:
    mode: str
    object_type: str
    object_id: str
    path: str


def _validate_ref(ref: str) -> None:
    if not ref or ref.startswith("-") or "\0" in ref:
        raise InvalidRepositoryError("invalid Git ref")


def _normalize_path(raw_path: str) -> str:
    if not raw_path or "\0" in raw_path or "\\" in raw_path:
        raise InvalidRepositoryError("invalid repository-relative path")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or ".." in path.parts:
        raise InvalidRepositoryError("invalid repository-relative path")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise InvalidRepositoryError("invalid repository-relative path")
    return normalized


def _decode_nul_paths(output: bytes) -> tuple[str, ...]:
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    return tuple(_normalize_path(field.decode("utf-8", errors="strict")) for field in fields)


def _is_contained_link(path: str, target: str) -> bool:
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        return False
    contained_parts: list[str] = []
    for part in (PurePosixPath(path).parent / target_path).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not contained_parts:
                return False
            contained_parts.pop()
            continue
        contained_parts.append(part)
    return True


def _validate_overlay_symlinks(repository: Path, paths: tuple[str, ...]) -> None:
    repository_root = repository.resolve()
    for path in paths:
        absolute = repository_root / path
        if not absolute.is_symlink():
            continue
        target = os.readlink(absolute)
        if not _is_contained_link(path, target):
            raise InvalidRepositoryError("symlink target escapes repository")


class GitWorkspaceAdapter:
    """Resolve review scopes through bounded, read-only Git commands."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def plan_scope(self, repository: Path, scope: ReviewScope) -> ScopePlan:
        """Pin object IDs and enumerate deterministic target path metadata."""

        current_head = await self._resolve_commit(repository, "HEAD")
        warnings: tuple[str, ...] = ()

        if isinstance(scope, BranchScope):
            base_ref_oid = await self._resolve_commit(repository, scope.base_ref)
            head_oid = await self._resolve_commit(repository, scope.target_ref)
            merge_base = await self._git.run(repository, "merge-base", base_ref_oid, head_oid)
            base_oid = self._validated_oid(merge_base.stdout)
            target_paths = await self._diff_paths(repository, base_oid, head_oid)
            capture_overlay = scope.include_workspace_changes
        elif isinstance(scope, CommitScope):
            base_oid = await self._resolve_commit(repository, scope.base_commit)
            head_oid = await self._resolve_commit(repository, scope.target_ref)
            ancestor = await self._git.run(
                repository,
                "merge-base",
                "--is-ancestor",
                base_oid,
                head_oid,
                ok_codes=(0, 1),
            )
            if ancestor.returncode == 1:
                warnings = (_NON_ANCESTOR_WARNING,)
            target_paths = await self._diff_paths(repository, base_oid, head_oid)
            capture_overlay = scope.include_workspace_changes
        elif isinstance(scope, UncommittedScope):
            base_oid = current_head
            head_oid = current_head
            target_paths = ()
            capture_overlay = True
        elif isinstance(scope, FullRepositoryScope):
            head_oid = await self._resolve_commit(repository, scope.target_ref)
            base_oid = head_oid
            target_paths = tuple((await self._tree_entries(repository, head_oid)).keys())
            capture_overlay = scope.include_workspace_changes
        else:
            raise TypeError(f"unsupported review scope: {type(scope).__name__}")

        if capture_overlay and head_oid != current_head:
            message = "workspace changes require the target to match current HEAD"
            raise InvalidRepositoryError(message)
        if capture_overlay:
            overlay_paths = await self._overlay_paths(repository, current_head)
            await asyncio.to_thread(_validate_overlay_symlinks, repository, overlay_paths)
            target_paths = tuple(sorted({*target_paths, *overlay_paths}))

        await self._validate_tree_symlinks(repository, head_oid, target_paths)
        return ScopePlan(
            base_oid=base_oid,
            head_oid=head_oid,
            target_paths=tuple(sorted(dict.fromkeys(target_paths))),
            capture_workspace_overlay=capture_overlay,
            warnings=warnings,
        )

    async def _resolve_commit(self, repository: Path, ref: str) -> str:
        _validate_ref(ref)
        result = await self._git.run(
            repository,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{ref}^{{commit}}",
        )
        if b"ambiguous" in result.stderr.lower():
            raise InvalidRepositoryError("Git ref is ambiguous")
        return self._validated_oid(result.stdout)

    @staticmethod
    def _validated_oid(output: bytes) -> str:
        oid = output.decode("ascii", errors="strict").strip()
        if _FULL_OID.fullmatch(oid) is None:
            raise InvalidRepositoryError("Git did not return a full object ID")
        return oid

    async def _diff_paths(self, repository: Path, base_oid: str, head_oid: str) -> tuple[str, ...]:
        result = await self._git.run(
            repository,
            "diff",
            "--name-status",
            "-z",
            "--find-renames",
            "--no-ext-diff",
            "--no-textconv",
            base_oid,
            head_oid,
            "--",
        )
        return self._parse_name_status(result.stdout)

    @staticmethod
    def _parse_name_status(output: bytes) -> tuple[str, ...]:
        fields = output.split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        paths: list[str] = []
        offset = 0
        while offset < len(fields):
            status = fields[offset].decode("ascii", errors="strict")
            offset += 1
            path_count = 2 if status.startswith(("R", "C")) else 1
            if offset + path_count > len(fields):
                raise InvalidRepositoryError("unexpected git name-status output")
            for raw_path in fields[offset : offset + path_count]:
                paths.append(_normalize_path(raw_path.decode("utf-8", errors="strict")))
            offset += path_count
        return tuple(sorted(dict.fromkeys(paths)))

    async def _overlay_paths(self, repository: Path, head_oid: str) -> tuple[str, ...]:
        staged = await self._git.run(
            repository,
            "diff",
            "--name-status",
            "-z",
            "--cached",
            head_oid,
            "--",
        )
        unstaged = await self._git.run(
            repository,
            "diff",
            "--name-status",
            "-z",
            "--",
        )
        untracked = await self._git.run(
            repository,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        return tuple(
            sorted(
                {
                    *self._parse_name_status(staged.stdout),
                    *self._parse_name_status(unstaged.stdout),
                    *_decode_nul_paths(untracked.stdout),
                }
            )
        )

    async def _tree_entries(self, repository: Path, head_oid: str) -> dict[str, _TreeEntry]:
        result = await self._git.run(repository, "ls-tree", "-r", "-z", "--full-tree", head_oid)
        records = result.stdout.split(b"\0")
        if records and records[-1] == b"":
            records.pop()
        entries: dict[str, _TreeEntry] = {}
        for record in records:
            try:
                header, raw_path = record.split(b"\t", 1)
                mode, object_type, object_id = header.decode("ascii", errors="strict").split(" ")
            except ValueError as error:
                raise InvalidRepositoryError("unexpected git tree output") from error
            path = _normalize_path(raw_path.decode("utf-8", errors="strict"))
            entries[path] = _TreeEntry(mode, object_type, object_id, path)
        return dict(sorted(entries.items()))

    async def _validate_tree_symlinks(
        self,
        repository: Path,
        head_oid: str,
        target_paths: tuple[str, ...],
    ) -> None:
        entries = await self._tree_entries(repository, head_oid)
        for path in target_paths:
            entry = entries.get(path)
            if entry is None or entry.mode != "120000":
                continue
            link = await self._git.run(repository, "cat-file", "blob", entry.object_id)
            target = link.stdout.decode("utf-8", errors="strict")
            if not _is_contained_link(path, target):
                raise InvalidRepositoryError("symlink target escapes repository")
