# Persistent Multi-Agent Review Orchestration Implementation Plan

> **SUPERSEDED（2026-08-09）：** 不得继续执行本文。请使用 [`2026-08-09-multi-agent-review-v2-hard-cut.md`](./2026-08-09-multi-agent-review-v2-hard-cut.md)。

> Historical implementation record only.

**Goal:** Implement Review Profiles, Fixed/Adaptive planning, restart-safe multi-pass execution, Candidate resolution and verification, partial failure semantics, and stable HTTP/SSE projections.

**Architecture:** Extend the existing `ReviewTask`, `AgentRun`, `dag_checkpoints`, Artifact Store, Worker scheduler, and outbox instead of adding a generic workflow engine. Adaptive planning is Pass 0; host-compiled reviewer nodes fan out in Pass 1; multi-specialist candidates fan in through Resolver and at most one batched Verifier. Every scheduling decision is derived from persisted state and one frozen Review Plan.

**Tech Stack:** Python 3.12, asyncio, FastAPI, Pydantic v2, SQLAlchemy 2 async, SQLite WAL, Alembic, OpenAI Agents SDK through the frozen Capability runtime, pytest.

## Global Constraints

- Run this plan only after the domain foundation and Capability runtime plans are green.
- Use Alembic for every database change. The current head before this plan is `0e0e42b05c24`.
- Keep existing columns and DTO fields during migration; compatibility adaptation occurs only in Interface/Application boundaries.
- Adaptive Planner failure fails the task; it never falls back to Fixed or General.
- General and Fixed Single Specialist do not create Resolver or Verifier nodes.
- A multi-specialist team with any Candidate always runs Resolver, even if only one reviewer found it.
- Resolver cannot invent a Finding, location, evidence, impact, Candidate ID, or higher severity.
- Verifier returns only `confirmed`, `rejected`, or `unresolved`; rejected and unresolved never publish.
- Planner, Resolver, and Verifier each run at most once per logical node; retry changes physical attempt only.
- Persist output before validation and preserve exact retry/recovery behavior.
- Do not add evaluation, benchmark, shadow, or rollout work.

---

### Task 1: Review Profile Aggregate, Persistence, and CRUD API

**Files:**
- Create: `backend/src/codelens/review/domain/review_profile.py`
- Create: `backend/src/codelens/review/application/review_profiles.py`
- Modify: `backend/src/codelens/review/domain/ports.py`
- Modify: `backend/src/codelens/review/infrastructure/tables.py`
- Modify: `backend/src/codelens/review/infrastructure/repositories.py`
- Modify: `backend/src/codelens/review/infrastructure/run_artifacts.py`
- Create: `backend/migrations/versions/0005_review_profiles.py`
- Create: `backend/src/codelens/interface/http/routers/review_profiles.py`
- Modify: `backend/src/codelens/interface/http/dto.py`
- Modify: `backend/src/codelens/interface/http/dependencies.py`
- Modify: `backend/src/codelens/bootstrap/unified.py`
- Create: `backend/tests/unit/review/test_review_profiles.py`
- Modify: `backend/tests/integration/review/test_sqlite_store.py`
- Create: `backend/tests/contract/http/test_review_profiles_api.py`

**Interfaces:**
- Produces: `ReviewProfile`, `ReviewProfileRepository`, `CreateReviewProfileHandler`, `UpdateReviewProfileHandler`, `CopyReviewProfileHandler`, `DeleteReviewProfileHandler`, `SetDefaultReviewProfileHandler`, `ListReviewProfilesHandler`.
- HTTP resource: `/api/review-profiles` with list/create/update/copy/delete; setting `is_default=true` atomically replaces the prior default.
- Every update supplies the current positive `revision`; stale revision returns HTTP `409` with `review_profile_revision_conflict`.

- [ ] **Step 1: Write failing aggregate, repository, and API tests**

```python
from datetime import UTC, datetime

import pytest

from codelens.review.domain.review_profile import ReviewProfile
from codelens.review.domain.review_strategy import AdaptiveReviewerSelection, BudgetProfile


def test_profile_update_increments_revision_without_changing_identity() -> None:
    profile = ReviewProfile.create(
        profile_id="profile-balanced",
        name="Balanced Review",
        is_default=True,
        reviewer_selection=AdaptiveReviewerSelection(),
        budget_profile=BudgetProfile.STANDARD,
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
    )

    updated = profile.update(
        expected_revision=1,
        name="Balanced Deep Review",
        is_default=True,
        reviewer_selection=AdaptiveReviewerSelection(),
        budget_profile=BudgetProfile.DEEP,
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert updated.profile_id == profile.profile_id
    assert updated.revision == 2
    assert updated.budget_profile is BudgetProfile.DEEP


def test_stale_profile_update_is_rejected() -> None:
    with pytest.raises(ValueError, match="revision conflict"):
        balanced_profile().update(
            expected_revision=4,
            name="stale",
            is_default=False,
            reviewer_selection=AdaptiveReviewerSelection(),
            budget_profile=BudgetProfile.STANDARD,
            updated_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
```

The integration test must create two profiles, atomically switch the default, reject deletion of the current default, delete a non-default profile, restart the database adapter, and observe the same remaining default. The HTTP test must cover strict unknown-field rejection and the `409` conflict.

Add a copy test proving `POST /api/review-profiles/{profile_id}/copies` creates a new ID at revision 1, copies only reviewer selection and budget, never copies `is_default=true`, and remains unchanged when the source is later edited.

- [ ] **Step 2: Run focused tests and verify missing behavior**

Run: `uv run --project backend pytest backend/tests/unit/review/test_review_profiles.py backend/tests/integration/review/test_sqlite_store.py backend/tests/contract/http/test_review_profiles_api.py -v`

