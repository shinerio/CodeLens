from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

type InstructionKind = Literal["agents", "review", "file_review"]

DEFAULT_ROOT_INSTRUCTION_MAX_LINES = 500
DEFAULT_NESTED_INSTRUCTION_MAX_LINES = 200
MAX_INSTRUCTION_LINE_LIMIT = 10_000


@dataclass(frozen=True)
class InstructionLineLimits:
    """Bound instruction files by scope while allowing broader root guidance."""

    root_max_lines: int = DEFAULT_ROOT_INSTRUCTION_MAX_LINES
    nested_max_lines: int = DEFAULT_NESTED_INSTRUCTION_MAX_LINES

    def __post_init__(self) -> None:
        for field_name, value in (
            ("root_max_lines", self.root_max_lines),
            ("nested_max_lines", self.nested_max_lines),
        ):
            if value < 1 or value > MAX_INSTRUCTION_LINE_LIMIT:
                raise ValueError(f"{field_name} must be between 1 and 10000")
        if self.root_max_lines < self.nested_max_lines:
            raise ValueError("root_max_lines must be greater than or equal to nested_max_lines")


class InstructionLineLimitsProviderPort(Protocol):
    """Provide the current instruction limits at rule-resolution time."""

    def get_line_limits(self) -> InstructionLineLimits:
        """Load the limits that apply to the next instruction resolution."""

        raise NotImplementedError


@dataclass(frozen=True)
class InstructionDocument:
    """Freeze one scoped control document, its precedence, and content identity."""

    relative_path: str
    content: str
    content_hash: str
    kind: InstructionKind
    scope_path: str
    precedence: int


@dataclass(frozen=True)
class InstructionChain:
    """Order the repository rules applicable to one target from general to specific."""

    target_path: str
    rule_paths: tuple[str, ...]


@dataclass(frozen=True)
class ParsedInstruction:
    """Separate prompt content from deterministic excludes and warnings."""

    content: str
    excludes: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedInstructionSet:
    """Contain deduplicated documents and target-specific ordered rule chains."""

    documents: tuple[InstructionDocument, ...]
    chains: tuple[InstructionChain, ...]
    excludes: tuple[str, ...]
    warnings: tuple[str, ...]


class InstructionParserPort(Protocol):
    """Parse untrusted rule text into deterministic internal instruction data."""

    def parse(self, text: str) -> ParsedInstruction:
        """Parse one bounded Markdown control document."""

        raise NotImplementedError


class InstructionResolutionPort(Protocol):
    """Resolve the ordered control inputs applicable to a repository target."""

    def resolve(self, repository: Path, target_path: str) -> ResolvedInstructionSet:
        """Return frozen instruction documents, excludes, and warnings."""

        raise NotImplementedError


class StructuredSkipPort(Protocol):
    """Apply deterministic structured exclusions from resolved instructions."""

    def excludes(self, path: str, instructions: ResolvedInstructionSet) -> bool:
        """Return whether a normalized repository path is policy-excluded."""

        raise NotImplementedError
