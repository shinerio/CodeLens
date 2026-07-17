# CodeLens Phase 5 Isolated Fix Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Implement Fix mode with a separate owned worktree, allowlisted Fix Agent changes, Snapshot-based immutable PatchSets, validation gates, manual-default application, explicitly enabled automatic application under hard gates, and complete recovery/UI behavior.

**Architecture:** A FixTask selects validated findings from one frozen ReviewReport and reconstructs that exact Snapshot in a new task-owned Fix worktree. The sandbox exposes the checkout without usable Git metadata. Trusted services derive a snapshot tree and fixed tree, generate only their difference, evaluate gates, and apply to an explicit target workspace under a short target lock after fingerprint revalidation.

**Tech Stack:** Python 3.12, asyncio subprocesses, Git CLI, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite WAL, OpenAI Agents SDK, React, TypeScript, Monaco Diff Editor or an equivalent mature diff viewer, Vitest, Playwright.

## Global Constraints

- Phase 0-4 acceptance gates pass before this plan starts.
- REVIEW mode remains strictly read-only.
- FIX mode writes only an isolated worktree until trusted application code applies a validated PatchSet.
- The source repository path is never mounted into the model sandbox.
- The sandbox does not receive usable .git worktree metadata, host Git credentials, SSH credentials, cloud credentials, or OpenAI/MCP secrets.
- Model and MCP tools cannot call the source-repository apply operation.
- Every FixTask has a frozen allowlist derived from selected validated finding locations and explicit user additions.
- Manual application is the default. Automatic application requires explicit per-FixTask opt-in and every hard gate.
- MCP write/delete/publish actions always require their own human approval; Fix auto-approval never grants them.
- UI may preselect the originating user workspace, but every apply request explicitly identifies and fingerprints its target; apply never commits or pushes.
- Repeated apply requests are idempotent.
- On conflict or gate failure, save the PatchSet and evidence; do not modify the source repository.
- Never reset, checkout, clean, stash, or otherwise erase user changes to recover from apply failure.
- Git and command calls use argv, timeout, output caps, contained paths, and allowed exit codes.
- Git behavior tests use real temporary repositories.

## 2026-07-17 Correctness Amendment

- Reuse Phase 1 WorktreeManager ownership and scoped cleanup. Never run global <code>git worktree prune</code>.
- Prepare the Fix worktree from the frozen ReviewSnapshot, including overlay/untracked/deleted state. Do not prepare
  from mutable source HEAD and then assume <code>git diff</code> represents only the Fix.
- Record <code>snapshot_tree_oid</code> before the Agent and <code>fixed_tree_oid</code> afterward using the Fix
  worktree's isolated index. PatchSet is the binary/full-index diff between those trees.
- Every apply request names an explicit target workspace and expected HEAD/index/worktree fingerprint. Fingerprint
  mismatch fails before write. 3-way apply is a policy choice, never an implicit way to bypass mismatch.
- Fix work may run concurrently in separate worktrees. Check+apply is serialized only per apply-target realpath;
  the lock is not held during Agent execution or validation.
- Apply persistence has no expiring lock lease in the single-Worker release. Recovery uses idempotency key plus
  before/after target fingerprints and an append-only apply journal.

---

## File And Module Map

~~~text
backend/src/codelens/
  changes/
    domain/fix_task.py               # FixTask state machine and selection
    domain/patch.py                  # PatchSet and changed-file contracts
    domain/gates.py                  # ValidationGate and approval decision
    domain/ports.py                  # worktree, fix runtime, scanner, apply ports
    application/create_fix.py        # selection and task creation
    application/fix_pipeline.py      # durable Fix DAG
    application/patch_validation.py  # path/symlink/binary/size gates
    application/approval.py          # hard-gate truth table
    application/apply.py             # repository lock and idempotency
    infrastructure/git_worktree.py   # isolated real worktree adapter
    infrastructure/openai_fix.py     # Fix Agent adapter
    infrastructure/repositories.py   # tasks, gates, patches, apply results
  worker/fix_worker.py
  interface/http/fixes.py
