import { api } from "../../shared/api/client";
import type {
  DirectoryListing,
  RepositoryCatalog,
  RepositoryInspectionResponse,
} from "./types";

export async function inspectRepository(path: string): Promise<RepositoryInspectionResponse> {
  return api<RepositoryInspectionResponse>("/repositories/inspect", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export async function getRepositoryCatalog(
  path: string,
  commitOffset = 0,
): Promise<RepositoryCatalog> {
  return api<RepositoryCatalog>("/repositories/catalog", {
    method: "POST",
    body: JSON.stringify({ path, commit_offset: commitOffset, commit_limit: 10 }),
  });
}

export async function browseDirectories(path: string | null): Promise<DirectoryListing> {
  return api<DirectoryListing>("/repositories/browse", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}
