from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class InstructionDocument:
    """Freeze one ordered control document and its content identity."""

    relative_path: str
    content: str
    content_hash: str


@dataclass(frozen=True)
class ParsedInstruction:
    """Separate prompt content from deterministic excludes and warnings."""

    content: str
    excludes: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedInstructionSet:
    """Contain the complete ordered rule chain applicable to one target path."""

    documents: tuple[InstructionDocument, ...]
    excludes: tuple[str, ...]
    warnings: tuple[str, ...]


class InstructionParserPort(Protocol):
    """Parse untrusted rule text into deterministic internal instruction data."""

    def parse(self, text: str) -> ParsedInstruction:
        """Parse one bounded Markdown control document."""

        raise NotImplementedError

