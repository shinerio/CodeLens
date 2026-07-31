# Multi-Agent Review Frontend v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the desktop frontend for Review Profiles, Fixed/Adaptive execution, plugin policy snapshots, live multi-Agent progress, coverage, and published v2 Findings.

**Architecture:** A shared `review-strategy` feature owns typed policy editing and validation for both manual reviews and plugin configuration. Pages fetch versioned Reviewer Catalog/Profile DTOs and submit stable backend commands; they do not reproduce Adaptive selection, Capability resolution, clustering, or verification. Review execution renders persisted Plan and coverage projections after every refresh, using SSE only as an invalidation signal.

**Tech Stack:** React 19, TypeScript 6 strict, Vite 8, React Router, Vitest, Testing Library, Playwright 1.61, existing CodeLens CSS/i18n primitives.

## Global Constraints

- Run this plan after the persistent orchestration plan is green. Tasks unrelated to Plugins may run in parallel with Plugin API v2; Task 5 waits for Plugin API v2 Task 6.
- Reuse the current visual system and layout primitives. Do not introduce a component framework or restyle unrelated pages.
- Support desktop only with a minimum viewport of `1280x800`; do not add mobile, touch, or narrow-screen behavior.
- Fixed and Adaptive are mutually exclusive. Adaptive never renders a reviewer checklist.
- `general:v1` is exclusive: selecting it clears specialists; selecting a specialist clears General.
- Profile selection copies an editable snapshot into the form. Manual edits never mutate the saved Profile implicitly.
- Plugin forms persist a copied policy snapshot. Profile provenance is explanatory metadata and has no live execution semantics.
- The result page is Findings-first. It never publishes rejected/unresolved Candidate details or exposes raw Planner/Resolver/Verifier prompts.
- Comment v2 categorical confidence is not converted back to a numeric percentage.
- Loading, empty, failed, partial, canceled, superseded, long-text, and stale-SSE recovery states must be explicit.
- All user-visible strings must be available in English and Chinese through the existing i18n layer.
- Do not add evaluation, benchmark, rollout, or backend behavior in this phase.

## Task 1: Add the frontend v2 API contract layer

**Files:**

- Modify: `frontend/src/features/catalog/api.ts`
- Create: `frontend/src/features/catalog/types.ts`
- Modify: `frontend/src/features/reviews/types.ts`
- Modify: `frontend/src/features/reviews/api.ts`
- Create: `frontend/src/features/review-profiles/types.ts`
- Create: `frontend/src/features/review-profiles/api.ts`
- Create: `frontend/src/features/reviews/api.test.ts`
- Create: `frontend/src/features/review-profiles/api.test.ts`

- [ ] **Step 1: Write failing parsing and request-shape tests**

```ts
import { describe, expect, it } from "vitest";
import { parseReviewResponse, toCreateReviewRequest } from "./api";

describe("review API v2", () => {
  it("submits one discriminated reviewer selection", () => {
    expect(
      toCreateReviewRequest({
        repositoryId: "repo-1",
        strategy: {
          reviewerSelection: { mode: "adaptive" },
          budgetProfile: "deep",
        },
        promptLocale: "en",
      }),
    ).toEqual({
      repository_id: "repo-1",
      reviewer_selection: { mode: "adaptive" },
      budget_profile: "deep",
      prompt_locale: "en",
    });
  });

  it("parses a partial review with persisted coverage", () => {
    const result = parseReviewResponse(partialReviewFixture);
    expect(result.status).toBe("partial");
    expect(result.coverage.failedReviewerVersions).toEqual(["security:v1"]);
    expect(result.plan.nodes[0]?.logicalRole).toBe("reviewer");
  });
});
```

Add negative tests for an unknown strategy mode, an unknown plan-node role, a missing coverage field on v2 responses, and a malformed Comment v2 confidence enum. Use the existing API client's runtime parsing convention; if the client currently relies on typed `fetch` only, add small explicit type guards rather than a new validation dependency.

- [ ] **Step 2: Run focused tests and observe failure**

```bash
pnpm --dir frontend test -- src/features/reviews/api.test.ts src/features/review-profiles/api.test.ts
```

Expected: failures because v2 DTOs and Profile endpoints do not exist.

