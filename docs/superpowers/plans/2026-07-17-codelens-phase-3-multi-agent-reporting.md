# CodeLens Phase 3 Multi-Agent Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Extend the Phase 0-2 vertical slice into a durable seven-reviewer workflow with bounded parallelism, selective evidence verification, suppression, deduplication, constrained synthesis, and complete Agent Run reporting.

**Architecture:** Python application code owns the fan-out/fan-in DAG and persists one idempotent AgentRun per concrete execution node (ReviewerVersion, pass, shard, and logical attempt group). Selected Reviewers concurrently read the same task-owned read-only worktree; same-repository ReviewTasks may also run concurrently in different worktrees. OpenAI Agents SDK remains behind runtime ports and does not choose configured nodes. Validated findings pass through verification, suppression, exact deduplication, bounded clustering, and constrained synthesis.

**Tech Stack:** Python 3.12, asyncio, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite WAL, OpenAI Agents SDK, React, TypeScript, TanStack Query, Vitest, Playwright.

## Global Constraints

- The authoritative design is <code>docs/superpowers/specs/2026-07-17-codelens-review-app-design.md</code>.
- Phase 0-2 is complete before this plan starts; preserve its REST/SSE contracts.
- The domain layer imports no FastAPI, SQLAlchemy, OpenAI, Git, filesystem, or MCP implementation.
- Every selected AgentVersion is frozen on ReviewTask creation and receives the same task-owned worktree/Snapshot.
- Application code controls scheduling, concurrency, retry, timeout, cancellation, completion, and resume.
- Default per-ReviewTask reviewer concurrency is 4 (1–16); the shared AgentRun limit defaults to 8 so multiple tasks can progress concurrently.
- A completed AgentRun is never executed again after Worker restart.
- Unvalidated final output is checkpointed before validation; only the atomic Finding/success transaction completes a run.
- Event payloads contain identifiers, states, counts, usage, and artifact references only; never raw prompts or full model output.
- At least one successful reviewer produces PARTIAL when another selected reviewer fails; all failed produces FAILED.
- The main synthesizer cannot create findings or increase severity without a verifier decision.
- TDD is mandatory: failing focused test, observed failure, minimal implementation, focused pass, then commit.

## Official OpenAI References

- Agents SDK overview and runtime ownership: https://developers.openai.com/api/docs/guides/agents
- Orchestration ownership patterns: https://developers.openai.com/api/docs/guides/agents/orchestration
- Tracing and observability: https://developers.openai.com/api/docs/guides/agents/integrations-observability
- Guardrails and resumable approval concepts: https://developers.openai.com/api/docs/guides/agents/guardrails-approvals
- Python result surfaces, typed final output, and raw responses: https://openai.github.io/openai-agents-python/results/

The implementation does not use handoffs for reviewer selection. Each reviewer is a bounded SDK run launched by the deterministic application scheduler.

## 2026-07-17 Correctness Amendment

- Phase 3 extends the Phase 0–2 AgentRun identity and state machine; it does not replace them with a unique
  <code>(task_id, agent_reference)</code> row or a public <code>succeed()</code> method.
- The fan-out calls Reviewer runtimes concurrently under both global and per-task semaphores. All inputs are
  contained in the verified task worktree; no Agent may write it.
- Runtime return values are persisted as canonical unvalidated-output Artifacts before deterministic validation.
  Finding insert, run success, usage, and outbox success event are atomic.
- One Reviewer failure produces PARTIAL when another succeeds. Cancellation propagates through the task group but
  does not cancel unrelated ReviewTasks.
- The singleton Worker recovery defined in Phase 0–2 remains authoritative; Phase 3 adds node-level resume and does
  not add leases, reclamation, or multiple Worker ownership.

---

## File And Module Map

~~~text
backend/src/codelens/
  reviewer_catalog/
    domain/models.py                 # immutable definitions, versions, model profiles
    application/builtin_catalog.py   # seven built-in definitions
  review/
    domain/agent_run.py              # AgentRun state and usage
    domain/report.py                 # ReviewReport and coverage contracts
    domain/ports.py                  # reviewer/verifier/cluster/synthesis ports
    application/multi_agent.py       # bounded fan-out and retry
    application/pipeline.py          # durable fan-in workflow
    application/synthesis.py         # synthesis validation and fallback
    infrastructure/repositories.py   # task, run, report persistence
    infrastructure/openai_nodes.py   # SDK adapters for system nodes
    infrastructure/run_artifacts.py  # opaque local run-output references
  findings/
    domain/verification.py           # verification decisions
    domain/suppression.py            # suppression contract
    application/verification.py      # candidate selection and decisions
    application/deduplication.py     # exact dedupe and clustering validation
    application/suppression.py       # deterministic suppression matching
  governance/
    domain/feedback.py               # explicit user feedback
  interface/http/
    agents.py                        # catalog queries
    reviews.py                       # report and AgentRun queries
    suppressions.py                  # suppression commands
backend/migrations/versions/
  0002_multi_agent_reporting.py
frontend/src/features/
  agents/api.ts
  agents/AgentEditorPage.tsx
  reviews/api.ts
  reviews/NewReviewPage.tsx
  reviews/ReviewRunPage.tsx
  reviews/AgentRunsPanel.tsx
  reviews/OverviewPanel.tsx
  findings/filters.ts
  findings/FindingList.tsx
  suppressions/api.ts