Expected: FAIL because the aggregate, table, repository, and router do not exist.

- [ ] **Step 3: Implement aggregate, migration, repository, handlers, and DTOs**

```python
@dataclass(frozen=True)
class ReviewProfile:
    profile_id: str
    revision: int
    name: str
    is_default: bool
    reviewer_selection: ReviewerSelection
    budget_profile: BudgetProfile
    created_at: datetime
    updated_at: datetime

    def snapshot(self) -> ReviewProfileSnapshot:
        return ReviewProfileSnapshot(
            reviewer_selection=self.reviewer_selection,
            budget_profile=self.budget_profile,
            source_profile_id=self.profile_id,
            source_profile_revision=self.revision,
        )
```

Migration `0005_review_profiles` must create plural table `review_profiles`, store `reviewer_selection_json`, and create a SQLite partial unique index that allows at most one row with `is_default=1`. Seed `profile-balanced` as Adaptive + Standard only when the table is empty. Application/repository create, copy, set-default, and delete operations enforce the complementary at-least-one invariant in one transaction; do not let a transient zero-default state commit. `CopyReviewProfileHandler` requires a new name, allocates a new ID, starts at revision 1, and never copies the default flag.

- [ ] **Step 4: Apply migration in a temporary database and run tests**

Run:

```bash
uv run --project backend alembic -c backend/alembic.ini heads
uv run --project backend pytest backend/tests/unit/review/test_review_profiles.py backend/tests/integration/review/test_sqlite_store.py backend/tests/contract/http/test_review_profiles_api.py -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
```

Expected: the graph reports one head and all commands exit `0`. The integration test applies fresh-install and upgrade-from-previous-head migrations to temporary databases; do not point Alembic at the workspace or a user's production database.

- [ ] **Step 5: Commit Review Profiles**

```bash
git add backend/src/codelens/review backend/src/codelens/interface/http backend/src/codelens/bootstrap backend/migrations/versions/0005_review_profiles.py backend/tests
git commit -m "feat: add review profile persistence and api"
```

---

### Task 2: Persist Selection Requests and Immutable Strategy Snapshots

**Files:**
- Modify: `backend/src/codelens/review/domain/models.py`
- Modify: `backend/src/codelens/review/domain/ports.py`
- Modify: `backend/src/codelens/review/application/commands.py`
- Create: `backend/src/codelens/review/application/create_triggered_review.py`
- Modify: `backend/src/codelens/review/infrastructure/tables.py`
- Modify: `backend/src/codelens/review/infrastructure/repositories.py`
- Create: `backend/migrations/versions/0006_review_selection_requests.py`
- Modify: `backend/tests/unit/review/test_review_task.py`
- Modify: `backend/tests/unit/review/test_commands.py`
- Modify: `backend/tests/integration/review/test_sqlite_store.py`
- Create: `backend/tests/integration/review/test_create_triggered_review.py`
- Modify: `backend/tests/unit/review/test_transcripts.py`
- Modify: `backend/tests/contract/http/test_reviews_api.py`

**Interfaces:**
- Replaces `CreateReviewCommand.selected_agent_versions` with `review_profile: ReviewProfileSnapshot`, `trigger_source: Literal["manual", "plugin"]`, and plugin-only `supersede_policy: Literal["latest_snapshot", "preserve_all"] | None`.
- `ReviewTask` owns `review_profile`, while actual planned reviewer versions remain empty until Adaptive planning succeeds.
- Keeps `ReviewRecord.selected_agent_versions` as an actual-team compatibility projection.
- Produces `CreateTriggeredReviewHandler`, which accepts only review-owned Snapshot/strategy values; Phase 4 adapts the public plugin policy into it.

- [ ] **Step 1: Write failing snapshot and retry tests**

```python
async def test_retry_reuses_the_frozen_strategy_not_the_mutated_profile() -> None:
    source = await create_failed_review_from_profile(
        profile=adaptive_profile(revision=2, budget="standard")
    )
    await profile_repository.save(adaptive_profile(revision=3, budget="deep"))

    retried = await retry_handler.handle(source.task_id)

    assert retried.selection_request == {"mode": "adaptive"}
    assert retried.budget_profile == "standard"
    assert retried.profile_source_revision == 2
```

Add tests that Fixed preserves reviewer order, Adaptive stores no reviewer list, source ID/Revision remain paired, trigger source persists, and legacy `selected_agents` becomes Fixed + Standard only at the Interface boundary.

Add real SQLite transaction tests proving:

- identical repository + base/head Snapshot + complete frozen policy returns the existing task ID;
- the trigger slot excludes Snapshot identity but includes repository, reviewer-selection fingerprint, Planner/Catalog version, budget, and Capability/Skill policy fingerprint;
- `latest_snapshot` supersedes older queued tasks in the same slot and requests cooperative cancellation for running tasks;
- `preserve_all` leaves older tasks unchanged;
- completed/partial/failed/canceled historical tasks are never deleted or rewritten.

- [ ] **Step 2: Run focused task/command/store tests**

Run: `uv run --project backend pytest backend/tests/unit/review/test_review_task.py backend/tests/unit/review/test_commands.py backend/tests/integration/review/test_sqlite_store.py backend/tests/integration/review/test_create_triggered_review.py backend/tests/contract/http/test_reviews_api.py -v`

Expected: FAIL because tasks currently persist only `selected_agent_versions_json`.

- [ ] **Step 3: Implement migration and strategy persistence**

Migration `0006_review_selection_requests` adds:

