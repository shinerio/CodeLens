export type ReviewerCapabilityStatus = "ready" | "degraded" | "unavailable";

export type AgentRole = "planner" | "reviewer" | "verifier";

export interface ReviewerCatalogEntry {
  reference: string;
  agentId: string;
  version: number;
  dimensions: readonly string[];
  isPlannerEligible: boolean;
  capabilityStatus: ReviewerCapabilityStatus;
}

export interface AgentPromptCatalogEntry {
  reference: string;
  agentId: string;
  version: number;
  role: AgentRole;
  dimensions: readonly string[];
  capabilityStatus: ReviewerCapabilityStatus;
}
