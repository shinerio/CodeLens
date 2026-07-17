# CodeLens Phase 7 Evaluation And Release Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add immutable golden datasets, reproducible local eval runs, prompt/model/policy comparisons, quality/cost/latency dashboards, and fail-closed release thresholds with auditable rollback.

**Architecture:** Eval fixtures are versioned real-repository bundles. A deterministic lane exercises production workflow correctness with fakes/replay; a live behavior lane runs the actual candidate/baseline Agent and model configuration on controlled golden cases. Both use deterministic scoring, while LLM-as-a-Judge remains auxiliary. ReleasePolicy requires the applicable lanes and blocks on threshold, safety, provenance, or completeness failure.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2, Alembic, SQLite WAL, Git CLI fixtures, OpenAI Agents SDK through existing ports, optional OpenAI trace/eval integration, React, TypeScript, TanStack Query, Vitest, Playwright.

## Global Constraints

- Phase 0-6 acceptance gates pass before this plan starts.
- Ordinary unit/integration suites run without live OpenAI, remote MCP, or general network using fakes/replay.
- A release that changes Prompt, model, context selection, tool/Skill/MCP bindings, output schema, or validation policy
  requires a credential-gated controlled live candidate-vs-baseline eval. Missing credentials/results blocks activation;
  it does not make ordinary developer tests network-dependent.
- A fixture repository is a real temporary Git repository, not mocked Git output.
- Eval input, expected findings, runner configuration, AgentVersion, ModelProfile, SkillVersion, MCP bindings, rules, validator policy, and cost profile are immutable references.
- Predictions pass the production schema/evidence validator before scoring.
- Executable oracles and human golden findings are primary.
- LLM-as-a-Judge is auxiliary, cannot override deterministic safety failures, and records model/prompt version.
- A candidate and baseline are comparable only on the same dataset version and metric schema.
- Missing usage, cost, latency, coverage, or required grader data fails the corresponding release rule closed.
- No pricing value is hardcoded into domain code; cost uses versioned administrator-owned ModelCostProfile values.
- Release activation moves immutable active-version pointers transactionally; it never rewrites historical versions.
- Rollback restores a previously approved pointer set; it does not delete failed candidate artifacts.

## Official OpenAI References

- Agent workflow evaluation: https://developers.openai.com/api/docs/guides/agent-evals
- Trace grading: https://developers.openai.com/api/docs/guides/trace-grading
- SDK tracing source: https://developers.openai.com/api/docs/guides/agents/integrations-observability

OpenAI-hosted trace grading is an optional integration. The local CodeLens golden suite remains the release authority because it can validate Git state, evidence hashes, location, suppression, PatchSet, and safety behavior end to end.

## 2026-07-17 Correctness Amendment

- record/replay proves orchestration, validation, persistence, safety, and deterministic metric behavior; it cannot
  prove that a changed Prompt/model finds the expected defects. It is never accepted as the live quality lane.
- Every EvalRun records <code>execution_lane = deterministic | live</code>, provider response IDs, actual immutable
  Agent/Model/context/capability references, dataset hash, CodeLens revision, environment, repeats, and completeness.
- Release input classification is deterministic from changed immutable references. A behavior-bearing change requires
  a comparable live baseline and candidate on the same dataset/metric schema. No result means BLOCKED.
- Live output still passes production schema/evidence validation and deterministic golden matching. LLM judge cannot
  turn a deterministic safety/critical-recall failure into pass.

---

## File And Module Map

~~~text
backend/src/codelens/
  evaluation/
    domain/dataset.py                 # dataset, case, golden finding
    domain/run.py                     # EvalRun and immutable configuration
    domain/metrics.py                 # metric values and comparison
    domain/release.py                 # release policy and decision
    domain/ports.py                   # runner, judge, cost, activation ports
    application/loader.py             # bundle validation/materialization
    application/runner.py             # production-pipeline execution
    application/matching.py           # deterministic prediction/golden match
    application/metrics.py            # all defined review/fix metrics
    application/comparison.py         # candidate vs baseline
    application/release.py            # fail-closed gate and activation
    application/rollback.py           # approved pointer rollback
    infrastructure/repositories.py    # dataset/run/metric/decision persistence
    infrastructure/openai_judge.py    # optional auxiliary judge
    infrastructure/trace_export.py    # optional trace linkage/import
  governance/
    domain/rule_proposal.py           # feedback-derived proposal state
    application/rule_proposals.py     # bounded aggregation, never auto-apply
  interface/http/evaluations.py
  interface/http/rule_proposals.py
  bootstrap/eval_cli.py
backend/migrations/versions/
  0006_evaluation_release.py
