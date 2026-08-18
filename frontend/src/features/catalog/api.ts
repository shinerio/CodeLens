import { api } from "../../shared/api/client";
import type { ReviewerCatalogEntry } from "./types";

type ReviewerCatalogDto = {
  reference: string;
  agent_id: string;
  version: number;
  dimensions: string[];
  planner_eligible: boolean;
  capability_readiness: "ready";
};

export async function listReviewerCatalog(): Promise<ReviewerCatalogEntry[]> {
  const entries = await api<ReviewerCatalogDto[]>("/reviewer-catalog");
  return entries.map((entry) => ({
    reference: entry.reference,
    agentId: entry.agent_id,
    version: entry.version,
    dimensions: entry.dimensions,
    isPlannerEligible: entry.planner_eligible,
    capabilityStatus: entry.capability_readiness,
  }));
}

export type ReviewerPrompt = { agent_id: string; version: number; locale: "en" | "zh-CN"; system_prompt: string; prompt: string; is_custom: boolean };
function reviewerPromptPath(agentId: string, version: number, locale: ReviewerPrompt["locale"]) {
  return `/reviewer-prompts/${encodeURIComponent(agentId)}?locale=${encodeURIComponent(locale)}&version=${version}`;
}

export function getReviewerPrompt(agentId: string, version: number, locale: ReviewerPrompt["locale"]) {
  return api<ReviewerPrompt>(reviewerPromptPath(agentId, version, locale));
}

export function updateReviewerPrompt(agentId: string, version: number, locale: ReviewerPrompt["locale"], prompt: string) {
  return api<ReviewerPrompt>(reviewerPromptPath(agentId, version, locale), { method: "PUT", body: JSON.stringify({ prompt }) });
}

export function resetReviewerPrompt(agentId: string, version: number, locale: ReviewerPrompt["locale"]) {
  return api<ReviewerPrompt>(reviewerPromptPath(agentId, version, locale), { method: "DELETE" });
}
