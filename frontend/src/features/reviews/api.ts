import { api } from "../../shared/api/client";
import type { FindingRecord } from "../findings/types";
import type { CreateReviewRequest, ReviewResponse } from "./types";

export interface TranscriptEntry {
  sequence: number;
  kind: "lifecycle" | "prompt" | "model_output" | "tool_call" | "tool_result" | "skill_loaded";
  content: string;
  created_at: string;
  redacted: boolean;
  truncated: boolean;
  metadata: Record<string, string>;
}

export async function getReview(taskId: string): Promise<ReviewResponse> {
  return api<ReviewResponse>(`/reviews/${taskId}`);
}

export async function createReview(request: CreateReviewRequest): Promise<ReviewResponse> {
  return api<ReviewResponse>("/reviews", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function listReviews(): Promise<ReviewResponse[]> {
  return api<ReviewResponse[]>("/reviews");
}

export async function deleteReview(taskId: string): Promise<void> {
  return api<void>(`/reviews/${taskId}`, { method: "DELETE" });
}

export async function listFindings(taskId: string): Promise<FindingRecord[]> {
  return api<FindingRecord[]>(`/reviews/${taskId}/findings`);
}

export async function getTranscript(taskId: string): Promise<TranscriptEntry[]> {
  return api<TranscriptEntry[]>(`/reviews/${taskId}/transcript`);
}
