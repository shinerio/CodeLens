import { api } from "../../shared/api/client";
import type { FindingRecord, FindingSourcePreview } from "../findings/types";
import type { CreateReviewRequest, ReviewResponse } from "./types";

export interface TranscriptEntry {
  sequence: number;
  kind: "lifecycle" | "prompt" | "model_output" | "tool_call" | "tool_result" | "skill_loaded" | "model_started" | "model_reasoning_delta" | "model_reasoning_completed" | "model_output_delta" | "model_output_completed" | "model_completed";
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

export async function getFindingSource(taskId: string, findingId: string): Promise<FindingSourcePreview> {
  return api<FindingSourcePreview>(`/reviews/${taskId}/findings/${findingId}/source`);
}

export async function getTranscript(taskId: string): Promise<TranscriptEntry[]> {
  return api<TranscriptEntry[]>(`/reviews/${taskId}/transcript`);
}
