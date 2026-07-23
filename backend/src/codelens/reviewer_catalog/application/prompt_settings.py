"""Application service for editable localized built-in reviewer prompts."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

PromptLocale = Literal["en", "zh-CN"]


@dataclass(frozen=True)
class ReviewerPromptView:
    agent_id: str
    version: int
    locale: PromptLocale
    system_prompt: str
    prompt: str
    is_custom: bool
    finalization_prompt: str
    format_repair_prompt: str


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
        finalization_prompt = await asyncio.to_thread(
            self._phase_prompt, agent_id, "finalization", locale
        )
        format_repair_prompt = await asyncio.to_thread(
            self._phase_prompt, agent_id, "format-repair", locale
        )
        override = await self._store.load_override(agent_id, locale)
        return ReviewerPromptView(
            agent_id,
            1,
            locale,
            system_prompt,
            override or system_prompt,
            override is not None,
            finalization_prompt,
            format_repair_prompt,
        )

    async def update(self, agent_id: str, locale: PromptLocale, prompt: str) -> ReviewerPromptView:
        if not prompt.strip():
            raise ValueError("reviewer prompt must not be blank")
        await self._store.save_override(agent_id, locale, prompt)
        return await self.get(agent_id, locale)

    async def reset(self, agent_id: str, locale: PromptLocale) -> ReviewerPromptView:
        await self._store.delete_override(agent_id, locale)
        return await self.get(agent_id, locale)

    def _system_prompt(self, agent_id: str, locale: PromptLocale) -> str:
        if agent_id != "correctness":
            raise ValueError("reviewer does not exist")
        path = self._prompt_dir / agent_id / f"{locale}.md"
        if not path.is_file():
            raise ValueError("system reviewer prompt is unavailable")
        prompt = path.read_text(encoding="utf-8").strip()
        if not prompt:
            raise ValueError("system reviewer prompt is blank")
        return prompt

    def _phase_prompt(self, agent_id: str, phase: str, locale: PromptLocale) -> str:
        path = self._prompt_dir / agent_id / phase / f"{locale}.md"
        if not path.is_file():
            raise ValueError("system reviewer phase prompt is unavailable")
        prompt = path.read_text(encoding="utf-8").strip()
        if not prompt:
            raise ValueError("system reviewer phase prompt is blank")
        return prompt
