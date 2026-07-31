# Multi-Agent Review Domain Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the provider-neutral domain contracts, versioned reviewer catalog, prompt identities, and Comment v2 Candidate model required by multi-Agent review without exposing unfinished runtime behavior.

**Architecture:** Keep selection and plan invariants in `review.domain`, reviewer identity in `reviewer_catalog.domain`, and Candidate/Resolution contracts in `findings.domain`. Preserve every Comment v1 type and `correctness:v1` behavior; new catalog entries remain internal until the orchestration plan wires their execution.

**Tech Stack:** Python 3.12 dataclasses and `StrEnum`, Pydantic v2 boundary schemas, pytest, Ruff, mypy strict.

## Global Constraints

- `correctness:v1` remains available only as Legacy Single Reviewer and keeps `confidence_floor=0.7` plus output contract `1`.
- Reviewer references use the existing public form `<agent_id>:v<integer>`.
- Fixed and Adaptive are a discriminated union; General and specialists cannot coexist.
- Budget protocol values are exactly `lean`, `standard`, and `deep`.
- Comment v2 removes numeric confidence and uses `evidence_strength`, `impact_certainty`, and `reproducibility`.
- This plan does not change `POST /api/reviews`, database schema, Worker orchestration, or visible frontend catalog.
- New Python code has complete type annotations and public contracts have docstrings explaining invariants and failure behavior.

---

### Task 1: Reviewer Selection and Strategy Snapshot Values

**Files:**
- Create: `backend/src/codelens/review/domain/review_strategy.py`
- Create: `backend/tests/unit/review/test_review_strategy.py`

**Interfaces:**
- Produces: `BudgetProfile`, `FixedReviewerSelection`, `AdaptiveReviewerSelection`, `ReviewerSelection`, `ReviewProfileSnapshot`.
- Consumes: no infrastructure or API types.
- Later phases must use these exact names instead of passing raw reviewer tuples plus budget strings.

- [ ] **Step 1: Write failing invariant tests**

```python
import pytest

from codelens.review.domain.review_strategy import (
    AdaptiveReviewerSelection,
    BudgetProfile,
    FixedReviewerSelection,
    ReviewProfileSnapshot,
)


def test_fixed_rejects_general_with_specialists() -> None:
    with pytest.raises(ValueError, match="General reviewer must run alone"):
        FixedReviewerSelection(("general:v1", "security:v1"))


def test_fixed_rejects_legacy_correctness_team() -> None:
    with pytest.raises(ValueError, match="correctness:v1 is legacy single-reviewer only"):
        FixedReviewerSelection(("correctness:v1", "test-regression:v1"))


def test_profile_source_identity_is_all_or_nothing() -> None:
    with pytest.raises(ValueError, match="source profile identity is incomplete"):
        ReviewProfileSnapshot(
            reviewer_selection=AdaptiveReviewerSelection(),
            budget_profile=BudgetProfile.STANDARD,
            source_profile_id="profile-balanced",
            source_profile_revision=None,
        )
```

- [ ] **Step 2: Run the focused test and verify the missing-module failure**

Run: `uv run --project backend pytest backend/tests/unit/review/test_review_strategy.py -v`

Expected: FAIL during collection because `codelens.review.domain.review_strategy` does not exist.

- [ ] **Step 3: Implement the immutable values and validation**

```python
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

_REFERENCE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*:v[1-9][0-9]*$")


class BudgetProfile(StrEnum):
    LEAN = "lean"
    STANDARD = "standard"
    DEEP = "deep"


def _validate_references(references: tuple[str, ...]) -> None:
    if not references:
        raise ValueError("Fixed reviewer selection requires at least one reviewer")
    if len(references) != len(set(references)):
        raise ValueError("Fixed reviewer selection contains duplicate reviewers")
    if any(_REFERENCE_PATTERN.fullmatch(reference) is None for reference in references):
        raise ValueError("Fixed reviewer selection contains an invalid reference")
    if "general:v1" in references and references != ("general:v1",):
        raise ValueError("General reviewer must run alone")
    if "correctness:v1" in references and references != ("correctness:v1",):
        raise ValueError("correctness:v1 is legacy single-reviewer only")


@dataclass(frozen=True)
class FixedReviewerSelection:
    reviewer_versions: tuple[str, ...]
    mode: Literal["fixed"] = field(default="fixed", init=False)

    def __post_init__(self) -> None:
        _validate_references(self.reviewer_versions)


@dataclass(frozen=True)
class AdaptiveReviewerSelection:
    mode: Literal["adaptive"] = field(default="adaptive", init=False)


type ReviewerSelection = FixedReviewerSelection | AdaptiveReviewerSelection


@dataclass(frozen=True)
class ReviewProfileSnapshot:
    reviewer_selection: ReviewerSelection
    budget_profile: BudgetProfile
    source_profile_id: str | None = None
    source_profile_revision: int | None = None

    def __post_init__(self) -> None:
        if (self.source_profile_id is None) != (self.source_profile_revision is None):
            raise ValueError("source profile identity is incomplete")
        if self.source_profile_revision is not None and self.source_profile_revision < 1:
            raise ValueError("source profile revision must be positive")
```

