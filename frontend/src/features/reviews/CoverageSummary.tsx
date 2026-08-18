import type { ReviewCoverageProjection } from "./types";
import { useI18n } from "../../shared/i18n/i18n";

const GROUPS = ["planned", "completed", "failed", "omitted"] as const;
const GROUP_LABELS = {
  planned: "coverage.planned",
  completed: "coverage.completed",
  failed: "coverage.failed",
  omitted: "coverage.omitted",
} as const;

export function CoverageSummary({ coverage, status }: { coverage: ReviewCoverageProjection; status: string }) {
  const { t } = useI18n();
  const hasGaps = coverage.failed.length > 0 || coverage.omitted.length > 0;
  return (
    <section className={`coverage-summary${hasGaps ? " coverage-summary--degraded" : ""}`} aria-label={t("coverage.label")} role={status === "partial" ? "status" : undefined}>
      <header><div><span>{t("coverage.ledger")}</span><strong>{t(status === "partial" ? "coverage.partial" : "coverage.persisted")}</strong></div>{hasGaps ? <p>{t("coverage.gapNote")}</p> : null}</header>
      <div className="coverage-summary__grid">
        {GROUPS.map((group) => <div key={group}><span>{t(GROUP_LABELS[group])}</span><strong>{coverage[group].length}</strong><p>{coverage[group].join(" · ") || "—"}</p></div>)}
      </div>
    </section>
  );
}
