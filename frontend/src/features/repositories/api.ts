import { api } from "../../shared/api/client";
import type { RepositoryInspectionResponse } from "../reviews/types";

export async function inspectRepository(path: string): Promise<RepositoryInspectionResponse> {
  return api<RepositoryInspectionResponse>("/repositories/inspect", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}