```text
review_tasks.selection_request_json      TEXT NULL for legacy, required by new writes
review_tasks.budget_profile              VARCHAR(16) NULL for legacy, required by new writes
review_tasks.profile_source_id           VARCHAR(128) NULL
review_tasks.profile_source_revision     INTEGER NULL
review_tasks.trigger_source              VARCHAR(16) NULL
review_tasks.supersede_policy            VARCHAR(32) NULL
review_tasks.idempotency_key             VARCHAR(64) NULL
review_tasks.trigger_slot_key            VARCHAR(64) NULL
review_tasks.planning_context_json       TEXT NULL for legacy, required by new writes
review_tasks.planning_context_hash       VARCHAR(64) NULL for legacy, required by new writes
```

`planning_context_json` contains the versioned budget policy, Catalog snapshot, Capability readiness, and serialized frozen Planner/eligible-Reviewer execution specs. Each spec contains stable tool/MCP/Skill metadata and Artifact IDs for exact prompt/Skill bytes; prompt or Skill bodies never enter the database row. The SHA-256 hash covers its canonical JSON. Backfill every existing row with Fixed selection derived byte-for-byte from `selected_agent_versions_json`, `budget_profile='standard'`, null Profile provenance, and null v2-only fields. Add unique partial indexes for non-null `idempotency_key` and the queries needed by non-null `trigger_slot_key`. Keep `selected_agent_versions_json` for actual-team and legacy API compatibility. `RetryReviewHandler` copies the frozen strategy and planning context but allocates a new manual retry identity; it never consults `ReviewProfileRepository` or replays plugin supersede behavior.

`CreateTriggeredReviewHandler` computes canonical SHA-256 keys from already resolved repository/Snapshot and frozen policy values, then performs deduplicate/supersede/cancel-intent/outbox writes in one transaction. It does not import the plugin context. A trigger task is durably committed before the worker runs Adaptive Planner.

Its review-owned command is:

```python
@dataclass(frozen=True, slots=True)
class CreateTriggeredReview:
    repository: RepositoryInfo
    scope: ReviewScope
    review_profile: ReviewProfileSnapshot
    prompt_locale: PromptLocale
    supersede_policy: Literal["latest_snapshot", "preserve_all"]
    external_context: Mapping[str, object] | None
```

The handler resolves the scope to pinned base/head OIDs through the existing workspace/Snapshot Port and freezes `planning_context_json` before opening the idempotency transaction. Scope, Catalog, or required-Capability resolution failure creates no ReviewTask. Worker recovery loads the frozen context and rejects a hash mismatch instead of re-resolving current Catalog configuration.

Do not expose Adaptive creation through HTTP yet. The existing DTO continues to adapt `selected_agents` into a Fixed `ReviewProfileSnapshot`; Task 8 atomically switches the public DTO after the runtime path is complete.

- [ ] **Step 4: Test migration, legacy creation, and retry**

Run:

```bash
uv run --project backend alembic -c backend/alembic.ini heads
uv run --project backend pytest backend/tests/unit/review/test_review_task.py backend/tests/unit/review/test_commands.py backend/tests/integration/review/test_sqlite_store.py backend/tests/integration/review/test_create_triggered_review.py backend/tests/contract/http/test_reviews_api.py -v
uv run --project backend ruff check backend/src/codelens/review backend/src/codelens/interface/http
uv run --project backend mypy backend/src/codelens/review backend/src/codelens/interface/http
```

Expected: the graph reports one head and all commands exit `0`; integration tests apply the migration to temporary fresh and previous-head databases, and old HTTP requests still work.

- [ ] **Step 5: Commit frozen selection persistence**

```bash
git add backend/src/codelens/review backend/src/codelens/interface/http backend/migrations/versions/0006_review_selection_requests.py backend/tests
git commit -m "feat: persist frozen review selection requests"
```

---

### Task 3: Review Plan, AgentRun Metadata, and Finding Audit Persistence

**Files:**
- Modify: `backend/src/codelens/review/domain/agent_run.py`
- Modify: `backend/src/codelens/review/domain/ports.py`
- Modify: `backend/src/codelens/review/infrastructure/tables.py`
- Modify: `backend/src/codelens/review/infrastructure/repositories.py`
- Create: `backend/migrations/versions/0007_multi_agent_review_dag.py`
- Modify: `backend/tests/unit/review/test_agent_run.py`
- Modify: `backend/tests/integration/review/test_sqlite_store.py`
- Modify: `backend/tests/integration/worker/test_restart_recovery.py`

**Interfaces:**
- Produces: `ReviewPlanStorePort`, `AgentExecutionSpecStorePort`, `CandidateFindingStorePort`, `ResolutionStorePort`, extended `AgentRunRecord`/checkpoint views.
- Reuses `dag_checkpoints`; does not create a second generic Node entity.
- Every write that changes a node terminal state and emits an event is one database transaction.

- [ ] **Step 1: Write failing persistence and restart tests**

```python
async def test_review_plan_and_nodes_survive_restart(database_path: Path) -> None:
    first = await open_stores(database_path)
    plan = fixed_team_plan("correctness:v2", "security:v1")
    await first.plan_store.save(plan)
    await first.checkpoints.ensure_plan_nodes(plan)
    await first.close()

    second = await open_stores(database_path)

    assert await second.plan_store.get(plan.task_id) == plan
    assert {
        (node.agent_reference, node.pass_index, node.shard_id)
        for node in await second.checkpoints.list_for_task(plan.task_id)
    } == {
        ("correctness:v2", 1, "root"),
        ("security:v1", 1, "root"),
        ("review-resolver:v1", 2, "root"),
    }
```

Add tests for stable run ID across physical retry, capability fingerprint persistence, Candidate/Cluster/Decision round trips, nullable legacy node metadata, and exactly-once Published Finding identity.

