from pathlib import Path

from codelens.shared.domain.errors import SnapshotStaleError
from codelens.workspace.domain.models import CapturedReviewInput, ReviewTarget
from codelens.workspace.domain.ports import (
    InputArtifactPort,
    ReviewInputCapturePort,
    ScopePlan,
)


class ReviewInputCaptureService:
    """Freeze eligible dirty state before any durable review command is created."""

    def __init__(self, source: ReviewInputCapturePort, artifacts: InputArtifactPort) -> None:
        self._source = source
        self._artifacts = artifacts

    async def capture(
        self,
        repository: Path,
        scope_plan: ScopePlan,
    ) -> CapturedReviewInput:
        """Capture a stable overlay, retrying once when the source changes mid-read."""

        if not scope_plan.capture_workspace_overlay:
            return CapturedReviewInput(
                target=ReviewTarget(scope_plan.base_oid, scope_plan.head_oid, None),
                overlay_artifact=None,
            )

        for _attempt in range(2):
            before = await self._source.fingerprint(repository, scope_plan.target_paths)
            payload = await self._source.capture_overlay(repository, scope_plan.target_paths)
            artifact = await self._artifacts.write_bytes(payload)
            after = await self._source.fingerprint(repository, scope_plan.target_paths)
            if before == after:
                return CapturedReviewInput(
                    target=ReviewTarget(
                        scope_plan.base_oid,
                        scope_plan.head_oid,
                        artifact.content_hash,
                    ),
                    overlay_artifact=artifact,
                )
            await self._artifacts.discard(artifact.reference)
        raise SnapshotStaleError("repository changed while capturing review input")