- [ ] **Step 3: Implement discriminated frontend contracts**

Use these canonical types:

```ts
export type BudgetProfile = "lean" | "standard" | "deep";

export interface ReviewerCatalogEntry {
  reference: string;
  role: "reviewer";
  dimensions: readonly string[];
  costClass: "low" | "medium" | "high";
  isPlannerEligible: boolean;
  isPublic: boolean;
  isLegacy: boolean;
  capabilityStatus: "ready" | "degraded" | "unavailable";
  unavailableReason: string | null;
}

export type ReviewerSelection =
  | {
      mode: "fixed";
      reviewerVersions: readonly string[];
    }
  | {
      mode: "adaptive";
    };

export interface ReviewStrategySnapshot {
  reviewerSelection: ReviewerSelection;
  budgetProfile: BudgetProfile;
}

export type ReviewTaskStatus =
  | "queued"
  | "planning"
  | "reviewing"
  | "resolving"
  | "verifying"
  | "completed"
  | "partial"
  | "failed"
  | "canceled"
  | "superseded";

export type PlanNodeRole = "planner" | "reviewer" | "resolver" | "verifier";

export interface ReviewCoverage {
  plannedReviewerVersions: readonly string[];
  completedReviewerVersions: readonly string[];
  failedReviewerVersions: readonly string[];
  omittedReviewerVersions: readonly string[];
}

export interface ReviewProfile {
  id: string;
  name: string;
  revision: number;
  isDefault: boolean;
  strategy: ReviewStrategySnapshot;
  createdAt: string;
  updatedAt: string;
}
```

Map snake_case only inside API adapters. Replace `selected_agents` in new create requests with `reviewer_selection`; retain parsing of the legacy response field only where the backend compatibility DTO still returns it. Keep `prompt_locale` as a task/plugin field outside `ReviewStrategySnapshot`. Add Profile list/get/create/update/copy/delete/set-default calls with revision-aware updates.

- [ ] **Step 4: Verify the contract layer and strict types**

```bash
pnpm --dir frontend test -- src/features/reviews/api.test.ts src/features/review-profiles/api.test.ts
pnpm --dir frontend build
```

Expected: both commands exit `0` without `any`, non-null assertions, or unchecked casts.

- [ ] **Step 5: Commit the frontend contracts**

```bash
git add frontend/src/features/catalog frontend/src/features/reviews frontend/src/features/review-profiles
git commit -m "feat: add frontend multi-agent review contracts"
```

## Task 2: Build the shared review-strategy editor

**Files:**

- Create: `frontend/src/features/review-strategy/ReviewStrategyEditor.tsx`
- Create: `frontend/src/features/review-strategy/ReviewStrategyEditor.css`
- Create: `frontend/src/features/review-strategy/ReviewerPicker.tsx`
- Create: `frontend/src/features/review-strategy/BudgetProfilePicker.tsx`
- Create: `frontend/src/features/review-strategy/ReviewStrategySummary.tsx`
- Create: `frontend/src/features/review-strategy/model.ts`
- Create: `frontend/src/features/review-strategy/ReviewStrategyEditor.test.tsx`

- [ ] **Step 1: Write failing interaction tests**

```tsx
describe("ReviewStrategyEditor", () => {
  it("hides reviewer selection in Adaptive mode", async () => {
    renderEditor(fixedStrategy(["security:v1"]));
    await userEvent.click(screen.getByRole("radio", { name: /adaptive/i }));
    expect(screen.queryByRole("group", { name: /reviewers/i })).not.toBeInTheDocument();
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ reviewerSelection: { mode: "adaptive" } }),
    );
  });

  it("keeps General exclusive", async () => {
    renderEditor(fixedStrategy(["security:v1"]));
    await userEvent.click(screen.getByRole("checkbox", { name: /general/i }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        reviewerSelection: { mode: "fixed", reviewerVersions: ["general:v1"] },
      }),
    );
  });

  it("does not offer legacy hidden reviewers", () => {
    renderEditor(fixedStrategy([]), catalogWithCorrectnessV1AndV2);
    expect(screen.queryByText("correctness:v1")).not.toBeInTheDocument();
    expect(screen.getByText("correctness:v2")).toBeInTheDocument();
  });
});
```