backend/migrations/versions/
  0004_fix_workflow.py
frontend/src/features/
  fixes/api.ts
  fixes/CreateFixDialog.tsx
  fixes/FixRunPage.tsx
  fixes/PatchViewer.tsx
  fixes/ValidationGatesPanel.tsx
  fixes/ApplyPanel.tsx
backend/tests/
  unit/changes/
  integration/changes/
  contract/http/test_fixes_api.py
frontend/e2e/fix-workflow.spec.ts
~~~

### Task 1: Define FixTask, Selection, PatchSet, And Gate Contracts

**Files:**
- Create: <code>backend/src/codelens/changes/domain/fix_task.py</code>
- Create: <code>backend/src/codelens/changes/domain/patch.py</code>
- Create: <code>backend/src/codelens/changes/domain/gates.py</code>
- Create: <code>backend/src/codelens/changes/domain/ports.py</code>
- Test: <code>backend/tests/unit/changes/test_fix_task.py</code>
- Test: <code>backend/tests/unit/changes/test_approval_policy.py</code>

**Interfaces:**
- Consumes: ReviewReport, validated Finding IDs, ReviewSnapshot ID and RepositoryFingerprint.
- Produces: complete FixTask state machine, immutable PatchSet, ValidationGateResult, and ApprovalDecision.

- [ ] **Step 1: Write failing state and selection tests**

~~~python
import pytest

from codelens.changes.domain.fix_task import FixSelection, FixStatus, FixTask
from codelens.workspace.domain.models import RepositoryFingerprint


def test_fix_task_follows_isolated_workflow() -> None:
    task = FixTask.create(
        fix_id="fix_1",
        review_id="review_1",
        snapshot_id="snapshot_1",
        repository_path="/srv/repos/billing",
        repository_path_hash="repo_hash",
        selection=FixSelection(
            finding_ids=("f1",),
            allowed_paths=("src/payment.py",),
        ),
        source_fingerprint=RepositoryFingerprint("head", "index", "worktree"),
        automatic_apply_requested=False,
    )
    task.start_preparing()
    task.attach_worktree("worktree_1")
    task.start_fixing()
    task.attach_patch("patch_1")
    task.start_verifying()
    task.await_approval()
    task.start_apply(requested_by="user")
    task.applied()
    assert task.status is FixStatus.APPLIED


def test_fix_selection_requires_validated_actionable_findings() -> None:
    with pytest.raises(ValueError, match="at least one finding"):
        FixSelection(finding_ids=(), allowed_paths=())
~~~

- [ ] **Step 2: Implement the state machine**

~~~python
class FixStatus(str, Enum):
    CREATED = "created"
    PREPARING_WORKTREE = "preparing_worktree"
    FIXING = "fixing"
    VERIFYING = "verifying"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    APPLIED = "applied"
    APPLY_CONFLICT = "apply_conflict"
    FIX_FAILED = "fix_failed"
    CANCELED = "canceled"


@dataclass(frozen=True)
class FixSelection:
    finding_ids: tuple[str, ...]
    allowed_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.finding_ids:
            raise ValueError("at least one finding is required")
        if not self.allowed_paths:
            raise ValueError("at least one allowed path is required")
        for path in self.allowed_paths:
            if path.startswith("/") or ".." in PurePosixPath(path).parts:
                raise ValueError(f"invalid allowed path: {path}")


@dataclass
class FixTask:
    fix_id: str
    review_id: str
    snapshot_id: str
    repository_path: str
    repository_path_hash: str
    selection: FixSelection
    source_fingerprint: RepositoryFingerprint
    automatic_apply_requested: bool = False
    status: FixStatus = FixStatus.CREATED
    worktree_id: str | None = None
    patch_id: str | None = None
    apply_result_id: str | None = None
~~~

Implement explicit transition methods shown by the test. Terminal states cannot reopen. A restarted singleton Worker
may resume PREPARING_WORKTREE, FIXING, VERIFYING, or AWAITING_APPROVAL but never repeat an APPLIED operation.

