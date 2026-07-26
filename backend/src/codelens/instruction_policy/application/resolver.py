import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from codelens.instruction_policy.domain.models import (
    InstructionChain,
    InstructionDocument,
    InstructionKind,
    InstructionLineLimits,
    InstructionLineLimitsProviderPort,
    InstructionParserPort,
    ResolvedInstructionSet,
)

_DEFAULT_MAX_INSTRUCTION_BYTES = 256 * 1024


@dataclass(frozen=True)
class _InstructionCandidate:
    relative_path: Path
    kind: InstructionKind
    scope_path: str
    precedence: int


def _normalize_target_path(target_path: str) -> PurePosixPath:
    target = PurePosixPath(target_path)
    if not target_path or target.is_absolute() or ".." in target.parts or "\0" in target_path:
        raise ValueError("target path must be repository-relative")
    return target


def _instruction_directories(target: PurePosixPath) -> tuple[Path, ...]:
    directories = [Path()]
    current = Path()
    for part in target.parent.parts:
        current /= part
        directories.append(current)
    return tuple(directories)


def _find_case_insensitive_file(
    repository_root: Path,
    relative_directory: Path,
    logical_name: str,
) -> Path | None:
    directory = (repository_root / relative_directory).resolve()
    if not directory.is_relative_to(repository_root):
        raise ValueError("instruction directory escapes repository")
    if not directory.is_dir():
        return None

    matches = tuple(
        entry
        for entry in sorted(directory.iterdir(), key=lambda path: path.name)
        if entry.name.casefold() == logical_name.casefold() and entry.is_file()
    )
    if len(matches) > 1:
        raise ValueError(
            f"instruction document name is ambiguous in {relative_directory.as_posix()}"
        )
    if not matches:
        return None
    return relative_directory / matches[0].name


class InstructionResolver:
    """Resolve root-to-file control inputs in deterministic scope order."""

    def __init__(
        self,
        parser: InstructionParserPort,
        *,
        max_instruction_bytes: int = _DEFAULT_MAX_INSTRUCTION_BYTES,
        line_limits: InstructionLineLimits | None = None,
        line_limits_provider: InstructionLineLimitsProviderPort | None = None,
    ) -> None:
        if max_instruction_bytes <= 0:
            raise ValueError("instruction size limit must be positive")
        if line_limits is not None and line_limits_provider is not None:
            raise ValueError("instruction line limits must have only one source")
        self._parser = parser
        self._max_instruction_bytes = max_instruction_bytes
        self._line_limits = line_limits or InstructionLineLimits()
        self._line_limits_provider = line_limits_provider

    def resolve(self, repository: Path, target_path: str) -> ResolvedInstructionSet:
        """Load the applicable frozen rule chain independently of ignore filtering."""

        repository_root = repository.resolve()
        target = _normalize_target_path(target_path)
        line_limits = (
            self._line_limits_provider.get_line_limits()
            if self._line_limits_provider is not None
            else self._line_limits
        )
        candidates: list[_InstructionCandidate] = []
        discovery_rules: tuple[tuple[InstructionKind, str, int], ...] = (
            ("agents", "agents.md", 0),
            ("review", "review.md", 1),
        )
        for directory in _instruction_directories(target):
            scope_path = "" if directory == Path() else directory.as_posix()
            depth = len(directory.parts)
            for kind, logical_name, rank in discovery_rules:
                instruction = _find_case_insensitive_file(
                    repository_root,
                    directory,
                    logical_name,
                )
                if instruction is not None:
                    candidates.append(
                        _InstructionCandidate(
                            instruction,
                            kind,
                            scope_path,
                            depth * 2 + rank,
                        )
                    )
        file_instruction = _find_case_insensitive_file(
            repository_root,
            Path(target.parent.as_posix()),
            f"{target.name}.review.md",
        )
        if file_instruction is not None:
            candidates.append(
                _InstructionCandidate(
                    file_instruction,
                    "file_review",
                    target.as_posix(),
                    len(target.parent.parts) * 2 + 2,
                )
            )

        documents: list[InstructionDocument] = []
        excludes: list[str] = []
        warnings: list[str] = []
        for candidate in candidates:
            relative = candidate.relative_path
            absolute = repository_root / relative
            if not absolute.is_file():
                continue
            resolved = absolute.resolve()
            if not resolved.is_relative_to(repository_root):
                raise ValueError("instruction path escapes repository")
            if resolved.stat().st_size > self._max_instruction_bytes:
                raise ValueError("instruction document exceeds the configured size limit")

            raw = resolved.read_bytes()
            text = raw.decode("utf-8")
            max_lines = (
                line_limits.root_max_lines
                if relative.parent == Path(".")
                else line_limits.nested_max_lines
            )
            if len(text.splitlines()) > max_lines:
                raise ValueError(
                    f"instruction document {relative.as_posix()} exceeds the {max_lines} line limit"
                )
            parsed = self._parser.parse(text)
            documents.append(
                InstructionDocument(
                    relative_path=relative.as_posix(),
                    content=text,
                    content_hash=hashlib.sha256(raw).hexdigest(),
                    kind=candidate.kind,
                    scope_path=candidate.scope_path,
                    precedence=candidate.precedence,
                )
            )
            base = relative.parent.as_posix()
            excludes.extend(
                pattern if base == "." else f"{base}/{pattern}"
                for pattern in parsed.excludes
            )
            warnings.extend(parsed.warnings)
        return ResolvedInstructionSet(
            documents=tuple(documents),
            chains=(
                InstructionChain(
                    target_path=target.as_posix(),
                    rule_paths=tuple(document.relative_path for document in documents),
                ),
            ),
            excludes=tuple(dict.fromkeys(excludes)),
            warnings=tuple(warnings),
        )
