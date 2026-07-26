import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from codelens.instruction_policy.domain.models import (
    InstructionDocument,
    ResolvedInstructionSet,
)
from codelens.review.application.review_scope import (
    ReviewFileInput,
    ReviewScopeLimitError,
    build_review_files,
)
from codelens.workspace.domain.models import ReviewSnapshot


class ContextContainmentError(ValueError):
    """Raised when frozen Review context contains an unsafe path or relationship."""


class ContextIntegrityError(ValueError):
    """Raised when frozen Review context no longer matches its immutable metadata."""


@dataclass(frozen=True)
class ReviewScopeLimits:
    """Bound the complete model-visible Review scope without truncating it."""

    max_review_files: int = 2_000
    max_review_ranges: int = 10_000

    def __post_init__(self) -> None:
        if self.max_review_files <= 0 or self.max_review_ranges <= 0:
            raise ReviewScopeLimitError("Review scope limits must be positive")


@dataclass(frozen=True)
class RepositoryInstructionInput:
    """Carry one trusted frozen rule body and its exact Review targets."""

    path: str
    content: str
    applies_to: tuple[str, ...]

    def as_payload(self) -> dict[str, object]:
        """Return the stable trusted system-instruction shape."""

        return {
            "path": self.path,
            "content": self.content,
            "applies_to": list(self.applies_to),
        }


