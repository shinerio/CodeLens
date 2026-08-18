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
  const entries = catalog;
  return (
    <div className="reviewer-picker" role="group" aria-label={t("strategy.reviewers")}>
      {entries.map((entry) => {
        const isUnavailable = entry.capabilityStatus !== "ready";
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
