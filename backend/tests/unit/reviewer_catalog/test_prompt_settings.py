from pathlib import Path

import pytest

from codelens.reviewer_catalog.application.prompt_settings import (
    PromptLocale,
    ReviewerPromptSettingsService,
)


class MemoryPromptStore:
    def __init__(self) -> None:
        self.overrides: dict[tuple[str, PromptLocale], str] = {}

    async def load_override(self, agent_id: str, locale: PromptLocale) -> str | None:
        return self.overrides.get((agent_id, locale))

    async def save_override(
        self, agent_id: str, locale: PromptLocale, prompt: str
    ) -> None:
        self.overrides[(agent_id, locale)] = prompt

    async def delete_override(self, agent_id: str, locale: PromptLocale) -> None:
        self.overrides.pop((agent_id, locale), None)


async def test_loads_agent_specific_prompt_without_a_hardcoded_agent_id(tmp_path: Path) -> None:
    prompt_directory = tmp_path / "security"
    prompt_directory.mkdir()
    (prompt_directory / "en.md").write_text("Review security boundaries.", encoding="utf-8")
    service = ReviewerPromptSettingsService(MemoryPromptStore(), tmp_path)

    view = await service.get("security", "en")

    assert view.agent_id == "security"
    assert view.prompt == "Review security boundaries."


async def test_rejects_unknown_agent_before_persisting_an_override(tmp_path: Path) -> None:
    store = MemoryPromptStore()
    service = ReviewerPromptSettingsService(store, tmp_path)

    with pytest.raises(ValueError, match="unavailable"):
        await service.update("unknown", "en", "Do something else.")

    assert store.overrides == {}