Add tests for empty, duplicate, malformed, General-only, valid specialist team, valid legacy single, and all three budgets.

- [ ] **Step 4: Run the domain tests and static checks**

Run:

```bash
uv run --project backend pytest backend/tests/unit/review/test_review_strategy.py -v
uv run --project backend ruff check backend/src/codelens/review/domain/review_strategy.py backend/tests/unit/review/test_review_strategy.py
uv run --project backend mypy backend/src/codelens/review/domain/review_strategy.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit the strategy values**

```bash
git add backend/src/codelens/review/domain/review_strategy.py backend/tests/unit/review/test_review_strategy.py
git commit -m "feat: add review strategy domain values"
```

---

### Task 2: Versioned Reviewer Catalog

**Files:**
- Modify: `backend/src/codelens/reviewer_catalog/domain/models.py`
- Modify: `backend/src/codelens/reviewer_catalog/infrastructure/builtin_agents.py`
- Create: `backend/tests/unit/reviewer_catalog/test_builtin_agents.py`
- Create: `prompts/correctness-v2/en.md`
- Create: `prompts/correctness-v2/zh-CN.md`
- Create: `prompts/general/en.md`
- Create: `prompts/general/zh-CN.md`
- Create: `prompts/security/en.md`
- Create: `prompts/security/zh-CN.md`
- Create: `prompts/reliability-concurrency/en.md`
- Create: `prompts/reliability-concurrency/zh-CN.md`
- Create: `prompts/contract-data/en.md`
- Create: `prompts/contract-data/zh-CN.md`
- Create: `prompts/architecture/en.md`
- Create: `prompts/architecture/zh-CN.md`
- Create: `prompts/performance/en.md`
- Create: `prompts/performance/zh-CN.md`
- Create: `prompts/test-regression/en.md`
- Create: `prompts/test-regression/zh-CN.md`
- Create: `prompts/review-planner/en.md`
- Create: `prompts/review-planner/zh-CN.md`
- Create: `prompts/review-resolver/en.md`
- Create: `prompts/review-resolver/zh-CN.md`
- Create: `prompts/review-verifier/en.md`
- Create: `prompts/review-verifier/zh-CN.md`

**Interfaces:**
- Produces: `AgentRole`, extended `AgentVersion`, `builtin_agent_catalog() -> dict[str, AgentVersion]`.
- `AgentVersion.reference` returns the canonical `<agent_id>:v<version>` value.
- Capability references are strings resolved in Phase 2; this task does not import the `capabilities` context.

- [ ] **Step 1: Write failing catalog tests**

```python
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog


def test_catalog_contains_the_approved_public_reviewers() -> None:
    catalog = builtin_agent_catalog()
    public = {reference for reference, agent in catalog.items() if agent.is_public}

    assert public == {
        "correctness:v2",
        "security:v1",
        "reliability-concurrency:v1",
        "contract-data:v1",
        "architecture:v1",
        "performance:v1",
        "test-regression:v1",
        "general:v1",
    }


def test_legacy_correctness_is_not_planner_eligible() -> None:
    legacy = builtin_agent_catalog()["correctness:v1"]

    assert legacy.is_legacy is True
    assert legacy.is_public is False
    assert legacy.planner_eligible is False
    assert legacy.output_contract_version == "1"
    assert legacy.confidence_floor == 0.7
