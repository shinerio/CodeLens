# Plugin API v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the plugin boundary to API v2 so automatic triggers can submit immutable Fixed or Adaptive reviewer-selection policies, retain exact reviewer versions during migration, supersede safely, and export the published multi-Agent result envelope.

**Architecture:** Plugins remain untrusted boundary adapters. Core-owned v2 value objects parse plugin configuration into a `TriggerReviewPolicy`; the trigger orchestrator translates that policy into the Phase 3 review use case without asking a user or invoking an LLM. Installed plugin configuration stores a copied selection snapshot, while Review Profiles remain core-owned authoring objects. Report sinks consume a versioned publication envelope and never inspect orchestration internals.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, SQLAlchemy 2 async, Git CLI adapters, pytest, Ruff, mypy.

## Global Constraints

- Run this plan after the persistent orchestration plan is green; it may run in parallel with the frontend plan.
- Follow `docs/plugin-upgradev2.md` as the public migration contract and update it if implementation proves a contract impossible.
- Automatic triggers never pause for reviewer selection and never invoke Adaptive planning before a review task is durably created.
- Fixed and Adaptive are mutually exclusive. General is allowed only as the sole Fixed reviewer.
- Preserve reviewer IDs and versions exactly during v1 migration; never translate `correctness:v1` to `correctness:v2`.
- Plugin configuration stores copied policy values, not a live foreign key to a Review Profile.
- Plugin modules must not import review infrastructure, SQLAlchemy models, OpenAI runtime classes, MCP clients, or Skill loaders.
- Plugin update and configuration migration are transactional from the user's perspective: failure retains the previously active plugin and config.
- Export only published Findings. Rejected, unresolved, invalid, and raw Candidate payloads are not report-sink input.
- Do not add evaluation, benchmark, rollout, or frontend work in this phase.

## Task 1: Introduce explicit plugin API compatibility

**Files:**

- Create: `backend/src/codelens/plugin/domain/versioning.py`
- Modify: `backend/src/codelens/plugin/domain/models.py`
- Modify: `backend/src/codelens/plugin/infrastructure/plugin_loader.py`
- Modify: `backend/src/codelens/plugin/infrastructure/git_installer.py`
- Modify: `backend/src/codelens/plugin/application/plugin_manager.py`
- Modify: `backend/src/codelens/__init__.py`
- Modify: `backend/src/codelens/interface/http/app.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `backend/tests/unit/test_package.py`
- Test: `backend/tests/plugin/test_plugin_loader.py`
- Test: `backend/tests/plugin/test_git_installer.py`
- Test: `backend/tests/plugin/test_plugin_manager.py`

- [ ] **Step 1: Write failing manifest-version tests**

Add these cases:

```python
from codelens.plugin.domain.versioning import PluginApiVersion, PluginCompatibilityError


def test_manifest_without_plugin_api_version_is_legacy_v1() -> None:
    manifest = load_manifest({"plugin_id": "legacy", "version": "1.4.2"})
    assert manifest.plugin_api_version is PluginApiVersion.V1


def test_loader_rejects_unsupported_major_version() -> None:
    with pytest.raises(PluginCompatibilityError, match="unsupported plugin API"):
        load_manifest(
            {
                "plugin_id": "example",
                "version": "3.0.0",
                "plugin_api_version": "3",
                "min_codelens_version": "0.2.0",
            }
        )


def test_v2_manifest_requires_minimum_codelens_version() -> None:
    with pytest.raises(PluginCompatibilityError, match="min_codelens_version"):
        load_manifest(
            {
                "plugin_id": "example",
                "version": "2.0.0",
                "plugin_api_version": "2",
            }
        )


def test_v2_manifest_requires_a_v2_plugin_release() -> None:
    with pytest.raises(PluginCompatibilityError, match="plugin version"):
        load_manifest(
            {
                "plugin_id": "example",
                "version": "1.9.0",
                "plugin_api_version": "2",
                "min_codelens_version": "0.2.0",
            }
        )
