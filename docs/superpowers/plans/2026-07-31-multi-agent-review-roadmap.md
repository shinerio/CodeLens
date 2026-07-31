# Multi-Agent Review Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved multi-Agent review design through five dependency-ordered, independently testable implementation plans.

**Architecture:** Preserve the current CodeLens DDD boundaries and evolve the single-Agent pipeline incrementally. Domain contracts land first, Capability resolution then replaces hard-coded tools without changing legacy behavior, the persisted fan-out/fan-in DAG follows, and plugin/frontend consumers move only after the backend contracts are stable.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite WAL, OpenAI Agents SDK, React 19, TypeScript 6 strict mode, TanStack Query, Vitest, Playwright.

## Global Constraints

- Treat [`docs/superpowers/specs/2026-07-31-multi-agent-review-design.md`](../specs/2026-07-31-multi-agent-review-design.md) as the approved behavior contract.
- Do not add model evaluation, benchmark, shadow-traffic, rollout-metric, or quality-scoring work; those are explicitly outside this phase.
- Preserve `correctness:v1` as an immutable Legacy Single Reviewer path using Comment v1; never auto-upgrade existing tasks, profiles, or plugin configuration.
- New Fixed and Adaptive configuration uses Comment v2-capable reviewer versions; `general:v1` is always mutually exclusive with specialists.
- Fixed means the LLM never changes the configured reviewers; Adaptive means the user and plugin never edit Planner output.
- Python remains `>=3.12,<3.13`, fully typed, Ruff-clean, and mypy-strict. I/O remains asynchronous; external processes use argument arrays, bounded output, explicit accepted exit codes, and timeouts.
- TypeScript remains strict with no unjustified `any`, non-null assertions, or unchecked casts.
- The source repository and task Snapshot remain read-only to every Agent. No new shell, write, arbitrary Git, network, or dynamic-tool capability may become model-visible.
- MCP and Skill work in this roadmap defines versioned contracts and frozen policy only; it does not connect a real MCP server or execute a Skill script.
- HTTP/JSON fields use `snake_case`; status and enum values use lowercase `snake_case`; SSE events are persisted, past-tense, and versioned when payload compatibility changes.
- Update `docs/ARCHITECTURE.md` in the phase that makes each new boundary or stable contract real.
- Use TDD for every behavior change and commit after every task-level green gate.
- Frontend support remains desktop-only at a minimum `1280x800` viewport.

---

## Delivery Sequence

```text
Phase 1: Domain contracts and reviewer catalog
                   |
                   v
Phase 2: Capability resolution and runtime gateway
                   |
                   v
Phase 3: Persistent multi-Agent orchestration and Finding pipeline
                   |
          +--------+--------+
          |                 |
          v                 v
Phase 4: Plugin API v2   Phase 5: Frontend v2
          |                 |
          +--------+--------+
                   v
             Full repository gate
```

Phase 4 and the non-plugin parts of Phase 5 may run in parallel only after Phase 3 publishes its final HTTP/SSE and application Port contracts. Frontend Task 5 (plugin configuration) starts after Plugin API v2 Task 6 publishes its HTTP DTOs. They must not guess DTOs from the design document.

## Plan Index

1. [Domain foundation](./2026-07-31-multi-agent-review-domain-foundation.md)
   - Reviewer selection values and Review Plan invariants
   - Versioned built-in catalog and prompt identities
   - Comment v2 Candidate contract alongside immutable Comment v1
   - Resolver/Verifier decision types without runtime wiring

2. [Capability runtime](./2026-07-31-multi-agent-review-capability-runtime.md)
   - Versioned Capability Profile and Skill Policy
   - Role-specific built-in tool allowlists
   - Frozen execution specification and OpenAI adapter migration
   - Declarative MCP/Skill bindings without live integrations

3. [Persistent orchestration](./2026-07-31-multi-agent-review-orchestration.md)
   - Review Profile CRUD and selection snapshots
   - Adaptive Planner and immutable Review Plan
   - Restart-safe multi-pass DAG with bounded fan-out/fan-in
   - Candidate validation, clustering, Resolver, conditional Verifier, publication
   - Partial failure, retry, cancel, recovery, Coverage, SSE, and query projections

4. [Plugin API v2](./2026-07-31-plugin-api-v2.md)
   - Manifest/API version enforcement and recoverable updates
   - `TriggerReviewPolicy` and `ReviewCreatorPort` v2
   - Core-owned v1 configuration migration
   - `latest_snapshot`/`preserve_all` semantics
   - `FindingExportEnvelope` 2.0

