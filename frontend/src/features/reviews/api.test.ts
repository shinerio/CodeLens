import { expect, it } from "vitest";

import { parseReviewResponse, toCreateReviewRequest } from "./api";

const legacyReview = {
  task_id: "review_1",
  status: "completed",
  scope_type: "branch",
  base_oid: "a".repeat(40),
  head_oid: "b".repeat(40),
  base_ref: "main",
  target_ref: "feature",
  selected_agents: ["security:v1"],
  worktree_status: "pending" as const,
  repository_id: "repository-1",
  repository_realpath_hash: "c".repeat(64),
  git_common_dir_hash: "d".repeat(64),
  cancellation_requested: false,
  repository_name: "repo",
  created_at: "2026-08-01T00:00:00Z",
  finding_count: 0,
  external_context: null,
};

it("serializes a fixed deep strategy without legacy selected_agents", () => {
  expect(toCreateReviewRequest({
    repositoryPath: "/repo",
    scope: { type: "uncommitted" },
    strategy: {
      reviewerSelection: { mode: "fixed", reviewerVersions: ["security:v1"] },
      budgetProfile: "deep",
    },
    promptLocale: "en",
  })).toEqual({
    repository_path: "/repo",
    scope: { type: "uncommitted" },
    reviewer_selection: { mode: "fixed", reviewer_versions: ["security:v1"] },
    budget_profile: "deep",
    prompt_locale: "en",
  });
});

it("normalizes a historical response into an explicit v2 projection", () => {
  const parsed = parseReviewResponse(legacyReview);
  expect(parsed.selection_request).toEqual({
    mode: "fixed",
    reviewer_versions: ["security:v1"],
  });
  expect(parsed.coverage.completed).toEqual(["security:v1"]);
  expect(parsed.budget_profile).toBe("standard");
});

it("rejects an incomplete native v2 coverage projection", () => {
  expect(() => parseReviewResponse({
    ...legacyReview,
    selection_request: { mode: "adaptive" },
    coverage: {
      planned: [],
      completed: [],
      failed: [],
    },
  })).toThrow("Missing Review coverage field: omitted");
});
