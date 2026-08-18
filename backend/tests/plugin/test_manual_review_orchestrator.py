"""Tests for the ManualReviewOrchestrator application service."""

from pathlib import Path
from typing import Any, cast

import pytest

from codelens.plugin.application.manual_review_orchestrator import (
    ManualReviewOrchestrator,
    ManualReviewRequestError,
)
from codelens.plugin.domain.models import (
    ManualReviewCapability,
    PluginManifest,
    PluginRecord,
)
from codelens.plugin.domain.ports import (
    ManualReviewSourcePort,
    PluginStorePort,
    ReviewCreatorPort,
    TriggerPluginLoaderPort,
)
from codelens.plugin.domain.versioning import PluginApiVersion


class FakeSource:
    """Fake ManualReviewSourcePort for testing."""

    def __init__(
        self,
        *,
        task_id: str | None = "task-123",
        raise_exc: Exception | None = None,
    ) -> None:
        self._task_id = task_id
        self._raise = raise_exc
        self.last_config: dict[str, Any] | None = None

    @property
    def source_id(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake Source"

    async def create_review_from_url(
        self,
        source_url: str,
        config: dict[str, Any],
    ) -> str | None:
        if self._raise is not None:
            raise self._raise
        self.last_config = config
        return self._task_id


class FakePluginStore:
    """Fake PluginStorePort for testing."""

    def __init__(self, record: PluginRecord | None) -> None:
        self._record = record

    async def list_plugins(self) -> tuple[PluginRecord, ...]:
        return (self._record,) if self._record is not None else ()

    async def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        if self._record is not None and self._record.plugin_id == plugin_id:
            return self._record
        return None

    async def save_plugin(self, record: PluginRecord) -> None:
        self._record = record

    async def delete_plugin(self, plugin_id: str) -> bool:
        return False


class FakePluginLoader:
    """Fake TriggerPluginLoaderPort that returns a pre-configured source."""

    def __init__(self, source: ManualReviewSourcePort) -> None:
        self._source = source

    def load_source(
        self,
        plugin_id: str,
        review_creator: ReviewCreatorPort,
        manifest: PluginManifest,
        install_path: Path | None,
    ) -> ManualReviewSourcePort:
        return self._source


def _make_record(
    *,
    manual_review_enabled: bool = True,
    manual_review_config: dict[str, Any] | None = None,
) -> PluginRecord:
    manifest = PluginManifest(
        plugin_id="codehub",
        name="CodeHub",
        version="2.1.0",
        description="",
        author="test",
        platform="codehub",
        capabilities={
            "manual_review": ManualReviewCapability(
                entry_point="codehub_trigger:CodehubTrigger",
                config_schema={},
            )
        },
        min_codelens_version="0.2.0",
        plugin_api_version=PluginApiVersion.V2,
    )
    return PluginRecord(
        plugin_id=manifest.plugin_id,
        manifest=manifest,
        is_builtin=False,
        install_path="/tmp/plugin",
        trigger_enabled=False,
        report_enabled=False,
        report_auto_export=False,
        trigger_config={},
        report_config={},
        manual_review_enabled=manual_review_enabled,
        manual_review_config=manual_review_config or {"codehub_host": "example.com"},
    )


def _orchestrator(
    store: FakePluginStore,
    source: FakeSource,
) -> ManualReviewOrchestrator:
    return ManualReviewOrchestrator(
        cast(PluginStorePort, store),
        cast(ReviewCreatorPort, object()),
        cast(TriggerPluginLoaderPort, FakePluginLoader(source)),
    )


async def test_create_review_happy_path() -> None:
    """Orchestrator delegates to the source and returns the task_id."""
    record = _make_record()
    store = FakePluginStore(record)
    source = FakeSource(task_id="task-xyz")
    orch = _orchestrator(store, source)

    task_id = await orch.create_review("codehub", "https://example.com/group/repo/merge_requests/42")

    assert task_id == "task-xyz"
    assert source.last_config == record.manual_review_config


async def test_create_review_plugin_not_found() -> None:
    """Missing plugin raises ManualReviewRequestError."""
    store = FakePluginStore(None)
    source = FakeSource()
    orch = _orchestrator(store, source)

    with pytest.raises(ManualReviewRequestError, match="not found"):
        await orch.create_review("missing", "https://example.com/mr/1")


async def test_create_review_capability_not_enabled() -> None:
    """Plugin without manual_review_enabled raises."""
    record = _make_record(manual_review_enabled=False)
    store = FakePluginStore(record)
    source = FakeSource()
    orch = _orchestrator(store, source)

    with pytest.raises(ManualReviewRequestError, match="not enabled"):
        await orch.create_review("codehub", "https://example.com/mr/1")


async def test_create_review_empty_url_rejected() -> None:
    """Empty source_url is rejected before loading the source."""
    record = _make_record()
    store = FakePluginStore(record)
    source = FakeSource()
    orch = _orchestrator(store, source)

    with pytest.raises(ManualReviewRequestError, match="source_url"):
        await orch.create_review("codehub", "")


async def test_create_review_url_too_long_rejected() -> None:
    """source_url longer than 2048 characters is rejected."""
    record = _make_record()
    store = FakePluginStore(record)
    source = FakeSource()
    orch = _orchestrator(store, source)

    with pytest.raises(ManualReviewRequestError, match="source_url"):
        await orch.create_review("codehub", "x" * 2049)


async def test_create_review_source_raises() -> None:
    """Plugin exception is caught and re-raised as ManualReviewRequestError."""
    record = _make_record()
    store = FakePluginStore(record)
    source = FakeSource(raise_exc=RuntimeError("boom"))
    orch = _orchestrator(store, source)

    with pytest.raises(ManualReviewRequestError, match="failed to create review"):
        await orch.create_review("codehub", "https://example.com/mr/1")


async def test_create_review_source_declines() -> None:
    """Source returning None (declined) raises ManualReviewRequestError."""
    record = _make_record()
    store = FakePluginStore(record)
    source = FakeSource(task_id=None)
    orch = _orchestrator(store, source)

    with pytest.raises(ManualReviewRequestError, match="declined"):
        await orch.create_review("codehub", "https://example.com/mr/1")