```

Also assert General is public but not combinable, every specialist is Planner-eligible, internal Planner/Resolver/Verifier are not public, and every catalog dictionary key equals `agent.reference`.

- [ ] **Step 2: Run the catalog test and verify it fails**

Run: `uv run --project backend pytest backend/tests/unit/reviewer_catalog/test_builtin_agents.py -v`

Expected: FAIL because `builtin_agent_catalog` and the extended metadata do not exist.

- [ ] **Step 3: Extend `AgentVersion` and build the catalog**

```python
from dataclasses import dataclass
from enum import StrEnum


class AgentRole(StrEnum):
    PLANNER = "planner"
    REVIEWER = "reviewer"
    RESOLVER = "resolver"
    VERIFIER = "verifier"


@dataclass(frozen=True)
class AgentVersion:
    agent_id: str
    version: int
    role: AgentRole
    prompt_key: str
    prompt_template: str
    model_profile_id: str
    output_contract_version: str
    capability_profile_ref: str
    skill_policy_ref: str
    timeout_seconds: float
    max_turns: int
    confidence_floor: float | None
    failure_policy: str
    dimensions: tuple[str, ...]
    planner_eligible: bool
    is_public: bool
    is_legacy: bool
    content_hash: str

    @property
    def reference(self) -> str:
        return f"{self.agent_id}:v{self.version}"
```

Keep `correctness_agent()` as a compatibility wrapper returning `builtin_agent_catalog()["correctness:v1"]`. Generate each `content_hash` from every execution-affecting field except the runtime-loaded prompt body; the frozen prompt body hash is added by Phase 2. Bind references as follows:

| Agent | Output | Capability | Skill policy | Failure policy |
| --- | --- | --- | --- | --- |
| `correctness:v1` | `1` | `legacy-reviewer:v1` | `none:v1` | `fail_task` |
| Public reviewers | `2` | `reviewer-comment-v2:v1` | `none:v1` | `partial_team` |
| `review-planner:v1` | `review-plan:1` | `planner:v1` | `none:v1` | `fail_task` |
| `review-resolver:v1` | `resolution:1` | `resolver:v1` | `none:v1` | `fail_task` |
| `review-verifier:v1` | `verification:1` | `verifier:v1` | `none:v1` | `partial_task` |

Prompt files must state the exact assigned dimension and explicitly forbid creating findings outside it. General covers all dimensions shallowly and must state that it runs alone. Planner selects either General or 2–N specialists and cannot produce Findings. Resolver and Verifier must state that they cannot invent a new root cause.

- [ ] **Step 4: Run catalog, legacy, and prompt-file tests**

Run:

```bash
uv run --project backend pytest backend/tests/unit/reviewer_catalog/test_builtin_agents.py backend/tests/unit/review/test_validate_findings.py -v
uv run --project backend ruff check backend/src/codelens/reviewer_catalog backend/tests/unit/reviewer_catalog/test_builtin_agents.py
uv run --project backend mypy backend/src/codelens/reviewer_catalog
```

Expected: all commands exit `0`; existing `correctness_agent()` callers remain green.

- [ ] **Step 5: Commit the catalog**

```bash
git add backend/src/codelens/reviewer_catalog backend/tests/unit/reviewer_catalog/test_builtin_agents.py prompts
git commit -m "feat: add versioned multi-agent reviewer catalog"
```

---

### Task 3: Versioned Reviewer Prompt Overrides

**Files:**
- Modify: `backend/src/codelens/reviewer_catalog/application/prompt_settings.py`
- Modify: `backend/src/codelens/reviewer_catalog/infrastructure/file_prompt_settings.py`
- Modify: `backend/src/codelens/interface/http/routers/reviewer_prompts.py`
- Modify: `backend/src/codelens/interface/http/dto.py`
- Modify: `backend/tests/unit/reviewer_catalog/test_prompt_settings.py`
- Modify: `backend/tests/contract/http/test_instruction_settings_api.py`

**Interfaces:**
- Produces: `ReviewerPromptSettingsService.get(agent: AgentVersion, locale: PromptLocale)` and corresponding `update`/`reset` signatures.
- Persists overrides by canonical reviewer reference, for example `correctness:v2`.
- Reads the old `{"correctness": {"en": ...}}` shape as `correctness:v1` without rewriting it until the user saves.

- [ ] **Step 1: Add failing version-isolation and legacy-read tests**

```python
from pathlib import Path

