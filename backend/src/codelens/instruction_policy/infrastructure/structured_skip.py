from pathspec import PathSpec

from codelens.instruction_policy.domain.models import ResolvedInstructionSet


class StructuredSkipMatcher:
    """Apply resolved path rules with Git-compatible wildcard semantics."""

    def excludes(self, path: str, instructions: ResolvedInstructionSet) -> bool:
        """Return whether a normalized path matches the resolved exclusion policy."""

        if not instructions.excludes:
            return False
        spec = PathSpec.from_lines("gitignore", instructions.excludes)
        return spec.match_file(path)
