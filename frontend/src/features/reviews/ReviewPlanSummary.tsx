import type { ReviewPlanProjection, ReviewerSelection } from "./types";
import { useI18n } from "../../shared/i18n/i18n";

export function ReviewPlanSummary({ selection, plan, status }: { selection: ReviewerSelection; plan: ReviewPlanProjection | null; status: string }) {
  const { t } = useI18n();
  const reviewers = plan?.reviewer_references ?? (selection.mode === "fixed" ? selection.reviewer_versions : []);
  const isTerminalWithoutPlan = plan === null && ["completed", "partial", "failed", "canceled"].includes(status);
  const emptyPlanLabel = isTerminalWithoutPlan ? t("plan.missing") : t("plan.pendingPlanner");
  return (
    <section className="review-plan-summary" aria-label={t("plan.label")}>
      <div><span>{t("plan.strategy")}</span><strong>{t(selection.mode === "adaptive" ? "strategy.adaptive" : "strategy.fixed")}</strong></div>
      <div className="review-plan-summary__reviewers"><span>{t("plan.reviewers")}</span><strong>{reviewers.length === 0 ? emptyPlanLabel : reviewers.join(" · ")}</strong></div>
      {plan?.planner_reason != null ? <p>{plan.planner_reason}</p> : null}
      {plan !== null ? <code title={plan.plan_hash}>plan {plan.plan_hash.slice(0, 10)}</code> : !isTerminalWithoutPlan ? <span className="review-plan-summary__pending">{t("plan.pending")}</span> : null}
    </section>
  );
}