Also cover empty active Catalog, unavailable reviewer retained in an existing snapshot, long descriptions, keyboard navigation, and all three budget values.

- [ ] **Step 2: Run the editor test and observe failure**

```bash
pnpm --dir frontend test -- src/features/review-strategy/ReviewStrategyEditor.test.tsx
```

Expected: component import failures.

- [ ] **Step 3: Implement controlled shared components**

The editor contract is:

```ts
export interface ReviewStrategyEditorProps {
  value: ReviewStrategySnapshot;
  catalog: readonly ReviewerCatalogEntry[];
  isDisabled?: boolean;
  validationErrors?: readonly StrategyValidationError[];
  onChange: (value: ReviewStrategySnapshot) => void;
}

export function updateSelectionMode(
  strategy: ReviewStrategySnapshot,
  mode: ReviewerSelection["mode"],
): ReviewStrategySnapshot;

export function toggleFixedReviewer(
  strategy: ReviewStrategySnapshot,
  reviewerVersion: string,
): ReviewStrategySnapshot;
```

`updateSelectionMode(..., "adaptive")` discards the Fixed list. Returning to Fixed starts with an empty list rather than resurrecting hidden state. `toggleFixedReviewer` implements General exclusivity and preserves catalog order for deterministic payloads. Hidden legacy entries remain visible as unavailable badges only when already referenced by the loaded snapshot; they cannot be newly selected.

`BudgetProfilePicker` displays user labels Economy/经济、Standard/标准、Deep/深度 while submitting exact protocol values `lean`, `standard`, and `deep`. Adaptive copy explains that the Planner chooses within the budget; it does not render selected/suggested Reviewer controls before the Plan exists.

`ReviewStrategySummary` must be read-only and reusable on New Review, Profile, Plugin, and Result pages. It shows strategy, budget, and reviewer count/list. The containing task or plugin form renders Locale separately because Locale is not part of a Review Profile.

- [ ] **Step 4: Verify editor behavior**

```bash
pnpm --dir frontend test -- src/features/review-strategy/ReviewStrategyEditor.test.tsx
pnpm --dir frontend build
```

Expected: all pass with no horizontal overflow at component-container widths used by the desktop pages.

- [ ] **Step 5: Commit the shared editor**

```bash
git add frontend/src/features/review-strategy
git commit -m "feat: add shared review strategy editor"
```

## Task 3: Add Review Profile management

**Files:**

- Create: `frontend/src/features/review-profiles/ReviewProfilesPage.tsx`
- Create: `frontend/src/features/review-profiles/ReviewProfilesPage.css`
- Create: `frontend/src/features/review-profiles/ReviewProfileForm.tsx`
- Create: `frontend/src/features/review-profiles/ReviewProfilePicker.tsx`
- Create: `frontend/src/features/review-profiles/ReviewProfilesPage.test.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/features/settings/SettingsPage.tsx`
- Modify: `frontend/src/features/settings/SettingsPage.test.tsx`

- [ ] **Step 1: Write failing Profile workflow tests**

```tsx
it("creates an Adaptive Deep profile", async () => {
  renderProfilesPage();
  await userEvent.click(screen.getByRole("button", { name: /new profile/i }));
  await userEvent.type(screen.getByLabelText(/name/i), "High risk changes");
  await userEvent.click(screen.getByRole("radio", { name: /adaptive/i }));
  await userEvent.click(screen.getByRole("radio", { name: /deep/i }));
  await userEvent.click(screen.getByRole("button", { name: /save/i }));

  expect(createProfile).toHaveBeenCalledWith(
    expect.objectContaining({
      name: "High risk changes",
      strategy: expect.objectContaining({
        reviewerSelection: { mode: "adaptive" },
        budgetProfile: "deep",
      }),
    }),
  );
});

it("shows a revision conflict without discarding edits", async () => {
  updateProfile.mockRejectedValue(new ApiError(409, "revision_conflict"));
  renderProfilesPage();
  await editAndSaveProfile();
  expect(screen.getByRole("alert")).toHaveTextContent(/changed elsewhere/i);
  expect(screen.getByDisplayValue("My edited name")).toBeInTheDocument();
});

it("copies a Profile without changing the unique default", async () => {
  renderProfilesPage();
  await userEvent.click(screen.getByRole("button", { name: /duplicate/i }));
  await userEvent.type(screen.getByLabelText(/new profile name/i), "Balanced copy");
  await userEvent.click(screen.getByRole("button", { name: /create copy/i }));
  expect(copyProfile).toHaveBeenCalledWith(defaultProfile.id, "Balanced copy");
  expect(setDefaultProfile).not.toHaveBeenCalled();
});
```

