"""Domain models for trigger plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class HookEvent(StrEnum):
    """Git hook events that can trigger reviews."""

    POST_COMMIT = "post-commit"
    PRE_PUSH = "pre-push"


class TriggerType(StrEnum):
    """Types of trigger plugins."""

    LOCAL_HOOK = "local-hook"
    WEBHOOK = "webhook"


@dataclass(frozen=True)
class TriggerManifest:
    """Metadata describing a trigger plugin's identity and capabilities.

    Attributes:
        plugin_id: Unique identifier for the plugin.
        name: Human-readable name.
        version: Semantic version string.
        description: Brief description of the plugin's purpose.
        author: Plugin author name or organization.
        entry_point: Module path to the plugin class (e.g., 'module:ClassName').
        trigger_type: Type of trigger mechanism (local-hook or webhook).
        supported_events: Tuple of git hook events this plugin can handle.
        config_schema: JSON Schema for validating plugin configuration.
        min_codelens_version: Minimum required CodeLens version, or None.
    """

    plugin_id: str
    name: str
    version: str
    description: str
    author: str
    entry_point: str
    trigger_type: TriggerType
    supported_events: tuple[HookEvent, ...]
    config_schema: dict[str, Any] = field(default_factory=dict)
    min_codelens_version: str | None = None


@dataclass(frozen=True)
class TriggerConfig:
    """Configuration for a trigger plugin instance.

    Attributes:
        repository_paths: Absolute paths to repositories to monitor.
        events: Git hook events that should trigger reviews.
        scope_type: Review scope type ('commit', 'branch', 'uncommitted').
        base_ref: Base reference for branch scope (e.g., 'main').
        target_ref: Target reference for branch scope (e.g., 'HEAD').
        selected_agents: Tuple of agent IDs to use for reviews.
        prompt_locale: Locale for review prompts ('en' or 'zh-CN').
        debounce_seconds: Minimum seconds between triggers for same repo/event.
        extra: Additional plugin-specific configuration.
    """

    repository_paths: tuple[str, ...]
    events: tuple[HookEvent, ...]
    scope_type: str
    base_ref: str | None
    target_ref: str | None
    selected_agents: tuple[str, ...]
    prompt_locale: str
    debounce_seconds: int
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TriggerRecord:
    """Persistent record of an installed trigger plugin.

    Attributes:
        plugin_id: Unique identifier matching the manifest.
        manifest: Plugin metadata and capabilities.
        is_enabled: Whether the plugin is currently active.
        is_builtin: True if this is a built-in plugin (cannot be uninstalled).
        install_path: Filesystem path to plugin code, or None for built-in.
        config: Current plugin configuration.
    """

    plugin_id: str
    manifest: TriggerManifest
    is_enabled: bool
    is_builtin: bool
    install_path: str | None
    config: TriggerConfig
