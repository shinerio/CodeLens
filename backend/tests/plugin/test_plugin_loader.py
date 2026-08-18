from pathlib import Path
from typing import cast

import pytest

from codelens.plugin.domain.models import (
    ManualReviewCapability,
    PluginManifest,
    ReportCapability,
)
from codelens.plugin.domain.ports import ReviewCreatorPort
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


def _manual_review_manifest() -> PluginManifest:
    return PluginManifest(
        plugin_id="external-manual",
        name="External manual review",
        version="2.1.0",
        description="",
        author="test",
        platform="codehub",
        capabilities={
            "manual_review": ManualReviewCapability(
                entry_point="codehub_source:CodehubSource"
            )
        },
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


def test_load_source_returns_manual_review_instance(tmp_path: Path) -> None:
    """load_source loads a ManualReviewSourcePort via importlib."""
    (tmp_path / "codehub_source.py").write_text(
        "class CodehubSource:\n"
        "    def __init__(self, review_creator):\n"
        "        self._review_creator = review_creator\n"
        "    source_id = 'codehub'\n"
        "    display_name = 'CodeHub'\n"
        "    async def create_review_from_url(self, source_url, config):\n"
        "        return 'task-123'\n",
        encoding="utf-8",
    )

    loader = CompositePluginLoader()
    source = loader.load_source(
        "external-manual",
        cast(ReviewCreatorPort, object()),
        manifest=_manual_review_manifest(),
        install_path=tmp_path,
    )

    assert source.source_id == "codehub"
    assert source.display_name == "CodeHub"


def test_load_source_rejects_missing_source_id(tmp_path: Path) -> None:
    """load_source raises PluginLoadError when source_id is missing."""
    (tmp_path / "codehub_source.py").write_text(
        "class CodehubSource:\n"
        "    def __init__(self, review_creator):\n"
        "        self._review_creator = review_creator\n"
        "    display_name = 'CodeHub'\n"
        "    async def create_review_from_url(self, source_url, config):\n"
        "        return None\n",
        encoding="utf-8",
    )

    loader = CompositePluginLoader()
    with pytest.raises(PluginLoadError, match="ManualReviewSourcePort"):
        loader.load_source(
            "external-manual",
            cast(ReviewCreatorPort, object()),
            manifest=_manual_review_manifest(),
            install_path=tmp_path,
        )
