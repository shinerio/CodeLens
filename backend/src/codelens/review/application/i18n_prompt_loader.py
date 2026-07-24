"""Stable, startup-loaded model-visible system prompt contracts."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SystemToolPrompt:
    """Describe one stable tool in a localized model-visible form."""

    name: str
    purpose: str
    parameters: str
    description: str


@dataclass(frozen=True)
class LocalizedSystemPrompts:
    """All platform-authored model text for one locale, loaded before execution."""

    locale: str
    platform_policy: str
    output_contract: str
    runtime_instruction: str
    tools: Mapping[str, SystemToolPrompt]
    comment_language_error: str

    @property
    def tool_catalog(self) -> tuple[dict[str, str], ...]:
        """Return the canonical input catalog without exposing implementation state."""

        return tuple(
            {
                "name": prompt.name,
                "purpose": prompt.purpose,
                "parameters": prompt.parameters,
            }
            for prompt in self.tools.values()
            if prompt.name not in {"comment", "task_done"}
        )


class I18nPromptLoaderPort(Protocol):
    """Return an immutable, already-loaded platform prompt bundle for a locale."""

    def get(self, locale: str) -> LocalizedSystemPrompts:
        """Resolve the requested locale using the configured fallback policy."""

        raise NotImplementedError