Cover list loading/failure, the seeded-default invariant, create, edit, duplicate, explicit default switch, blocked default deletion, non-default delete confirmation, unavailable legacy reviewer, and long names. The page must never present an empty steady state because the backend always seeds one default Profile.

- [ ] **Step 2: Run Profile tests and observe failure**

```bash
pnpm --dir frontend test -- src/features/review-profiles/ReviewProfilesPage.test.tsx
```

Expected: page and route do not exist.

- [ ] **Step 3: Implement Profile-first management**

Add `/settings/review-profiles` to the router and link it from the existing Settings page rather than adding another primary navigation item. The page has a master list and desktop detail/editor panel. A form edits one `ReviewStrategySnapshot` through the shared editor, plus name and `isDefault`. `ReviewProfilePicker` is a controlled, reusable source selector for New Review and Plugin pages; it returns a Profile identity and never mutates or submits strategy values itself.

Save behavior:

- create sends no revision;
- update sends the last loaded revision;
- `409 revision_conflict` preserves local fields and offers explicit Reload or Cancel;
- duplicate asks for a new name and creates revision 1 with `isDefault=false`;
- switching default is explicit and atomic; the current default delete action is disabled with guidance to switch first;
- delete requires confirmation and affects no prior review/plugin snapshots;
- dirty navigation uses a page-owned confirm dialog registered through React Router's blocker API.

The default marker is first-class. The UI must display exactly one default and treat zero/multiple defaults in a successful API response as a contract error rather than guessing.

- [ ] **Step 4: Verify the Profile page**

```bash
pnpm --dir frontend test -- src/features/review-profiles/ReviewProfilesPage.test.tsx
pnpm --dir frontend build
```

Expected: all pass.

- [ ] **Step 5: Commit Profile management**

```bash
git add frontend/src/features/review-profiles frontend/src/features/settings frontend/src/main.tsx
git commit -m "feat: manage multi-agent review profiles"
```

## Task 4: Make New Review Profile-first with an inline snapshot editor

**Files:**

- Modify: `frontend/src/features/reviews/NewReviewPage.tsx`
- Modify: `frontend/src/features/reviews/NewReviewPage.css`
- Modify: `frontend/src/features/reviews/NewReviewPage.test.tsx`

- [ ] **Step 1: Replace the correctness toggle tests with v2 workflows**

```tsx
it("copies the selected Profile and submits without another reviewer step", async () => {
  renderNewReviewPage({ profiles: [securityProfile], catalog });
  await userEvent.selectOptions(screen.getByLabelText(/review profile/i), securityProfile.id);
  await userEvent.click(screen.getByRole("button", { name: /start review/i }));

  expect(createReview).toHaveBeenCalledWith(
    expect.objectContaining({
      reviewer_selection: {
        mode: "fixed",
        reviewer_versions: ["security:v1", "correctness:v2"],
      },
      budget_profile: "standard",
    }),
  );
});

it("marks inline edits as a custom snapshot without mutating the Profile", async () => {
  renderNewReviewPage({ profiles: [securityProfile], catalog });
  await userEvent.selectOptions(screen.getByLabelText(/review profile/i), securityProfile.id);
  await userEvent.click(screen.getByRole("button", { name: /customize/i }));
  await userEvent.click(screen.getByRole("radio", { name: /adaptive/i }));
  expect(screen.getByText(/customized for this review/i)).toBeInTheDocument();
  expect(updateProfile).not.toHaveBeenCalled();
});

it("selects the instance default and can save the draft as a new Profile", async () => {
  renderNewReviewPage({ profiles: [securityProfile, defaultAdaptiveProfile], catalog });
  expect(screen.getByLabelText(/review profile/i)).toHaveValue(defaultAdaptiveProfile.id);
  await userEvent.click(screen.getByRole("button", { name: /customize/i }));
  await userEvent.click(screen.getByRole("checkbox", { name: /save as new profile/i }));
  await userEvent.type(screen.getByLabelText(/new profile name/i), "Review once and reuse");
  await userEvent.click(screen.getByRole("button", { name: /start review/i }));
  expect(createProfile).toHaveBeenCalledWith(
    expect.objectContaining({ name: "Review once and reuse", isDefault: false }),
  );
});
```

