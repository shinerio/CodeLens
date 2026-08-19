import json
from pathlib import Path

import pytest

from codelens.reviewer_catalog.application.prompt_settings import (
    AgentPromptSettingsService,
    PromptLocale,
)
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog
from codelens.reviewer_catalog.infrastructure.file_prompt_settings import (
    FilesystemAgentPromptStore,
)

PROMPT_DIR = Path(__file__).resolve().parents[4] / "prompts"


class MemoryPromptStore:
    def __init__(self) -> None:
        self.overrides: dict[tuple[str, PromptLocale], str] = {}

    async def load_override(self, agent_id: str, locale: PromptLocale) -> str | None:
        return self.overrides.get((agent_id, locale))

    async def save_override(self, agent_id: str, locale: PromptLocale, prompt: str) -> None:
        self.overrides[(agent_id, locale)] = prompt

    async def delete_override(self, agent_id: str, locale: PromptLocale) -> None:
        self.overrides.pop((agent_id, locale), None)


async def test_loads_agent_specific_prompt_without_a_hardcoded_agent_id(tmp_path: Path) -> None:
    prompt_directory = tmp_path / "security"
    prompt_directory.mkdir()
    (prompt_directory / "en.md").write_text("Review security boundaries.", encoding="utf-8")
    service = AgentPromptSettingsService(MemoryPromptStore(), tmp_path)

    agent = builtin_agent_catalog()["security:v2"]
    view = await service.get(agent, "en")

    assert view.agent_id == "security"
    assert view.prompt == "Review security boundaries."


async def test_rejects_unknown_agent_before_persisting_an_override(tmp_path: Path) -> None:
    store = MemoryPromptStore()
    service = AgentPromptSettingsService(store, tmp_path)

    agent = builtin_agent_catalog()["security:v2"]
    with pytest.raises(ValueError, match="unavailable"):
        await service.update(agent, "en", "Do something else.")

    assert store.overrides == {}


async def test_prompt_override_is_loaded_by_canonical_reference(tmp_path: Path) -> None:
    store = FilesystemAgentPromptStore(tmp_path)
    service = AgentPromptSettingsService(store, PROMPT_DIR)
    catalog = builtin_agent_catalog()

    await service.update(catalog["correctness:v2"], "en", "v2 custom")

    assert (await service.get(catalog["correctness:v2"], "en")).prompt == "v2 custom"


async def test_unversioned_override_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "agent-prompts.json").write_text(
        '{"correctness":{"en":"legacy custom"}}', encoding="utf-8"
    )
    store = FilesystemAgentPromptStore(tmp_path)
    service = AgentPromptSettingsService(store, PROMPT_DIR)
    catalog = builtin_agent_catalog()

    assert (await service.get(catalog["correctness:v2"], "en")).prompt != "legacy custom"


async def test_saving_override_uses_the_canonical_versioned_key(tmp_path: Path) -> None:
    store = FilesystemAgentPromptStore(tmp_path)
    service = AgentPromptSettingsService(store, PROMPT_DIR)
    reviewer = builtin_agent_catalog()["correctness:v2"]

    await service.update(reviewer, "en", "saved custom")

    payload = json.loads((tmp_path / "agent-prompts.json").read_text(encoding="utf-8"))
    assert payload["correctness:v2"]["en"] == "saved custom"
    assert "correctness" not in payload


async def test_legacy_prompt_file_is_migrated_once(tmp_path: Path) -> None:
    legacy = tmp_path / "reviewer-prompts.json"
    legacy.write_text('{"correctness:v2":{"en":"migrated custom"}}', encoding="utf-8")
    store = FilesystemAgentPromptStore(tmp_path)

    assert (tmp_path / "agent-prompts.json").exists()
    assert not legacy.exists()

    service = AgentPromptSettingsService(store, PROMPT_DIR)
    view = await service.get(builtin_agent_catalog()["correctness:v2"], "en")
    assert view.prompt == "migrated custom"
    assert view.is_custom