```

For Git installer tests, use a real temporary Git repository. Install v1, commit an incompatible v3 update, attempt update, then assert the active checkout and stored manifest still point at v1.

- [ ] **Step 2: Run the focused tests and observe failure**

```bash
uv run --project backend pytest \
  backend/tests/plugin/test_plugin_loader.py \
  backend/tests/plugin/test_git_installer.py \
  backend/tests/plugin/test_plugin_manager.py -v
```

Expected: failures because manifests have no explicit API version or atomic compatibility gate.

- [ ] **Step 3: Implement typed compatibility values and pre-activation validation**

Use these public domain shapes:

```python
from enum import StrEnum
from packaging.version import Version


class PluginApiVersion(StrEnum):
    V1 = "1"
    V2 = "2"


class PluginCompatibilityError(ValueError):
    """Raised before activation when a plugin cannot run on this host."""


def ensure_plugin_compatible(
    *,
    plugin_api_version: PluginApiVersion,
    minimum_codelens_version: Version,
    current_codelens_version: Version,
) -> None:
    if plugin_api_version not in {PluginApiVersion.V1, PluginApiVersion.V2}:
        raise PluginCompatibilityError("unsupported plugin API")
    if current_codelens_version < minimum_codelens_version:
        raise PluginCompatibilityError("CodeLens version is below plugin minimum")
```

Add `plugin_api_version` to `PluginManifest`, defaulting an absent field to v1. A v2 manifest requires a plugin SemVer major of at least 2 and non-null `min_codelens_version`; a v1 manifest retains its historical version rules. Add direct runtime dependency `packaging>=26,<27` to `backend/pyproject.toml` (it is currently only transitive through pytest) and refresh `backend/uv.lock`. Parse and validate a candidate checkout before changing the active installation pointer.

Set the CodeLens package, `codelens.__version__`, and FastAPI application version to `0.2.0`; update `backend/tests/unit/test_package.py` to keep those three sources aligned. Use `Version(codelens.__version__)` as the compatibility input rather than another hard-coded constant.

- [ ] **Step 4: Verify compatibility and rollback behavior**

Run the focused command from Step 2. Expected: all pass, including the real-Git rollback case.

- [ ] **Step 5: Commit the compatibility gate**

```bash
git add backend/src/codelens backend/tests/plugin backend/tests/unit/test_package.py backend/pyproject.toml backend/uv.lock
git commit -m "feat: version the plugin API boundary"
```

## Task 2: Publish the core-owned Plugin API v2 types

**Files:**

- Create: `backend/src/codelens/plugin/api/__init__.py`
- Create: `backend/src/codelens/plugin/api/v2.py`
- Create: `backend/src/codelens/plugin/application/v1_adapter.py`
- Modify: `backend/src/codelens/plugin/domain/ports.py`
- Test: `backend/tests/plugin/test_api_v2.py`
- Test: `backend/tests/plugin/test_v1_adapter.py`

- [ ] **Step 1: Write failing contract tests for the public import surface**

```python
from codelens.plugin.api.v2 import (
    AdaptiveReviewerSelection,
    BudgetProfile,
    FixedReviewerSelection,
    ReviewCreatorPort,
    TriggerReviewPolicy,
)


def test_fixed_policy_preserves_ordered_exact_versions() -> None:
    policy = TriggerReviewPolicy.from_config(
        {
            "reviewer_selection": {
                "mode": "fixed",
                "reviewer_versions": ["security:v1", "correctness:v2"],
            },
            "budget_profile": "standard",
            "supersede_policy": "latest_snapshot",
            "prompt_locale": "en",
        }
    )
    selection = policy.reviewer_selection
    assert isinstance(selection, FixedReviewerSelection)
    assert selection.reviewer_versions == ("security:v1", "correctness:v2")


def test_adaptive_policy_rejects_fixed_reviewers() -> None:
    with pytest.raises(ValueError, match="adaptive"):
        TriggerReviewPolicy.from_config(
            {
                "reviewer_selection": {
                    "mode": "adaptive",
                    "reviewer_versions": ["security:v1"],
                },
                "budget_profile": "deep",
                "supersede_policy": "preserve_all",
                "prompt_locale": "en",
            }
        )