backend/tests/evals/fixtures/
  manifest.json
  correctness/
  security/
  concurrency/
  performance/
  testing/
  cross_file/
  clean_refactors/
  malicious_inputs/
  multi_language/
frontend/src/features/evaluations/
  api.ts
  EvaluationsPage.tsx
  EvalRunPage.tsx
  ComparisonPage.tsx
  ReleaseDecisionPanel.tsx
frontend/src/features/governance/
  RuleProposalsPage.tsx
backend/tests/
  unit/evaluation/
  integration/evaluation/
  acceptance/test_phase_7.py
frontend/e2e/evaluations.spec.ts
~~~

### Task 1: Define Immutable Dataset, Case, Golden Finding, And Oracle Contracts

**Files:**
- Create: <code>backend/src/codelens/evaluation/domain/dataset.py</code>
- Create: <code>backend/src/codelens/evaluation/application/loader.py</code>
- Test: <code>backend/tests/unit/evaluation/test_dataset.py</code>
- Test: <code>backend/tests/integration/evaluation/test_fixture_loader.py</code>

**Interfaces:**
- Produces: <code>EvalDatasetVersion</code>, <code>EvalCase</code>, <code>GoldenFinding</code>, and materialized real Git fixture.

- [ ] **Step 1: Write failing immutability and safety tests**

Create a bundle, load it, mutate one repository byte, rule, golden field, or manifest field, and assert content hash changes or verification rejects the bundle. Reject absolute/traversing paths, symlink escape, missing commit, duplicate case/golden IDs, invalid ReviewScope, unknown AgentVersion references, and a golden finding whose location does not exist in the fixture revision.

- [ ] **Step 2: Define Pydantic contracts**

~~~python
class GoldenLocation(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    side: Literal["old", "new"]
    line_tolerance: int = Field(default=0, ge=0, le=20)


class GoldenFinding(BaseModel):
    model_config = ConfigDict(frozen=True)
    golden_id: str
    category: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    disposition: Literal["blocking", "non_blocking", "pre_existing"]
    location: GoldenLocation
    accepted_fingerprints: tuple[str, ...]
    accepted_semantic_keys: tuple[str, ...]
    required_evidence_kinds: tuple[str, ...]
    actionable: bool
    must_not_report: bool = False


class ExecutableOracle(BaseModel):
    model_config = ConfigDict(frozen=True)
    command_profile_id: str
    command_id: str
    expected_exit_codes: tuple[int, ...]
    expected_output_digests: tuple[str, ...] = ()


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)
    case_id: str
    repository_bundle_path: str
    scope: dict[str, object]
    rule_paths: tuple[str, ...]
    agent_references: tuple[str, ...]
    golden_findings: tuple[GoldenFinding, ...]
    executable_oracles: tuple[ExecutableOracle, ...]
    tags: tuple[str, ...]


class EvalDatasetVersion(BaseModel):
    model_config = ConfigDict(frozen=True)
    dataset_id: str
    version: int
    metric_schema_version: int
    cases: tuple[EvalCase, ...]
    content_hash: str
~~~

- [ ] **Step 3: Implement canonical hashing and real Git materialization**

Hash canonical manifest JSON plus every bundle path and byte in sorted order. Materialize into a temporary contained directory, initialize or unpack the recorded real Git repository, verify HEAD/refs, then run production RepositoryInspector and SnapshotService. Never execute fixture code during loading.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/evaluation/test_dataset.py backend/tests/integration/evaluation/test_fixture_loader.py -v
git add backend
git commit -m "feat: define immutable golden evaluation datasets"
~~~

---

### Task 2: Build The Minimum Representative Golden Suite

**Files:**
- Create: files under <code>backend/tests/evals/fixtures/</code>
- Create: <code>backend/tests/evals/fixtures/manifest.json</code>
- Create: <code>backend/tests/integration/evaluation/test_golden_suite_integrity.py</code>

**Interfaces:**
- Consumes: Task 1 loader.
- Produces: minimum release dataset covering product quality and safety boundaries.

- [ ] **Step 1: Add fixture categories**

The first version includes at least:

- correctness: state transition, exception swallowing, off-by-one;
- security: path traversal, command injection, secret exposure;
- concurrency: duplicate external effect, lease race, cancellation leak;
- performance: blocking I/O in async path, N+1, unbounded memory;
- testing: missing regression path and false-positive test smell;
- cross-file: incompatible signature/import/config contract;
- clean refactors: no report expected;
- pre-existing unrelated issue: must not report;
- malicious inputs: README/comment/Skill/MCP prompt injection cannot widen capability;
- multi-language: Python plus TypeScript at minimum;
- Fix cases: successful safe patch, gate failure, secret introduction, source conflict.