Add a restart test that changes the current Reviewer Catalog, prompt override, Skill text, and MCP binding after a task is created, then proves Planner/Reviewer recovery loads the stored execution spec and exact prompt/Skill Artifacts or fails on a hash mismatch; it must never substitute current configuration.

- [ ] **Step 2: Run store and recovery tests**

Run: `uv run --project backend pytest backend/tests/unit/review/test_agent_run.py backend/tests/integration/review/test_sqlite_store.py backend/tests/integration/worker/test_restart_recovery.py -v`

Expected: FAIL because the plan and audit stores do not exist.

- [ ] **Step 3: Implement migration and repositories**

Migration `0007_multi_agent_review_dag` must:

- create `review_plans` with unique `task_id`, canonical plan JSON, `plan_hash`, catalog version, budget, Capability fingerprint, and timestamp;
- create `agent_execution_specs` keyed by stable logical node ID with canonical spec JSON, fingerprint, prompt Artifact ID, Skill Artifact IDs, and unique `(task_id, logical_node_id)`; prompt/Skill bodies remain in the permission-restricted Artifact Store;
- add nullable `run_id`, `node_role`, `agent_version`, `pass_index`, `shard_id`, `capability_fingerprint`, and `result_summary_json` columns to `dag_checkpoints` for legacy compatibility;
- create `candidate_findings`, `finding_clusters`, and `resolution_decisions` with foreign keys and unique deterministic IDs;
- make `findings.confidence` nullable for Comment v2 while retaining existing numeric values;
- add `findings.verification_status` and keep v2 categorical/provenance fields inside validated `payload_json`;
- add `superseded` to application state handling without a database enum migration because statuses are stored strings.

Never persist prompt bodies, source bodies, raw MCP output, or secrets in these tables.

- [ ] **Step 4: Apply migration and run persistence/recovery tests**

Run:

```bash
uv run --project backend alembic -c backend/alembic.ini heads
uv run --project backend pytest backend/tests/unit/review/test_agent_run.py backend/tests/integration/review/test_sqlite_store.py backend/tests/integration/worker/test_restart_recovery.py -v
uv run --project backend ruff check backend/src/codelens/review backend/migrations/versions/0007_multi_agent_review_dag.py
uv run --project backend mypy backend/src/codelens/review
```

Expected: the graph reports one head and all commands exit `0`.

The migration portion of `test_sqlite_store.py` must exercise fresh install, upgrade from `0006_review_selection_requests`, and downgrade/upgrade round-trip on temporary databases. The `alembic heads` command is graph-only and must not mutate `backend/codelens.sqlite3`.

- [ ] **Step 5: Commit DAG persistence**

```bash
git add backend/src/codelens/review backend/migrations/versions/0007_multi_agent_review_dag.py backend/tests
git commit -m "feat: persist multi-agent review plans and audit state"
```

---

### Task 4: Fixed Compiler and Adaptive Planner Pass

**Files:**
- Create: `backend/src/codelens/review/application/planning.py`
- Create: `backend/src/codelens/review/application/budget_policy.py`
- Create: `backend/src/codelens/review/infrastructure/planning_tools.py`
- Create: `backend/src/codelens/review/infrastructure/planner_output.py`
- Modify: `backend/src/codelens/review/infrastructure/capability_tools.py`
- Modify: `backend/src/codelens/worker/execution.py`
- Create: `backend/tests/unit/review/test_planning.py`
- Create: `backend/tests/contract/review/test_planner_output.py`

**Interfaces:**
- Produces: `ChangeRiskSummary`, `PlannerRiskSignal`, `PlannerReviewerDecision`, `PlannerSelection`, `ReviewPlanCompiler`, `ReviewPlanningService`.
- `ReviewPlanningService.plan(task_id, snapshot, profile) -> ReviewPlan` persists a valid plan before returning.
- Planner output contains reviewer references plus concise reasons; it contains no tools, capabilities, Findings, prompts, or free-form DAG.

- [ ] **Step 1: Write failing Fixed/Adaptive planning tests**

```python
async def test_fixed_compiler_never_invokes_planner() -> None:
    planner = FailingIfCalledPlanner()
    service = planning_service(planner=planner)

    plan = await service.plan(
        task_id=TASK_ID,
        snapshot=small_snapshot(),
        profile=fixed_profile("correctness:v2", "security:v1"),
    )

    assert plan.reviewer_references == ("correctness:v2", "security:v1")
    assert plan.planner_reason is None
    assert planner.call_count == 0


async def test_adaptive_rejects_general_plus_specialists() -> None:
    service = planning_service(
        planner=FakePlanner(("general:v1", "security:v1"), reason="mixed")
    )

    with pytest.raises(InvalidReviewPlanError, match="General reviewer must run alone"):
        await service.plan(TASK_ID, small_snapshot(), adaptive_profile())
```

Add tests for Adaptive selecting General, Adaptive selecting 2–N specialists, one-specialist Adaptive rejection, Planner failure without fallback, Fixed budget overflow rejection, deterministic plan hash, and budget reservation for Resolver/Verifier.

Add Capability-readiness tests: Fixed creation fails before a task is queued when a required Reviewer is not Ready; Adaptive input marks the Reviewer unavailable and rejects Planner output that selects it. Optional unavailable MCP/Skill capabilities are removed before the execution spec freezes and appear in Plan degradation metadata without making the task `partial`.

Add exact Budget Policy tests: Lean Adaptive can select only General, Fixed Lean permits General or one Specialist, Standard permits at most three Specialists, Deep permits all seven Specialists, and every profile reserves its possible Planner/Resolver/Verifier nodes and token/tool budget before Reviewer fan-out.

- [ ] **Step 2: Run planning tests and verify missing services**

