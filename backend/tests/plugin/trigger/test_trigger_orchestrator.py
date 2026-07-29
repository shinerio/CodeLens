from pathlib import Path
from typing import Any

from codelens.plugin.application.trigger_orchestrator import TriggerOrchestrator
from codelens.plugin.domain.models import (
    HookEvent,
    PluginManifest,
    PluginRecord,
    TriggerCapability,
)
from codelens.plugin.infrastructure.plugin_loader import CompositePluginLoader


class StaticPluginStore:
    def __init__(self, plugin: PluginRecord) -> None:
        self._plugin = plugin

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        return (self._plugin,)


class RecordingReviewCreator:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        selected_agents: tuple[str, ...],
        prompt_locale: str,
        external_context: dict[str, Any] | None = None,
    ) -> str:
        self.requests.append(
            {
                "repository_path": repository_path,
                "scope_type": scope_type,
                "scope_params": scope_params,
                "selected_agents": selected_agents,
                "prompt_locale": prompt_locale,
                "external_context": external_context,
            }
        )
        return "review_from_post_commit"


async def test_builtin_local_plugin_dispatches_post_commit_with_composite_loader(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    plugin = PluginRecord(
        plugin_id="local",
        manifest=PluginManifest(
            plugin_id="local",
            name="Local Development Plugin",
            version="1.0.0",
            description="Local trigger",
            author="CodeLens Team",
            platform="local",
            capabilities={
                "trigger": TriggerCapability(
                    trigger_type="local-hook",
                    supported_events=("post-commit", "pre-push"),
                    entry_point="local_hook_trigger:LocalHookTriggerAdapter",
                )
            },
        ),
        is_builtin=True,
        install_path=None,
        trigger_enabled=True,
        report_enabled=False,
        report_auto_export=False,
        trigger_config={
            "repository_paths": [str(repository_path)],
            "events": ["post-commit"],
            "scope_type": "commit",
            "selected_agents": ["correctness:v1"],
            "prompt_locale": "zh-CN",
            "debounce_seconds": 0,
        },
    )
    review_creator = RecordingReviewCreator()
    orchestrator = TriggerOrchestrator(
        StaticPluginStore(plugin),
        review_creator,
        CompositePluginLoader(),
    )

    task_ids = await orchestrator.handle_event(
        HookEvent.POST_COMMIT,
        repository_path,
        {"commit_sha": "a" * 40},
    )

    assert task_ids == ("review_from_post_commit",)
    assert review_creator.requests == [
        {
            "repository_path": repository_path,
            "scope_type": "commit",
            "scope_params": {
                "base_commit": f"{'a' * 40}~1",
                "target_ref": "a" * 40,
            },
            "selected_agents": ("correctness:v1",),
            "prompt_locale": "zh-CN",
            "external_context": None,
        }
    ]