- [ ] **Step 3: Define immutable PatchSet and gate results**

~~~python
class PatchChangeKind(str, Enum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    RENAME = "rename"
    BINARY = "binary"


class PatchFile(BaseModel):
    model_config = ConfigDict(frozen=True)
    old_path: str | None
    new_path: str | None
    kind: PatchChangeKind
    added_lines: int
    deleted_lines: int
    old_hash: str | None
    new_hash: str | None


class PatchSet(BaseModel):
    model_config = ConfigDict(frozen=True)
    patch_id: str
    fix_id: str
    base_snapshot_id: str
    snapshot_tree_oid: str
    fixed_tree_oid: str
    content_hash: str
    artifact_ref: str
    files: tuple[PatchFile, ...]
    total_added_lines: int
    total_deleted_lines: int


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class ValidationGateResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    gate_id: str
    required: bool
    status: GateStatus
    code: str
    summary: str
    artifact_ref: str | None = None
~~~

- [ ] **Step 4: Define approval policy input**

~~~python
@dataclass(frozen=True)
class AutoApprovalLimits:
    max_files: int
    max_changed_lines: int


@dataclass(frozen=True)
class ApprovalDecision:
    automatic_apply_allowed: bool
    failed_gate_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
~~~

The pure evaluator requires explicit automatic opt-in, fingerprint match, allowlist pass,
path/symlink/binary pass, every required command/security gate pass, secret scan pass, size thresholds,
tree/hash match, and plain patch-check pass. SKIPPED required gates fail closed.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/changes -v
uv run --project backend ruff check backend/src/codelens/changes/domain
uv run --project backend mypy backend/src/codelens/changes/domain
git add backend
git commit -m "feat: define isolated fix and approval contracts"
~~~

---

### Task 2: Persist Fix Tasks, Tree Baselines, Gates, And Apply Journal

**Files:**
- Modify: <code>backend/src/codelens/review/infrastructure/tables.py</code>
- Create: <code>backend/src/codelens/changes/infrastructure/repositories.py</code>
- Create: <code>backend/migrations/versions/0004_fix_workflow.py</code>
- Test: <code>backend/tests/integration/changes/test_fix_persistence.py</code>

**Interfaces:**
- Consumes: Fix domain contracts and Phase 0–3 job/checkpoint/Artifact stores.
- Produces: FixTask, PatchSet, gate, apply-journal, and idempotency persistence.

- [ ] **Step 1: Write failing atomicity and restart tests**

Assert FixTask/job/event creation is atomic. Persist/reload Snapshot tree OID, fixed tree OID, PatchSet hash/ref, every gate, target realpath hash, expected fingerprint, apply idempotency key, and before/after fingerprints. Restart at each Fix stage and assert no duplicate worktree, PatchSet, or apply.

- [ ] **Step 2: Add migration and explicit uniqueness**

Store one immutable PatchSet per Fix attempt and one append-only apply journal entry per idempotency key. Do not create expiring apply locks, heartbeat, lease owner, or reclaim columns. The single Worker uses an in-process target lock; persistence detects repeated requests and ambiguous interrupted applies.

- [ ] **Step 3: Define interrupted-apply recovery**

Before writing, journal <code>CHECKED</code> with target fingerprint and patch hash. After apply, immediately capture/persist the resulting fingerprint and <code>APPLIED</code>. On restart:

- expected before fingerprint means apply did not occur and may be retried after a new check;
- exact recorded after fingerprint means return the existing success;
- any other fingerprint means <code>APPLY_RECOVERY_REQUIRED</code> and no automatic retry.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend alembic upgrade head
uv run --project backend pytest backend/tests/integration/changes/test_fix_persistence.py -v
uv run --project backend mypy backend/src/codelens/changes
uv run --project backend ruff check backend
git add backend
git commit -m "feat: persist fix trees gates and apply journal"
~~~

Expected: migration, restart, idempotency, and ambiguous-apply tests pass without lease fields.

---

### Task 3: Reconstruct The Snapshot In A Separate Owned Fix Worktree

**Files:**
- Create: <code>backend/src/codelens/changes/application/prepare_fix_worktree.py</code>
- Create: <code>backend/src/codelens/changes/infrastructure/git_tree.py</code>
- Reuse: <code>backend/src/codelens/workspace/infrastructure/git_worktrees.py</code>
- Test: <code>backend/tests/integration/changes/test_fix_worktree.py</code>

**Interfaces:**
- Consumes: ReviewSnapshot Artifact/Manifest, pinned head OID, Phase 1 WorktreeManager, and FixSelection.
- Produces: owned Fix worktree plus <code>snapshot_tree_oid</code>.

- [ ] **Step 1: Write failing real-Git reconstruction tests**

Review a dirty workspace containing staged, unstaged, untracked, deleted, renamed, ignored, and symlink cases. Change the user workspace after Review. Prepare Fix and assert its files match the frozen Snapshot, not the current user workspace. Assert its path differs from the Review worktree and all user worktrees.

- [ ] **Step 2: Reuse scoped ownership lifecycle**

Create a detached Fix worktree at the Snapshot head OID through WorktreeManager. Apply the persisted Snapshot overlay and materialize allowed untracked/deleted state. Verify every Manifest entry hash. A mismatch fails before the Fix Agent starts.

- [ ] **Step 3: Freeze the exact Snapshot tree**

Use only the isolated Fix index: stage the reconstructed Snapshot with <code>git add -A</code>, write its tree OID, and persist that OID before Agent execution. This may add objects to the repository object database through Git's worktree mechanism but must not update user refs, index, or files.

Cleanup verifies ownership and removes only the exact Fix checkout. Never call global prune, reset, clean, stash, or checkout on a user workspace.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/integration/changes/test_fix_worktree.py -v
uv run --project backend mypy backend/src/codelens/changes
uv run --project backend ruff check backend
git add backend
git commit -m "feat: reconstruct snapshots in fix worktrees"
~~~

Expected: frozen Snapshot reconstruction, tree OID, ownership, and scoped cleanup pass.

---

### Task 4: Run A Bounded Fix Agent Against Only The Isolated Copy

**Files:**
- Create: <code>backend/src/codelens/changes/infrastructure/openai_fix.py</code>
- Create: <code>backend/src/codelens/changes/application/fix_pipeline.py</code>
- Modify: <code>backend/src/codelens/changes/domain/ports.py</code>
- Test: <code>backend/tests/contract/changes/test_fix_runtime.py</code>
- Test: <code>backend/tests/integration/changes/test_fix_agent_isolation.py</code>

**Interfaces:**
- Consumes: FixTask selection, selected Finding details, isolated worktree content view, frozen capabilities.
- Produces: structured FixAgentResult and modified isolated worktree.

- [ ] **Step 1: Define Fix Agent output**

~~~python
class FixAgentResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    addressed_finding_ids: tuple[str, ...]
    modified_paths: tuple[str, ...]
    summary: str
    validation_recommendations: tuple[str, ...]
~~~

The runtime port:

~~~python
class FixRuntimePort(Protocol):
    async def run(
        self,
        task: FixTask,
        findings: tuple[Finding, ...],
        workspace: FixWorkspaceView,
        capabilities: tuple[CapabilityGrant, ...],
    ) -> FixAgentResult:
        raise NotImplementedError
~~~

- [ ] **Step 2: Write isolation and allowlist tests**

Inject a fake file tool and assert:

- reads stay inside the isolated content root;
- writes outside allowed_paths fail with <code>fix_path_denied</code>;
- source repository content and fingerprint never change;
- tool path traversal and escaping symlink fail;
- repository Skill/MCP cannot widen write allowlist;
- Fix auto-approval does not approve an MCP write tool;
- cancellation terminates the Agent run and open subprocesses.

- [ ] **Step 3: Implement adapter**

The SDK Agent receives the selected findings, exact allowed paths, bounded ContextPlan, and file tools rooted at the isolated content view. It has no apply tool. File writes call a trusted contained adapter that rejects paths not in FixSelection. The output must reference every selected finding or explain omissions in summary; unknown finding IDs and paths fail output validation.

- [ ] **Step 4: Add durable pipeline checkpoints**

<code>FixPipeline.execute</code> prepares/reuses the worktree, persists FIXING, runs the Fix Agent once per attempt,
validates output, generates PatchSet in Task 5, evaluates gates in Tasks 6-7, and transitions to AWAITING_APPROVAL
or APPLYING only after explicit auto-apply opt-in. Any pre-apply failure stores artifacts, marks FIX_FAILED, and
cleans/quarantines the worktree.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/contract/changes/test_fix_runtime.py backend/tests/integration/changes/test_fix_agent_isolation.py -v
git add backend
git commit -m "feat: run fix agents only in isolated workspaces"
~~~

---

### Task 5: Generate And Validate Snapshot-To-FixedTree PatchSets

**Files:**
- Create: <code>backend/src/codelens/changes/application/build_patch.py</code>
- Create: <code>backend/src/codelens/changes/application/patch_validation.py</code>
- Test: <code>backend/tests/integration/changes/test_patch_generation.py</code>
- Test: <code>backend/tests/unit/changes/test_patch_validation.py</code>

**Interfaces:**
- Consumes: owned Fix worktree, persisted snapshot tree OID, FixSelection, and policy limits.
- Produces: immutable <code>PatchSet(snapshot_tree_oid, fixed_tree_oid, patch_ref, patch_hash, changed_files, stats)</code>.

- [ ] **Step 1: Write the contamination regression test**

Create a Review Snapshot that already contains an unrelated dirty file. Let the Fix Agent change only an allowlisted target. Generate/apply the PatchSet to a clean copy of the Snapshot and assert the result equals the fixed worktree. Assert the PatchSet does not re-introduce the pre-existing dirty file as a Fix delta.

Cover add/delete/rename/mode/symlink/binary metadata, empty diff, disallowed path, and deterministic hash.

- [ ] **Step 2: Build fixed tree and diff tree-to-tree**

After the Agent, validate containment and stage the isolated Fix index, write <code>fixed_tree_oid</code>, then generate:

~~~text
git diff --binary --full-index --no-ext-diff --no-color <snapshot_tree_oid> <fixed_tree_oid>
git diff --name-status -z <snapshot_tree_oid> <fixed_tree_oid>
git diff --numstat -z <snapshot_tree_oid> <fixed_tree_oid>
~~~

The comparison must name both tree OIDs. A bare <code>git diff</code>, <code>git diff HEAD</code>, or diff against the mutable source workspace is prohibited.

- [ ] **Step 3: Validate immutable PatchSet structure**

Reject absolute/traversal/out-of-allowlist paths, unsafe symlink targets, unsupported binary auto-apply, too many files/lines/bytes, malformed patch headers, and tree/hash mismatch. Persist patch bytes through opaque Artifact storage and reparse from stored bytes before gates.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/integration/changes/test_patch_generation.py backend/tests/unit/changes/test_patch_validation.py -v
uv run --project backend mypy backend/src/codelens/changes
uv run --project backend ruff check backend
git add backend
git commit -m "feat: build snapshot based patch sets"
~~~

Expected: contamination regression and structural matrix pass.

---

### Task 6: Execute Required Validation And Secret Gates

**Files:**
- Create: <code>backend/src/codelens/changes/application/validation.py</code>
- Create: <code>backend/src/codelens/changes/infrastructure/secret_scan.py</code>
- Test: <code>backend/tests/unit/changes/test_secret_scan.py</code>
- Test: <code>backend/tests/integration/changes/test_validation_gates.py</code>

**Interfaces:**
- Consumes: PatchSet, isolated worktree, frozen command profile, Sandbox/Local executor.
- Produces: ordered ValidationGateResults with Artifact references.

- [ ] **Step 1: Write gate truth and failure tests**

Required test/lint/build/security gates must all pass for automatic apply. Non-zero disallowed exit, timeout, unavailable executor, truncated required output, or skipped required command fails closed. Optional gate error is visible but does not alone block manual approval.

- [ ] **Step 2: Implement built-in secret scan**

Scan only added patch lines and newly added binary metadata. Detect private-key headers, common high-entropy token prefixes, and values matching configured secret fingerprints without storing the matched secret. Emit path, line, rule ID, and redacted digest. A match produces FAILED <code>secret_scan</code>.

~~~python
@dataclass(frozen=True)
class SecretMatch:
    path: str
    line: int
    rule_id: str
    value_digest: str


class SecretScannerPort(Protocol):
    async def scan(self, patch: PatchSet) -> tuple[SecretMatch, ...]:
        raise NotImplementedError
~~~

The default scanner is deterministic and offline. An administrator may bind a stronger scanner command profile; its unavailable state still fails a required gate.

- [ ] **Step 3: Execute gates in stable order**

Order: structural, secret, configured lint, test, build, security, source fingerprint, 3-way check. Independent command gates may run under a separate configured semaphore; cancellation terminates them. Save summaries and output Artifact references, never raw full logs in events.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/changes/test_secret_scan.py backend/tests/integration/changes/test_validation_gates.py -v
git add backend
git commit -m "feat: execute fix validation and secret gates"
~~~

---

### Task 7: Apply PatchSets Idempotently To An Explicit Target

**Files:**
- Create: <code>backend/src/codelens/changes/application/apply.py</code>
- Create: <code>backend/src/codelens/changes/infrastructure/git_apply.py</code>
- Test: <code>backend/tests/integration/changes/test_patch_apply.py</code>
- Test: <code>backend/tests/unit/changes/test_apply_policy.py</code>

**Interfaces:**
- Consumes: PatchSet, all gate results, apply mode, explicit target workspace, expected fingerprint, journal, and target-lock registry.
- Produces: <code>ApplyResult</code> with before/after fingerprints or a stable conflict/recovery outcome.

- [ ] **Step 1: Write the manual-default and gate truth table**

Assert a new FixTask always enters AWAITING_APPROVAL unless its creation request explicitly opts into automatic apply. Flip every hard gate independently and assert automatic apply is denied with an exact reason. Human confirmation cannot bypass containment, symlink, PatchSet hash/tree, target fingerprint, or patch-check failure.

- [ ] **Step 2: Write real-Git target and idempotency tests**

Cover:

- explicit target realpath/root containment;
- unchanged exact fingerprint applies only allowlisted PatchSet files and does not commit;
- HEAD, index, tracked, untracked, or symlink change yields APPLY_CONFLICT before write;
- two requests for the same target/idempotency key return one result;
- different Fix tasks can run concurrently, while check+apply to one target is serialized;
- restart from CHECKED/APPLIED/ambiguous journal states follows Task 2;
- unexpected apply failure never invokes reset/clean/checkout/stash.

- [ ] **Step 3: Implement a short target critical section**

Normalize and authorize the target before locking. Under a lock keyed by target realpath hash:

1. reread PatchSet Artifact and verify hash/tree metadata;
2. compute current fingerprint and compare exactly with expected;
3. rerun all hard policy decisions;
4. run <code>git apply --check --recount &lt;patch-file&gt;</code>;
5. write CHECKED journal;
6. run <code>git apply --recount &lt;patch-file&gt;</code>;
7. capture/persist after fingerprint and APPLIED journal.

The first release does not use <code>--3way</code>; a fingerprint mismatch is a conflict, not an invitation to merge automatically. Release the target lock before emitting downstream verification review.

- [ ] **Step 4: Fail closed on ambiguous execution**

If apply returns unexpectedly after a successful check, capture current fingerprint/touched-path hashes, persist <code>APPLY_RECOVERY_REQUIRED</code>, and require inspection. Do not retry automatically or mutate the target to roll back.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/changes/test_apply_policy.py backend/tests/integration/changes/test_patch_apply.py -v
uv run --project backend mypy backend/src/codelens/changes
uv run --project backend ruff check backend
git add backend
git commit -m "feat: apply patch sets to explicit targets"
~~~

Expected: manual-default, fingerprint, idempotency, conflict, target-lock, and recovery tests pass.

---

### Task 8: Integrate Review-to-Fix And Direct Fix Mode

**Files:**
- Create: <code>backend/src/codelens/changes/application/create_fix.py</code>
- Modify: <code>backend/src/codelens/review/application/pipeline.py</code>
- Modify: <code>backend/src/codelens/bootstrap/components.py</code>
- Test: <code>backend/tests/integration/changes/test_create_fix.py</code>
- Test: <code>backend/tests/integration/changes/test_direct_fix_mode.py</code>

**Interfaces:**
- Consumes: ReviewReport and ReviewTask.mode.
- Produces: explicit selection FixTask or automatically created direct-mode FixTask.

- [ ] **Step 1: Write selection policy tests**

Explicit selection accepts only IDs in the persisted report, not suppressed or rejected findings. Default direct mode selects actionable findings with disposition blocking or severity high/critical, <code>change_origin=introduced</code>, and a valid target path. The user may narrow or widen severity before creation but cannot select pre-existing findings for automatic application.

- [ ] **Step 2: Derive the path allowlist**

Start with each selected finding primary target path. Related locations remain read-only context. User-added paths must exist in SnapshotManifest or be a new path under an explicitly selected directory; normalize and freeze them. The Agent cannot add paths later.

- [ ] **Step 3: Integrate direct mode**

When a ReviewTask has mode FIX, the normal review pipeline still produces a validated report first. If the default selection is non-empty, create exactly one FixTask transactionally and emit <code>fix.created</code>; if empty, complete the review with <code>fix.not_required</code>. A retry must not create a duplicate FixTask.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/integration/changes/test_create_fix.py backend/tests/integration/changes/test_direct_fix_mode.py -v
git add backend
git commit -m "feat: connect review reports to isolated fixes"
~~~

---

### Task 9: Expose Fix APIs And Build The Fix Workspace UI

**Files:**
- Create: <code>backend/src/codelens/interface/http/fixes.py</code>
- Modify: <code>backend/src/codelens/interface/http/app.py</code>
- Test: <code>backend/tests/contract/http/test_fixes_api.py</code>
- Create: <code>frontend/src/features/fixes/api.ts</code>
- Create: <code>frontend/src/features/fixes/CreateFixDialog.tsx</code>
- Create: <code>frontend/src/features/fixes/FixRunPage.tsx</code>
- Create: <code>frontend/src/features/fixes/PatchViewer.tsx</code>
- Create: <code>frontend/src/features/fixes/ValidationGatesPanel.tsx</code>
- Create: <code>frontend/src/features/fixes/ApplyPanel.tsx</code>
- Test: <code>frontend/src/features/fixes/CreateFixDialog.test.tsx</code>
- Test: <code>frontend/src/features/fixes/FixRunPage.test.tsx</code>
- Test: <code>frontend/src/features/fixes/PatchViewer.test.tsx</code>
- Test: <code>frontend/src/features/fixes/ValidationGatesPanel.test.tsx</code>
- Test: <code>frontend/src/features/fixes/ApplyPanel.test.tsx</code>

**Interfaces:**
- Produces:
  - <code>POST /api/reviews/{id}/fixes</code>
  - <code>GET /api/fixes/{id}</code>
  - <code>GET /api/fixes/{id}/patch</code>
  - <code>POST /api/fixes/{id}/apply</code>
  - <code>POST /api/fixes/{id}/cancel</code>

- [ ] **Step 1: Write API contract tests**

Creation validates idempotency key, finding IDs, allowed paths, and approval policy. Patch endpoint returns structured file metadata plus a short-lived Artifact download reference, never a filesystem path. Apply requires JSON content type and an idempotency key. Automatic apply cannot be forced by request when gates fail. Conflicts return 409 with stable code and stored task state.

- [ ] **Step 2: Write UI state tests**

Cover create selection, manual-default explanation, explicit auto-apply opt-in, fixing progress, PatchSet diff,
every gate state, auto-apply countdown/cancel, manual apply, conflict, failed cleanup/quarantine, duplicate apply,
and post-apply “review uncommitted changes” action. At mobile width, diff may horizontally scroll but actions and warnings remain visible.

- [ ] **Step 3: Implement safe diff rendering**

Render patch content as text through a mature diff component with HTML escaping. Binary changes show metadata only. Never interpolate Artifact path/contents into HTML. Long files use virtualization or bounded visible hunks.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/contract/http/test_fixes_api.py -v
pnpm --dir frontend test
pnpm --dir frontend build
git add backend frontend
git commit -m "feat: expose and display isolated fix workflows"
~~~

---

### Task 10: Add Fix Safety, Recovery, And Playwright Acceptance Gates

**Files:**
- Create: <code>backend/tests/acceptance/test_phase_5.py</code>
- Create: <code>frontend/e2e/fix-workflow.spec.ts</code>
- Modify: <code>README.md</code>

**Interfaces:**
- Consumes: complete Phase 5.
- Produces: deterministic safety and user-flow gate.

- [ ] **Step 1: Add real-repository acceptance matrix**

Use separate source, snapshot, isolated worktree, and verification clone directories. Assert:

- Review mode creates only scoped owned-worktree metadata and makes no user working-tree/index/ref writes;
- Fix Agent writes only isolated content;
- generated Snapshot-tree-to-fixed-tree patch reproduces the isolated result without pre-existing-change contamination;
- all hard gates pass before automatic apply;
- successful apply changes only allowlisted source files and does not commit;
- gate failure preserves PatchSet and source fingerprint;
- user edit between snapshot and apply yields APPLY_CONFLICT;
- Worker restart does not duplicate worktree, PatchSet, or apply;
- cancellation and cleanup do not affect source;
- quarantine retry handles cleanup failure.

- [ ] **Step 2: Add Playwright flow**

Create a review, select findings, start a FixTask, inspect patch/gates, manually apply, then start a new
uncommitted review. Add separate scenarios for explicit auto-apply opt-in, gate failure, conflict, cancellation,
and long patch at 1440x900 and 390x844.

- [ ] **Step 3: Run the full phase gate**

~~~bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test frontend/e2e/fix-workflow.spec.ts
~~~

- [ ] **Step 4: Commit**

~~~bash
git add README.md backend frontend
git commit -m "test: add phase five fix safety gate"
~~~

## Phase 5 Acceptance Checklist

- [ ] Fix selection references only persisted validated findings.
- [ ] Path allowlist is normalized, explicit, frozen, and enforced by file tools and PatchSet validation.
- [ ] Worktree content matches the ReviewSnapshot, including dirty/untracked/deleted state.
- [ ] Source repository is not mounted into the Agent sandbox.
- [ ] Fix Agent cannot apply, commit, push, or widen permissions.
- [ ] PatchSet compares persisted Snapshot tree to fixed tree and includes text, untracked, deleted, renamed, and binary metadata correctly.
- [ ] Containment, symlink, secret, required commands, size, tree/hash, fingerprint, and plain patch-check gates fail closed.
- [ ] Manual apply is the default; automatic apply requires explicit task opt-in and every hard gate.
- [ ] MCP side effects remain independently human-approved.
- [ ] Apply is repository-locked, idempotent, non-committing, and conflict-safe.
- [ ] No failure recovery uses reset, clean, checkout, or stash on the source repository.
- [ ] Worker restart, timeout, cancellation, quarantine, and duplicate requests are tested.
- [ ] API, SSE, desktop, mobile, patch, gate, conflict, and recovery states pass.

## Deferred To Later Plans

- Phase 6 enables Docker/Podman execution, upgrades SecretStore/redaction/Artifact retention, and packages the application.
- Phase 7 measures fix apply/test success and adds release thresholds.
