from pathlib import Path

import pytest

from codelens.plugin.domain.models import PluginManifest, ReportCapability
from codelens.plugin.domain.versioning import PluginApiVersion
from codelens.plugin.infrastructure.plugin_loader import CompositePluginLoader, PluginLoadError


def _manifest() -> PluginManifest:
    return PluginManifest(
        plugin_id="external-report",
        name="External report",
        version="2.0.0",
        description="",
        author="test",
        platform="local",
        capabilities={"report": ReportCapability(entry_point="report_sink:ExternalSink")},
        min_codelens_version="0.2.0",
    )


def test_load_sink_uses_the_report_capability_entry_point(tmp_path: Path) -> None:
    (tmp_path / "report_sink.py").write_text(
        "class ExternalSink:\n"
        "    sink_id = 'external-report'\n"
        "    display_name = 'External report'\n"
        "    async def export(self, envelope, config, repository_path):\n"
        "        return None\n",
        encoding="utf-8",
    )

    sink = CompositePluginLoader().load_sink(_manifest(), tmp_path)

    assert sink.sink_id == "external-report"


def test_invalidating_a_plugin_reloads_its_updated_code(tmp_path: Path) -> None:
    module_path = tmp_path / "report_sink.py"
    module_path.write_text(
        "class ExternalSink:\n"
        "    sink_id = 'version-one'\n"
        "    async def export(self, envelope, config, repository_path):\n"
        "        return None\n",
        encoding="utf-8",
    )
    loader = CompositePluginLoader()
    assert loader.load_sink(_manifest(), tmp_path).sink_id == "version-one"

    module_path.write_text(
        "class ExternalSink:\n"
        "    sink_id = 'version-two'\n"
        "    async def export(self, envelope, config, repository_path):\n"
        "        return None\n",
        encoding="utf-8",
    )
    loader.invalidate("external-report")

    assert loader.load_sink(_manifest(), tmp_path).sink_id == "version-two"


def test_loader_rejects_incompatible_v2_manifest_before_import(tmp_path: Path) -> None:
    manifest = PluginManifest(
        **{
            **_manifest().__dict__,
            "version": "2.0.0",
            "plugin_api_version": PluginApiVersion.V2,
            "min_codelens_version": "99.0.0",
        }
    )

    with pytest.raises(PluginLoadError, match="incompatible"):
        CompositePluginLoader().load_sink(manifest, tmp_path)
