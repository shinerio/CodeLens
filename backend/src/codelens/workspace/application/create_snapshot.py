import asyncio
import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from codelens.instruction_policy.domain.models import (
    InstructionDocument,
    InstructionResolutionPort,
    ResolvedInstructionSet,
    StructuredSkipPort,
)
from codelens.workspace.application.worktree_lifecycle import ReviewWorktreeLifecycle
from codelens.workspace.domain.models import (
    CapturedReviewInput,
    ChangeIndex,
    ReviewSnapshot,
    SnapshotBuild,
    TaskWorktree,
)
from codelens.workspace.domain.ports import InputArtifactPort, ScopePlan


class SnapshotManifestPort(Protocol):
    """Freeze and verify a Manifest inside one owned review worktree."""

    async def build(
        self,
        worktree: TaskWorktree,
        target_paths: tuple[str, ...],
        instructions: ResolvedInstructionSet,
        structured_skip: StructuredSkipPort,
    ) -> SnapshotBuild:
        """Return a complete, hash-identified Snapshot Manifest."""

        raise NotImplementedError


class ChangeIndexPort(Protocol):
    """Build deterministic changed-hunk evidence for a frozen worktree."""

    async def build(self, worktree: TaskWorktree, base_oid: str) -> ChangeIndex:
        """Return all changed hunk identities relative to a pinned base."""

        raise NotImplementedError


def _snapshot_metadata(
    snapshot_id: str,
    worktree: TaskWorktree,
    captured: CapturedReviewInput,
    build: SnapshotBuild,
    change_index: ChangeIndex,
) -> bytes:
    payload = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "worktree_id": worktree.worktree_id,
        "worktree_path_hash": hashlib.sha256(str(worktree.root).encode("utf-8")).hexdigest(),
        "repository_common_dir_hash": worktree.repository_common_dir_hash,
        "base_oid": captured.target.base_oid,
        "head_oid": captured.target.head_oid,
        "overlay_hash": captured.target.overlay_hash,
        "manifest_hash": build.manifest_hash,
        "manifest": asdict(build.manifest),
        "change_index": asdict(change_index),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


class SnapshotService:
    """Provision a task worktree, resolve rules, and persist an immutable Snapshot."""

    def __init__(
        self,
        *,
        lifecycle: ReviewWorktreeLifecycle,
        manifest_builder: SnapshotManifestPort,
        change_index: ChangeIndexPort,
        artifacts: InputArtifactPort,
        instructions: InstructionResolutionPort,
        structured_skip: StructuredSkipPort,
    ) -> None:
        self._lifecycle = lifecycle
        self._manifest_builder = manifest_builder
        self._change_index = change_index
        self._artifacts = artifacts
        self._instructions = instructions
        self._structured_skip = structured_skip

    async def create(
        self,
        task_id: str,
        repository: Path,
        captured: CapturedReviewInput,
        scope_plan: ScopePlan,
    ) -> ReviewSnapshot:
        """Create the only repository view that Reviewers may subsequently read."""

        worktree = await self._lifecycle.create(task_id, repository, captured)
        try:
            resolved = await self.resolve_instructions(worktree, scope_plan.target_paths)
            return await self.freeze(worktree, captured, scope_plan, resolved)
        except BaseException:
            await self._lifecycle.remove_owned(worktree)
            raise

    async def freeze(
        self,
        worktree: TaskWorktree,
        captured: CapturedReviewInput,
        scope_plan: ScopePlan,
        instructions: ResolvedInstructionSet,
    ) -> ReviewSnapshot:
        """Freeze a Snapshot inside an already recovered and verified worktree."""

        build = await self._manifest_builder.build(
            worktree,
            scope_plan.target_paths,
            instructions,
            self._structured_skip,
        )
        change_index = await self._change_index.build(worktree, captured.target.base_oid)
        snapshot_id = f"snapshot_{uuid.uuid4().hex}"
        artifact = await self._artifacts.write_bytes(
            _snapshot_metadata(snapshot_id, worktree, captured, build, change_index)
        )
        return ReviewSnapshot(
            snapshot_id=snapshot_id,
            worktree=worktree,
            target=captured.target,
            fingerprint=build.fingerprint,
            manifest=build.manifest,
            change_index=change_index,
            manifest_hash=build.manifest_hash,
            snapshot_artifact=artifact,
        )

    async def resolve_instructions(
        self,
        worktree: TaskWorktree,
        target_paths: tuple[str, ...],
    ) -> ResolvedInstructionSet:
        """Resolve and merge the immutable instruction chain for every target path."""

        documents_by_path: dict[str, InstructionDocument] = {}
        excludes: list[str] = []
        warnings: list[str] = []
        for target_path in target_paths:
            resolved = await asyncio.to_thread(
                self._instructions.resolve,
                worktree.root,
                target_path,
            )
            for document in resolved.documents:
                documents_by_path.setdefault(document.relative_path, document)
            excludes.extend(resolved.excludes)
            warnings.extend(resolved.warnings)
        return ResolvedInstructionSet(
            documents=tuple(documents_by_path.values()),
            excludes=tuple(dict.fromkeys(excludes)),
            warnings=tuple(dict.fromkeys(warnings)),
        )
