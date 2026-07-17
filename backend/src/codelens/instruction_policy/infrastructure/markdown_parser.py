from fnmatch import translate
from re import compile as compile_pattern

import frontmatter
from markdown_it import MarkdownIt

from codelens.instruction_policy.domain.models import ParsedInstruction


def _is_path_rule(value: str) -> bool:
    candidate = value.strip()
    if not candidate or " " in candidate or candidate.startswith(("/", "..")):
        return False
    compile_pattern(translate(candidate))
    return True


class MarkdownInstructionParser:
    """Parse YAML frontmatter and Markdown Skip lists without string slicing."""

    def __init__(self) -> None:
        self._markdown = MarkdownIt()

    def parse(self, text: str) -> ParsedInstruction:
        """Return validated path excludes while preserving prompt-only prose."""

        post = frontmatter.loads(text)
        raw_excludes = post.metadata.get("exclude", [])
        if isinstance(raw_excludes, list):
            excludes = [str(value) for value in raw_excludes]
        elif isinstance(raw_excludes, str):
            excludes = [raw_excludes]
        else:
            excludes = []

        warnings: list[str] = []
        tokens = self._markdown.parse(post.content)
        in_skip = False
        for index, token in enumerate(tokens):
            if token.type == "heading_open":
                inline = tokens[index + 1] if index + 1 < len(tokens) else None
                in_skip = bool(
                    inline
                    and inline.type == "inline"
                    and inline.content.strip().casefold() == "skip"
                )
            elif in_skip and token.type == "inline" and token.level >= 2:
                value = token.content.strip()
                if _is_path_rule(value):
                    excludes.append(value)
                elif value:
                    warnings.append(f"non-path Skip entry kept as prompt only: {value}")
        valid = tuple(dict.fromkeys(value for value in excludes if _is_path_rule(value)))
        return ParsedInstruction(post.content, valid, tuple(warnings))

