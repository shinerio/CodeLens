import type { BudgetProfile } from "../reviews/types";
import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";

const BUDGETS: Array<{ value: BudgetProfile; label: TranslationKey; note: TranslationKey }> = [
  { value: "lean", label: "strategy.budgetLean", note: "strategy.budgetLeanNote" },
  { value: "standard", label: "strategy.budgetStandard", note: "strategy.budgetStandardNote" },
  { value: "deep", label: "strategy.budgetDeep", note: "strategy.budgetDeepNote" },
];

export function BudgetProfilePicker({
  value,
  isDisabled,
  onChange,
}: {
  value: BudgetProfile;
  isDisabled: boolean;
  onChange: (value: BudgetProfile) => void;
}) {
  const { t } = useI18n();
  return (
    <fieldset className="budget-picker">
      <legend>{t("strategy.budget")}</legend>
      {BUDGETS.map((budget) => (
        <label className="budget-choice" key={budget.value}>
          <input
            checked={value === budget.value}
            disabled={isDisabled}
            name="budget-profile"
            type="radio"
            onChange={() => onChange(budget.value)}
          />
          <span><strong>{t(budget.label)}</strong><small>{t(budget.note)}</small></span>
        </label>
      ))}
    </fieldset>
  );
}
