import json
from pathlib import Path

import pytest

from codelens.reviewer_catalog.application.prompt_settings import (
    PromptLocale,
    ReviewerPromptSettingsService,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog
from codelens.reviewer_catalog.infrastructure.file_prompt_settings import (
    FilesystemReviewerPromptStore,
)

PROMPT_DIR = Path(__file__).resolve().parents[4] / "prompts"


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

    agent = builtin_agent_catalog()["security:v1"]
    view = await service.get(agent, "en")

    assert view.agent_id == "security"
    assert view.prompt == "Review security boundaries."


async def test_rejects_unknown_agent_before_persisting_an_override(tmp_path: Path) -> None:
    store = MemoryPromptStore()
    service = ReviewerPromptSettingsService(store, tmp_path)

    agent = builtin_agent_catalog()["security:v1"]
    with pytest.raises(ValueError, match="unavailable"):
        await service.update(agent, "en", "Do something else.")

    assert store.overrides == {}


async def test_prompt_overrides_are_isolated_by_reviewer_version(tmp_path: Path) -> None:
    store = FilesystemReviewerPromptStore(tmp_path)
    service = ReviewerPromptSettingsService(store, PROMPT_DIR)
    catalog = builtin_agent_catalog()

    await service.update(catalog["correctness:v2"], "en", "v2 custom")

    assert (await service.get(catalog["correctness:v2"], "en")).prompt == "v2 custom"
    assert (await service.get(catalog["correctness:v1"], "en")).prompt != "v2 custom"


async def test_legacy_agent_id_override_loads_only_for_correctness_v1(tmp_path: Path) -> None:
    (tmp_path / "reviewer-prompts.json").write_text(
        '{"correctness":{"en":"legacy custom"}}', encoding="utf-8"
    )
    store = FilesystemReviewerPromptStore(tmp_path)
    service = ReviewerPromptSettingsService(store, PROMPT_DIR)
    catalog = builtin_agent_catalog()

    assert (await service.get(catalog["correctness:v1"], "en")).prompt == "legacy custom"
    assert (await service.get(catalog["correctness:v2"], "en")).prompt != "legacy custom"


async def test_saving_legacy_override_uses_the_canonical_versioned_key(tmp_path: Path) -> None:
    store = FilesystemReviewerPromptStore(tmp_path)
    service = ReviewerPromptSettingsService(store, PROMPT_DIR)
    legacy = builtin_agent_catalog()["correctness:v1"]

    await service.update(legacy, "en", "saved legacy custom")

    payload = json.loads(
        (tmp_path / "reviewer-prompts.json").read_text(encoding="utf-8")
    )
    assert payload["correctness:v1"]["en"] == "saved legacy custom"
    assert "correctness" not in payload