5. [Frontend v2](./2026-07-31-multi-agent-review-frontend.md)
   - Profile management
   - Profile-first create flow with inline editing
   - Shared plugin Review Strategy editor and copied snapshot provenance
   - Findings-first Review result page with Plan, Coverage, Execution, and Logs

## Integration Rules

- Phase 1 adds internal contracts but does not expose a user-selectable reviewer that the Worker cannot run.
- Phase 2 migrates `correctness:v1` through the Capability gateway first; its contract tests must prove the visible tool set and output remain unchanged.
- Phase 3 owns all database migrations for Review/Profile/DAG/Finding state. Plugin JSON persistence remains owned by the plugin context.
- Phase 3 exposes the new creation/query contracts only after Fixed, Adaptive, General, partial failure, restart recovery, and legacy adaptation tests are green.
- Phase 4 consumes Phase 3 through `ReviewCreatorPort`; it must not import `review.infrastructure` or persistence rows.
- Phase 5 consumes only validated HTTP/SSE DTOs. It must not reimplement General exclusivity or Reviewer Catalog legality as the sole authority.
- Phase 5 Tasks 1–4 and 6–7 may use Phase 3 mock contracts while Phase 4 runs; Phase 5 Task 5 and the final Plugins Playwright scenario wait for Phase 4 Task 6.
- No phase may delete compatibility fields until all internal callers and the plugin/frontend v2 consumers have migrated and explicit legacy tests remain green.

## Approved-Spec Coverage Map

| Approved design sections | Owning implementation tasks |
| --- | --- |
| 6–8: Fixed/Adaptive, Catalog, Correctness v2, ReviewPlan/Planner | Domain Tasks 1–3/5; Orchestration Task 4 |
| 9: persisted DAG, identity, status, bounded concurrency | Orchestration Tasks 3/5/8 |
| 10–11: Comment v2, Candidate, clustering, Resolver/Verifier, General | Domain Task 4/5; Orchestration Tasks 6/7 |
| 12: built-in tools and Capability Profile | Capability Tasks 1–3/5 |
| 13–15: MCP/Skill boundary and frozen execution | Capability Tasks 1/4/5; Orchestration Tasks 2–3 |
| 16: budget and shared limits | Capability Tasks 1/3; Orchestration Tasks 4–5 |
| 17: Profiles, API, frontend, automatic plugins | Orchestration Tasks 1–2/8; Plugin Tasks 2–4/6; Frontend Tasks 1–6 |
| 18–19: SSE, Transcript, failure/retry/cancel/recovery | Orchestration Tasks 3/5/7/8; Frontend Tasks 6–7 |
| 20–21: ownership and compatibility migration | All backend architecture gates; Plugin Tasks 1/3/5 |
| 22–23: validation requirements and final invariants | Every task's focused gate plus roadmap checkpoints 1–6 |

Sections 2–5 explain motivation, scope, industry alternatives, and the selected architecture; they introduce no separate implementation task. Evaluation work remains excluded by section 3.2 and the Global Constraints above.

## Repository Gates

Run the focused command in every task first. At the end of each backend phase run:

```bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
```

At the end of the frontend phase run:

```bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

After Phase 5, run both backend and frontend gates from a clean worktree. Real model, remote MCP, and network tests remain opt-in and are not part of the default gate.

## Execution Checkpoints

- [ ] **Checkpoint 1:** Phase 1 is merged; legacy tests still pass and no new reviewer is exposed through HTTP.
- [ ] **Checkpoint 2:** Phase 2 is merged; `correctness:v1` runs through a frozen Capability Profile with exactly the legacy seven visible tools.
- [ ] **Checkpoint 3:** Phase 3 is merged; backend Fixed, Adaptive, General, partial failure, retry, cancel, and restart integration tests pass.
- [ ] **Checkpoint 4:** Phase 4 is merged; v1 plugin configurations migrate without reviewer-version changes and v2 automatic triggers need no user interaction.
- [ ] **Checkpoint 5:** Phase 5 is merged; all four approved desktop flows pass component and Playwright coverage.
- [ ] **Checkpoint 6:** `docs/ARCHITECTURE.md`, `docs/plugin-upgradev2.md`, API examples, and implementation behavior agree; the full repository gates pass.
