import { expect, it } from "vitest";

import { parseReviewResponse, toCreateReviewRequest } from "./api";

const review = {
  task_id: "review_1",
  status: "completed",
  scope_type: "branch",
  base_oid: "a".repeat(40),
  head_oid: "b".repeat(40),
  base_ref: "main",
  target_ref: "feature",
  selected_agents: ["security:v2"],
  worktree_status: "pending" as const,
  repository_id: "repository-1",
  repository_realpath_hash: "c".repeat(64),
  git_common_dir_hash: "d".repeat(64),
  cancellation_requested: false,
  repository_name: "repo",
  created_at: "2026-08-01T00:00:00Z",
  finding_count: 0,
  external_context: null,
  selection_request: { mode: "fixed" as const, reviewer_versions: ["security:v2"] },
  profile_source: null,
  review_plan: null,
  coverage: {
    planned: [],
    completed: ["security:v2"],
    failed: [],
    omitted: [],
  },
  verdict_summary: { accept: 0, deny: 0, merge: 0 },
};

it("serializes a fixed strategy without legacy selected_agents", () => {
  expect(toCreateReviewRequest({
    repositoryPath: "/repo",
    scope: { type: "uncommitted" },
    strategy: {
      reviewerSelection: { mode: "fixed", reviewerVersions: ["security:v2"] },
    },
    promptLocale: "en",
  })).toEqual({
    repository_path: "/repo",
    scope: { type: "uncommitted" },
    reviewer_selection: { mode: "fixed", reviewer_versions: ["security:v2"] },
    prompt_locale: "en",
  });
});

it("parses a complete v2 response", () => {
  const parsed = parseReviewResponse(review);
  expect(parsed.selection_request).toEqual({
    mode: "fixed",
    reviewer_versions: ["security:v2"],
  });
  expect(parsed.coverage.completed).toEqual(["security:v2"]);
});

it("rejects an incomplete native v2 coverage projection", () => {
  const incomplete = {
    ...review,
    selection_request: { mode: "adaptive" },
    coverage: {
      planned: [],
      completed: [],
      failed: [],
    },
  } as unknown as typeof review;
  expect(() => parseReviewResponse(incomplete)).toThrow(
    "Missing Review coverage field: omitted",
  );
});
