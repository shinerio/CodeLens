import { api } from "../../shared/api/client";
import type { FindingRecord, FindingSourcePreview } from "../findings/types";
import type { CreateReviewRequest, ReviewResponse } from "./types";

export interface TranscriptEntry {
  sequence: number;
  kind: "lifecycle" | "prompt" | "model_output" | "tool_call" | "tool_result" | "skill_loaded" | "model_started" | "model_reasoning_delta" | "model_reasoning_completed" | "model_output_delta" | "model_output_completed" | "model_completed" | "model_raw_output";
  content: string;
  created_at: string;
  redacted: boolean;
  truncated: boolean;
  metadata: Record<string, string>;
}

export interface ToolUsageSummary {
  tool_name: string;
  call_count: number;
  result_count: number;
}

export interface AgentProcessSummary {
  agent: string;
  model_name: string | null;
  llm_call_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  tool_call_count: number;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface ReviewProcessReport {
  task_id: string;
  status: string;
  usage_is_complete: boolean;
  agent_run_count: number;
  llm_call_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  tool_call_count: number;
  tool_result_count: number;
  unmatched_tool_result_count: number;
  finding_count: number;
  transcript_entry_count: number;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  tools: ToolUsageSummary[];
  agents: AgentProcessSummary[];
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

export async function cancelReview(taskId: string): Promise<ReviewResponse> {
  return api<ReviewResponse>(`/reviews/${taskId}/cancel`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function retryReview(taskId: string): Promise<ReviewResponse> {
  return api<ReviewResponse>(`/reviews/${taskId}/retry`, {
    method: "POST",
    body: JSON.stringify({}),
  });
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

export async function getProcessReport(taskId: string): Promise<ReviewProcessReport> {
  return api<ReviewProcessReport>(`/reviews/${taskId}/process-report`);
}

export interface ExportResultResponse {
  plugin_id: string;
  task_id: string;
  success: boolean;
  output_path: string | null;
  error: string | null;
  exported_at: string;
}

export async function exportFindings(taskId: string, pluginId: string): Promise<ExportResultResponse> {
  return api<ExportResultResponse>(`/reviews/${taskId}/export`, {
    method: "POST",
    body: JSON.stringify({ plugin_id: pluginId }),
  });
}
