export type BranchScopeRequest = {
  type: "branch";
  base_ref: string;
  target_ref: string;
  include_workspace_changes: boolean;
};

export type CommitScopeRequest = {
  type: "commit";
  base_commit: string;
  target_ref: string;
  include_workspace_changes: boolean;
};

export type UncommittedScopeRequest = {
  type: "uncommitted";
};

export type FullRepositoryScopeRequest = {
  type: "full";
  target_ref: string;
  include_workspace_changes: boolean;
};

export type ScopeRequest =
  | BranchScopeRequest
  | CommitScopeRequest
  | UncommittedScopeRequest
  | FullRepositoryScopeRequest;

export type ReviewerSelection =
  | { mode: "fixed"; reviewer_versions: string[] }
  | { mode: "adaptive" };

export type ReviewStrategySnapshot = {
  reviewerSelection:
    | { mode: "fixed"; reviewerVersions: readonly string[] }
    | { mode: "adaptive" };
};

export type ReviewProfileSourceRequest = {
  profile_id: string;
  revision: number;
};

export type CreateReviewRequest = {
  repository_path: string;
  scope: ScopeRequest;
  reviewer_selection: ReviewerSelection;
  profile_source?: ReviewProfileSourceRequest;
  prompt_locale: "en" | "zh-CN";
};

export type ReviewResponse = {
  task_id: string;
  status: string;
  scope_type: string;
  base_oid: string;
  head_oid: string;
  base_ref: string | null;
  target_ref: string | null;
  selected_agents: string[];
  worktree_status: "pending";
  repository_id: string;
  repository_realpath_hash: string;
  git_common_dir_hash: string;
  cancellation_requested: boolean;
  repository_name: string;
  created_at: string;
  finding_count: number;
  external_context: Record<string, unknown> | null;
  selection_request: ReviewerSelection;
  profile_source: ReviewProfileSourceRequest | null;
  review_plan: ReviewPlanProjection | null;
  coverage: ReviewCoverageProjection;
  resolution_summary: Record<"publish" | "suppress" | "verify", number>;
};

export type ReviewPlanNodeRole = "planner" | "reviewer" | "resolver" | "verifier";

export type ReviewPlanNodeProjection = {
  node_id: string;
  node_type: ReviewPlanNodeRole;
  agent_reference: string;
  depends_on: string[];
  pass_index: number;
  shard_id: string;
  logical_attempt_group: string;
  task_id: string;
};

export type ReviewPlanProjection = {
  selection_mode: "fixed" | "adaptive";
  reviewer_references: string[];
  plan_hash: string;
  nodes: ReviewPlanNodeProjection[];
  planner_reason: string | null;
};

export type ReviewCoverageProjection = Record<
  "planned" | "completed" | "failed" | "omitted",
  string[]
>;
