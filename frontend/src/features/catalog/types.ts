export type ReviewerCapabilityStatus = "ready" | "degraded" | "unavailable";

export interface ReviewerCatalogEntry {
  reference: string;
  agentId: string;
  version: number;
  dimensions: readonly string[];
  isPlannerEligible: boolean;
  isLegacy: boolean;
  capabilityStatus: ReviewerCapabilityStatus;
}