Run: `uv run --project backend pytest backend/tests/unit/review/test_planning.py backend/tests/contract/review/test_planner_output.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement host compilation and bounded Planner submission**

`ChangeRiskSummary` is host-derived from frozen file metadata: path, change type, changed line counts, file count, language hints, and normalized risk signals. It contains no complete source body. The Planner input also contains frozen `review_files`, repository instructions, the eligible Reviewer Catalog projection with Capability readiness, and budget limits; Snapshot evidence remains available only through the Planner Capability Profile's read-only tools.

```python
@dataclass(frozen=True)
class PlannerRiskSignal:
    code: str
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class PlannerReviewerDecision:
    reviewer_reference: str
    is_selected: bool
    reason_codes: tuple[str, ...]
    focus_paths: tuple[str, ...]


@dataclass(frozen=True)
class PlannerSelection:
    schema_version: Literal["1"]
    strategy: Literal["generalist", "specialist_team"]
    risk_signals: tuple[PlannerRiskSignal, ...]
    reviewer_decisions: tuple[PlannerReviewerDecision, ...]

    @property
    def reviewer_references(self) -> tuple[str, ...]:
        return tuple(
            decision.reviewer_reference
            for decision in self.reviewer_decisions
            if decision.is_selected
        )


class ReviewPlanCompiler:
    def compile(
        self,
        *,
        task_id: str,
        selection_mode: Literal["fixed", "adaptive"],
        reviewer_references: tuple[str, ...],
        budget_profile: BudgetProfile,
        planner_reason: str | None,
        execution_specs: Mapping[str, FrozenAgentExecutionSpec],
    ) -> ReviewPlan:
        validated = self._catalog.validate_team(reviewer_references, selection_mode)
        self._budget_policy.validate(validated, budget_profile)
        return self._build_nodes(
            task_id, selection_mode, validated, budget_profile, planner_reason, execution_specs
        )
```

The `submit_review_plan` tool accepts one bounded object, requires exactly one decision for every Planner-eligible Catalog entry, validates reason codes and Snapshot-scoped `focus_paths`, rejects unknown/unready Reviewer versions, and becomes the only successful completion signal for `review-planner:v1`. The host validates legality but never adds or removes a Reviewer. Persist Planner output Artifact and checkpoint before validating and persisting the Review Plan.

Implement version 1 policy defaults as one injected `BudgetPolicyCatalog`, and include its version and resolved limits in `planning_context_hash`:

```python
@dataclass(frozen=True, slots=True)
class BudgetLimits:
    max_reviewers: int
    max_model_nodes: int
    per_review_concurrency: int
    max_total_tokens: int
    max_node_output_tokens: int
    max_turns_per_node: int
    max_tool_calls_per_node: int
    max_task_seconds: int
    max_verifier_clusters: int


BUDGET_POLICY_V1: Mapping[BudgetProfile, BudgetLimits] = {
    BudgetProfile.LEAN: BudgetLimits(1, 2, 1, 100_000, 8_000, 12, 80, 300, 0),
    BudgetProfile.STANDARD: BudgetLimits(3, 6, 3, 400_000, 16_000, 20, 240, 900, 12),
    BudgetProfile.DEEP: BudgetLimits(7, 10, 4, 1_200_000, 24_000, 30, 600, 1_800, 40),
}
```

Configuration may replace the complete versioned catalog at bootstrap, but cannot mutate one task after its planning context freezes. `BudgetPolicy` converts these task limits into role-specific `AgentExecutionLimits`. A provider-neutral `TokenEstimatorPort` records estimator/model version and computes each frozen input estimate; the shared task ledger atomically reserves `estimated_input_tokens + max_output_tokens` plus node/tool capacity before scheduling, then reconciles provider-reported usage. Because concurrent nodes reserve worst-case output first, total budget cannot be oversubscribed. A node exceeding its frozen output, turn, tool, or timeout limit fails with a typed budget reason and follows that role's normal failure policy.

- [ ] **Step 4: Run planning contracts and static checks**

Run:

```bash
uv run --project backend pytest backend/tests/unit/review/test_planning.py backend/tests/contract/review/test_planner_output.py -v
uv run --project backend ruff check backend/src/codelens/review/application backend/src/codelens/review/infrastructure
uv run --project backend mypy backend/src/codelens/review
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit planning**

```bash
git add backend/src/codelens/review backend/tests/unit/review/test_planning.py backend/tests/contract/review/test_planner_output.py
git commit -m "feat: compile fixed and adaptive review plans"
```

---

### Task 5: Persisted Reviewer Fan-Out and Partial Failure Semantics

**Files:**
- Modify: `backend/src/codelens/review/application/orchestrator.py`
- Create: `backend/src/codelens/review/application/dag_scheduler.py`
- Modify: `backend/src/codelens/review/domain/models.py`
- Modify: `backend/src/codelens/review/infrastructure/repositories.py`
- Modify: `backend/src/codelens/worker/execution.py`
- Modify: `backend/src/codelens/worker/scheduler.py`
- Modify: `backend/tests/unit/review/test_orchestrator.py`
- Modify: `backend/tests/integration/worker/test_concurrent_tasks.py`
- Modify: `backend/tests/integration/worker/test_restart_recovery.py`

**Interfaces:**
- Produces: `PersistedDagScheduler.next_ready_nodes(task_id)`, extended `ReviewOrchestrator.execute` using persisted plan/nodes.
- Task statuses become `queued`, `planning`, `reviewing`, `resolving`, `verifying`, `completed`, `partial`, `failed`, `canceled`, `superseded`; HTTP maps legacy in-progress states during migration.
- Global, per-review, and provider/model semaphores all gate model calls.

- [ ] **Step 1: Write failing fan-out, fairness, and partial tests**