Every category includes at least one positive and one negative case.

- [ ] **Step 2: Add executable oracles where possible**

For deterministic bugs, include a command profile and exact failing/passing behavior. Use temporary repository commands through SandboxProvider; do not invoke a shell. Golden-only cases document why an executable oracle is not practical and include human annotation provenance.

- [ ] **Step 3: Verify suite integrity**

The integrity test loads every case, creates its production snapshot, validates every golden location/evidence expectation, uses the container adapter only for cases tagged <code>requires_container</code> and the deterministic fake adapter for all other cases, and asserts at least one clean negative per reviewer category.

- [ ] **Step 4: Commit**

~~~bash
uv run --project backend pytest backend/tests/integration/evaluation/test_golden_suite_integrity.py -v
git add backend/tests/evals backend/tests/integration/evaluation
git commit -m "test: add the minimum CodeLens golden suite"
~~~

---

### Task 3: Implement Deterministic Prediction Matching

**Files:**
- Create: <code>backend/src/codelens/evaluation/application/matching.py</code>
- Test: <code>backend/tests/unit/evaluation/test_matching.py</code>

**Interfaces:**
- Consumes: validated predicted Findings and GoldenFindings.
- Produces: one-to-one MatchResult with unmatched predictions/goldens and location/evidence labels.

- [ ] **Step 1: Write ambiguous matching tests**

Cover exact fingerprint, accepted alias, same category/location with semantic key, line tolerance, overlapping candidate edges, duplicate predictions, must-not-report golden, wrong category, wrong file, invalid evidence, and deterministic result independent of input order.

- [ ] **Step 2: Define matching output**

~~~python
@dataclass(frozen=True)
class FindingMatch:
    predicted_id: str
    golden_id: str
    match_kind: Literal["fingerprint", "alias", "semantic"]
    location_accurate: bool
    evidence_valid: bool
    actionable: bool


@dataclass(frozen=True)
class MatchResult:
    matches: tuple[FindingMatch, ...]
    unmatched_predicted_ids: tuple[str, ...]
    unmatched_golden_ids: tuple[str, ...]
    prohibited_predicted_ids: tuple[str, ...]
~~~

- [ ] **Step 3: Implement deterministic candidate edges**

An edge exists when:

1. predicted fingerprint is an accepted fingerprint; or
2. category and normalized path match, line ranges overlap within tolerance, and <code>normalize_semantic_key(predicted.title)</code> appears in the golden finding's administrator/human-maintained <code>accepted_semantic_keys</code>; or
3. optional JudgePort confirms semantic equivalence for an otherwise location/category-compatible pair.

Deterministic edges have priority over judge edges. Use a deterministic maximum bipartite matching implementation in standard Python, sorting by edge priority, golden ID, and predicted ID. One prediction and one golden may appear in at most one match. A prediction matching <code>must_not_report=True</code> is prohibited rather than a true positive.

- [ ] **Step 4: Keep LLM judge auxiliary**

Judge results include model ID, prompt version, confidence, rationale digest, and trace/artifact reference. Release scoring reports deterministic-only metrics and augmented metrics separately. Judge cannot turn a prohibited/safety result into a pass.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/evaluation/test_matching.py -v
uv run --project backend ruff check backend/src/codelens/evaluation/application/matching.py
uv run --project backend mypy backend/src/codelens/evaluation
git add backend
git commit -m "feat: match review predictions to golden findings"
~~~

---

### Task 4: Compute Complete Quality, Cost, Latency, Coverage, And Fix Metrics

**Files:**
- Create: <code>backend/src/codelens/evaluation/domain/metrics.py</code>
- Create: <code>backend/src/codelens/evaluation/application/metrics.py</code>
- Create: <code>backend/src/codelens/governance/domain/rule_proposal.py</code>
- Create: <code>backend/src/codelens/governance/application/rule_proposals.py</code>
- Test: <code>backend/tests/unit/evaluation/test_metrics.py</code>
- Test: <code>backend/tests/unit/governance/test_rule_proposals.py</code>

**Interfaces:**
- Consumes: MatchResults, Finding/verification state, AgentUsage, coverage, feedback, Fix results, ModelCostProfile.
- Produces: immutable metric set with numerators, denominators, values, and completeness.

- [ ] **Step 1: Define exact formulas in tests**

~~~text
precision = TP / (TP + FP)
recall = TP / (TP + FN)
false_positive_rate = FP / (TP + FP)
precision_at_k = TP among stable top-k / min(k, surfaced count)
location_accuracy = accurate matched locations / matched findings
evidence_validity = evidence-valid matched findings / matched findings
actionable_rate = actionable surfaced findings / surfaced findings
user_acceptance_rate = accepted feedback / decided feedback
signal_to_noise = TP / max(FP, 1)
coverage_rate = reviewed target units / target units
fix_apply_success = applied fixes / attempted fixes
fix_gate_success = fixes with all required gates pass / attempted fixes
~~~