backend/tests/
  unit/reviewer_catalog/
  unit/review/
  unit/findings/
  integration/review/
  contract/http/
frontend/src/features/reviews/*.test.tsx
frontend/e2e/multi-agent-review.spec.ts
~~~

Phase 3 deliberately leaves Skill, MCP, command execution, Fix, container execution, and eval release gates to later plans.

### Task 1: Define Seven Immutable Built-In Reviewer And Model Profile Versions

**Files:**
- Modify: <code>backend/src/codelens/reviewer_catalog/domain/models.py</code>
- Create: <code>backend/src/codelens/reviewer_catalog/application/builtin_catalog.py</code>
- Test: <code>backend/tests/unit/reviewer_catalog/test_builtin_catalog.py</code>

**Interfaces:**
- Consumes: Phase 0-2 <code>AgentVersion</code>.
- Produces: <code>AgentDefinition</code>, extended <code>AgentVersion</code>, <code>ModelProfile</code>, <code>BuiltInAgentCatalog.get(reference)</code>, and <code>BuiltInAgentCatalog.defaults()</code>.

- [ ] **Step 1: Write the failing catalog test**

~~~python
from codelens.reviewer_catalog.application.builtin_catalog import BuiltInAgentCatalog


def test_catalog_exposes_seven_default_reviewers_with_unique_versions() -> None:
    catalog = BuiltInAgentCatalog.create()

    definitions = catalog.list_definitions()

    assert [item.agent_id for item in definitions] == [
        "correctness",
        "security",
        "performance",
        "maintainability",
        "testing",
        "docs_style",
        "cross_file",
    ]
    assert all(item.enabled_by_default for item in definitions)
    references = catalog.defaults()
    assert len(references) == 7
    assert len(set(references)) == 7
    assert all(catalog.get(reference).content_hash for reference in references)
    assert catalog.get_model_profile("quality:v1").model_id == "test-model"
~~~

- [ ] **Step 2: Run the test and observe the missing catalog**

Run:

~~~bash
uv run --project backend pytest backend/tests/unit/reviewer_catalog/test_builtin_catalog.py -v
~~~

Expected: FAIL because <code>BuiltInAgentCatalog</code> does not exist.

- [ ] **Step 3: Add immutable definition metadata**

Add to <code>reviewer_catalog/domain/models.py</code>:

~~~python
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    name: str
    description: str
    enabled_by_default: bool
    active_version: int

    @property
    def active_reference(self) -> str:
        return f"{self.agent_id}:v{self.active_version}"


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    version: int
    name: str
    model_id: str
    reasoning_effort: str | None
    max_output_tokens: int
    max_attempts: int
    content_hash: str

    @property
    def reference(self) -> str:
        return f"{self.profile_id}:v{self.version}"
~~~

Extend the existing <code>AgentVersion</code> with <code>output_schema_version: int</code>, <code>failure_policy: Literal["partial", "required"]</code>, and <code>mode_support: tuple[ReviewMode, ...]</code>. Set the built-ins to schema 1, partial failure, and REVIEW/FIX support. Do not add capability bindings yet; Phase 4 adds them without changing the reference format.

- [ ] **Step 4: Implement the built-in catalog**

Create <code>builtin_catalog.py</code> with one explicit tuple of definitions and one version factory. Each prompt must state its narrow review category, require evidence, forbid reporting unchanged unrelated issues, and require the Phase 0-2 <code>FindingBatch</code> output. Use <code>hashlib.sha256</code> over the UTF-8 prompt plus the stable serialized policy fields to compute <code>content_hash</code>.

~~~python
from hashlib import sha256

from codelens.reviewer_catalog.domain.models import (
    AgentDefinition,
    AgentVersion,
    ModelProfile,
)
from codelens.workspace.domain.models import ReviewMode


_DEFINITIONS = (
    AgentDefinition("correctness", "Correctness", "Logic, boundaries, failures, concurrency, and state.", True, 1),
    AgentDefinition("security", "Security", "Authorization, injection, secrets, exposure, and supply chain.", True, 1),
    AgentDefinition("performance", "Performance", "Complexity, blocking I/O, memory, and resource usage.", True, 1),
    AgentDefinition("maintainability", "Maintainability", "Responsibilities, coupling, contracts, and testability.", True, 1),
    AgentDefinition("testing", "Testing", "Regression risk, edge cases, failure paths, and test quality.", True, 1),
    AgentDefinition("docs_style", "Docs & Style", "Public contracts, documentation, naming, and repository rules.", True, 1),
    AgentDefinition("cross_file", "Cross-file", "Call paths, imports, compatibility, and downstream effects.", True, 1),
)


def _quality_profile(model_id: str) -> ModelProfile:
    payload = f"quality|1|{model_id}|medium|8192|2"
    return ModelProfile(
        profile_id="quality",
        version=1,
        name="Quality",
        model_id=model_id,
        reasoning_effort="medium",
        max_output_tokens=8192,
        max_attempts=2,
        content_hash=sha256(payload.encode()).hexdigest(),
    )


def _prompt(definition: AgentDefinition) -> str:
    return (
        f"You are the {definition.name} reviewer. Focus only on: {definition.description} "
        "Report only issues introduced or exposed by target changes. "
        "Every finding must identify an exact changed hunk and verifiable evidence. "
        "Return the supplied FindingBatch schema and do not emit prose outside it."
    )


def _version(definition: AgentDefinition) -> AgentVersion:
    prompt = _prompt(definition)
    digest = sha256(
        f"{definition.agent_id}|1|quality:v1|1|partial|review,fix|"
        f"120|8|12000|0.65|{prompt}".encode()
    ).hexdigest()
    return AgentVersion(
        agent_id=definition.agent_id,
        version=1,
        name=definition.name,
        prompt_template=prompt,
        model_profile_id="quality:v1",
        output_schema_version=1,
        timeout_seconds=120,
        max_turns=8,
        token_budget=12_000,
        confidence_floor=0.65,
        failure_policy="partial",
        mode_support=(ReviewMode.REVIEW, ReviewMode.FIX),
        content_hash=digest,
    )


class BuiltInAgentCatalog:
    def __init__(
        self,
        definitions: tuple[AgentDefinition, ...],
        versions: tuple[AgentVersion, ...],
        model_profiles: tuple[ModelProfile, ...],
    ) -> None:
        self._definitions = definitions
        self._versions = {item.reference: item for item in versions}
        self._model_profiles = {
            item.reference: item for item in model_profiles
        }

    @classmethod
    def create(cls, model_id: str = "test-model") -> "BuiltInAgentCatalog":
        profile = _quality_profile(model_id)
        return cls(
            _DEFINITIONS,
            tuple(_version(item) for item in _DEFINITIONS),
            (profile,),
        )

    def list_definitions(self) -> tuple[AgentDefinition, ...]:
        return self._definitions

    def defaults(self) -> tuple[str, ...]:
        return tuple(
            item.active_reference for item in self._definitions if item.enabled_by_default
        )

    def get(self, reference: str) -> AgentVersion:
        try:
            return self._versions[reference]
        except KeyError as error:
            raise KeyError(f"unknown AgentVersion: {reference}") from error

    def get_model_profile(self, reference: str) -> ModelProfile:
        try:
            return self._model_profiles[reference]
        except KeyError as error:
            raise KeyError(f"unknown ModelProfile: {reference}") from error
~~~

<code>_quality_profile</code> hashes the explicit configured model ID and policy values; production bootstrap requires <code>CODELENS_QUALITY_MODEL</code> or an existing active profile before a live run. It never guesses a current provider model alias.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/reviewer_catalog -v
uv run --project backend ruff check backend/src/codelens/reviewer_catalog backend/tests/unit/reviewer_catalog
uv run --project backend mypy backend/src/codelens/reviewer_catalog
git add backend
git commit -m "feat: add seven built-in reviewer versions"
~~~

Expected: seven stable defaults and no duplicate reference or hash.

---

### Task 2: Extend AgentRun Identity, Usage, And Resume Contracts

**Files:**
- Modify: <code>backend/src/codelens/review/domain/agent_run.py</code>
- Modify: <code>backend/src/codelens/review/domain/models.py</code>
- Test: <code>backend/tests/unit/review/test_agent_run.py</code>

**Interfaces:**
- Consumes: Phase 0–2 <code>AgentRun</code> and immutable AgentVersion references.
- Produces: pass/shard-aware node keys, usage, retry history, and fan-in summaries.

- [ ] **Step 1: Write failing identity and recovery tests**

Assert unique stable run IDs for root pass, shard pass, evidence verification, and synthesis nodes. Assert retries increment execution attempt but preserve the logical node ID and every output Artifact reference. Assert <code>OUTPUT_SAVED</code> resumes at validation and <code>SUCCEEDED</code> cannot reopen.

- [ ] **Step 2: Extend the domain model**

Use a frozen <code>AgentNodeIdentity(task_id, agent_reference, pass_index, shard_id, logical_attempt_group)</code>. Derive the run ID from its canonical serialization. Keep retry attempts as child execution records or append-only history, not as identity collisions. Add aggregate input/output tokens, tool calls, elapsed time, and error classifications.

There is no entity method that directly transitions RUNNING to SUCCEEDED. Persistence owns the validated atomic completion boundary.

- [ ] **Step 3: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/review/test_agent_run.py -v
uv run --project backend mypy backend/src/codelens/review/domain
uv run --project backend ruff check backend
git add backend
git commit -m "feat: extend multi-agent run identity"
~~~

Expected: pass/shard identities, retry history, and resume transitions are stable.

---

### Task 3: Persist Catalogs, Concrete Agent Nodes, Reports, Feedback, And Suppressions

**Files:**
- Modify: <code>backend/src/codelens/review/infrastructure/tables.py</code>
- Modify: <code>backend/src/codelens/review/infrastructure/repositories.py</code>
- Modify: <code>backend/src/codelens/review/domain/ports.py</code>
- Create: <code>backend/src/codelens/governance/domain/feedback.py</code>
- Create: <code>backend/migrations/versions/0002_multi_agent_reporting.py</code>
- Test: <code>backend/tests/integration/review/test_multi_agent_persistence.py</code>

**Interfaces:**
- Consumes: Task 2 AgentRun extensions and Phase 0–2 checkpoint/Artifact stores.
- Produces: immutable catalog stores, concrete node/attempt persistence, report, feedback, and suppression stores.

- [ ] **Step 1: Write failing migration and restart tests**

Persist two passes and two shards for the same task/Reviewer and assert four rows with different run IDs. Save an unvalidated Artifact for one node, reopen the database, and complete it from Artifact without a runtime call. Inject a failure inside the Finding/success/outbox transaction and assert none of the three commits.

Also persist/reload immutable AgentVersion and ModelProfile versions, one report, accepted/ignored/false-positive/rule-suggestion feedback, and a suppression. Editing creates a new version and atomically moves an active pointer without rewriting history.

- [ ] **Step 2: Add explicit schema identities**

The migration adds catalog/version, report, feedback, and suppression tables plus any Phase 3 run columns. <code>agent_runs</code> has a unique constraint over task ID, Agent reference, pass index, shard ID, and logical attempt group; it must not have unique <code>(task_id, agent_reference)</code>. Store attempts append-only with their own key and Artifact references.

Preserve Phase 0–2 rows by migrating them to pass 0, shard <code>root</code>, logical group 0. The downgrade is loss-aware and must refuse if non-root pass/shard data cannot be represented.

- [ ] **Step 3: Implement immutable stores and atomic node completion**

Catalog writes verify content hashes and create N+1 versions. Run creation uses conflict-ignore on the complete node identity. Output checkpoints reuse the Phase 0–2 opaque Artifact store. One transaction validates prior state, inserts deterministic Findings, writes usage, marks success, and appends an outbox event.

Feedback is append-only. <code>ignored_once</code> affects only current presentation; persistent filtering requires an explicit Suppression.

- [ ] **Step 4: Verify migration and restart behavior**

~~~bash
uv run --project backend alembic upgrade head
uv run --project backend pytest backend/tests/integration/review/test_multi_agent_persistence.py -v
uv run --project backend mypy backend/src/codelens/review backend/src/codelens/governance
uv run --project backend ruff check backend
~~~

Expected: multi-pass/shard identity, immutable versions, atomic completion, and restart-from-output tests pass.

- [ ] **Step 5: Commit multi-agent persistence**

~~~bash
git add backend
git commit -m "feat: persist concrete multi-agent nodes"
~~~

---

### Task 4: Execute Selected Reviewers With Bounded Structured Concurrency

**Files:**
- Create: <code>backend/src/codelens/review/application/multi_agent.py</code>
- Modify: <code>backend/src/codelens/review/domain/ports.py</code>
- Test: <code>backend/tests/unit/review/test_multi_agent_executor.py</code>
- Test: <code>backend/tests/integration/review/test_multi_agent_worktree_readonly.py</code>

**Interfaces:**
- Consumes: runtime, catalog, checkpoint/Artifact stores, Finding validator, task worktree verifier, and global/per-task semaphores.
- Produces: <code>MultiAgentExecutor.execute</code> returning successful validated batches plus terminal run states.

- [ ] **Step 1: Write failing concurrency, isolation, retry, and cancellation tests**

Gate four fake calls and configure global concurrency 3 plus per-task concurrency 2. Assert one task never exceeds 2 and two concurrent tasks together never exceed 3. Assert every selected Reviewer starts, sees the same Snapshot/worktree ID inside one task, and two same-repository tasks see different worktree IDs.

Add tests for transient retry, permanent invalid output, timeout, cancellation before acquire, cancellation during runtime, persisted OUTPUT_SAVED resume without runtime, persisted SUCCEEDED skip, and one failed Reviewer not canceling siblings.

Use a real worktree test where a malicious fake Reviewer writes a file. Assert the post-run hash check produces <code>WORKTREE_MUTATED</code>, quarantines the task worktree, and does not affect the user checkout or the other task worktree.

- [ ] **Step 2: Define error and retry classification**

Map rate limits, connect/read timeouts, and retryable provider failures to transient errors. Schema/path/evidence failure after one explicit repair attempt is permanent for that attempt. Cancellation never retries. Backoff is bounded, jittered, cancel-aware, and recorded.

- [ ] **Step 3: Implement task-group fan-out**

Create one child coroutine per selected concrete node. Acquire per-task then global/model semaphores in a single documented order, release them in <code>finally</code>, and never hold repository locks. Each child:

1. verifies task worktree ownership/hash;
2. skips SUCCEEDED or resumes OUTPUT_SAVED;
3. invokes runtime when needed;
4. stores unvalidated output and checkpoint;
5. validates from the stored bytes;
6. atomically completes with Findings/outbox;
7. verifies worktree hash again.

Collect child exceptions into explicit run states; do not let one ordinary Agent failure cancel the task group. User/task cancellation intentionally propagates.

- [ ] **Step 4: Verify bounded concurrency and read-only isolation**

~~~bash
uv run --project backend pytest backend/tests/unit/review/test_multi_agent_executor.py backend/tests/integration/review/test_multi_agent_worktree_readonly.py -v
uv run --project backend mypy backend/src/codelens/review
uv run --project backend ruff check backend
~~~

Expected: concurrency bounds, retries, restart skips, partial failure, cancellation, and worktree mutation tests pass.

- [ ] **Step 5: Commit multi-Agent execution**

~~~bash
git add backend
git commit -m "feat: execute reviewers with bounded concurrency"
~~~

---

### Task 5: Selectively Verify Evidence And Apply Suppressions

**Files:**
- Create: <code>backend/src/codelens/findings/domain/verification.py</code>
- Create: <code>backend/src/codelens/findings/domain/suppression.py</code>
- Create: <code>backend/src/codelens/findings/application/verification.py</code>
- Create: <code>backend/src/codelens/findings/application/suppression.py</code>
- Test: <code>backend/tests/unit/findings/test_verification.py</code>
- Test: <code>backend/tests/unit/findings/test_suppression.py</code>

**Interfaces:**
- Consumes: deterministic Phase 0-2 FindingValidator output.
- Produces: <code>VerificationDecision</code>, <code>EvidenceVerificationPort</code>, and suppression filtering with reasons.

- [ ] **Step 1: Write failing candidate and decision tests**

Test that verification is required for critical/high, blocking, a confidence value in the configured review band, conflicting same-location conclusions, and tool-backed evidence. Test that a verifier may confirm, lower confidence, lower severity, or reject, but cannot raise severity unless <code>severity_increase_confirmed=True</code>.

~~~python
decision = VerificationDecision(
    finding_id="f1",
    outcome=VerificationOutcome.DOWNGRADED,
    confidence=0.72,
    severity="medium",
    reason="The claimed race requires a shared mutable instance that is not present.",
    evidence=(),
    severity_increase_confirmed=False,
)
verified = apply_verification(finding, decision)
assert verified.severity == "medium"
assert verified.confidence == 0.72
~~~

- [ ] **Step 2: Define immutable verification contracts**

~~~python
class VerificationOutcome(str, Enum):
    CONFIRMED = "confirmed"
    DOWNGRADED = "downgraded"
    REJECTED = "rejected"


@dataclass(frozen=True)
class VerificationDecision:
    finding_id: str
    outcome: VerificationOutcome
    confidence: float
    severity: Literal["critical", "high", "medium", "low", "info"]
    reason: str
    evidence: tuple[Evidence, ...]
    severity_increase_confirmed: bool = False


class EvidenceVerificationPort(Protocol):
    async def verify(
        self,
        snapshot: ReviewSnapshot,
        findings: tuple[Finding, ...],
    ) -> tuple[VerificationDecision, ...]:
        raise NotImplementedError
~~~

<code>select_candidates</code> returns stable finding ID order. <code>apply_verification</code> rejects unknown IDs, empty reasons, confidence outside [0, 1], and unconfirmed severity increases. Rejected findings remain auditable in a verification artifact but do not enter the report.

- [ ] **Step 3: Write suppression matching tests**

A suppression may specify an exact fingerprint, or category plus repository-relative path pattern. It never trusts absolute paths. Assert one-time ignored feedback is not a suppression.

~~~python
suppression = Suppression(
    suppression_id="sup_1",
    repository_path_hash="repo_hash",
    reason="Generated compatibility shim",
    fingerprint=None,
    category="maintainability",
    path_pattern="src/generated/**",
)
assert SuppressionMatcher((suppression,)).match(finding) == suppression
~~~

- [ ] **Step 4: Implement deterministic suppression**

Normalize all patterns on creation, reject <code>..</code>, a leading slash, empty selectors, and invalid pathspec patterns. Match exact fingerprint first, then category/path. Return both retained and suppressed tuples so report counts remain accurate. Store only suppression IDs on individual findings; do not mutate the immutable Finding model in place.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/findings/test_verification.py backend/tests/unit/findings/test_suppression.py -v
uv run --project backend mypy backend/src/codelens/findings
git add backend
git commit -m "feat: verify evidence and apply finding suppressions"
~~~

---

### Task 6: Deduplicate, Cluster, And Constrain Synthesis

**Files:**
- Create: <code>backend/src/codelens/findings/application/fingerprint.py</code>
- Create: <code>backend/src/codelens/findings/application/deduplication.py</code>
- Create: <code>backend/src/codelens/review/domain/report.py</code>
- Create: <code>backend/src/codelens/review/application/synthesis.py</code>
- Modify: <code>backend/src/codelens/review/domain/ports.py</code>
- Create: <code>backend/src/codelens/review/infrastructure/openai_nodes.py</code>
- Test: <code>backend/tests/unit/findings/test_deduplication.py</code>
- Test: <code>backend/tests/unit/findings/test_fingerprint.py</code>
- Test: <code>backend/tests/unit/review/test_synthesis.py</code>

**Interfaces:**
- Consumes: retained verified findings and terminal AgentRuns.
- Produces: canonical findings, FindingClusters, validated ReviewReport, and deterministic fallback report.

- [ ] **Step 1: Write exact-dedupe and clustering-boundary tests**

Do not trust the model-supplied fingerprint. After deterministic FindingValidator succeeds, recompute it from normalized category, primary path/range/side, changed hunk ID, and a normalized title signature:

~~~python
def canonical_fingerprint(finding: Finding) -> str:
    location = finding.primary_location
    title_signature = " ".join(
        token
        for token in re.findall(r"[a-z0-9_]+", finding.title.lower())
        if token not in _STOP_WORDS
    )
    payload = "|".join(
        (
            finding.category.lower(),
            location.path,
            str(location.start_line),
            str(location.end_line),
            location.side,
            finding.changed_hunk_id,
            title_signature,
        )
    )
    return sha256(payload.encode()).hexdigest()
~~~

Replace the immutable Finding with <code>finding.model_copy(update={"fingerprint": digest})</code>; keep the original model batch only in its raw RunArtifact. Exact canonical fingerprint duplicates choose the finding with the strongest verified severity, then confidence, then lexicographically smallest ID. The cluster runtime receives only IDs and compact normalized fields. Reject clusters containing an unknown ID, the same ID twice, or an ID in multiple clusters.

~~~python
canonical = ExactDeduplicator().deduplicate((low_confidence, high_confidence))
assert canonical == (high_confidence,)

with pytest.raises(ValueError, match="unknown finding"):
    validate_clusters((FindingCluster("c1", ("invented",), "x"),), canonical)
~~~

- [ ] **Step 2: Define report contracts**

~~~python
class FindingCluster(BaseModel):
    model_config = ConfigDict(frozen=True)
    cluster_id: str
    finding_ids: tuple[str, ...] = Field(min_length=1)
    rationale: str


class AgentRunSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    agent_reference: str
    status: str
    finding_count: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    elapsed_seconds: float
    error_code: str | None = None


class CoverageSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    target_files: int
    reviewed_files: int
    uncovered_paths: tuple[str, ...]
    notes: tuple[str, ...]


class SynthesisAdjustment(BaseModel):
    model_config = ConfigDict(frozen=True)
    finding_id: str
    presented_severity: Literal["critical", "high", "medium", "low", "info"]
    reason: str


class ReviewReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id: str
    status: Literal["completed", "partial"]
    headline: str
    summary: str
    ordered_finding_ids: tuple[str, ...]
    clusters: tuple[FindingCluster, ...]
    adjustments: tuple[SynthesisAdjustment, ...]
    synthesis_notes: tuple[str, ...]
    agent_runs: tuple[AgentRunSummary, ...]
    coverage: CoverageSummary
    suppressed_count: int
    rejected_count: int
    fallback_used: bool
~~~

- [ ] **Step 3: Define constrained system-node ports**

~~~python
class FindingClusterPort(Protocol):
    async def cluster(self, findings: tuple[Finding, ...]) -> tuple[FindingCluster, ...]:
        raise NotImplementedError


class ReviewSynthesisPort(Protocol):
    async def synthesize(
        self,
        task_id: str,
        findings: tuple[Finding, ...],
        clusters: tuple[FindingCluster, ...],
        runs: tuple[AgentRun, ...],
        coverage: CoverageSummary,
    ) -> SynthesisDraft:
        raise NotImplementedError
~~~

<code>SynthesisDraft</code> contains headline, summary, ordered finding IDs, optional per-ID severity overrides that may only lower severity, and synthesis notes. The validator rejects unknown IDs, repeated IDs, omitted canonical IDs, empty adjustment reasons, and severity increases. Accepted overrides become <code>SynthesisAdjustment</code> records; canonical Finding rows remain unchanged.

- [ ] **Step 4: Implement deterministic fallback**

The fallback orders severity critical through info, then confidence descending, path, line, ID. It emits a fixed headline, compact counts by severity, all validated clusters, terminal AgentRun summaries, and <code>fallback_used=True</code>. It must run when the synthesis SDK call fails or returns an invalid draft.

~~~python
def fallback_report(inputs: SynthesisInputs) -> ReviewReport:
    ordered = tuple(
        item.id
        for item in sorted(inputs.findings, key=_stable_finding_order)
    )
    return ReviewReport(
        task_id=inputs.task_id,
        status=inputs.status,
        headline=f"{len(ordered)} validated findings",
        summary=_count_summary(inputs.findings),
        ordered_finding_ids=ordered,
        clusters=inputs.clusters,
        adjustments=(),
        synthesis_notes=("Model synthesis unavailable; deterministic report generated.",),
        agent_runs=inputs.agent_summaries,
        coverage=inputs.coverage,
        suppressed_count=inputs.suppressed_count,
        rejected_count=inputs.rejected_count,
        fallback_used=True,
    )
~~~

- [ ] **Step 5: Implement SDK adapters and verify**

The adapter creates separate Agent instances for Evidence Verifier, Finding Clusterer, and Review Synthesizer, each with a Pydantic output type. Inputs contain compact validated fields, no repository secrets, and no ability to call review agents. Wrap each system node in a custom trace span identified by task ID and node type; configure sensitive trace data off.

~~~bash
uv run --project backend pytest backend/tests/unit/findings/test_fingerprint.py backend/tests/unit/findings/test_deduplication.py backend/tests/unit/review/test_synthesis.py -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
git add backend
git commit -m "feat: constrain finding synthesis and fallback reporting"
~~~

---

### Task 7: Replace The Single-Agent Flow With A Durable Fan-In Pipeline

**Files:**
- Create: <code>backend/src/codelens/review/application/pipeline.py</code>
- Modify: <code>backend/src/codelens/worker/scheduler.py</code>
- Test: <code>backend/tests/integration/review/test_multi_agent_pipeline.py</code>

**Interfaces:**
- Consumes: Task 4 executor, verification, suppression, deduplication, constrained synthesis, report store, and Phase 0–2 singleton scheduler.
- Produces: restart-safe multi-Agent report generation and task completion.

- [ ] **Step 1: Write failing crash-matrix and completion tests**

Cover all-success COMPLETED, mixed PARTIAL, all-failed FAILED, synthesizer deterministic fallback, and cancellation. Inject a restart after fan-out, verification, suppression, dedupe, and synthesis checkpoints. Assert already successful nodes are not rerun and every later stage rebuilds only from persisted validated inputs.

Run two same-repository tasks through the whole pipeline concurrently and assert separate worktree/snapshot/report IDs with no repository-wide execution lock.

- [ ] **Step 2: Implement explicit persisted stage checkpoints**

Use stable stage keys for fan-out, validation fan-in, evidence verification, suppression, dedupe, synthesis, report persistence, and cleanup eligibility. Each stage validates its persisted inputs by content hash before reuse. A task resumes from the first incomplete valid stage; corrupt/missing artifacts fail closed or rerun only the owning stage.

Compute terminal status from selected Reviewer run states. System-node failure never invents Reviewer success. If synthesis fails, generate a deterministic report from validated, unsuppressed clusters and record the degradation.

- [ ] **Step 3: Preserve output before worktree cleanup**

Do not mark the worktree cleanup-eligible until the Snapshot Artifact, all unvalidated-output references, validated Findings, verification decisions, and final report are durable. Cleanup failure does not rewrite report status; it creates an operational warning and scoped retry.

- [ ] **Step 4: Verify restart and concurrency**

~~~bash
uv run --project backend pytest backend/tests/integration/review/test_multi_agent_pipeline.py -v
uv run --project backend mypy backend/src/codelens/review backend/src/codelens/worker
uv run --project backend ruff check backend
~~~

Expected: completion policies, restart stages, deterministic fallback, same-repository task concurrency, and cleanup eligibility pass.

- [ ] **Step 5: Commit the fan-in pipeline**

~~~bash
git add backend
git commit -m "feat: orchestrate durable multi-agent reports"
~~~

---

### Task 8: Expose Catalog, Agent Runs, Reports, And Suppression APIs

**Files:**
- Create: <code>backend/src/codelens/interface/http/agents.py</code>
- Modify: <code>backend/src/codelens/interface/http/reviews.py</code>
- Create: <code>backend/src/codelens/interface/http/suppressions.py</code>
- Modify: <code>backend/src/codelens/interface/http/app.py</code>
- Test: <code>backend/tests/contract/http/test_multi_agent_api.py</code>

**Interfaces:**
- Produces:
  - <code>GET/POST/PUT /api/agents</code>
  - <code>GET/POST/PUT /api/model-profiles</code>
  - <code>POST /api/agents/{id}/sample-runs</code>
  - <code>GET /api/repositories/recent</code>
  - <code>GET /api/reviews/{id}/agent-runs</code>
  - <code>GET /api/reviews/{id}/report</code>
  - <code>POST /api/findings/{id}/feedback</code>
  - <code>POST /api/suppressions</code>
  - <code>DELETE /api/suppressions/{id}</code>

- [ ] **Step 1: Write contract tests**

Assert <code>GET /api/agents</code> returns seven enabled defaults and exact immutable references. POST/PUT creates immutable versions and never changes a running task. Creating a review with three IDs freezes their active references and ModelProfile references. Unknown or disabled IDs return 422 with <code>unknown_agent</code>. AgentRun responses expose usage and stable error codes, never raw provider messages. Reports return 404 until fan-in persists one. Sample runs use an administrator-selected fixture repository and the same validation path; live provider execution remains explicit. Recent repositories returns only previously contained repositories in last-seen order and revalidates containment before selection.

Suppression creation body:

~~~json
{
  "repository_path": "/srv/repos/billing",
  "fingerprint": "sha256-value",
  "category": null,
  "path_pattern": null,
  "reason": "Accepted generated compatibility behavior"
}
~~~

Exactly one of fingerprint or category/path selector is required.

Feedback body uses a discriminated kind and optional bounded reason. The server verifies the finding belongs to the task/repository context. A feedback command never changes Prompt, AgentVersion, REVIEW.md, or Suppression automatically.

- [ ] **Step 2: Implement typed DTOs**

Use Pydantic DTOs rather than domain serialization. Hash the normalized repository realpath server-side after root containment validation. The response never returns the repository path hash as an authorization token.

- [ ] **Step 3: Extend SSE events**

Document and test these stable event types:

~~~text
review.prepared
agent.started
agent.retrying
agent.succeeded
agent.failed
agent.timed_out
agent.canceled
review.validating
review.verifying
review.deduplicating
review.synthesizing
review.completed
review.partial
review.failed
~~~

Reconnect with Last-Event-ID returns each event once in ascending event ID order.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/contract/http/test_multi_agent_api.py -v
uv run --project backend pytest backend/tests/contract -v
git add backend
git commit -m "feat: expose multi-agent review reporting APIs"
~~~

---

### Task 9: Build Reviewer Selection And Agent Run Reporting UI

**Files:**
- Modify: <code>frontend/src/features/reviews/api.ts</code>
- Modify: <code>frontend/src/features/reviews/NewReviewPage.tsx</code>
- Modify: <code>frontend/src/features/reviews/ReviewRunPage.tsx</code>
- Create: <code>frontend/src/features/reviews/AgentRunsPanel.tsx</code>
- Create: <code>frontend/src/features/reviews/OverviewPanel.tsx</code>
- Modify: <code>frontend/src/features/findings/FindingList.tsx</code>
- Create: <code>frontend/src/features/agents/api.ts</code>
- Create: <code>frontend/src/features/agents/AgentEditorPage.tsx</code>
- Test: <code>frontend/src/features/reviews/NewReviewPage.test.tsx</code>
- Test: <code>frontend/src/features/reviews/ReviewRunPage.test.tsx</code>
- Test: <code>frontend/src/features/agents/AgentEditorPage.test.tsx</code>

**Interfaces:**
- Consumes: Task 8 APIs and SSE events.
- Produces: accessible selection, progress, usage, coverage, partial/failure, and synthesis-fallback states.

- [ ] **Step 1: Write failing UI tests**

Test:

- seven reviewers load selected by default;
- “Select all” and individual toggles work without losing server-disabled status;
- submission sends agent IDs, not mutable prompt text or version objects;
- Agent Runs shows state, attempts, duration, tokens, tool calls, and stable error;
- Overview shows coverage, suppression/rejection counts, and fallback banner;
- PARTIAL retains visible findings and names failed reviewers;
- Agent editor shows immutable version history/diff, ModelProfile, budget, timeout, confidence floor, output schema, sample run, and active-pointer rollback;
- finding detail records accept, ignore once, false positive, rule suggestion, or explicit suppression as distinct actions;
- long paths and titles wrap at desktop and mobile widths.

- [ ] **Step 2: Implement strict API types**

~~~typescript
export type AgentDefinitionDto = {
  agentId: string;
  name: string;
  description: string;
  enabledByDefault: boolean;
  activeReference: string;
  health: "ready" | "degraded" | "unavailable";
};

export type AgentRunDto = {
  runId: string;
  agentReference: string;
  status: "pending" | "running" | "succeeded" | "failed" | "timed_out" | "canceled" | "skipped";
  attempt: number;
  findingCount: number;
  inputTokens: number | null;
  outputTokens: number | null;
  toolCalls: number | null;
  elapsedSeconds: number | null;
  errorCode: string | null;
};
~~~

Validate unknown enum values at the API boundary and surface an “Unsupported server response” error; do not cast with <code>as</code>.

- [ ] **Step 3: Implement operational states**

The run page keeps stale successful data while reconnecting SSE, displays separate “live connection lost” and task failure banners, and polls once after every terminal event. Findings filters add reviewer, disposition, origin, and verification outcome without changing backend data.

- [ ] **Step 4: Verify desktop/mobile behavior and commit**

~~~bash
pnpm --dir frontend test
pnpm --dir frontend build
git add frontend
git commit -m "feat: show multi-agent selection and run coverage"
~~~

---

### Task 10: Add Phase 3 Acceptance And Failure-Recovery Gates

**Files:**
- Create: <code>backend/tests/acceptance/test_phase_3.py</code>
- Create: <code>frontend/e2e/multi-agent-review.spec.ts</code>
- Modify: <code>README.md</code>

**Interfaces:**
- Consumes: complete Phase 3 backend and frontend.
- Produces: repeatable no-network acceptance gate; live OpenAI remains opt-in.

- [ ] **Step 1: Add a deterministic seven-reviewer fixture**

Create a temporary real Git repository containing one correctness issue and one security issue. Inject fake runtimes where five reviewers succeed, one times out, and one returns invalid output. Assert:

- all seven frozen reviewers were scheduled;
- maximum active reviewers is 4;
- task is PARTIAL;
- invalid and timed-out outputs do not remove successful findings;
- verifier and synthesizer receive only validated IDs;
- report contains no invented ID;
- a singleton Worker restart does not repeat successful AgentRuns;
- all Reviewer inputs reference the same task worktree, while another same-repository task uses a different worktree;
- a write attempt is detected and cannot affect sibling tasks or the user checkout.

- [ ] **Step 2: Add Playwright coverage**

Run API and Worker with deterministic fake adapters. Create a review with three selected reviewers, watch live transitions, disconnect/reconnect the event stream, and assert the final partial report, coverage, Agent Runs, findings, and fallback state. Repeat at 390x844 and 1440x900.

- [ ] **Step 3: Run the full phase gate**

~~~bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test frontend/e2e/multi-agent-review.spec.ts
~~~

Expected: all deterministic checks pass without network access or a real OpenAI key.

- [ ] **Step 4: Commit the phase gate**

~~~bash
git add README.md backend frontend
git commit -m "test: add phase three multi-agent acceptance gate"
git status --short --branch
~~~

Expected: clean branch.

## Phase 3 Acceptance Checklist

- [ ] All seven built-in reviewers have immutable references and default selection.
- [ ] Only user-selected reviewers run, and every selected reviewer gets the same read-only task worktree/Snapshot.
- [ ] Same-repository ReviewTasks use different worktrees and can overlap with no cross-task input contamination.
- [ ] Reviewer concurrency obeys both global and per-task limits without holding repository locks.
- [ ] Timeout, transient retry, permanent failure, cancellation, and Worker restart are tested.
- [ ] OUTPUT_SAVED resumes from Artifact; completed AgentRuns and Findings are idempotent across restart.
- [ ] Deterministic validation isolates invalid output to its AgentRun.
- [ ] Selective evidence verification saves every decision and reason.
- [ ] Suppression and rejected findings remain countable but do not enter synthesis.
- [ ] Exact dedupe and semantic clustering accept only existing finding IDs.
- [ ] Synthesizer cannot invent findings or increase severity without verifier confirmation.
- [ ] All-failed skips synthesis; partial success retains valid findings.
- [ ] Deterministic fallback produces a complete report when synthesis fails.
- [ ] REST, SSE, UI, desktop, and mobile partial/failure states pass.
- [ ] Agent/ModelProfile edits create immutable versions; running tasks retain frozen references.
- [ ] User feedback is append-only and never changes rules, prompts, or suppressions implicitly.

## Deferred To Later Plans

- Phase 4 owns Skill/MCP bindings, repository trust, command profiles, and advanced CodeContextProvider adapters.
- Phase 5 reuses the owned-worktree infrastructure for Fix and owns Snapshot-based PatchSet application.
- Phase 6 owns container sandbox hardening, SecretStore/Artifact provider upgrades, retention, and packaging.
- Phase 7 owns golden datasets, quality metrics, dashboards, and release gates.
