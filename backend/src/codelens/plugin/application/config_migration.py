from collections.abc import Mapping

from codelens.plugin.api.v2 import TriggerReviewPolicy
from codelens.plugin.domain.versioning import PluginApiVersion

type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


def migrate_config_to_v2(
    *,
    manifest_id: str,
    source_api_version: PluginApiVersion,
    config: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Return a validated v2 policy copy without retaining live Profile references."""

    migrated = dict(config)
    migrated.pop("review_profile_id", None)
    if source_api_version is PluginApiVersion.V1:
        selected = migrated.pop("selected_agents", ["correctness:v1"])
        if not isinstance(selected, list):
            raise ValueError(f"{manifest_id} selected_agents must be a list")
        migrated["reviewer_selection"] = {
            "mode": "fixed",
            "reviewer_versions": selected,
        }
    migrated.setdefault("budget_profile", "standard")
    migrated.setdefault("supersede_policy", "latest_snapshot")
    migrated.setdefault("prompt_locale", "en")
    TriggerReviewPolicy.from_config(migrated)
    return migrated
