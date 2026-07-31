"""Anti-corruption layer adapter bridging trigger and review contexts."""

import logging
from pathlib import Path
from typing import Protocol

from codelens.plugin.domain.ports import ReviewCreatorPort, TriggerRepositoryValidatorPort
from codelens.review.application.commands import CreateReviewCommand, CreateReviewHandler
from codelens.review.domain.review_strategy import (
    BudgetProfile,
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)
from codelens.workspace.domain.models import (
    BranchScope,
    CommitScope,
    UncommittedScope,
)
from codelens.workspace.domain.ports import RepositoryInfo

_LOGGER = logging.getLogger("codelens.plugin.trigger.adapter")


class RepositoryMetadataInspectorPort(Protocol):
    """Port for inspecting repository metadata.

    This is a minimal port that abstracts the workspace infrastructure's
    GitRepositoryMetadataAdapter, allowing the trigger context to validate
    repository paths without directly depending workspace infrastructure.
    """

    async def inspect(self, repository: Path) -> RepositoryInfo:
        """Validate a git repository path and return its metadata.

        Args:
            repository: Absolute path to the git repository root.

        Returns:
            Validated repository metadata.

        Raises:
            InvalidRepositoryError: If the path is not a valid git repository root.
        """
        ...


class TriggerRepositoryValidatorAdapter(TriggerRepositoryValidatorPort):
    """Bridge workspace repository inspection into the trigger plugin boundary."""

    def __init__(self, repository_inspector: RepositoryMetadataInspectorPort) -> None:
        self._repository_inspector = repository_inspector

    async def validate_repository(self, repository_path: Path) -> Path:
        """Return the canonical root after workspace access and Git validation."""

        repository = await self._repository_inspector.inspect(repository_path)
        return repository.path


class ReviewCreatorAdapter(ReviewCreatorPort):
    """Adapter that bridges trigger context to review context.

    Implements ReviewCreatorPort by wrapping CreateReviewHandler and
    converting trigger-specific parameters to review domain models.

    This is the anti-corruption layer that prevents the trigger context
    from directly depending on review context implementation details.
    """

    def __init__(
        self,
        handler: CreateReviewHandler,
        repository_inspector: RepositoryMetadataInspectorPort,
    ) -> None:
        """Initialize the adapter with a review creation handler and repository inspector.

        Args:
            handler: The review application layer's CreateReviewHandler.
            repository_inspector: Port for validating repository paths and extracting metadata.
        """
        self._handler = handler
        self._repository_inspector = repository_inspector

    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        selected_agents: tuple[str, ...],
        prompt_locale: str,
        external_context: dict | None = None,
    ) -> str:
        """Create a review from a trigger event.

        Converts trigger context parameters to review domain models and
        delegates to the review application layer.

        Args:
            repository_path: Absolute path to the git repository.
            scope_type: Type of review scope ('commit', 'branch', 'uncommitted').
            scope_params: Scope-specific parameters:
                - For 'commit': base_commit, target_ref
                - For 'branch': base_ref, target_ref
                - For 'uncommitted': (no parameters needed)
            selected_agents: Tuple of agent IDs to use for the review.
            prompt_locale: Locale for review prompts ('en' or 'zh-CN').
            external_context: Platform-specific context for export routing.

        Returns:
            Task ID of the created review.

        Raises:
            ValueError: If scope_type is invalid or required parameters are missing.
            InvalidRepositoryError: If repository_path is not a valid git repository.
            Exception: If review creation fails.
        """
        # Validate repository and get metadata
        repository_info = await self._repository_inspector.inspect(repository_path)

        # Build the appropriate scope based on scope_type
        scope = self._build_scope(scope_type, scope_params)

        # Build the review creation command
        command = CreateReviewCommand(
            repository=repository_info,
            scope=scope,
            review_profile=ReviewProfileSnapshot(
                FixedReviewerSelection(selected_agents), BudgetProfile.STANDARD
            ),
            trigger_source="plugin",
            prompt_locale=prompt_locale,
            external_context=external_context,
            skip_if_duplicate=True,
        )

        _LOGGER.info(
            "Creating review from trigger: repo=%s, scope=%s, agents=%s",
            repository_path,
            scope_type,
            selected_agents,
        )

        # Delegate to the review application layer
        result = await self._handler.handle(command)

        _LOGGER.info("Review created from trigger: task_id=%s", result.task_id)

        return result.task_id

    def _build_scope(
        self,
        scope_type: str,
        scope_params: dict[str, str | None],
    ) -> BranchScope | CommitScope | UncommittedScope:
        """Build a review scope from type and parameters.

        Args:
            scope_type: Type of scope ('commit', 'branch', 'uncommitted').
            scope_params: Scope-specific parameters.

        Returns:
            Appropriate scope object for the review.

        Raises:
            ValueError: If scope_type is invalid or required parameters are missing.
        """
        if scope_type == "commit":
            base_commit = scope_params.get("base_commit")
            target_ref = scope_params.get("target_ref")

            if not base_commit or not target_ref:
                raise ValueError("Commit scope requires 'base_commit' and 'target_ref' parameters")

            return CommitScope(
                base_commit=base_commit,
                target_ref=target_ref,
                include_workspace_changes=False,
            )

        elif scope_type == "branch":
            base_ref = scope_params.get("base_ref")
            target_ref = scope_params.get("target_ref")

            if not base_ref or not target_ref:
                raise ValueError("Branch scope requires 'base_ref' and 'target_ref' parameters")

            return BranchScope(
                base_ref=base_ref,
                target_ref=target_ref,
                include_workspace_changes=False,
            )

        elif scope_type == "uncommitted":
            return UncommittedScope()

        else:
            raise ValueError(f"Invalid scope_type: {scope_type}")
