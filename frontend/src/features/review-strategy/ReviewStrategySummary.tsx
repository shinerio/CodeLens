import type { ReviewStrategySnapshot } from "../reviews/types";
import { useI18n } from "../../shared/i18n/i18n";

const BUDGET_LABELS = {
  lean: "strategy.budgetLean",
  standard: "strategy.budgetStandard",
  deep: "strategy.budgetDeep",
} as const;

export function ReviewStrategySummary({ strategy }: { strategy: ReviewStrategySnapshot }) {
  const { t } = useI18n();
  const selection = strategy.reviewerSelection;
  return (
    <div className="strategy-summary">
      <span className="strategy-summary__mode">{t(selection.mode === "adaptive" ? "strategy.adaptive" : "strategy.fixed")}</span>
      <strong>{t(BUDGET_LABELS[strategy.budgetProfile])}</strong>
      <span>
        {selection.mode === "adaptive"
          ? t("strategy.plannerSelects")
          : selection.reviewerVersions.join(" · ") || t("strategy.noReviewer")}
      </span>
    </div>
  );
}
