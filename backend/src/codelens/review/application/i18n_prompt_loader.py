"""Stable, startup-loaded model-visible system prompt contracts."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SystemToolPrompt:
    """Describe one stable tool in a localized model-visible form."""

    name: str
    description: str


@dataclass(frozen=True)
class LocalizedSystemPrompts:
    """All platform-authored model text for one locale, loaded before execution."""

    locale: str
    review_policy: str
    review_workflow: str
    tool_loop_warning: str
    tools: Mapping[str, SystemToolPrompt]
    review_feedback: str
    checkpoint_compaction: str
    tool_not_found: str
    no_progress_nudge: str
    completion_nudge: str
    all_files_reviewed_nudge: str


class I18nPromptLoaderPort(Protocol):
    """Return an immutable, already-loaded platform prompt bundle for a locale."""

    def get(self, locale: str) -> LocalizedSystemPrompts:
        """Resolve the requested locale using the configured fallback policy."""

        raise NotImplementedError