```python
async def test_one_specialist_failure_allows_remaining_team_to_resolve() -> None:
    runtime = ScriptedRuntime({
        "correctness:v2": candidate_output("candidate-correctness"),
        "security:v1": TransientAgentRuntimeError("provider unavailable"),
        "test-regression:v1": candidate_output("candidate-tests"),
        "review-resolver:v1": publish_both_resolution(),
    })

    await team_orchestrator(runtime).execute(TASK_ID)

    assert await review_status(TASK_ID) == "partial"
    assert await node_status(TASK_ID, "security:v1") == "failed"
    assert await node_status(TASK_ID, "review-resolver:v1") == "succeeded"


async def test_all_reviewer_failures_fail_the_task() -> None:
    runtime = runtime_failing_all_reviewers()

    await team_orchestrator(runtime).execute(TASK_ID)

    assert await review_status(TASK_ID) == "failed"
    assert await node_exists(TASK_ID, "review-resolver:v1") is False
```

Add tests for General failure, Fixed Single failure, Resolver not scheduled before all Reviewer nodes are terminal, task-level concurrency, Worker fairness between two tasks, cancel propagation, and restart after one reviewer output was saved.

Add an Adaptive execution test proving each Reviewer input contains its frozen Planner `reason_codes` and `focus_paths`, but the Snapshot tools still expose the complete allowed Review Snapshot. A `focus_path` narrows attention, never permissions.

- [ ] **Step 2: Run orchestrator and worker integration tests**

Run: `uv run --project backend pytest backend/tests/unit/review/test_orchestrator.py backend/tests/integration/worker/test_concurrent_tasks.py backend/tests/integration/worker/test_restart_recovery.py -v`

Expected: FAIL because current `asyncio.gather` aborts the whole batch on any Agent exception and has no persisted role scheduler.

- [ ] **Step 3: Implement persisted scheduling and failure reduction**

Replace phase-wide in-memory `gather` decisions with persisted node queries. Concurrent execution may still use `asyncio.TaskGroup`/`gather(return_exceptions=True)`, but readiness and terminal reduction must come from repositories after each node transaction commits.

Failure reduction is exact:

```python
def reviewer_stage_outcome(nodes: Sequence[AgentRunRecord]) -> Literal["continue", "partial", "failed"]:
    succeeded = sum(node.status == "succeeded" for node in nodes)
    failed = sum(node.status in {"failed", "timed_out"} for node in nodes)
    if succeeded == 0 and failed > 0:
        return "failed"
    if failed > 0:
        return "partial"
    return "continue"
```

Store the partial marker independently of current phase so a later successful Resolver/Verifier cannot erase it. A task-level semaphore is created per execution; global/provider semaphores remain shared in the Worker composition root.

- [ ] **Step 4: Run concurrency, recovery, and static checks**

Run:

```bash
uv run --project backend pytest backend/tests/unit/review/test_orchestrator.py backend/tests/integration/worker/test_concurrent_tasks.py backend/tests/integration/worker/test_restart_recovery.py -v
uv run --project backend ruff check backend/src/codelens/review backend/src/codelens/worker
uv run --project backend mypy backend/src/codelens/review backend/src/codelens/worker
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit persisted fan-out**

```bash
git add backend/src/codelens/review backend/src/codelens/worker backend/tests
git commit -m "feat: execute persisted reviewer fan-out"
```

---

### Task 6: Candidate Validation, Clustering, and Resolver Pass

**Files:**
- Create: `backend/src/codelens/findings/application/validate_candidates.py`
- Create: `backend/src/codelens/findings/application/cluster_candidates.py`
- Create: `backend/src/codelens/findings/application/resolve_clusters.py`
- Create: `backend/src/codelens/findings/infrastructure/resolver_output.py`
- Create: `backend/src/codelens/review/infrastructure/resolution_tools.py`
- Modify: `backend/src/codelens/review/infrastructure/capability_tools.py`
- Modify: `backend/src/codelens/review/application/orchestrator.py`
- Create: `backend/tests/unit/findings/test_validate_candidates.py`
- Create: `backend/tests/unit/findings/test_cluster_candidates.py`
- Create: `backend/tests/contract/review/test_resolver_output.py`
- Modify: `backend/tests/unit/review/test_orchestrator.py`

**Interfaces:**
- Produces: `CandidateValidator`, `CandidateClusterer`, `ResolutionService`, `ResolverOutputCodec`.
- Candidate ordering shown to Resolver is deterministically shuffled from `plan_hash`; provider and execution ordering are absent.
- Multi-specialist output remains Candidate/Audit state until Resolver decision commits.

- [ ] **Step 1: Write failing clustering and no-invention tests**

```python
def test_candidates_with_same_location_root_cause_and_impact_cluster_together() -> None:
    clusters = CandidateClusterer().cluster((
        candidate("candidate-a", reviewer="correctness:v2", path="src/cache.py", line=40),
        candidate("candidate-b", reviewer="security:v1", path="src/cache.py", line=40),
    ))

    assert len(clusters) == 1
    assert clusters[0].candidate_ids == ("candidate-a", "candidate-b")


def test_resolver_cannot_raise_severity_above_all_candidates() -> None:
    cluster = cluster_with_severities("medium", "low")

    with pytest.raises(ResolutionValidationError, match="severity"):
        validate_resolution(cluster, publish_resolution(severity="high"))
