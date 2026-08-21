import { api } from "../../shared/api/client";
import type { AgentPromptCatalogEntry, ReviewerCatalogEntry } from "./types";

type ReviewerCatalogDto = {
  reference: string;
  agent_id: string;
  version: number;
  dimensions: string[];
  planner_eligible: boolean;
  capability_readiness: "ready";
};

type AgentPromptCatalogDto = {
  reference: string;
  agent_id: string;
  version: number;
  role: "planner" | "reviewer" | "verifier" | "deduplicator" | "remediator";
  dimensions: string[];
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

export async function listAgentPromptCatalog(): Promise<AgentPromptCatalogEntry[]> {
  const entries = await api<AgentPromptCatalogDto[]>("/agent-prompts");
  return entries.map((entry) => ({
    reference: entry.reference,
    agentId: entry.agent_id,
    version: entry.version,
    role: entry.role,
    dimensions: entry.dimensions,
    capabilityStatus: entry.capability_readiness,
  }));
}

export type AgentPrompt = { agent_id: string; version: number; locale: "en" | "zh-CN"; system_prompt: string; prompt: string; is_custom: boolean };

function agentPromptPath(agentId: string, version: number, locale: AgentPrompt["locale"]) {
  return `/agent-prompts/${encodeURIComponent(agentId)}?locale=${encodeURIComponent(locale)}&version=${version}`;
}

export function getAgentPrompt(agentId: string, version: number, locale: AgentPrompt["locale"]) {
  return api<AgentPrompt>(agentPromptPath(agentId, version, locale));
}

export function updateAgentPrompt(agentId: string, version: number, locale: AgentPrompt["locale"], prompt: string) {
  return api<AgentPrompt>(agentPromptPath(agentId, version, locale), { method: "PUT", body: JSON.stringify({ prompt }) });
}

export function resetAgentPrompt(agentId: string, version: number, locale: AgentPrompt["locale"]) {
  return api<AgentPrompt>(agentPromptPath(agentId, version, locale), { method: "DELETE" });
}