from codelens.reviewer_catalog.application.prompt_settings import ReviewerPromptSettingsService
from codelens.reviewer_catalog.infrastructure.builtin_agents import builtin_agent_catalog
from codelens.reviewer_catalog.infrastructure.file_prompt_settings import (
    FilesystemReviewerPromptStore,
)


async def test_prompt_overrides_are_isolated_by_reviewer_version(tmp_path: Path) -> None:
    store = FilesystemReviewerPromptStore(tmp_path)
    prompt_dir = Path(__file__).resolve().parents[4] / "prompts"
    service = ReviewerPromptSettingsService(store, prompt_dir)
    catalog = builtin_agent_catalog()

    await service.update(catalog["correctness:v2"], "en", "v2 custom")

    assert (await service.get(catalog["correctness:v2"], "en")).prompt == "v2 custom"
    assert (await service.get(catalog["correctness:v1"], "en")).prompt != "v2 custom"
```

Add a fixture containing the old JSON shape and assert it loads only for `correctness:v1`. Add an HTTP contract test for `GET /api/reviewer-prompts/correctness?version=2&locale=en`.

- [ ] **Step 2: Run the prompt tests and verify signature failures**

Run: `uv run --project backend pytest backend/tests/unit/reviewer_catalog/test_prompt_settings.py backend/tests/contract/http/test_instruction_settings_api.py -v`

Expected: FAIL because the service and route currently key only by `agent_id`.

- [ ] **Step 3: Implement reference-keyed storage and route lookup**

Use the injected built-in catalog to resolve `(agent_id, version)` before reading a prompt. The stored representation is:

```json
{
  "correctness:v2": {
    "en": "v2 custom"
  }
}
```

`ReviewerPromptView.version` must come from `AgentVersion.version`, and `_system_prompt` must use `agent.prompt_key` after validating that the catalog supplied the Agent. Preserve the existing route path and add a validated `version` query parameter with default `1` so old callers keep working.

- [ ] **Step 4: Run prompt and HTTP contract tests**

Run:

```bash
uv run --project backend pytest backend/tests/unit/reviewer_catalog/test_prompt_settings.py backend/tests/contract/http/test_instruction_settings_api.py -v
uv run --project backend ruff check backend/src/codelens/reviewer_catalog backend/src/codelens/interface/http/routers/reviewer_prompts.py
uv run --project backend mypy backend/src/codelens/reviewer_catalog backend/src/codelens/interface/http/routers/reviewer_prompts.py
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit versioned prompt settings**

```bash
git add backend/src/codelens/reviewer_catalog backend/src/codelens/interface/http/routers/reviewer_prompts.py backend/src/codelens/interface/http/dto.py backend/tests
git commit -m "feat: isolate reviewer prompts by version"
```

---

### Task 4: Comment v2 Candidate Contract and Collector

**Files:**
- Create: `backend/src/codelens/findings/domain/candidates.py`
- Create: `backend/src/codelens/findings/infrastructure/comment_v2_output.py`
- Create: `backend/src/codelens/review/infrastructure/comment_collector_v2.py`
- Modify: `backend/src/codelens/findings/infrastructure/agent_output_codec.py`
- Modify: `backend/src/codelens/review/infrastructure/comment_collector.py`
- Create: `backend/tests/unit/findings/test_comment_v2_schema.py`
- Create: `backend/tests/unit/review/test_comment_collector_v2.py`
- Modify: `backend/tests/unit/review/test_validate_findings.py`

**Interfaces:**
- Produces: `EvidenceStrength`, `ImpactCertainty`, `Reproducibility`, `CandidateFinding`, `CandidateFindingBatch`, `CommentV2OutputCodec`, `ReviewCommentCollectorV2`.
- Preserves: `ReviewCommentCollector` and `AgentOutputCodec("1")` as Comment v1.
- The v2 collector returns resolved Snapshot locations and never accepts numeric confidence.

- [ ] **Step 1: Write failing strict-schema tests**