def test_general_must_be_the_only_fixed_reviewer() -> None:
    with pytest.raises(ValueError, match="general:v1"):
        TriggerReviewPolicy.from_config(
            {
                "reviewer_selection": {
                    "mode": "fixed",
                    "reviewer_versions": ["general:v1", "security:v1"],
                },
                "budget_profile": "lean",
                "supersede_policy": "latest_snapshot",
                "prompt_locale": "en",
            }
        )
```

- [ ] **Step 2: Run tests and observe the missing API**

```bash
uv run --project backend pytest \
  backend/tests/plugin/test_api_v2.py \
  backend/tests/plugin/test_v1_adapter.py -v
```

Expected: import and contract failures.

- [ ] **Step 3: Implement the stable v2 surface**

Define the complete public types in `api/v2.py`:

```python
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Mapping, Protocol


@dataclass(frozen=True)
class FixedReviewerSelection:
    mode: Literal["fixed"]
    reviewer_versions: tuple[str, ...]


@dataclass(frozen=True)
class AdaptiveReviewerSelection:
    mode: Literal["adaptive"]


type ReviewerSelection = FixedReviewerSelection | AdaptiveReviewerSelection


class BudgetProfile(StrEnum):
    LEAN = "lean"
    STANDARD = "standard"
    DEEP = "deep"


class SupersedePolicy(StrEnum):
    LATEST_SNAPSHOT = "latest_snapshot"
    PRESERVE_ALL = "preserve_all"


@dataclass(frozen=True)
class TriggerReviewPolicy:
    reviewer_selection: ReviewerSelection
    budget_profile: BudgetProfile
    supersede_policy: SupersedePolicy
    prompt_locale: Literal["en", "zh-CN"]

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> "TriggerReviewPolicy":
        """Strictly parse standard v2 fields and enforce selection invariants."""
        selection = _parse_reviewer_selection(config.get("reviewer_selection"))
        budget = _required_string(config, "budget_profile")
        supersede = _required_string(config, "supersede_policy")
        locale = _required_string(config, "prompt_locale")
        if locale not in ("en", "zh-CN"):
            raise ValueError("unsupported prompt_locale")
        return cls(
            reviewer_selection=selection,
            budget_profile=BudgetProfile(budget),
            supersede_policy=SupersedePolicy(supersede),
            prompt_locale=locale,
        )


def _required_string(config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _parse_reviewer_selection(value: object) -> ReviewerSelection:
    if not isinstance(value, Mapping):
        raise ValueError("reviewer_selection must be an object")
    mode = value.get("mode")
    if mode == "adaptive":
        if set(value) != {"mode"}:
            raise ValueError("adaptive selection accepts only mode")
        return AdaptiveReviewerSelection(mode="adaptive")
    if mode != "fixed":
        raise ValueError("unsupported reviewer selection mode")
    if set(value) != {"mode", "reviewer_versions"}:
        raise ValueError("fixed selection has unknown or missing fields")
    raw_references = value.get("reviewer_versions")
    if not isinstance(raw_references, list):
        raise ValueError("reviewer_versions must be a non-empty string list")
    parsed_references: list[str] = []
    for raw_reference in raw_references:
        if not isinstance(raw_reference, str) or not raw_reference:
            raise ValueError("reviewer_versions must be a non-empty string list")
        parsed_references.append(raw_reference)
    references = tuple(parsed_references)
    if not references or len(references) != len(set(references)):
        raise ValueError("reviewer_versions must be non-empty and unique")
    if "general:v1" in references and references != ("general:v1",):
        raise ValueError("general:v1 must be the only reviewer")
    if "correctness:v1" in references and references != ("correctness:v1",):
        raise ValueError("correctness:v1 is legacy single-reviewer only")
    return FixedReviewerSelection(mode="fixed", reviewer_versions=references)


class ReviewCreatorPort(Protocol):
    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        review_policy: TriggerReviewPolicy,
        external_context: dict[str, object] | None = None,
    ) -> str: ...