@dataclass(frozen=True)
class AgentInput:
    """Carry the internal Runtime envelope for one Agent invocation."""

    review_files: tuple[ReviewFileInput, ...]
    repository_instructions: tuple[RepositoryInstructionInput, ...]

    def canonical_bytes(self) -> bytes:
        """Serialize scope and trusted rules for deterministic Runtime splitting."""

        return json.dumps(
            {
                "review_files": [item.as_payload() for item in self.review_files],
                "repository_instructions": [
                    item.as_payload() for item in self.repository_instructions
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")


class ContextBuilder:
    """Build the Runtime input envelope from one frozen ReviewSnapshot."""

    def build(
        self,
        snapshot: ReviewSnapshot,
        instructions: ResolvedInstructionSet,
        limits: ReviewScopeLimits | None = None,
    ) -> AgentInput:
        resolved_limits = limits or ReviewScopeLimits()
        self._validate_snapshot_controls(snapshot, instructions)
        review_files = build_review_files(
            snapshot,
            max_files=resolved_limits.max_review_files,
            max_ranges=resolved_limits.max_review_ranges,
        )
        return AgentInput(
            review_files=review_files,
            repository_instructions=self._repository_instruction_inputs(
                instructions,
                tuple(item.path for item in review_files),
            ),
        )

    @staticmethod
    def _repository_instruction_inputs(
        instructions: ResolvedInstructionSet,
        review_file_paths: tuple[str, ...],
    ) -> tuple[RepositoryInstructionInput, ...]:
        """Deduplicate rule bodies while preserving exact target scope and precedence."""

        chains_by_target = {chain.target_path: chain for chain in instructions.chains}
        applies_to_by_rule: dict[str, list[str]] = {}
        for target_path in review_file_paths:
            for rule_path in chains_by_target[target_path].rule_paths:
                applies_to_by_rule.setdefault(rule_path, []).append(target_path)

        active_documents = sorted(
            (
                document
                for document in instructions.documents
                if document.relative_path in applies_to_by_rule
            ),
            key=lambda document: (document.precedence, document.relative_path),
        )
        return tuple(
            RepositoryInstructionInput(
                path=document.relative_path,
                content=document.content,
                applies_to=tuple(applies_to_by_rule[document.relative_path]),
            )
            for document in active_documents
        )

    @staticmethod
    def _validate_snapshot_controls(
        snapshot: ReviewSnapshot,
        instructions: ResolvedInstructionSet,
    ) -> None:
        entries = {entry.path: entry for entry in snapshot.manifest.entries}
        if len(entries) != len(snapshot.manifest.entries):
            raise ContextIntegrityError("Snapshot manifest contains duplicate entries")

        referenced_paths = (
            *snapshot.manifest.target_paths,
            *snapshot.manifest.context_paths,
            *snapshot.manifest.instruction_paths,
        )
        if any(not ContextBuilder._is_normalized_relative(path) for path in referenced_paths):
            raise ContextContainmentError("Snapshot manifest contains an unsafe path")

        active_targets = set(snapshot.manifest.target_paths)
        for target_path in active_targets:
            entry = entries.get(target_path)
            if entry is None or entry.origin != "target":
                raise ContextContainmentError("Snapshot target has no target entry")

        for hunk in snapshot.change_index.hunks:
            entry = entries.get(hunk.path)
            if (
                not ContextBuilder._is_normalized_relative(hunk.path)
                or hunk.path not in active_targets
                or entry is None
                or entry.origin != "target"
            ):
                raise ContextContainmentError("changed hunk is outside the Snapshot targets")

        chains = tuple(
            chain for chain in instructions.chains if chain.target_path in active_targets
        )
        chains_by_target = {chain.target_path: chain for chain in chains}
        if len(chains_by_target) != len(chains) or set(chains_by_target) != active_targets:
            raise ContextContainmentError(
                "Snapshot target has no unique repository instruction chain"
            )

        active_rule_paths = {
            rule_path for chain in chains for rule_path in chain.rule_paths
        }
        manifest_instruction_paths = snapshot.manifest.instruction_paths
        if (
            len(set(manifest_instruction_paths)) != len(manifest_instruction_paths)
            or set(manifest_instruction_paths) != active_rule_paths
        ):
            raise ContextContainmentError(
                "Snapshot instructions do not match active repository instruction chains"
            )

        instruction_entry_paths = {
            entry.path for entry in snapshot.manifest.entries if entry.origin == "instruction"
        }
        if instruction_entry_paths != active_rule_paths:
            raise ContextContainmentError("Snapshot instruction entries do not match active rules")

        active_documents = tuple(
            document
            for document in instructions.documents
            if document.relative_path in active_rule_paths
        )
        documents_by_path = {document.relative_path: document for document in active_documents}
        if (
            len(active_documents) != len(active_rule_paths)
            or set(documents_by_path) != active_rule_paths
        ):
            raise ContextContainmentError(
                "active repository instructions are not unique and complete"
            )

        for document in active_documents:
            entry = entries.get(document.relative_path)
            if (
                not ContextBuilder._is_normalized_relative(document.relative_path)
                or entry is None
                or entry.origin != "instruction"
            ):
                raise ContextContainmentError("instruction path is outside the Snapshot")
            actual_hash = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
            if actual_hash != document.content_hash or entry.content_hash != actual_hash:
                raise ContextIntegrityError("instruction content is stale or corrupted")

        for chain in chains:
            if not ContextBuilder._is_normalized_relative(chain.target_path):
                raise ContextContainmentError("instruction target path is unsafe")
            priorities: list[int] = []
            for rule_path in chain.rule_paths:
                document = documents_by_path[rule_path]
                if not ContextBuilder._instruction_applies(document, chain.target_path):
                    raise ContextContainmentError(
                        "instruction document is outside its target scope"
                    )
                priorities.append(document.precedence)
            if priorities != sorted(priorities) or len(priorities) != len(set(priorities)):
                raise ContextContainmentError("instruction order is not deterministic")

    @staticmethod
    def _instruction_applies(document: InstructionDocument, target_path: str) -> bool:
        if document.kind == "file_review":
            return document.scope_path == target_path
        target_parent = PurePosixPath(target_path).parent
        return not document.scope_path or target_parent.is_relative_to(
            PurePosixPath(document.scope_path)
        )

    @staticmethod
    def _is_normalized_relative(path: str) -> bool:
        if not path or "\0" in path or "\\" in path:
            return False
        candidate = PurePosixPath(path)
        return (
            not candidate.is_absolute()
            and ".." not in candidate.parts
            and candidate.as_posix() == path
        )
