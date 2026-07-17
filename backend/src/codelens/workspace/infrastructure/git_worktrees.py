import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from codelens.shared.domain.errors import WorktreeOwnershipError
from codelens.workspace.domain.models import TaskWorktree
from codelens.workspace.domain.ports import WorktreeRegistryPort
from codelens.workspace.infrastructure.git_cli import GitCli

_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_FULL_OID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MARKER_FILENAME = "ownership.json"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolve_common_dir(repository: Path, raw_path: bytes) -> Path:
    common_dir = Path(raw_path.decode("utf-8", errors="strict").strip())
    if not common_dir.is_absolute():
        common_dir = repository / common_dir
    return common_dir.resolve()


def _prepare_parent(parent: Path, checkout: Path) -> None:
    parent.mkdir(parents=True, exist_ok=False)
    if checkout.exists():
        raise WorktreeOwnershipError("owned worktree path already exists")


def _write_marker(path: Path, payload: dict[str, object]) -> None:
    staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with staging.open("xb") as marker:
        marker.write(encoded)
        marker.flush()
        os.fsync(marker.fileno())
    os.replace(staging, path)


def _read_marker(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WorktreeOwnershipError("invalid worktree ownership marker")
    return value


def _remove_task_directory(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


class RepositoryLockRegistry:
    """Own in-process short critical-section locks keyed by Git common-dir hash."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, common_dir_hash: str) -> asyncio.Lock:
        """Return the stable lock for one canonical repository identity."""

        async with self._guard:
            return self._locks.setdefault(common_dir_hash, asyncio.Lock())


class GitReviewWorktreeManager:
    """Provision and remove only CodeLens-owned detached review worktrees.

    A short per-common-dir lock covers Git worktree registration and removal. The
    manager never calls global prune and quarantines any checkout whose durable
    record, marker, canonical path, common directory, or token hash disagrees.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        git: GitCli,
        registry: WorktreeRegistryPort,
        locks: RepositoryLockRegistry,
    ) -> None:
        self._data_dir = data_dir.expanduser().resolve()
        self._git = git
        self._registry = registry
        self._locks = locks

    async def create(self, task_id: str, repository: Path, head_oid: str) -> TaskWorktree:
        """Create and register one detached checkout at an already pinned OID."""

        if _TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise WorktreeOwnershipError("invalid task ID for worktree ownership")
        if _FULL_OID_PATTERN.fullmatch(head_oid) is None:
            raise WorktreeOwnershipError("worktree head must be a full object ID")

        common_dir = await self._common_dir(repository)
        common_dir_hash = _sha256_text(str(common_dir))
        task_root = self._data_dir / "worktrees" / task_id
        checkout = task_root / "checkout"
        await asyncio.to_thread(_prepare_parent, task_root, checkout)
        lock = await self._locks.get(common_dir_hash)
        token_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()

        try:
            async with lock:
                await self._git.run(repository, "cat-file", "-e", f"{head_oid}^{{commit}}")
                await self._git.run(
                    repository,
                    "worktree",
                    "add",
                    "--detach",
                    str(checkout),
                    head_oid,
                )
            root = await asyncio.to_thread(checkout.resolve)
            worktree = TaskWorktree(
                worktree_id=f"worktree-{uuid.uuid4().hex}",
                task_id=task_id,
                repository_common_dir_hash=common_dir_hash,
                root=root,
                head_oid=head_oid,
                ownership_token_hash=token_hash,
            )
            marker = {
                "schema_version": 1,
                "worktree_id": worktree.worktree_id,
                "task_id": task_id,
                "checkout_path_hash": _sha256_text(str(root)),
                "repository_common_dir_hash": common_dir_hash,
                "head_oid": head_oid,
                "ownership_token_hash": token_hash,
                "created_at": time.time(),
            }
            await asyncio.to_thread(_write_marker, task_root / _MARKER_FILENAME, marker)
            await self._registry.register(worktree)
            return worktree
        except BaseException:
            if await asyncio.to_thread(checkout.exists):
                async with lock:
                    await self._git.run(
                        repository,
                        "worktree",
                        "remove",
                        "--force",
                        str(checkout),
                        ok_codes=(0, 128),
                    )
            await asyncio.to_thread(_remove_task_directory, task_root)
            raise

    async def verify_ownership(self, worktree: TaskWorktree) -> None:
        """Fail closed unless the registry, marker, path, common-dir, and token agree."""

        registered = await self._registry.get(worktree.task_id)
        if registered != worktree:
            await self._quarantine(worktree)
            raise WorktreeOwnershipError("worktree registry ownership mismatch")
        marker_path = worktree.root.parent / _MARKER_FILENAME
        try:
            marker = await asyncio.to_thread(_read_marker, marker_path)
            root = await asyncio.to_thread(worktree.root.resolve)
            common_dir = await self._common_dir(worktree.root)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            await self._quarantine(worktree)
            raise WorktreeOwnershipError("worktree ownership metadata is unreadable") from error

        expected = {
            "worktree_id": worktree.worktree_id,
            "task_id": worktree.task_id,
            "checkout_path_hash": _sha256_text(str(root)),
            "repository_common_dir_hash": _sha256_text(str(common_dir)),
            "head_oid": worktree.head_oid,
            "ownership_token_hash": worktree.ownership_token_hash,
        }
        if any(marker.get(key) != value for key, value in expected.items()):
            await self._quarantine(worktree)
            raise WorktreeOwnershipError("worktree ownership marker mismatch")
        if expected["repository_common_dir_hash"] != worktree.repository_common_dir_hash:
            await self._quarantine(worktree)
            raise WorktreeOwnershipError("worktree common directory mismatch")

    async def is_present(self, worktree: TaskWorktree) -> bool:
        """Return whether the recorded checkout path exists on disk."""

        return await asyncio.to_thread(worktree.root.exists)

    async def forget_missing(self, worktree: TaskWorktree, repository: Path) -> None:
        """Remove exact stale Git metadata after proving a recorded checkout is absent."""

        registered = await self._registry.get(worktree.task_id)
        if registered != worktree or await self.is_present(worktree):
            raise WorktreeOwnershipError("missing worktree recovery ownership mismatch")
        marker_path = worktree.root.parent / _MARKER_FILENAME
        try:
            marker = await asyncio.to_thread(_read_marker, marker_path)
            common_dir = await self._common_dir(repository)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            await self._quarantine(worktree)
            raise WorktreeOwnershipError("missing worktree metadata is unreadable") from error
        expected = {
            "worktree_id": worktree.worktree_id,
            "task_id": worktree.task_id,
            "checkout_path_hash": _sha256_text(str(worktree.root)),
            "repository_common_dir_hash": _sha256_text(str(common_dir)),
            "head_oid": worktree.head_oid,
            "ownership_token_hash": worktree.ownership_token_hash,
        }
        if any(marker.get(key) != value for key, value in expected.items()):
            await self._quarantine(worktree)
            raise WorktreeOwnershipError("missing worktree marker mismatch")
        lock = await self._locks.get(worktree.repository_common_dir_hash)
        async with lock:
            await self._git.run(
                repository,
                "worktree",
                "remove",
                "--force",
                str(worktree.root),
                ok_codes=(0, 128),
            )
        await self._registry.remove(worktree.task_id)
        await asyncio.to_thread(_remove_task_directory, worktree.root.parent)

    async def remove_owned(self, worktree: TaskWorktree) -> None:
        """Remove exactly one verified owned checkout without global Git cleanup."""

        await self.verify_ownership(worktree)
        lock = await self._locks.get(worktree.repository_common_dir_hash)
        async with lock:
            await self._git.run(
                worktree.root,
                "worktree",
                "remove",
                "--force",
                str(worktree.root),
            )
        await self._registry.remove(worktree.task_id)
        await asyncio.to_thread(_remove_task_directory, worktree.root.parent)

    async def _common_dir(self, repository: Path) -> Path:
        result = await self._git.run(repository, "rev-parse", "--git-common-dir")
        return await asyncio.to_thread(_resolve_common_dir, repository, result.stdout)

    async def _quarantine(self, worktree: TaskWorktree) -> None:
        task_root = worktree.root.parent
        if not await asyncio.to_thread(task_root.exists):
            return
        quarantine = self._data_dir / "quarantine" / f"{worktree.task_id}-{uuid.uuid4().hex}"
        await asyncio.to_thread(quarantine.parent.mkdir, parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(os.replace, task_root, quarantine)
        except OSError:
            return
