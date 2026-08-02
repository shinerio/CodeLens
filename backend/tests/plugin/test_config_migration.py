from codelens.plugin.application.config_migration import migrate_config_to_v2
from codelens.plugin.domain.versioning import PluginApiVersion


def test_v1_selected_agents_migrate_without_version_upgrade() -> None:
    migrated = migrate_config_to_v2(
        manifest_id="local-hook",
        source_api_version=PluginApiVersion.V1,
        config={"selected_agents": ["correctness:v1"], "prompt_locale": "zh-CN"},
    )
    assert migrated["reviewer_selection"] == {
        "mode": "fixed",
        "reviewer_versions": ["correctness:v1"],
    }
    assert migrated["budget_profile"] == "standard"
    assert migrated["supersede_policy"] == "latest_snapshot"


def test_migration_removes_live_profile_reference() -> None:
    migrated = migrate_config_to_v2(
        manifest_id="local-hook",
        source_api_version=PluginApiVersion.V2,
        config={
            "review_profile_id": "profile-123",
            "reviewer_selection": {"mode": "adaptive"},
        },
    )
    assert "review_profile_id" not in migrated
    assert migrated["reviewer_selection"] == {"mode": "adaptive"}
