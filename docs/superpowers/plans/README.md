# CodeLens Implementation Plan Index

The authoritative product and architecture contract is:

- [CodeLens local multi-agent review application design](../specs/2026-07-17-codelens-review-app-design.md)

Implementation is split into independently reviewable and testable phases:

1. [Phase 0-2: task-owned review worktree to single-agent vertical slice](2026-07-17-codelens-phase-0-2.md)
2. [Phase 3: durable multi-agent review and reporting](2026-07-17-codelens-phase-3-multi-agent-reporting.md)
3. [Phase 4: Skill, MCP, trust, commands, and context expansion](2026-07-17-codelens-phase-4-capabilities-context.md)
4. [Phase 5: isolated Fix workflow and safe PatchSet application](2026-07-17-codelens-phase-5-fix-workflow.md)
5. [Phase 6: deployment, sandbox, secrets, artifacts, and packaging](2026-07-17-codelens-phase-6-deployment-security.md)
6. [Phase 7: evaluation, comparison, release gates, and rollback](2026-07-17-codelens-phase-7-evaluation-release-gates.md)

## Execution Order

Each phase assumes the previous phase acceptance checklist and complete quality gate pass. Do not execute all plans as one large change. At each boundary:

1. Review the phase plan against the authoritative design.
2. Execute tasks with TDD and focused commits.
3. Run focused tests followed by the phase-wide gate.
4. Resolve failures and review the resulting architecture.
5. Record the accepted immutable contract before starting the next phase.

Default verification uses injected fakes, record/replay fixtures, real temporary Git repositories, real SQLite
databases, and local browser tests. Real OpenAI/MCP/network tests remain explicit opt-in, but a controlled live eval
is mandatory before activating a Prompt/model/context-policy change; replay fixtures are not a quality gate.

## Correctness-First Execution Contract

This contract supersedes any older task wording that mentions direct source-worktree review, multi-Worker lease,
`0.0.0.0 + auth=none`, or a Phase-5-only worktree introduction:

1. Every ReviewTask creates one detached, application-owned worktree at pinned `base_oid/head_oid` before Snapshot.
2. The same repository may run concurrent ReviewTasks for different features; each task has a different worktree.
3. Reviewers in one task run concurrently and read the same worktree through a read-only boundary.
4. Worktree add/remove/repair is the only per-repository short critical section; Agent execution is never under that lock.
5. Exactly one Worker may use a data directory. Restart recovery uses durable node/output checkpoints, not leases.
6. Raw Agent output is stored before validation; `SUCCEEDED` is committed atomically with validated Findings.
7. Fix patches compare frozen Snapshot to fixed workspace. Manual apply is the default.
8. The unauthenticated first release binds loopback only. Secret references, redaction, opaque Artifacts, containment,
   and sensitive-tracing defaults exist before the first external model/tool call.

## Stable Cross-Phase Boundaries

- <code>TaskWorktree</code>, <code>ReviewSnapshot</code>, and <code>SnapshotManifest</code> freeze the repository view.
- <code>AgentVersion</code>, capability grants, rules, model profiles, and evaluation configuration are immutable references.
- Python application code owns the outer DAG, concurrency, timeout, retry, cancellation, and recovery.
- OpenAI Agents SDK remains an infrastructure adapter for individual model-driven nodes.
- Reviewer output becomes trusted only after schema, path, location, hunk, and evidence validation.
- Review registers/removes only its own worktree metadata and never writes the user's working tree, index, or refs.
- Fix writes an isolated worktree and only trusted application code can apply a validated PatchSet.
- Secrets remain server-side and never enter persisted run context or user-visible payloads.
- Release activation changes immutable active pointers only after a passing comparison gate.

## Design Coverage

| Authoritative design area | Owning plan |
|---|---|
| Goals, DDD boundaries, repository inspection, scopes, task-owned worktree, ignore, snapshot, instructions | Phase 0-2 |
| Reviewer catalog, Agent/ModelProfile versions, deterministic multi-Agent DAG, Finding validation/verification/deduplication/synthesis, durable AgentRun, report, feedback capture | Phase 3 |
| ContextPlan, CodeGraph/provider priority, full-repository sharding, Skills, MCP, commands, capability grants, repository trust, result cache | Phase 4 |
| FixTask, isolated worktree, Fix Agent, PatchSet, validation gates, approval, conflict-safe apply | Phase 5 |
| foundational loopback/secret/redaction/artifact safety | Phase 0-2 |
| container/local sandbox hardening, SecretStore providers, retention UI, audit, packaging | Phase 6 |
| feedback-derived RuleProposal review, golden datasets, metrics, comparison, release thresholds, activation, rollback | Phase 7 |
| Error handling, cancellation, restart recovery, security tests, desktop/mobile states, quality gates | Every phase, with final aggregation in Phases 6-7 |

## Scope Note

The separate <code>TODO.md</code> request for manual text-selection review and learning user comments is intentionally outside these Phase 0-7 plans, per the current planning decision. It requires its own approved specification before implementation.