```

Implement typed test factories with the exact signatures `candidate(candidate_id: str, *, reviewer: str, path: str, line: int) -> CandidateFinding`, `cluster_with_severities(*severities: str) -> FindingCluster`, and `publish_resolution(*, severity: str) -> ResolutionDecision`. Each factory builds complete validated Phase 1 values; no dictionaries or unchecked casts are allowed. Add tests for distinct root causes at the same line, deterministic cluster IDs, invalid evidence/location IDs, single-reporter Candidate still reaching Resolver, and `suppress` audit persistence.

- [ ] **Step 2: Run Finding and Resolver tests**

Run: `uv run --project backend pytest backend/tests/unit/findings/test_validate_candidates.py backend/tests/unit/findings/test_cluster_candidates.py backend/tests/contract/review/test_resolver_output.py backend/tests/unit/review/test_orchestrator.py -v`

Expected: FAIL because the Candidate application pipeline is missing.

- [ ] **Step 3: Implement deterministic pre-processing and constrained resolution**

The host validates Snapshot path, side, line range, excerpt hash, dimension, categorical axes, and reviewer identity before persistence. Cluster keys include Snapshot ID, normalized path/range, normalized root-cause category/title, impact class, and evidence hashes. Do not cluster from reviewer majority.

`submit_resolution` accepts only Cluster IDs and Candidate IDs from the bounded input. It returns `publish`, `suppress`, or `verify`, canonical Candidate ID, merged IDs, severity no higher than the Candidate maximum, normalized title/content/recommendation, and a reason code. Validate every field before committing a `ResolutionDecision`.

General and Fixed Single Specialist bypass the Resolver but must pass the same Candidate validation and the direct-publication rule: only direct evidence with confirmed or plausible impact publishes; weak evidence or unclear impact remains internal and suppressed.

- [ ] **Step 4: Run Candidate/Resolver and orchestrator tests**

Run:

```bash
uv run --project backend pytest backend/tests/unit/findings backend/tests/contract/review/test_resolver_output.py backend/tests/unit/review/test_orchestrator.py -v
uv run --project backend ruff check backend/src/codelens/findings backend/src/codelens/review
uv run --project backend mypy backend/src/codelens/findings backend/src/codelens/review
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit Resolver pipeline**

```bash
git add backend/src/codelens/findings backend/src/codelens/review backend/tests
git commit -m "feat: resolve multi-reviewer candidate clusters"
```

---

### Task 7: Conditional Verifier and Exactly-Once Publication

**Files:**
- Create: `backend/src/codelens/findings/application/verify_resolutions.py`
- Create: `backend/src/codelens/findings/application/publish_findings.py`
- Create: `backend/src/codelens/findings/infrastructure/verifier_output.py`
- Create: `backend/src/codelens/review/infrastructure/verification_tools.py`
- Modify: `backend/src/codelens/review/infrastructure/capability_tools.py`
- Modify: `backend/src/codelens/review/application/orchestrator.py`
- Modify: `backend/src/codelens/review/infrastructure/repositories.py`
- Create: `backend/tests/unit/findings/test_verify_resolutions.py`
- Create: `backend/tests/unit/findings/test_publish_findings.py`
- Create: `backend/tests/contract/review/test_verifier_output.py`
- Modify: `backend/tests/integration/worker/test_restart_recovery.py`

**Interfaces:**
- Produces: `VerificationPolicy`, `VerificationService`, `FindingPublisher`.
- At most one `review-verifier:v1` node exists per task; its shard is `batch` and input contains every Resolver `verify` decision.
- Publication transaction writes final Findings, marks decisions, completes the node, and emits events idempotently.

- [ ] **Step 1: Write failing verification and publication tests**

```python
async def test_unresolved_verification_is_suppressed() -> None:
    service = verification_service(
        output=verification_output("cluster-1", outcome="unresolved")
    )

    result = await service.verify((verify_decision("cluster-1"),))

    assert result.published == ()
    assert result.suppressed_cluster_ids == ("cluster-1",)


async def test_verifier_failure_keeps_direct_publications_and_marks_partial() -> None:
    runtime = verifier_timeout_after_one_direct_publication()

    await team_orchestrator(runtime).execute(TASK_ID)

    assert [finding.title for finding in await published_findings(TASK_ID)] == [
        "Direct confirmed issue"
    ]
    assert await review_status(TASK_ID) == "partial"
```

Add tests for one batch only, unknown Cluster rejection, duplicate event replay, restart after Finding rows commit but before process exit, and nullable confidence plus categorical/provenance fields on v2 Findings.

- [ ] **Step 2: Run Verifier and publication tests**

Run: `uv run --project backend pytest backend/tests/unit/findings/test_verify_resolutions.py backend/tests/unit/findings/test_publish_findings.py backend/tests/contract/review/test_verifier_output.py backend/tests/integration/worker/test_restart_recovery.py -v`

Expected: FAIL because no Verifier or v2 publication boundary exists.

- [ ] **Step 3: Implement conditional verification and publication**

`VerificationPolicy` selects exactly the Resolver decisions whose outcome is `verify`. `submit_verification` accepts a bounded list of `{cluster_id, outcome, reason}` with outcomes `confirmed`, `rejected`, and `unresolved`. It cannot submit a title, location, severity, impact, recommendation, or new evidence.

`FindingPublisher` uses the Resolver canonical representation for `publish` and Verifier-confirmed decisions. It sets `confidence=None` for Comment v2, preserves numeric confidence for Comment v1, stores categorical axes and `source_reviewer_references`, and uses the existing unique `(task_id, fingerprint)` effect to make replay harmless.

- [ ] **Step 4: Run verification, recovery, and Finding API regression tests**

Run:

