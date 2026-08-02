import type { ReviewerCatalogEntry } from "../catalog/types";
import { useI18n } from "../../shared/i18n/i18n";

export function ReviewerPicker({
  catalog,
  selected,
  isDisabled,
  onToggle,
}: {
  catalog: readonly ReviewerCatalogEntry[];
  selected: readonly string[];
  isDisabled: boolean;
  onToggle: (reference: string) => void;
}) {
  const { t } = useI18n();
  const selectedSet = new Set(selected);
  const catalogReferences = new Set(catalog.map((entry) => entry.reference));
  const retainedLegacyEntries: ReviewerCatalogEntry[] = selected
    .filter((reference) => reference === "correctness:v1" && !catalogReferences.has(reference))
    .map((reference) => ({
      reference,
      agentId: "correctness",
      version: 1,
      dimensions: ["correctness"],
      costClass: "balanced",
      isPlannerEligible: false,
      isLegacy: true,
      capabilityStatus: "ready",
    }));
  const entries = [
    ...catalog.filter((entry) => !entry.isLegacy),
    ...catalog.filter((entry) => entry.isLegacy && selectedSet.has(entry.reference)),
    ...retainedLegacyEntries,
  ];
  return (
    <div className="reviewer-picker" role="group" aria-label={t("strategy.reviewers")}>
      {entries.map((entry) => {
        const isUnavailable = entry.capabilityStatus !== "ready" || entry.isLegacy;
        return (
          <label className={`reviewer-choice${isUnavailable ? " reviewer-choice--unavailable" : ""}`} key={entry.reference}>
            <input
              checked={selectedSet.has(entry.reference)}
              disabled={isDisabled || isUnavailable}
              type="checkbox"
              onChange={() => onToggle(entry.reference)}
            />
            <span className="reviewer-choice__identity">
              <strong>{entry.agentId.replaceAll("_", " ")}</strong>
              <code>{entry.reference}</code>
            </span>
            <span className="reviewer-choice__dimensions">{entry.dimensions.join(" · ")}</span>
            {isUnavailable ? <span className="reviewer-choice__badge">{t("strategy.retainedSnapshot")}</span> : null}
          </label>
        );
      })}
      {entries.length === 0 ? <p className="strategy-empty">{t("strategy.noReviewers")}</p> : null}
    </div>
  );
}