Empty denominators return an explicit unavailable metric, not zero or one. Aggregate metrics expose micro and macro values. p50/p95 use a documented nearest-rank percentile. Cost per effective finding is total configured cost divided by TP and is unavailable when pricing or TP is missing.

- [ ] **Step 2: Define metric types**

~~~python
class MetricValue(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    numerator: float | None
    denominator: float | None
    value: float | None
    unit: Literal["ratio", "count", "seconds", "tokens", "currency"]
    complete: bool
    missing_reason: str | None = None


class ModelCostProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile_id: str
    version: int
    model_id: str
    input_cost_per_million: Decimal
    output_cost_per_million: Decimal
    currency: str
    effective_at: datetime
    content_hash: str
~~~

Cost profiles are configuration snapshots, not claims about current provider pricing.

- [ ] **Step 3: Add sliced metrics**

Compute by reviewer, category, severity, language, repository size, case tag, and clean/positive case. Store aggregate plus slices so a high overall score cannot hide a security or clean-refactor regression.

- [ ] **Step 4: Aggregate repeated feedback into reviewable proposals**

Feedback never edits a rule or Prompt directly. Aggregate only repeated, explicit <code>false_positive</code> and <code>rule_suggestion</code> feedback across distinct ReviewTasks. Ignore one-time feedback and duplicate submissions. Require at least three distinct tasks before creating a draft.

~~~python
class RuleProposalTarget(str, Enum):
    ROOT_REVIEW = "root_review"
    DIRECTORY_REVIEW = "directory_review"
    FILE_REVIEW = "file_review"
    AGENT_VERSION = "agent_version"


class RuleProposalStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class RuleProposal(BaseModel):
    model_config = ConfigDict(frozen=True)
    proposal_id: str
    repository_path_hash: str
    target: RuleProposalTarget
    target_path: str | None
    agent_id: str | None
    source_feedback_ids: tuple[str, ...] = Field(min_length=3)
    summary: str
    proposed_content: str
    content_hash: str
    status: RuleProposalStatus
~~~

Target selection uses the narrowest scope shared by the contributing findings: exact file, then nearest common directory, then repository root; agent-behavior proposals target an inactive AgentVersion. The user must confirm or reject every proposal. Confirmation of a REVIEW.md proposal produces a downloadable proposed-rule Artifact and never writes the repository. Confirmation of an AgentVersion proposal creates an inactive immutable candidate that must pass the Phase 7 eval/release gate before activation.

<code>RuleProposalService.refresh(repository_path_hash)</code> is idempotent by sorted source-feedback IDs and content hash. The Phase 3 feedback command enqueues a lightweight refresh job after committing feedback; a refresh failure never changes feedback or ReviewTask state.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/evaluation/test_metrics.py backend/tests/unit/governance/test_rule_proposals.py -v
git add backend
git commit -m "feat: compute review and fix quality metrics"
~~~

---

### Task 5: Run Deterministic And Live Eval Lanes Through Production Pipelines

**Files:**
- Create: <code>backend/src/codelens/evaluation/application/runner.py</code>
- Create: <code>backend/src/codelens/evaluation/application/change_classifier.py</code>
- Create: <code>backend/src/codelens/evaluation/infrastructure/openai_live.py</code>
- Test: <code>backend/tests/integration/evaluation/test_eval_runner.py</code>
- Test: <code>backend/tests/integration/evaluation/test_live_lane_contract.py</code>

**Interfaces:**
- Consumes: immutable dataset/configuration and production review/fix composition.
- Produces: resumable lane-labeled EvalRun with validated predictions, usage, provenance, and completeness.

- [ ] **Step 1: Write failing deterministic-lane tests**

Inject fake/replay reviewer, verifier, synthesis, command, MCP, and Fix adapters. Assert every case creates its own task-owned worktree, follows production validation/persistence, stores predictions before scoring, resumes without duplicate cases, and records failures/coverage rather than dropping them.

- [ ] **Step 2: Write failing live-lane contract tests**

Use an injected live provider stub that returns unique response IDs and behavior dependent on Agent/Model references. Assert the live lane rejects fake/replay adapters, missing response IDs, floating/unrecorded model configuration, mixed dataset hashes, missing repeats, and candidate/baseline reference mismatch.

A behavior-change classifier marks Prompt, model, context planner, capability binding, Skill/MCP/tool version, output schema, and validator-policy changes as <code>LIVE_REQUIRED</code>. Pure UI/docs or deterministic infrastructure changes may be <code>DETERMINISTIC_ONLY</code>, with the classification stored in the release decision.

- [ ] **Step 3: Implement isolated repeat execution**

Run cases under an eval semaphore separate from interactive review concurrency. Every repeat uses a fresh materialized real repository, task worktree, database aggregate IDs, and cache namespace. Record CodeLens revision, Python/platform, sandbox provider, exact model/Agent/context/capability references, provider response IDs, timestamps, token/latency/cost data, and random seed where supported.

- [ ] **Step 4: Implement explicit live provider gate**

The live command requires credentials and an exact released model identifier/configuration. It runs both approved baseline and candidate in the same controlled job on the same dataset and repeat policy. A missing key/provider/model/dataset result yields a machine-readable incomplete run and nonzero gate input; it never silently substitutes replay output.

Provider output goes through the same unvalidated-output Artifact checkpoint, deterministic validation, report pipeline, and golden matching as interactive review.

- [ ] **Step 5: Keep Judge and trace linkage auxiliary**

Optional judge/trace integration records versions and response/trace IDs but cannot create predictions, override oracle results, or satisfy the live Reviewer lane.

- [ ] **Step 6: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/integration/evaluation/test_eval_runner.py backend/tests/integration/evaluation/test_live_lane_contract.py -v
uv run --project backend mypy backend/src/codelens/evaluation
uv run --project backend ruff check backend
git add backend
git commit -m "feat: run deterministic and live evaluation lanes"
~~~

Expected: deterministic isolation/resume and live provenance/completeness contracts pass without requiring a network key.

---

### Task 6: Persist Datasets, Runs, Case Results, Metrics, And Comparisons

**Files:**
- Create: <code>backend/src/codelens/evaluation/infrastructure/repositories.py</code>
- Modify: <code>backend/src/codelens/review/infrastructure/tables.py</code>
- Create: <code>backend/migrations/versions/0006_evaluation_release.py</code>
- Test: <code>backend/tests/integration/evaluation/test_persistence.py</code>

**Interfaces:**
- Produces: immutable eval data and baseline/candidate queries.

- [ ] **Step 1: Add tables**

~~~text
eval_dataset_versions
  dataset_reference PK, dataset_id, version, metric_schema_version, content_hash,
  manifest_artifact_ref, created_at
eval_runs
  eval_run_id PK, execution_lane, configuration_json, configuration_hash, status,
  environment_json, provenance_json, provider_response_ids_json,
  created_at, started_at, finished_at
eval_case_results
  case_result_id PK, eval_run_id, case_id, repeat_index, status,
  prediction_artifact_ref, match_json, usage_json, latency_seconds,
  coverage_json, trace_ref, error_code,
  UNIQUE(eval_run_id, case_id, repeat_index)
eval_metrics
  metric_id PK, eval_run_id, metric_schema_version, slice_json,
  name, numerator, denominator, value, unit, complete, missing_reason
eval_comparisons
  comparison_id PK, baseline_run_id, candidate_run_id, payload_json, created_at
release_decisions
  decision_id PK, comparison_id, live_comparison_id, change_classification_json,
  policy_reference, status, payload_json, created_at
release_pointer_sets
  pointer_set_id PK, status, references_json, source_decision_id, created_at
rule_proposals
  proposal_id PK, repository_path_hash, target, target_path, agent_id,
  source_feedback_ids_json, summary, proposed_content_artifact_ref,
  content_hash, status, created_at, decided_at
~~~

- [ ] **Step 2: Write persistence invariants**

Dataset reference/content hash is immutable. Completed run/case/metric rows cannot be edited. Duplicate case result
with identical hash is idempotent; different payload conflicts. Baseline and candidate must use the same dataset,
metric schema, execution lane, repeat policy, and behavior-bearing environment. A live row requires provider response
IDs and exact model/configuration provenance. Deleting ordinary task data does not delete eval/release history unless
the user explicitly clears it.

- [ ] **Step 3: Verify migration and commit**

~~~bash
uv run --project backend alembic upgrade head
uv run --project backend pytest backend/tests/integration/evaluation/test_persistence.py -v
uv run --project backend alembic downgrade 0005
uv run --project backend alembic upgrade head
git add backend
git commit -m "feat: persist immutable eval and comparison history"
~~~

---

### Task 7: Compare Candidate Versions And Enforce Release Policy

**Files:**
- Create: <code>backend/src/codelens/evaluation/domain/release.py</code>
- Create: <code>backend/src/codelens/evaluation/application/comparison.py</code>
- Create: <code>backend/src/codelens/evaluation/application/release.py</code>
- Create: <code>backend/src/codelens/evaluation/application/rollback.py</code>
- Test: <code>backend/tests/unit/evaluation/test_comparison.py</code>
- Test: <code>backend/tests/unit/evaluation/test_release_policy.py</code>
- Test: <code>backend/tests/integration/evaluation/test_activation_rollback.py</code>

**Interfaces:**
- Consumes: comparable baseline/candidate metrics and safety results.
- Produces: PASS/BLOCKED decision, reasons, atomic activation, and rollback.

- [ ] **Step 1: Define default release policy**

~~~python
class ThresholdRule(BaseModel):
    model_config = ConfigDict(frozen=True)
    metric_name: str
    slice_filter: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    max_relative_regression: float | None = None
    required_complete: bool = True


class ReleasePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    policy_id: str
    version: int
    rules: tuple[ThresholdRule, ...]
    required_suite_tags: tuple[str, ...]
    block_on_any_prohibited_finding: bool
    block_on_any_safety_failure: bool
    require_live_for_behavior_changes: bool
    content_hash: str
~~~

Ship one versioned default policy:

- precision at least 0.80;
- recall at least 0.70;
- evidence validity at least 0.95;
- location accuracy at least 0.90;
- false-positive rate at most 0.20;
- clean-refactor false positives do not increase;
- each critical security/concurrency slice has recall 1.0;
- coverage does not regress;
- p95 latency relative regression at most 20%;
- cost per effective finding relative regression at most 25%;
- all malicious-input, isolation, secret, and Fix safety cases pass;
- every required metric is complete.
- a behavior-bearing candidate has complete comparable live baseline/candidate runs.

These are default project release thresholds and remain versioned/configurable through reviewed policy changes.

- [ ] **Step 2: Write comparison tests**

Compare absolute values, deltas, relative deltas, confidence intervals where repeat_count permits, and slices.
Reject different datasets/schema/lane/provenance, missing required case tags, incomplete metrics, incomparable cost
profiles, and fewer completed repeats. A deterministic comparison cannot satisfy a required live comparison.

- [ ] **Step 3: Implement fail-closed decision**

~~~python
class ReleaseDecisionStatus(str, Enum):
    PASSED = "passed"
    BLOCKED = "blocked"


class ReleaseDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    decision_id: str
    status: ReleaseDecisionStatus
    baseline_run_id: str
    candidate_run_id: str
    live_baseline_run_id: str | None
    live_candidate_run_id: str | None
    change_classification: str
    policy_reference: str
    passed_rules: tuple[str, ...]
    failed_rules: tuple[str, ...]
    warnings: tuple[str, ...]
~~~

Warnings never convert a failed required rule to pass.

- [ ] **Step 4: Activate and roll back immutable references**

Activation transaction verifies the decision PASSED, candidate configuration hash still matches, and every referenced version exists. It writes a new active pointer set for AgentVersion, ModelProfile, SkillVersion, validator/context/synthesis policy; it never mutates the version rows. Rollback can select only a previously PASSED pointer set and writes another audit event/pointer set.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/evaluation/test_comparison.py backend/tests/unit/evaluation/test_release_policy.py backend/tests/integration/evaluation/test_activation_rollback.py -v
git add backend
git commit -m "feat: block regressions and activate approved versions"
~~~

---

### Task 8: Add Eval CLI And HTTP APIs

**Files:**
- Create: <code>backend/src/codelens/bootstrap/eval_cli.py</code>
- Create: <code>backend/src/codelens/interface/http/evaluations.py</code>
- Create: <code>backend/src/codelens/interface/http/rule_proposals.py</code>
- Modify: <code>backend/src/codelens/bootstrap/cli.py</code>
- Modify: <code>backend/src/codelens/interface/http/app.py</code>
- Test: <code>backend/tests/contract/http/test_evaluations_api.py</code>
- Test: <code>backend/tests/unit/bootstrap/test_eval_cli.py</code>

**Interfaces:**
- Produces:
  - <code>codelens-review eval run</code>
  - <code>codelens-review eval compare</code>
  - <code>codelens-review eval gate</code>
  - <code>codelens-review eval activate</code>
  - <code>codelens-review eval rollback</code>
  - dataset/run/comparison/release REST queries and commands.

- [ ] **Step 1: Write CLI behavior tests**

<code>eval run</code> requires <code>--lane deterministic|live</code>, dataset/config refs, tags/filters/repeats,
and prints IDs plus artifact paths only for local CLI. The live lane requires credentials and exact released model
configuration. <code>compare</code> rejects incompatible/lane-mixed runs. <code>gate</code> exits zero only for PASSED
and nonzero for BLOCKED/incomplete. <code>activate</code> requires a PASSED decision with every classified lane.
No command prints secrets or raw prompts.

- [ ] **Step 2: Write HTTP contracts**

~~~text
GET  /api/eval-datasets
POST /api/eval-runs
GET  /api/eval-runs/{id}
POST /api/eval-runs/{id}/cancel
GET  /api/eval-runs/{id}/metrics
POST /api/eval-comparisons
GET  /api/eval-comparisons/{id}
POST /api/eval-comparisons/{id}/gate
POST /api/release-decisions/{id}/activate
POST /api/release-pointer-sets/{id}/rollback
GET  /api/rule-proposals
POST /api/rule-proposals/{id}/confirm
POST /api/rule-proposals/{id}/reject
~~~

Mutation endpoints require JSON, idempotency keys, Host/Origin policy, audit, and stable errors. A browser cannot upload an arbitrary repository archive as a trusted release fixture; dataset installation is an administrator/local CLI operation.

- [ ] **Step 3: Add resumable progress events**

Eval SSE/outbox events include run/case IDs, repeat, status, counts, elapsed, and metric availability only. They exclude fixture source, golden text, prompt, raw prediction, and judge reasoning.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/contract/http/test_evaluations_api.py backend/tests/unit/bootstrap/test_eval_cli.py -v
git add backend
git commit -m "feat: expose evaluation and release commands"
~~~

---

### Task 9: Build Evaluation, Comparison, And Release Dashboard

**Files:**
- Create: <code>frontend/src/features/evaluations/api.ts</code>
- Create: <code>frontend/src/features/evaluations/EvaluationsPage.tsx</code>
- Create: <code>frontend/src/features/evaluations/EvalRunPage.tsx</code>
- Create: <code>frontend/src/features/evaluations/ComparisonPage.tsx</code>
- Create: <code>frontend/src/features/evaluations/ReleaseDecisionPanel.tsx</code>
- Create: <code>frontend/src/features/governance/RuleProposalsPage.tsx</code>
- Modify: <code>frontend/src/app/router.tsx</code>
- Test: <code>frontend/src/features/evaluations/EvaluationsPage.test.tsx</code>
- Test: <code>frontend/src/features/evaluations/EvalRunPage.test.tsx</code>
- Test: <code>frontend/src/features/evaluations/ComparisonPage.test.tsx</code>
- Test: <code>frontend/src/features/evaluations/ReleaseDecisionPanel.test.tsx</code>
- Test: <code>frontend/src/features/governance/RuleProposalsPage.test.tsx</code>
- Create: <code>frontend/e2e/evaluations.spec.ts</code>

**Interfaces:**
- Consumes: Task 8 APIs/events.
- Produces: dataset/run visibility, metric slices, comparisons, blocking reasons, activation, and rollback UI.

- [ ] **Step 1: Write UI tests**

Cover:

- dataset version/hash/case/tag summary;
- running, partial data, failed case, cancel, resume, and terminal states;
- precision/recall/location/evidence/actionable/SNR/coverage/fix/latency/token/cost metrics;
- unavailable metric distinct from zero;
- baseline/candidate absolute and relative delta;
- filters by reviewer/category/severity/language/tag;
- prohibited/safety regression pinned above aggregate improvements;
- release BLOCKED with exact failed rules;
- activation only for PASSED decision;
- rollback only to previously approved pointer set;
- repeated feedback can create a draft RuleProposal, while one-time feedback cannot;
- confirming a REVIEW.md proposal yields an Artifact without modifying the repository;
- confirming an AgentVersion proposal creates an inactive candidate and does not bypass eval;
- long metric names/case paths at desktop/mobile widths.

- [ ] **Step 2: Implement compact visualizations without hiding exact values**

Use accessible tables as the source of truth and small CSS/SVG bars/sparklines for trends. Do not add a chart dependency unless the existing frontend cannot meet accessibility and bundle constraints. Every visualization has text values, numerator/denominator, completeness, and baseline/candidate labels.

- [ ] **Step 3: Add Playwright release flow**

Start a deterministic candidate eval, observe progress, compare to baseline, show one passing and one blocked case, activate the passing candidate, then roll back to the prior approved pointer set. Verify at 1440x900 and 390x844.

- [ ] **Step 4: Verify and commit**

~~~bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test frontend/e2e/evaluations.spec.ts
git add frontend
git commit -m "feat: visualize eval comparisons and release gates"
~~~

---

### Task 10: Wire The Final Release Gate And Document Operational Rollback

**Files:**
- Create: <code>backend/tests/acceptance/test_phase_7.py</code>
- Modify: <code>.github/workflows/release.yml</code>
- Modify: <code>README.md</code>
- Create: <code>docs/release-process.md</code>

**Interfaces:**
- Consumes: complete Phase 7.
- Produces: mandatory candidate-vs-approved-baseline release gate and operator procedure.

- [ ] **Step 1: Add acceptance test**

Run the minimum dataset with deterministic adapters. Assert:

- all required tags/cases/repeats complete;
- predictions are production-validated before scoring;
- clean/pre-existing/malicious cases influence gates;
- one constructed good candidate passes;
- precision, critical recall, evidence, coverage, latency, cost, incomplete data, prohibited finding, and Fix safety regressions each independently block;
- activation is atomic and audited;
- rollback restores exact previous references;
- historical results remain queryable.

- [ ] **Step 2: Update release CI**

Release workflow order:

1. Phase 0-7 unit/integration/contract/E2E/security gates.
2. Real container isolation gate.
3. Build wheel/sdist and clean install smoke.
4. Classify changed immutable references as DETERMINISTIC_ONLY or LIVE_REQUIRED.
5. Always run the deterministic release eval and compare with its approved deterministic baseline.
6. For LIVE_REQUIRED, run protected-credential live baseline and candidate jobs on the same controlled dataset;
   missing credentials, incomplete cases, or missing response IDs block the workflow.
7. Execute one release decision over the required deterministic and live comparisons.
8. Publish only when the decision passes and artifacts/provenance match hashes.

The live job is conditional but mandatory for behavior-bearing changes. It complements rather than replaces the
deterministic lane. Ordinary pull-request tests remain network-free; protected release/activation is blocked until
the required live job completes.

- [ ] **Step 3: Document baseline and rollback**

Document how to install a reviewed dataset version, run/inspect a candidate, approve a new baseline, activate version pointers, respond to a blocked gate, and roll back. Include explicit warning that changing thresholds is itself a reviewed versioned policy change and cannot be done inside a failed release job.

- [ ] **Step 4: Run final complete gate**

~~~bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
uv build --project backend
uv run --project backend pytest -m container backend/tests/integration/sandbox/test_container_runtime.py -v
uv run --project backend codelens-review eval run --lane deterministic --dataset builtin-release:v1 --config release-candidate.json
uv run --project backend codelens-review eval compare --lane deterministic --baseline approved --candidate latest
uv run --project backend codelens-review eval gate --comparison latest --policy default:v1
~~~

The three eval commands must emit concrete IDs so CI never races on ambiguous aliases in a real implementation; aliases above are documentation shorthand replaced by captured command outputs in the workflow.

- [ ] **Step 5: Commit**

~~~bash
git add .github README.md docs backend frontend
git commit -m "test: enforce CodeLens quality release gates"
git status --short --branch
~~~

Expected: clean branch and complete Phase 0-7 implementation design.

## Phase 7 Acceptance Checklist

- [ ] Dataset/case/golden/oracle/configuration versions are immutable and content-hashed.
- [ ] Fixtures use real Git repositories and production snapshot/rule behavior.
- [ ] Positive, negative, clean, pre-existing, malicious, multi-language, and Fix cases exist.
- [ ] Matching is one-to-one, deterministic-first, location/evidence aware, and input-order stable.
- [ ] LLM judge is optional, versioned, auxiliary, and unable to override safety failures.
- [ ] Precision, recall, precision@k, location, evidence, actionable, FPR, SNR, coverage, latency, token, cost, feedback, and Fix metrics have exact formulas and completeness.
- [ ] Metrics are sliced by reviewer/category/severity/language/tag.
- [ ] EvalRunner uses production pipelines, persists before scoring, resumes, cancels, and isolates fixtures.
- [ ] Behavior-bearing changes require complete live baseline/candidate runs with exact model/configuration provenance.
- [ ] Fake/record-replay results cannot satisfy the live quality lane.
- [ ] Baseline/candidate comparison rejects incompatible or incomplete data.
- [ ] Default policy blocks critical/safety/clean-case regressions even when aggregates improve.
- [ ] Activation and rollback move immutable pointer sets atomically and auditably.
- [ ] Repeated feedback produces only user-confirmed RuleProposals; it never directly changes rules or prompts.
- [ ] CLI, REST, SSE, desktop, and mobile eval/release states pass.
- [ ] Release CI cannot publish when deterministic gate or a classified-required live gate is blocked/incomplete.

## Explicitly Outside This Plan

- Authentication, RBAC, multi-tenant dataset sharing, hosted SaaS benchmarking, and public leaderboards.
- Automatic Prompt optimization or automatic threshold changes.
- Treating user feedback or LLM judge output as an unquestioned golden label.
- Automatically publishing failed-candidate source, prompts, traces, or repository content to third parties.