```python
import pytest
from pydantic import ValidationError
from typing import cast

from codelens.findings.infrastructure.comment_v2_output import CommentV2BatchSchema


def valid_comment_v2_payload() -> dict[str, object]:
    return {
        "schema_version": "2",
        "findings": [{
            "reviewer_id": "security",
            "path": "src/webhook.py",
            "side": "new",
            "existing_code": "payload = parse(body)",
            "title": "Body parsed before signature verification",
            "content": "Untrusted input is parsed before authentication.",
            "recommendation": "Verify the signature before parsing.",
            "category": "authentication",
            "severity": "high",
            "primary_dimension": "security",
            "secondary_dimensions": ["performance"],
            "evidence_strength": "direct",
            "impact_certainty": "confirmed",
            "reproducibility": "deterministic",
        }],
    }


def test_comment_v2_accepts_categorical_evidence_axes() -> None:
    batch = CommentV2BatchSchema.model_validate(valid_comment_v2_payload())

    assert batch.findings[0].evidence_strength == "direct"


def test_comment_v2_rejects_numeric_confidence() -> None:
    payload = valid_comment_v2_payload()
    findings = cast(list[dict[str, object]], payload["findings"])
    findings[0]["confidence"] = 0.9

    with pytest.raises(ValidationError, match="confidence"):
        CommentV2BatchSchema.model_validate(payload)
```

Collector tests must also prove per-item rejection, General/specialist `primary_dimension` enforcement, old/new side resolution, and unchanged Comment v1 confidence filtering.

- [ ] **Step 2: Run the v2 tests and verify missing contracts**

Run: `uv run --project backend pytest backend/tests/unit/findings/test_comment_v2_schema.py backend/tests/unit/review/test_comment_collector_v2.py -v`

Expected: FAIL during import because the v2 modules do not exist.

- [ ] **Step 3: Implement v2 models, codec, and collector**

```python
class EvidenceStrength(StrEnum):
    DIRECT = "direct"
    INFERRED = "inferred"
    WEAK = "weak"


class ImpactCertainty(StrEnum):
    CONFIRMED = "confirmed"
    PLAUSIBLE = "plausible"
    UNCLEAR = "unclear"


class Reproducibility(StrEnum):
    DETERMINISTIC = "deterministic"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CandidateFinding:
    task_id: str
    candidate_id: str
    run_id: str
    snapshot_id: str
    reviewer_reference: str
    category: str
    title: str
    severity: FindingSeverity
    primary_dimension: str
    secondary_dimensions: tuple[str, ...]
    evidence_strength: EvidenceStrength
    impact_certainty: ImpactCertainty
    reproducibility: Reproducibility
    primary_location: SourceLocation
    related_locations: tuple[SourceLocation, ...]
    changed_hunk_id: str
    existing_code_hash: str
    evidence_hashes: tuple[str, ...]
    content: str
    recommendation: str
    fingerprint: str
```

`CommentV2BatchSchema` accepts the model-facing fields from the approved Comment v2 JSON only. `ReviewCommentCollectorV2` resolves them into the complete domain type above: `existing_code_hash` is SHA-256 over the normalized submitted excerpt, `evidence_hashes` contains that hash plus any validated related-location excerpt hashes, and `fingerprint` is SHA-256 over canonical Snapshot/location/root-cause/impact/evidence fields. Derive `candidate_id` from task ID, run ID, reviewer reference, normalized location, title, and categorical axes after Snapshot resolution. The collector may share private location-resolution functions with v1, but v1 public schemas and serialized bytes must remain byte-compatible.

- [ ] **Step 4: Run v1 and v2 contract tests**

Run:

```bash
uv run --project backend pytest backend/tests/unit/findings/test_finding_schema.py backend/tests/unit/findings/test_comment_v2_schema.py backend/tests/unit/review/test_comment_collector_v2.py backend/tests/unit/review/test_validate_findings.py -v
uv run --project backend ruff check backend/src/codelens/findings backend/src/codelens/review/infrastructure/comment_collector.py backend/src/codelens/review/infrastructure/comment_collector_v2.py
uv run --project backend mypy backend/src/codelens/findings backend/src/codelens/review/infrastructure/comment_collector_v2.py
```

Expected: all commands exit `0`; v1 fixtures still decode as schema `1`.

- [ ] **Step 5: Commit Comment v2 foundation**

```bash
git add backend/src/codelens/findings backend/src/codelens/review/infrastructure/comment_collector.py backend/src/codelens/review/infrastructure/comment_collector_v2.py backend/tests/unit/findings backend/tests/unit/review
git commit -m "feat: add comment v2 candidate contract"
```

---

