import type { ReviewerCatalogEntry } from "../catalog/types";
import { useI18n } from "../../shared/i18n/i18n";
import type { ReviewStrategySnapshot } from "../reviews/types";
import { BudgetProfilePicker } from "./BudgetProfilePicker";
import { ReviewerPicker } from "./ReviewerPicker";
import { toggleFixedReviewer, updateSelectionMode, type StrategyValidationError } from "./model";
import "./ReviewStrategyEditor.css";

export interface ReviewStrategyEditorProps {
  value: ReviewStrategySnapshot;
  catalog: readonly ReviewerCatalogEntry[];
  isDisabled?: boolean;
  validationErrors?: readonly StrategyValidationError[];
  onChange: (value: ReviewStrategySnapshot) => void;
}

export function ReviewStrategyEditor({ value, catalog, isDisabled = false, validationErrors = [], onChange }: ReviewStrategyEditorProps) {
  const { t } = useI18n();
  return (
    <section className="strategy-editor" aria-label={t("strategy.title")}>
      <div className="strategy-mode" role="radiogroup" aria-label={t("strategy.selection")}>
        {(["fixed", "adaptive"] as const).map((mode) => (
          <label className={value.reviewerSelection.mode === mode ? "strategy-mode__option strategy-mode__option--active" : "strategy-mode__option"} key={mode}>
            <input
              checked={value.reviewerSelection.mode === mode}
              disabled={isDisabled}
              name="selection-strategy"
              type="radio"
              onChange={() => onChange(updateSelectionMode(value, mode))}
            />
            <span><strong>{t(mode === "fixed" ? "strategy.fixed" : "strategy.adaptive")}</strong><small>{t(mode === "fixed" ? "strategy.fixedNote" : "strategy.adaptiveNote")}</small></span>
          </label>
        ))}
      </div>
      {value.reviewerSelection.mode === "fixed" ? (
        <ReviewerPicker
          catalog={catalog}
          isDisabled={isDisabled}
          selected={value.reviewerSelection.reviewerVersions}
          onToggle={(reference) => onChange(toggleFixedReviewer(value, reference, catalog))}
        />
      ) : (
        <p className="strategy-adaptive-note">{t("strategy.adaptivePending")}</p>
      )}
      <BudgetProfilePicker
        isDisabled={isDisabled}
        value={value.budgetProfile}
        onChange={(budgetProfile) => onChange({ ...value, budgetProfile })}
      />
      {validationErrors.length > 0 ? (
        <div className="strategy-errors" role="alert">
          {validationErrors.map((error) => <p key={`${error.code}:${error.reviewerVersion ?? ""}`}>{error.code === "empty_fixed" ? t("strategy.emptyFixed") : t("strategy.unavailable", { reviewer: error.reviewerVersion ?? "Reviewer" })}</p>)}
        </div>
      ) : null}
    </section>
  );
}
