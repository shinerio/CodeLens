import { api } from "../../shared/api/client";

export type ReviewerPrompt = { agent_id: string; version: number; locale: "en" | "zh-CN"; system_prompt: string; prompt: string; is_custom: boolean };
export function getReviewerPrompt(locale: ReviewerPrompt["locale"]) { return api<ReviewerPrompt>(`/reviewer-prompts/correctness?locale=${encodeURIComponent(locale)}`); }
export function updateReviewerPrompt(locale: ReviewerPrompt["locale"], prompt: string) { return api<ReviewerPrompt>(`/reviewer-prompts/correctness?locale=${encodeURIComponent(locale)}`, { method: "PUT", body: JSON.stringify({ prompt }) }); }
export function resetReviewerPrompt(locale: ReviewerPrompt["locale"]) { return api<ReviewerPrompt>(`/reviewer-prompts/correctness?locale=${encodeURIComponent(locale)}`, { method: "DELETE" }); }
