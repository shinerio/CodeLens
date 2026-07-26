import { api } from "../../shared/api/client";
import type {
  DirectoryListing,
  RecentRepository,
  RepositoryCatalog,
  RepositoryInspectionResponse,
} from "./types";

export async function listRecentRepositories(): Promise<RecentRepository[]> {
  return api<RecentRepository[]>("/repositories/recent");
}

export async function deleteRecentRepository(repositoryPath: string): Promise<void> {
  return api<void>("/repositories/recent", {
    method: "DELETE",
    body: JSON.stringify({ repository_path: repositoryPath }),
  });
}

export async function inspectRepository(path: string): Promise<RepositoryInspectionResponse> {
  return api<RepositoryInspectionResponse>("/repositories/inspect", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export async function getRepositoryCatalog(
  path: string,
  targetRef: string | null,
  commitOffset = 0,
): Promise<RepositoryCatalog> {
  return api<RepositoryCatalog>("/repositories/catalog", {
    method: "POST",
    body: JSON.stringify({
      path,
      target_ref: targetRef,
      commit_offset: commitOffset,
      commit_limit: 10,
    }),
  });
}

export async function browseDirectories(path: string | null): Promise<DirectoryListing> {
  return api<DirectoryListing>("/repositories/browse", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}