```

`from_config` reads the four standard policy fields from the full plugin config while leaving Debounce and plugin-specific fields to their owners. It rejects unknown selection-object fields, non-string reviewer references, duplicates, empty Fixed lists, General-plus-specialist, `correctness:v1` teams, and unsupported enum values. It preserves exact reviewer order and versions. Keep the trigger-policy portion of `api/v2.py` limited to Python standard-library and plugin-domain imports. Task 5 may add only the stable report DTO re-export at this Interface facade. Adapt the old `selected_agents` port through `v1_adapter.py`; do not expose ReviewTask or internal command types.

- [ ] **Step 4: Verify public contracts and static typing**

```bash
uv run --project backend pytest \
  backend/tests/plugin/test_api_v2.py \
  backend/tests/plugin/test_v1_adapter.py -v
uv run --project backend mypy backend/src/codelens/plugin
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the v2 API**

```bash
git add backend/src/codelens/plugin backend/tests/plugin/test_api_v2.py backend/tests/plugin/test_v1_adapter.py
git commit -m "feat: publish plugin API v2 review policy"
```

## Task 3: Migrate installed trigger configuration to copied policy snapshots

**Files:**

- Create: `backend/src/codelens/plugin/application/config_migration.py`
- Modify: `backend/src/codelens/plugin/application/plugin_manager.py`
- Modify: `backend/src/codelens/plugin/infrastructure/plugin_store.py`
- Modify: `backend/src/codelens/plugin/domain/models.py`
- Test: `backend/tests/plugin/test_config_migration.py`
- Test: `backend/tests/plugin/test_plugin_manager.py`
- Test: `backend/tests/plugin/test_plugin_store.py`

- [ ] **Step 1: Write failing migration and rollback tests**

```python
def test_v1_selected_agents_migrate_to_fixed_without_version_upgrade() -> None:
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
    assert migrated["prompt_locale"] == "zh-CN"


def test_plugin_config_has_no_live_profile_reference() -> None:
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
```

Also test that an invalid migrated config leaves both the stored config revision and active plugin revision unchanged.

- [ ] **Step 2: Run migration/store tests and observe failure**

```bash
uv run --project backend pytest \
  backend/tests/plugin/test_config_migration.py \
  backend/tests/plugin/test_plugin_manager.py \
  backend/tests/plugin/test_plugin_store.py -v
```

Expected: failures because configuration is schema-shaped JSON without an API migration transaction.

- [ ] **Step 3: Implement deterministic migration and transactional activation**

Define:

```python
@dataclass(frozen=True, slots=True)
class InstalledPluginConfig:
    plugin_id: str
    plugin_revision: str
    plugin_api_version: PluginApiVersion
    config_revision: int
    values: Mapping[str, JsonValue]
    profile_source: "PluginProfileSource | None"


@dataclass(frozen=True, slots=True)
class PluginProfileSource:
    profile_id: str
    profile_name: str
    profile_revision: int
    copied_at: datetime


def migrate_config_to_v2(
    *,
    manifest_id: str,
    source_api_version: PluginApiVersion,
    config: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Return a validated v2 copy without mutating the installed revision."""
```

Migration rules are exact:

- v1 `selected_agents` becomes Fixed `reviewer_versions` with original order and versions.
- Missing v1 budget becomes `standard`; missing supersede policy becomes `latest_snapshot`.
- v1 never becomes Adaptive automatically.
- A UI-selected Profile copies only `reviewer_selection` and `budget_profile` into plugin-owned `values`. Core stores optional `PluginProfileSource` beside, never inside, the plugin config; it has no execution or fingerprint semantics. Locale, Debounce, supersede policy, and plugin-specific fields retain their independent draft values.
- Activation writes the candidate plugin revision and migrated config in one store transaction. Any parse, compatibility, schema, or write error retains the previous active pair.

- [ ] **Step 4: Verify exact migration and rollback**

Run the focused command from Step 2. Expected: all pass.

- [ ] **Step 5: Commit configuration migration**

```bash
git add backend/src/codelens/plugin backend/tests/plugin
git commit -m "feat: migrate plugin triggers to copied review policies"
```

## Task 4: Route automatic triggers through idempotent v2 review creation

**Files:**