Cover the backend-invariant violation of no Profiles, Catalog failure, invalid empty Fixed selection, unavailable snapshot reviewer, Profile-save failure before task creation, task-submit failure, duplicate submit prevention, and long repository labels.

- [ ] **Step 2: Run New Review tests and observe failure**

```bash
pnpm --dir frontend test -- src/features/reviews/NewReviewPage.test.tsx
```

Expected: old single correctness reviewer UI fails the new assertions.

- [ ] **Step 3: Implement the streamlined form**

The default view shows repository/scope, the instance-default Profile, compact `ReviewStrategySummary`, and Start Review. “Change or customize” expands the Profile selector and `ReviewStrategyEditor` in place. Selecting a Profile deep-copies its strategy into local state and records provenance for display only.

Submission sends one v2 strategy plus the current UI `prompt_locale`. There is no risk-analysis dialog, no “system suggested reviewers” confirmation, and no post-submit reviewer chooser. Adaptive mode submits immediately; Planner runs later in the worker. If “Save as new Profile” is selected, create the non-default Profile first and submit the returned ID/revision as provenance; if that save fails, keep the draft and do not create a task. Disable submit while pending and navigate to the review route only after the task response is received.

- [ ] **Step 4: Verify New Review behavior**

```bash
pnpm --dir frontend test -- src/features/reviews/NewReviewPage.test.tsx
pnpm --dir frontend build
```

Expected: all pass.

- [ ] **Step 5: Commit New Review v2**

```bash
git add frontend/src/features/reviews/NewReviewPage.tsx frontend/src/features/reviews/NewReviewPage.css frontend/src/features/reviews/NewReviewPage.test.tsx
git commit -m "feat: create reviews from profile snapshots"
```

## Task 5: Reuse the strategy editor for plugin trigger configuration

**Files:**

- Modify: `frontend/src/features/plugins/PluginsPage.tsx`
- Modify: `frontend/src/features/plugins/PluginsPage.css`
- Modify: `frontend/src/features/plugins/PluginsPage.test.tsx`
- Modify: `frontend/src/features/plugins/types.ts`
- Modify: `frontend/src/features/plugins/api.ts`
- Modify: `frontend/e2e/plugins.spec.ts`

- [ ] **Step 1: Write failing copied-snapshot tests**

```tsx
it("copies a Profile into plugin config", async () => {
  renderPluginsPage({ plugin: localHookV2, profiles: [adaptiveProfile], catalog });
  await userEvent.selectOptions(screen.getByLabelText(/review profile/i), adaptiveProfile.id);
  await userEvent.click(screen.getByRole("button", { name: /save configuration/i }));

  expect(updatePluginConfig).toHaveBeenCalledWith(
    "local-hook",
    expect.objectContaining({
      config: expect.objectContaining({
        reviewer_selection: { mode: "adaptive" },
        budget_profile: "deep",
      }),
      profile_source: {
        profile_id: adaptiveProfile.id,
        profile_name: adaptiveProfile.name,
        profile_revision: adaptiveProfile.revision,
      },
    }),
  );
});

it("does not silently follow later Profile edits", async () => {
  renderPluginsPage({ plugin: pluginWithCopiedProfileV1, profiles: [sameProfileRevision2], catalog });
  expect(screen.getByText(/profile has changed/i)).toBeInTheDocument();
  expect(screen.getByText(/using copied revision 1/i)).toBeInTheDocument();
  expect(updatePluginConfig).not.toHaveBeenCalled();
});
```

Cover explicit Reload from Profile, keeping copied config, v1 compatibility display, incompatible plugin API, General exclusivity, Adaptive mode, save errors, and arbitrary non-review schema fields continuing to render through the existing generic schema form.

