import { api } from "../../shared/api/client";
import type { ReviewerCatalogEntry } from "./types";

type ReviewerCatalogDto = {
  reference: string;
  agent_id: string;
  version: number;
  dimensions: string[];
  cost_class: "balanced";
  planner_eligible: boolean;
  capability_readiness: "ready";
  is_legacy: boolean;
};

export async function listReviewerCatalog(): Promise<ReviewerCatalogEntry[]> {
  const entries = await api<ReviewerCatalogDto[]>("/reviewer-catalog");
  return entries.map((entry) => ({
    reference: entry.reference,
    agentId: entry.agent_id,
    version: entry.version,
    dimensions: entry.dimensions,
    costClass: entry.cost_class,
    isPlannerEligible: entry.planner_eligible,
    isLegacy: entry.is_legacy,
    capabilityStatus: entry.capability_readiness,
  }));
}

export type ReviewerPrompt = { agent_id: string; version: number; locale: "en" | "zh-CN"; system_prompt: string; prompt: string; is_custom: boolean };
export function getReviewerPrompt(locale: ReviewerPrompt["locale"]) { return api<ReviewerPrompt>(`/reviewer-prompts/correctness?locale=${encodeURIComponent(locale)}`); }
export function updateReviewerPrompt(locale: ReviewerPrompt["locale"], prompt: string) { return api<ReviewerPrompt>(`/reviewer-prompts/correctness?locale=${encodeURIComponent(locale)}`, { method: "PUT", body: JSON.stringify({ prompt }) }); }
export function resetReviewerPrompt(locale: ReviewerPrompt["locale"]) { return api<ReviewerPrompt>(`/reviewer-prompts/correctness?locale=${encodeURIComponent(locale)}`, { method: "DELETE" }); }