- Modify: `backend/src/codelens/plugin/application/trigger_orchestrator.py`
- Modify: `backend/src/codelens/plugin/trigger/local_hook/local_hook_trigger.py`
- Modify: `backend/src/codelens/plugin/trigger/local_hook/plugin_loader.py`
- Modify: `backend/src/codelens/plugin/trigger/local_hook/hook_script.sh`
- Modify: `backend/src/codelens/trigger/application/review_creator_adapter.py`
- Modify: `backend/src/codelens/review/application/create_triggered_review.py`
- Modify: `backend/src/codelens/interface/http/dependencies.py`
- Test: `backend/tests/plugin/trigger/test_trigger_orchestrator.py`
- Test: `backend/tests/plugin/trigger/test_hook_installer.py`
- Create: `backend/tests/unit/trigger/test_review_creator_adapter.py`
- Modify: `backend/tests/integration/review/test_create_triggered_review.py`

- [ ] **Step 1: Write failing idempotency and supersede tests**

```python
async def test_repeated_hook_delivery_returns_same_review() -> None:
    policy = TriggerReviewPolicy(
        reviewer_selection=AdaptiveReviewerSelection(mode="adaptive"),
        budget_profile=BudgetProfile.DEEP,
        supersede_policy=SupersedePolicy.LATEST_SNAPSHOT,
        prompt_locale="en",
    )
    first = await creator.create_review_from_trigger(
        repository_path=repository_path,
        scope_type="commit",
        scope_params={"commit": "abc123"},
        review_policy=policy,
    )
    second = await creator.create_review_from_trigger(
        repository_path=repository_path,
        scope_type="commit",
        scope_params={"commit": "abc123"},
        review_policy=policy,
    )
    assert second == first


async def test_latest_snapshot_supersedes_non_terminal_older_review() -> None:
    older = await create_triggered_review(source_revision="abc123")
    newer = await create_triggered_review(source_revision="def456")
    assert await task_repository.status_of(older.review_id) == ReviewTaskStatus.SUPERSEDED
    assert newer != older.task_id


async def test_preserve_all_keeps_older_review_active() -> None:
    older = await create_triggered_review(
        source_revision="abc123",
        supersede_policy=SupersedePolicy.PRESERVE_ALL,
    )
    await create_triggered_review(
        source_revision="def456",
        supersede_policy=SupersedePolicy.PRESERVE_ALL,
    )
    assert await task_repository.status_of(older.review_id) is not ReviewTaskStatus.SUPERSEDED
```

Use a real temporary Git repository for hook installation and invocation. The hook process must exit according to its documented fire-and-forget behavior after the host accepts or durably rejects the trigger; it must never wait for review completion.

- [ ] **Step 2: Run trigger tests and observe failure**

```bash
uv run --project backend pytest \
  backend/tests/plugin/trigger/test_trigger_orchestrator.py \
  backend/tests/plugin/trigger/test_hook_installer.py \
  backend/tests/unit/trigger/test_review_creator_adapter.py \
  backend/tests/integration/review/test_create_triggered_review.py -v
```

Expected: failures because the trigger port passes a reviewer list and has no trigger-key or supersede contract.

- [ ] **Step 3: Implement the v2 trigger boundary**

Map the public plugin policy into the existing review-owned command only in `ReviewCreatorAdapter`:

```python
class ReviewCreatorAdapter(ReviewCreatorPort):
    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        review_policy: TriggerReviewPolicy,
        external_context: dict[str, object] | None = None,
    ) -> str:
        repository = await self._repository_inspector.inspect(repository_path)
        scope = self._build_scope(scope_type, scope_params)
        review_snapshot = ReviewProfileSnapshot(
            reviewer_selection=to_review_selection(review_policy.reviewer_selection),
            budget_profile=to_budget_profile(review_policy.budget_profile),
        )
        return await self._handler.handle(
            CreateTriggeredReview(
                repository=repository,
                scope=scope,
                review_profile=review_snapshot,
                prompt_locale=review_policy.prompt_locale,
                supersede_policy=review_policy.supersede_policy.value,
                external_context=external_context,
            )
        )
```

The Phase 3 handler already owns the transaction. This task completes its v2 anti-corruption adapter and verifies that it:

