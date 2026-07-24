"""Application service for editable localized built-in reviewer prompts."""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

PromptLocale = Literal["en", "zh-CN"]
_AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class ReviewerPromptView:
    agent_id: str
    version: int
    locale: PromptLocale
    system_prompt: str
    prompt: str
    is_custom: bool


class ReviewerPromptStorePort(Protocol):
    async def load_override(self, agent_id: str, locale: PromptLocale) -> str | None: ...

    async def save_override(self, agent_id: str, locale: PromptLocale, prompt: str) -> None: ...

    async def delete_override(self, agent_id: str, locale: PromptLocale) -> None: ...


class ReviewerPromptSettingsService:
    """Keep default prompts immutable while allowing per-locale user overrides."""

    def __init__(self, store: ReviewerPromptStorePort, prompt_dir: Path) -> None:
        self._store = store
        self._prompt_dir = prompt_dir.expanduser().resolve()

    async def get(self, agent_id: str, locale: PromptLocale) -> ReviewerPromptView:
        system_prompt = await asyncio.to_thread(self._system_prompt, agent_id, locale)
        override = await self._store.load_override(agent_id, locale)
        return ReviewerPromptView(
            agent_id,
            1,
            locale,
            system_prompt,
            override or system_prompt,
            override is not None,
        )

    async def update(self, agent_id: str, locale: PromptLocale, prompt: str) -> ReviewerPromptView:
        if not prompt.strip():
            raise ValueError("reviewer prompt must not be blank")
        await asyncio.to_thread(self._system_prompt, agent_id, locale)
        await self._store.save_override(agent_id, locale, prompt)
        return await self.get(agent_id, locale)

    async def reset(self, agent_id: str, locale: PromptLocale) -> ReviewerPromptView:
        await asyncio.to_thread(self._system_prompt, agent_id, locale)
        await self._store.delete_override(agent_id, locale)
        return await self.get(agent_id, locale)

    def _system_prompt(self, agent_id: str, locale: PromptLocale) -> str:
        if _AGENT_ID_PATTERN.fullmatch(agent_id) is None:
            raise ValueError("reviewer does not exist")
        path = (self._prompt_dir / agent_id / f"{locale}.md").resolve()
        if not path.is_relative_to(self._prompt_dir):
            raise ValueError("reviewer prompt escapes the prompt catalog")
        if not path.is_file():
            raise ValueError("system reviewer prompt is unavailable")
        prompt = path.read_text(encoding="utf-8").strip()
        if not prompt:
            raise ValueError("system reviewer prompt is blank")
        return prompt
