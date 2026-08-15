"""Filesystem implementation of the startup-loaded localized system prompt catalog."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from codelens.review.application.i18n_prompt_loader import (
    I18nPromptLoaderPort,
    LocalizedSystemPrompts,
    SystemToolPrompt,
)

_REQUIRED_TOOL_NAMES = frozenset(
    {
        "find_files",
        "grep",
        "read_file",
        "get_diff",
        "comment",
        "retract_comment",
        "task_done",
        "finalize_plan",
        "verdict",
        "merge",
        "finalize_verdicts",
    }
)


class SystemPromptLoadError(ValueError):
    """Raised at startup when a localized system prompt bundle is incomplete."""


@dataclass(frozen=True)
class I18nPromptLoader(I18nPromptLoaderPort):
    """Load every `prompts/sys/<locale>` bundle once and resolve locale fallbacks.

    This adapter is constructed only by composition roots. Runtime consumers receive
    immutable text and never read prompt files during a model call.
    """

    bundles: Mapping[str, LocalizedSystemPrompts]
    default_locale: str = "en"

    @classmethod
    def load(cls, prompt_dir: Path, default_locale: str = "en") -> "I18nPromptLoader":
        """Read and validate every system locale bundle before processes start."""

        system_root = prompt_dir / "sys"
        if not system_root.is_dir():
            raise SystemPromptLoadError(f"system prompt directory is missing: {system_root}")
        bundles = {
            locale_directory.name: cls._load_bundle(locale_directory)
            for locale_directory in sorted(system_root.iterdir())
            if locale_directory.is_dir()
        }
        if default_locale not in bundles:
            raise SystemPromptLoadError(
                f"default system prompt locale is missing: {default_locale}"
            )
        return cls(MappingProxyType(bundles), default_locale)

    def get(self, locale: str) -> LocalizedSystemPrompts:
        """Return an exact locale when available, otherwise the configured default."""

        return self.bundles.get(locale, self.bundles[self.default_locale])

    @staticmethod
    def _load_bundle(directory: Path) -> LocalizedSystemPrompts:
        def read_markdown(name: str) -> str:
            path = directory / name
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise SystemPromptLoadError(f"cannot read system prompt: {path}") from error
            if not value:
                raise SystemPromptLoadError(f"system prompt is empty: {path}")
            return value

        tool_path = directory / "tools.json"
        try:
            raw_tools = json.loads(tool_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SystemPromptLoadError(f"cannot parse system tool prompts: {tool_path}") from error
        if not isinstance(raw_tools, dict):
            raise SystemPromptLoadError(f"system tool prompts must be an object: {tool_path}")
        tools: dict[str, SystemToolPrompt] = {}
        for name, raw_tool in raw_tools.items():
            if not isinstance(name, str) or not isinstance(raw_tool, dict):
                raise SystemPromptLoadError(f"invalid system tool prompt: {tool_path}")
            description = raw_tool.get("description")
            if (
                set(raw_tool) != {"description"}
                or not isinstance(description, str)
                or not description.strip()
            ):
                raise SystemPromptLoadError(f"incomplete system tool prompt: {tool_path}#{name}")
            tools[name] = SystemToolPrompt(name=name, description=description)
        if set(tools) != _REQUIRED_TOOL_NAMES:
            raise SystemPromptLoadError(
                "system tool prompts must define the complete stable tool set"
            )
        tool_not_found = read_markdown("tool-not-found.md")
        try:
            tool_not_found.format(tool_name="example", available_tools="read_file")
        except (KeyError, ValueError) as error:
            raise SystemPromptLoadError(
                f"invalid tool-not-found template: {directory / 'tool-not-found.md'}"
            ) from error
        return LocalizedSystemPrompts(
            locale=directory.name,
            review_policy=read_markdown("review-policy.md"),
            review_workflow=read_markdown("review-workflow.md"),
            tool_loop_warning=read_markdown("tool-loop-warning.md"),
            tools=MappingProxyType(tools),
            review_feedback=read_markdown("review-feedback.md"),
            checkpoint_compaction=read_markdown("checkpoint-compaction.md"),
            tool_not_found=tool_not_found,
        )