1. resolves the repository and scope into the frozen base/head Snapshot identity;
2. validates the policy against the versioned Reviewer Catalog and freezes the Phase 3 selection/Capability/Skill fingerprints;
3. deduplicates by repository + base/head Snapshot + selection-policy fingerprint + Planner/Catalog version + budget + Capability/Skill fingerprint;
4. under the same transaction, mark eligible older queued tasks `superseded` and request cooperative cancellation for older running tasks only for `latest_snapshot`;
5. appends bounded outbox events, commits, and returns;
6. lets the worker perform Adaptive Planner execution later.

The local hook parses only plugin config and revision metadata. It does not call model APIs, enumerate dynamic reviewers, or prompt the user.

- [ ] **Step 4: Verify restart-safe automatic triggering**

Run the focused command from Step 2, then:

```bash
uv run --project backend pytest backend/tests/plugin/trigger backend/tests/integration/review -v
```

Expected: all pass, including duplicate delivery and both supersede policies.

- [ ] **Step 5: Commit automatic trigger v2**

```bash
git add backend/src/codelens/plugin backend/src/codelens/trigger backend/src/codelens/review/application/create_triggered_review.py backend/src/codelens/interface/http/dependencies.py backend/tests
git commit -m "feat: trigger v2 reviews without interactive selection"
```

## Task 5: Publish report envelope 2.0 to sinks

**Files:**

- Modify: `backend/src/codelens/review/application/export_findings.py`
- Modify: `backend/src/codelens/plugin/api/v2.py`
- Modify: `backend/src/codelens/plugin/application/export_orchestrator.py`
- Modify: `backend/src/codelens/plugin/report/local_file_export/sink.py`
- Modify: `backend/tests/plugin/test_api_v2.py`
- Test: `backend/tests/plugin/test_export_orchestrator.py`
- Test: `backend/tests/plugin/test_local_file_export.py`
- Modify: `backend/tests/unit/review/test_process_report.py`

- [ ] **Step 1: Write failing envelope and publication-filter tests**

```python
def test_export_envelope_v2_contains_selection_plan_and_coverage() -> None:
    envelope = build_export_envelope(review_projection)
    assert envelope.schema_version == "2.0"
    assert envelope.review.selection_request.mode == "adaptive"
    assert envelope.review.plan_summary.selected_reviewer_versions == (
        "security:v1",
        "correctness:v2",
    )
    assert envelope.review.coverage.failed_reviewer_versions == ("security:v1",)


def test_export_contains_only_published_findings() -> None:
    envelope = build_export_envelope(
        projection_with_candidates(
            published=(published_finding,),
            rejected=(rejected_candidate,),
            unresolved=(unresolved_candidate,),
        )
    )
    assert [finding.finding_id for finding in envelope.findings] == [published_finding.finding_id]
    serialized = envelope.model_dump_json()
    assert str(rejected_candidate.candidate_id) not in serialized
    assert str(unresolved_candidate.candidate_id) not in serialized
```

Retain a fixture test that loads a historical `1.0` envelope for offline compatibility. New v2 sinks need not accept internal v1 domain objects.

- [ ] **Step 2: Run report tests and observe failure**

```bash
uv run --project backend pytest \
  backend/tests/plugin/test_export_orchestrator.py \
  backend/tests/plugin/test_local_file_export.py \
  backend/tests/unit/review/test_process_report.py -v
```

Expected: failures because the current envelope is `1.0` and has only selected Agent versions.

- [ ] **Step 3: Implement the 2.0 envelope**

Use a stable public schema:

```python
class ReviewExportMetaV2(BaseModel):
    task_id: str
    repository_name: str
    scope_type: str
    base_oid: str
    head_oid: str
    base_ref: str | None
    target_ref: str | None
    status: Literal["completed", "partial"]
    selection_request: SelectionRequestDto
    plan_summary: ReviewPlanSummaryDto
    coverage: ReviewCoverageDto


class FindingExportEnvelopeV2(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    exported_at: datetime
    review: ReviewExportMetaV2
    findings: tuple[PublishedFindingDto, ...]


class ReportSinkPort(Protocol):
    async def export(
        self,
        envelope: FindingExportEnvelopeV2,
        config: Mapping[str, object],
        repository_path: Path,
    ) -> ExportResult: ...
```