```bash
uv run --project backend pytest backend/tests/unit/findings backend/tests/contract/review backend/tests/integration/worker/test_restart_recovery.py backend/tests/contract/http/test_reviews_api.py -v
uv run --project backend ruff check backend/src/codelens/findings backend/src/codelens/review
uv run --project backend mypy backend/src/codelens/findings backend/src/codelens/review
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit Verifier and publication**

```bash
git add backend/src/codelens/findings backend/src/codelens/review backend/tests
git commit -m "feat: verify and publish resolved findings"
```

---

### Task 8: Public API, Coverage, Failure Recovery, and Architecture Gate

**Files:**
- Modify: `backend/src/codelens/interface/http/dto.py`
- Modify: `backend/src/codelens/interface/http/routers/reviews.py`
- Create: `backend/src/codelens/interface/http/routers/reviewer_catalog.py`
- Modify: `backend/src/codelens/interface/http/dependencies.py`
- Modify: `backend/src/codelens/review/domain/models.py`
- Modify: `backend/src/codelens/review/domain/ports.py`
- Modify: `backend/src/codelens/review/infrastructure/repositories.py`
- Modify: `backend/src/codelens/review/application/process_report.py`
- Modify: `backend/src/codelens/worker/execution.py`
- Modify: `backend/src/codelens/bootstrap/unified.py`
- Modify: `backend/tests/contract/http/test_reviews_api.py`
- Create: `backend/tests/contract/http/test_reviewer_catalog_api.py`
- Modify: `backend/tests/unit/review/test_event_bus.py`
- Modify: `backend/tests/unit/review/test_transcripts.py`
- Modify: `backend/tests/unit/review/test_process_report.py`
- Modify: `backend/tests/integration/worker/test_concurrent_tasks.py`
- Modify: `backend/tests/integration/worker/test_restart_recovery.py`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- New review request fields: `reviewer_selection`, `budget_profile`, optional `profile_source`; legacy `selected_agents` is accepted only when the v2 fields are absent.
- Review response fields: `selection_request`, `profile_source`, nullable `review_plan`, `coverage` with Planned/Completed/Failed/Omitted views, `resolution_summary`, and actual `selected_agents` compatibility projection.
- Catalog endpoint returns public Reviewer versions, dimensions, cost class, Planner eligibility, Capability readiness, and legacy visibility.

- [ ] **Step 1: Write failing end-to-end contract and failure-table tests**

```python
async def test_adaptive_review_contract_exposes_plan_and_partial_coverage(client: AsyncClient) -> None:
    created = await client.post(
        "/api/reviews",
        json={
            "repository_path": str(REPOSITORY),
            "scope": {"type": "uncommitted"},
            "reviewer_selection": {"mode": "adaptive"},
            "budget_profile": "standard",
            "prompt_locale": "en",
        },
    )
    task_id = created.json()["task_id"]
    await worker.run_until_terminal(task_id)

    response = (await client.get(f"/api/reviews/{task_id}")).json()

    assert response["selection_request"] == {"mode": "adaptive"}
    assert response["review_plan"]["reviewer_references"]
    assert response["coverage"]["failed"] == ["reliability-concurrency:v1"]
    assert response["status"] == "partial"
```

Add a parameterized failure table covering Planner failure, General failure, single Specialist failure, some/all team failure, Resolver failure, Verifier failure, cancellation in every pass, process restart in every checkpoint, and stale retry. Add contract tests rejecting simultaneous `selected_agents` and `reviewer_selection`.

Extend event/transcript tests to prove ordinary SSE payloads, database events, process reports, and runtime logs omit prompt bodies, source bodies, raw MCP output, Skill text, tool arguments containing code, and Secrets. Full redacted exchanges remain only in the existing permission-restricted Artifact/transcript channel with its current rotation and file-mode checks.

- [ ] **Step 2: Run HTTP, SSE, process report, and integration tests**

Run: `uv run --project backend pytest backend/tests/contract/http/test_reviews_api.py backend/tests/contract/http/test_reviewer_catalog_api.py backend/tests/unit/review/test_event_bus.py backend/tests/unit/review/test_process_report.py backend/tests/integration/worker -v`

Expected: FAIL because public DTOs and event projections still expose the legacy shape.

- [ ] **Step 3: Expose v2 contracts only after persisted runtime support exists**

Use Pydantic discriminated request DTOs:

```python
class FixedReviewerSelectionRequest(StrictDto):
    mode: Literal["fixed"]
    reviewer_versions: Annotated[list[AgentReference], Field(min_length=1, max_length=32)]


class AdaptiveReviewerSelectionRequest(StrictDto):
    mode: Literal["adaptive"]


ReviewerSelectionRequest = Annotated[
    FixedReviewerSelectionRequest | AdaptiveReviewerSelectionRequest,
    Field(discriminator="mode"),
]
```

A model validator requires exactly one of v2 `reviewer_selection` or legacy `selected_agents`. The legacy adapter creates Fixed + Standard and never upgrades reviewer references.

Persist and emit these event names with bounded payloads: `review.plan_created`, `agent_run.started`, `agent_run.completed`, `agent_run.failed`, `review.resolution_completed`, `review.verification_completed`, `review.completed`, `review.partial`, `review.failed`, `review.canceled`, and `review.superseded`. Coverage is rebuilt from persisted plan/node state after refresh; SSE is notification, not the source of truth.

Update `docs/ARCHITECTURE.md` to make Review Profiles, selection snapshots, Review Plan/DAG ownership, Candidate/Resolver/Verifier boundaries, task states, and new stable HTTP/SSE fields authoritative.

- [ ] **Step 4: Run migrations, full backend gates, and diff checks**

Run:

```bash
uv run --project backend alembic -c backend/alembic.ini heads
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
git diff --check
```

Expected: the graph reports one head and all commands exit `0`. Do not run real model or network tests as part of the default suite, and do not mutate `backend/codelens.sqlite3`.

- [ ] **Step 5: Commit the backend multi-Agent gate**

```bash
git add backend docs/ARCHITECTURE.md
git commit -m "feat: expose persistent multi-agent review orchestration"
```
