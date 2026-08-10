import type { ReviewerCatalogEntry } from "../catalog/types";
import type { ReviewStrategySnapshot } from "../reviews/types";

export type StrategyValidationError = {
  code: "empty_fixed" | "unavailable_reviewer";
  reviewerVersion?: string;
};

export function updateSelectionMode(
  strategy: ReviewStrategySnapshot,
  mode: "fixed" | "adaptive",
): ReviewStrategySnapshot {
  return {
    ...strategy,
    reviewerSelection: mode === "adaptive" ? { mode } : { mode, reviewerVersions: [] },
  };
}

export function toggleFixedReviewer(
  strategy: ReviewStrategySnapshot,
  reviewerVersion: string,
  catalog: readonly ReviewerCatalogEntry[],
): ReviewStrategySnapshot {
  const current =
    strategy.reviewerSelection.mode === "fixed"
      ? [...strategy.reviewerSelection.reviewerVersions]
      : [];
  const without = current.filter((reference) => reference !== reviewerVersion);
  if (without.length !== current.length) {
    return { ...strategy, reviewerSelection: { mode: "fixed", reviewerVersions: without } };
  }
  const next =
    reviewerVersion === "general:v2"
      ? [reviewerVersion]
      : [...without.filter((reference) => reference !== "general:v2"), reviewerVersion];
  const order = new Map(catalog.map((entry, index) => [entry.reference, index]));
  next.sort((left, right) => (order.get(left) ?? Number.MAX_SAFE_INTEGER) - (order.get(right) ?? Number.MAX_SAFE_INTEGER));
  return { ...strategy, reviewerSelection: { mode: "fixed", reviewerVersions: next } };
}

export function validateStrategy(
  strategy: ReviewStrategySnapshot,
  catalog: readonly ReviewerCatalogEntry[],
): StrategyValidationError[] {
  if (strategy.reviewerSelection.mode === "adaptive") {
    return [];
  }
  if (strategy.reviewerSelection.reviewerVersions.length === 0) {
    return [{ code: "empty_fixed" }];
  }
  const ready = new Set(
    catalog
      // Legacy versions cannot be newly selected, but an existing Profile or
      // plugin snapshot must remain executable and visible during migration.
      .filter((entry) => entry.capabilityStatus === "ready")
      .map((entry) => entry.reference),
  );
  ready.add("correctness:v2");
  return strategy.reviewerSelection.reviewerVersions
    .filter((reference) => !ready.has(reference))
    .map((reviewerVersion) => ({ code: "unavailable_reviewer", reviewerVersion }));
}
