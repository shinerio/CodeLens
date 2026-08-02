export type ReviewerCapabilityStatus = "ready" | "degraded" | "unavailable";

export interface ReviewerCatalogEntry {
  reference: string;
  agentId: string;
  version: number;
  dimensions: readonly string[];
  costClass: "balanced";
  isPlannerEligible: boolean;
  isLegacy: boolean;
  capabilityStatus: ReviewerCapabilityStatus;
}