- [ ] **Step 2: Run plugin tests and observe failure**

```bash
pnpm --dir frontend test -- src/features/plugins/PluginsPage.test.tsx
```

Expected: existing specialized `selected_agents` controls fail.

- [ ] **Step 3: Implement v2 strategy composition without breaking generic fields**

For Plugin API v2 manifests that declare the standard review-policy capability, render Profile selector + `ReviewStrategyEditor` + supersede policy. Continue rendering unrelated manifest fields with the generic schema form.

Persist:

```ts
export interface PluginReviewPolicyConfig {
  reviewerSelection: ReviewerSelection;
  budgetProfile: BudgetProfile;
  promptLocale: "en" | "zh-CN";
  supersedePolicy: "latest_snapshot" | "preserve_all";
  debounceSeconds: number;
}

export interface PluginProfileSource {
  profileId: string;
  profileName: string;
  profileRevision: number;
  copiedAt: string;
}
```

The API adapter maps `PluginReviewPolicyConfig` into manifest-owned `config` and maps `PluginProfileSource` into Core-owned `profile_source`; it never inserts provenance keys into the plugin config object. Profile drift is informational. Only an explicit “Reload from Profile” action overwrites the form snapshot; saving then persists the new copy. For API v1 plugins, show exact legacy reviewer versions and migration status without offering Adaptive fields the plugin cannot consume.

- [ ] **Step 4: Verify unit and desktop E2E behavior**

```bash
pnpm --dir frontend test -- src/features/plugins/PluginsPage.test.tsx
pnpm --dir frontend build
pnpm --dir frontend exec playwright test e2e/plugins.spec.ts --project=chromium
```

Expected: all pass at the configured `1280x800` desktop viewport.

- [ ] **Step 5: Commit plugin configuration v2**

```bash
git add frontend/src/features/plugins frontend/e2e/plugins.spec.ts
git commit -m "feat: configure plugin review policy snapshots"
```

## Task 6: Make the review result page Findings-first and Plan-aware

**Files:**

- Create: `frontend/src/features/reviews/ReviewPlanSummary.tsx`
- Create: `frontend/src/features/reviews/CoverageSummary.tsx`
- Create: `frontend/src/features/reviews/AgentRunTimeline.tsx`
- Modify: `frontend/src/features/reviews/ReviewRunPage.tsx`
- Modify: `frontend/src/features/reviews/ReviewRunPage.css`
- Modify: `frontend/src/features/reviews/ReviewRunPage.test.tsx`
- Modify: `frontend/src/features/findings/FindingList.tsx`
- Modify: `frontend/src/features/findings/FindingDetail.tsx`
- Modify: `frontend/src/features/findings/FindingDetail.test.tsx`

- [ ] **Step 1: Write failing Plan, partial, and Finding v2 tests**

```tsx
it("opens on published Findings and summarizes the frozen Plan", () => {
  renderReviewRunPage(completedAdaptiveReview);
  expect(screen.getByRole("tab", { name: /findings/i })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText(/adaptive/i)).toBeInTheDocument();
  expect(screen.getByText("security:v1")).toBeInTheDocument();
  expect(screen.getByText(/resolver completed/i)).toBeInTheDocument();
});

it("explains partial coverage without hiding published Findings", () => {
  renderReviewRunPage(partialReviewWithOneFailure);
  expect(screen.getByRole("status")).toHaveTextContent(/partial coverage/i);
  expect(screen.getByText(/performance:v1 failed/i)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: publishedFinding.title })).toBeInTheDocument();
});

it("renders categorical Comment v2 confidence without a percentage", () => {
  render(<FindingDetail finding={commentV2Finding} />);
  expect(screen.getByText(/evidence: strong/i)).toBeInTheDocument();
  expect(screen.queryByText(/%/)).not.toBeInTheDocument();
});
```

Add Planner failure, all-reviewers-failed, Resolver failure, Verifier rejected/unresolved summary counts, canceled, superseded, no Findings, long evidence/path, and v1 historical numeric-confidence cases.

- [ ] **Step 2: Run result-page tests and observe failure**

```bash
pnpm --dir frontend test -- \
  src/features/reviews/ReviewRunPage.test.tsx \
  src/features/findings/FindingDetail.test.tsx
```