`ReviewPlanSummaryDto` fields are `strategy`, `selected_reviewer_versions`, nullable `planner_version`, and `plan_hash`. `ReviewCoverageDto` fields are `completed_reviewer_versions`, `failed_reviewer_versions`, and `omitted_reviewer_versions`, matching `docs/plugin-upgradev2.md` exactly.

Define the envelope in `review.application.export_findings`, then explicitly re-export the immutable DTO and v2 `ReportSinkPort` from `codelens.plugin.api.v2`; external plugins import only the public API module. Keep the legacy domain `ReportSinkPort` as the v1 compatibility surface until installed v1 plugins have been migrated.

Build this projection in the review application layer from persisted Phase 3 state. The plugin export orchestrator receives only the envelope and sink metadata. Local file export uses atomic replace, bounded file names, restrictive permissions, and never writes prompts, transcripts, Candidate payloads, or secrets.

- [ ] **Step 4: Verify v2 and historical envelope behavior**

Run the focused command from Step 2. Expected: all pass.

- [ ] **Step 5: Commit report envelope 2.0**

```bash
git add backend/src/codelens/review/application/export_findings.py backend/src/codelens/plugin backend/tests
git commit -m "feat: export multi-agent review envelope v2"
```

## Task 6: Expose v2 plugin contracts and close the architecture gate

**Files:**

- Modify: `backend/src/codelens/interface/http/routers/plugins.py`
- Modify: `backend/src/codelens/interface/http/dto.py`
- Modify: `backend/tests/contract/http/test_plugins_api.py`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/plugin-upgradev2.md`

- [ ] **Step 1: Write failing HTTP contract tests**

Add cases asserting that plugin responses include `plugin_api_version`, compatibility status, config revision, the v2 reviewer policy, and separate Core-owned `profile_source`; updates reject provenance keys inside manifest config, mixed Fixed/Adaptive fields, and General-plus-specialist. Confirm legacy installed plugins are returned with an explicit compatibility projection rather than silently rewritten.

```python
async def test_plugin_config_response_exposes_copied_v2_policy(client: AsyncClient) -> None:
    response = await client.get("/api/plugins/local-hook")
    assert response.status_code == 200
    payload = response.json()
    assert payload["plugin_api_version"] == "2"
    assert payload["config"]["reviewer_selection"]["mode"] == "fixed"
    assert "review_profile_id" not in payload["config"]
    assert payload["profile_source"] == {
        "profile_id": "profile-balanced",
        "profile_name": "Balanced Review",
        "profile_revision": 3,
        "copied_at": "2026-07-31T12:00:00Z",
    }
```

- [ ] **Step 2: Run the contract test and observe failure**

```bash
uv run --project backend pytest backend/tests/contract/http/test_plugins_api.py -v
```

Expected: response-schema failures.

- [ ] **Step 3: Implement HTTP DTOs and update the authoritative documents**

Expose only plugin API values and the copied execution policy. Do not expose checkout paths, raw manifests, internal transaction IDs, prompt bodies, or review orchestration records. A missing `plugin_api_version` in a legacy manifest is projected explicitly as compatibility API `"1"`; it is not written back until an actual plugin upgrade succeeds.

Update `docs/ARCHITECTURE.md` with:

- plugin API v1 compatibility and v2 stable boundary;
- copied Profile-snapshot ownership;
- trigger idempotency/supersede transaction;
- async trigger/worker boundary;
- report envelope 2.0 and Published-only export rule.

Reconcile `docs/plugin-upgradev2.md` field names and examples with the implemented DTOs. Keep migration guidance normative and remove any example that no longer compiles.

- [ ] **Step 4: Run the complete plugin/backend quality gate**

```bash
uv run --project backend pytest backend/tests/plugin backend/tests/contract/http/test_plugins_api.py -v
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
git diff --check
```

Expected: every command exits `0`; default tests make no real model, remote MCP, or network calls.

- [ ] **Step 5: Commit the Plugin API v2 gate**

```bash
git add backend docs/ARCHITECTURE.md docs/plugin-upgradev2.md
git commit -m "feat: complete plugin API v2 migration"
```
