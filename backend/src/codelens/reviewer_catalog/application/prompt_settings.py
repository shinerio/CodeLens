"""Application service for editable localized built-in agent prompts."""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from codelens.reviewer_catalog.domain.models import AgentVersion

PromptLocale = Literal["en", "zh-CN"]
_AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PROMPT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class AgentPromptView:
    agent_id: str
    version: int
    locale: PromptLocale
    system_prompt: str
    prompt: str
    is_custom: bool


class AgentPromptStorePort(Protocol):
    async def load_override(self, reference: str, locale: PromptLocale) -> str | None: ...

    async def save_override(self, reference: str, locale: PromptLocale, prompt: str) -> None: ...

    async def delete_override(self, reference: str, locale: PromptLocale) -> None: ...


class AgentPromptSettingsService:
    """Keep default prompts immutable while allowing per-locale user overrides."""

    def __init__(self, store: AgentPromptStorePort, prompt_dir: Path) -> None:
        self._store = store
        self._prompt_dir = prompt_dir.expanduser().resolve()

    async def get(self, agent: AgentVersion, locale: PromptLocale) -> AgentPromptView:
        """Return the immutable default plus a version-isolated user override."""

        system_prompt = await asyncio.to_thread(self._system_prompt, agent, locale)
        override = await self._store.load_override(agent.reference, locale)
        return AgentPromptView(
            agent.agent_id,
            agent.version,
            locale,
            system_prompt,
            override or system_prompt,
            override is not None,
        )

    async def update(
        self, agent: AgentVersion, locale: PromptLocale, prompt: str
    ) -> AgentPromptView:
        """Persist a non-blank override under the canonical Agent reference."""

        if not prompt.strip():
            raise ValueError("agent prompt must not be blank")
        await asyncio.to_thread(self._system_prompt, agent, locale)
        await self._store.save_override(agent.reference, locale, prompt)
        return await self.get(agent, locale)

    async def reset(self, agent: AgentVersion, locale: PromptLocale) -> AgentPromptView:
        """Delete one versioned override and return its immutable default."""

        await asyncio.to_thread(self._system_prompt, agent, locale)
        await self._store.delete_override(agent.reference, locale)
        return await self.get(agent, locale)

    def _system_prompt(self, agent: AgentVersion, locale: PromptLocale) -> str:
        if (
            _AGENT_ID_PATTERN.fullmatch(agent.agent_id) is None
            or _PROMPT_KEY_PATTERN.fullmatch(agent.prompt_key) is None
            or agent.version < 1
        ):
            raise ValueError("agent does not exist")
        path = (self._prompt_dir / agent.prompt_key / f"{locale}.md").resolve()
        if not path.is_relative_to(self._prompt_dir):
            raise ValueError("agent prompt escapes the prompt catalog")
        if not path.is_file():
            raise ValueError("system agent prompt is unavailable")
        prompt = path.read_text(encoding="utf-8").strip()
        if not prompt:
            raise ValueError("system agent prompt is blank")
        return prompt
