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

export type CreateReviewRequest = {
  repository_path: string;
  scope: ScopeRequest;
  selected_agents: string[];
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
};
