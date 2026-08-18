import type { ReviewStrategySnapshot } from "../reviews/types";
import { useI18n } from "../../shared/i18n/i18n";

export function ReviewStrategySummary({ strategy }: { strategy: ReviewStrategySnapshot }) {
  const { t } = useI18n();
  const selection = strategy.reviewerSelection;
  return (
    <div className="strategy-summary">
      <span className="strategy-summary__mode">{t(selection.mode === "adaptive" ? "strategy.adaptive" : "strategy.fixed")}</span>
      <span>
        {selection.mode === "adaptive"
          ? t("strategy.plannerSelects")
          : selection.reviewerVersions.join(" · ") || t("strategy.noReviewer")}
      </span>
    </div>
  );
}
