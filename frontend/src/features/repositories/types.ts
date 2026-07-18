export type RepositoryInspectionResponse = {
  repository_id: string;
  repository_realpath_hash: string;
  git_common_dir_hash: string;
  display_path: string;
  head_oid: string;
  current_branch: string | null;
  is_dirty: boolean;
};

export type RepositoryBranch = {
  name: string;
  oid: string;
  is_current: boolean;
  is_remote: boolean;
};

export type RepositoryCommit = {
  oid: string;
  short_oid: string;
  author: string;
  message: string;
  committed_at: string;
};

export type RepositoryCatalog = {
  branches: RepositoryBranch[];
  commits: RepositoryCommit[];
  next_commit_offset: number | null;
};

export type DirectoryEntry = {
  name: string;
  path: string;
  is_git_repository: boolean;
};

export type DirectoryListing = {
  current_path: string | null;
  parent_path: string | null;
  roots: string[];
  directories: DirectoryEntry[];
  current_is_git_repository: boolean;
  is_truncated: boolean;
};
