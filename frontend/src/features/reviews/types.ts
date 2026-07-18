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

export type ReviewMode = "review" | "fix";

export type CreateReviewRequest = {
  repository_path: string;
  scope: ScopeRequest;
  selected_agents: string[];
  mode: ReviewMode;
};

export type ReviewResponse = {
  task_id: string;
  status: string;
  scope_type: string;
  base_oid: string;
  head_oid: string;
  selected_agents: string[];
  worktree_status: "pending";
  repository_id: string;
  repository_realpath_hash: string;
  git_common_dir_hash: string;
  cancellation_requested: boolean;
  repository_name: string;
  created_at: string;
};