Expected: current Agent-runs-first page and numeric confidence UI fail.

- [ ] **Step 3: Implement persisted projection rendering**

Page structure:

1. terminal/live status header and immutable strategy summary;
2. compact Review Plan summary with role/node states;
3. coverage banner, including failed/skipped reviewer versions;
4. tabs ordered Findings, Coverage, Execution, Logs, followed by Plugins when plugin panels exist;
5. Findings list/detail restricted to backend-published Findings.

`CoverageSummary` lists Planned, Completed, Failed, and Omitted reviewer versions and explains that planned omissions or unavailable optional capabilities are degradation, not unexpected `partial`. `AgentRunTimeline` groups physical attempts under one logical node and labels Planner, Reviewer, Resolver, and Verifier. It does not infer required nodes from received SSE events and never manufactures skipped Resolver/Verifier nodes for General or Fixed Single Specialist. On each relevant SSE notification, refetch the review projection through the existing bounded refresh mechanism; after reconnect, perform one unconditional refetch.

For Comment v2 render `evidence_strength`, `reproducibility`, `impact`, and `verification_state` labels. Render numeric confidence only when `finding.schemaVersion === "1.0"`.

- [ ] **Step 4: Verify result rendering**

```bash
pnpm --dir frontend test -- \
  src/features/reviews/ReviewRunPage.test.tsx \
  src/features/findings/FindingDetail.test.tsx
pnpm --dir frontend build
```

Expected: all pass.

- [ ] **Step 5: Commit the result experience**

```bash
git add frontend/src/features/reviews frontend/src/features/findings
git commit -m "feat: show multi-agent plan coverage and findings"
```

## Task 7: Complete i18n, desktop state coverage, and frontend gates

**Files:**

- Modify: `frontend/src/shared/i18n/i18n.tsx`
- Modify: `frontend/src/shared/i18n/i18n.test.tsx`
- Modify: `frontend/e2e/review-flow.spec.ts`
- Modify: `frontend/e2e/plugins.spec.ts`
- Create: `frontend/e2e/review-profiles.spec.ts`

- [ ] **Step 1: Write failing locale and end-to-end scenarios**

Add translation-key parity tests for all new Profile, strategy, budget, plan-role, coverage, supersede, compatibility, and categorical-confidence strings.

Add mocked-backend E2E scenarios at exactly `1280x800`:

- Fixed General/Lean manual review reaches a completed no-Findings state.
- Adaptive/Deep manual review shows planning, multi-reviewer progress, Resolver, conditional Verifier, and completed Findings.
- Partial review keeps one published Finding and names one failed reviewer.
- Planner failure and all-reviewers-failed states provide actionable retry/navigation affordances.
- Profile create/edit/conflict/delete flows retain form data correctly.
- Plugin copies a Profile, detects drift, and reloads only after explicit action.
- English and Chinese long labels, paths, evidence, and failure text do not overflow or cover controls.

- [ ] **Step 2: Run E2E tests and observe missing coverage**

```bash
pnpm --dir frontend test -- src/shared/i18n/i18n.test.tsx
pnpm --dir frontend exec playwright test \
  e2e/review-flow.spec.ts \
  e2e/plugins.spec.ts \
  e2e/review-profiles.spec.ts \
  --project=chromium
```

Expected: failures for missing translations/routes/scenarios before implementation.

- [ ] **Step 3: Add translations, stable fixtures, and overflow fixes**

Keep test fixtures at the HTTP/SSE boundary so E2E validates frontend behavior without real model, MCP, Skill, or network dependencies. Use wrapping, min-width, grid/flex overflow rules, and scroll regions within the existing desktop shell; do not add responsive breakpoints below 1280px.

For long paths/evidence, preserve full text through selectable wrapped content or the existing tooltip pattern. Do not truncate the only visible failure reason.

- [ ] **Step 4: Run the full frontend quality gate**

```bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
git diff --check
```

Expected: every command exits `0` at desktop configuration; no real backend/model/network is required.

- [ ] **Step 5: Commit the frontend v2 gate**

```bash
git add frontend
git commit -m "feat: complete multi-agent review frontend v2"
```