### Task 5: Review Plan and Resolution Domain Invariants

**Files:**
- Create: `backend/src/codelens/review/domain/review_plan.py`
- Create: `backend/src/codelens/findings/domain/resolution.py`
- Create: `backend/tests/unit/review/test_review_plan.py`
- Create: `backend/tests/unit/findings/test_resolution.py`

**Interfaces:**
- Produces: `ReviewPass`, `ReviewPlanNodeType`, `ReviewPlanNode`, `ReviewPlan`, `CoverageStatus`, `FindingCluster`, `ResolutionOutcome`, `ResolutionDecision`, `VerificationOutcome`, `VerificationDecision`.
- Runtime execution and persistence are explicitly deferred to Phase 3; these values must remain pure and deterministic.

- [ ] **Step 1: Write failing plan and no-invention tests**

```python
import pytest

from codelens.findings.domain.resolution import FindingCluster, ResolutionDecision
from codelens.review.domain.review_plan import (
    ReviewPlan,
    ReviewPlanNode,
    ReviewPlanNodeType,
)


def reviewer_nodes_only() -> tuple[ReviewPlanNode, ...]:
    task_id = "review_" + "a" * 32
    return tuple(
        ReviewPlanNode.create(
            task_id=task_id,
            node_type=ReviewPlanNodeType.REVIEWER,
            agent_reference=reference,
            pass_index=1,
            shard_id="root",
            logical_attempt_group="primary",
            depends_on=(),
        )
        for reference in ("correctness:v2", "security:v1")
    )


def test_multi_specialist_plan_requires_resolver() -> None:
    with pytest.raises(ValueError, match="multi-specialist plan requires a resolver"):
        ReviewPlan.create(
            task_id="review_" + "a" * 32,
            selection_mode="fixed",
            budget_profile="standard",
            reviewer_references=("correctness:v2", "security:v1"),
            nodes=reviewer_nodes_only(),
            planner_reason=None,
        )


def test_resolution_decision_cannot_reference_unknown_candidate() -> None:
    cluster = FindingCluster(cluster_id="cluster-1", candidate_ids=("candidate-1",))

    with pytest.raises(ValueError, match="unknown candidate"):
        ResolutionDecision.publish(
            cluster=cluster,
            canonical_candidate_id="candidate-2",
            merged_candidate_ids=("candidate-2",),
        )
```

Add tests for General-only, Fixed Single Specialist, Adaptive planner reason required, deterministic `plan_hash`, verifier decisions limited to confirmed/rejected/unresolved, and unresolved never becoming publishable.

- [ ] **Step 2: Run the domain tests and verify missing modules**

Run: `uv run --project backend pytest backend/tests/unit/review/test_review_plan.py backend/tests/unit/findings/test_resolution.py -v`

Expected: FAIL during import.

- [ ] **Step 3: Implement pure plan and decision values**

`ReviewPlanNode` identity must be derived from `(task_id, agent_reference, pass_index, shard_id, logical_attempt_group)`. `ReviewPlan.create` must canonicalize reviewers and nodes before hashing, but it must preserve the user-specified Fixed reviewer order in `selection_request` outside the Plan. `ResolutionDecision` may merge, normalize, suppress, or request verification only for Candidate IDs in its cluster. `VerificationDecision` may only decide Candidate/Cluster IDs supplied in its batch.

Use these exact pass numbers:

```python
class ReviewPass(IntEnum):
    PLANNER = 0
    REVIEWER = 1
    RESOLVER = 2
    VERIFIER = 3
```

Use `CoverageStatus` values `planned`, `completed`, `failed`, and `omitted`.

- [ ] **Step 4: Run foundation and full backend gates**

Run:

```bash
uv run --project backend pytest backend/tests/unit/review/test_review_strategy.py backend/tests/unit/review/test_review_plan.py backend/tests/unit/findings/test_resolution.py backend/tests/unit/findings/test_comment_v2_schema.py backend/tests/unit/reviewer_catalog -v
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
```

Expected: all commands exit `0`; no HTTP response or database migration changes in this phase.

- [ ] **Step 5: Commit the domain foundation gate**

```bash
git add backend/src/codelens/review/domain backend/src/codelens/findings/domain backend/tests/unit/review backend/tests/unit/findings
git commit -m "feat: add review plan and resolution invariants"
```
