import { api } from "../../shared/api/client";
import type { FindingRecord } from "../findings/types";
import type { CreateReviewRequest, ReviewResponse } from "./types";

export async function getReview(taskId: string): Promise<ReviewResponse> {
  return api<ReviewResponse>(`/reviews/${taskId}`);
}

export async function createReview(request: CreateReviewRequest): Promise<ReviewResponse> {
  return api<ReviewResponse>("/reviews", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function listFindings(taskId: string): Promise<FindingRecord[]> {
  return api<FindingRecord[]>(`/reviews/${taskId}/findings`);
}
