import hashlib
from pathlib import Path, PurePosixPath

from codelens.instruction_policy.domain.models import (
    InstructionDocument,
    InstructionParserPort,
    ResolvedInstructionSet,
)

_DEFAULT_MAX_INSTRUCTION_BYTES = 256 * 1024


def _normalize_target_path(target_path: str) -> PurePosixPath:
    target = PurePosixPath(target_path)
    if not target_path or target.is_absolute() or ".." in target.parts or "\0" in target_path:
        raise ValueError("target path must be repository-relative")
    return target


class InstructionResolver:
    """Resolve root-to-file control inputs in deterministic precedence order."""

    def __init__(
        self,
        parser: InstructionParserPort,
        *,
        max_instruction_bytes: int = _DEFAULT_MAX_INSTRUCTION_BYTES,
    ) -> None:
        if max_instruction_bytes <= 0:
            raise ValueError("instruction size limit must be positive")
        self._parser = parser
        self._max_instruction_bytes = max_instruction_bytes

    def resolve(self, repository: Path, target_path: str) -> ResolvedInstructionSet:
        """Load the applicable frozen rule chain independently of ignore filtering."""

        repository_root = repository.resolve()
        target = _normalize_target_path(target_path)
        candidates = [Path("AGENTS.md"), Path("REVIEW.md")]
        current = Path()
        for part in target.parent.parts:
            current /= part
            candidates.append(current / "REVIEW.md")
        candidates.append(Path(f"{target.as_posix()}.review.md"))

        documents: list[InstructionDocument] = []
        excludes: list[str] = []
        warnings: list[str] = []
        for relative in dict.fromkeys(candidates):
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
            parsed = self._parser.parse(text)
            documents.append(
                InstructionDocument(
                    relative_path=relative.as_posix(),
                    content=text,
                    content_hash=hashlib.sha256(raw).hexdigest(),
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
            excludes=tuple(dict.fromkeys(excludes)),
            warnings=tuple(warnings),
        )
