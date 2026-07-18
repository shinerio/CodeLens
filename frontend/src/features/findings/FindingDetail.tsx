import type { FindingRecord } from "./types";
import { useI18n } from "../../shared/i18n/i18n";

function formatLocation(finding: FindingRecord) {
  return `${finding.primary_location.path}:${finding.primary_location.start_line}-${finding.primary_location.end_line}`;
}

export function FindingDetail({ finding }: { finding: FindingRecord | null }) {
  const { t } = useI18n();
  if (finding === null) {
    return (
      <div className="finding-detail finding-detail--empty">
        {t("finding.select")}
      </div>
    );
  }

  return (
    <article className="finding-detail">
      <header className="finding-detail__header">
        <div>
          <p className="finding-detail__eyebrow">{finding.severity}</p>
          <h3>{finding.title}</h3>
        </div>
        <div className="finding-detail__meta">
          <span>{finding.reviewer_id}</span>
          <span>{formatLocation(finding)}</span>
        </div>
      </header>

      <dl className="finding-detail__facts">
        <div>
          <dt>{t("finding.impact")}</dt>
          <dd>{finding.impact}</dd>
        </div>
        <div>
          <dt>{t("finding.explanation")}</dt>
          <dd>{finding.explanation}</dd>
        </div>
        <div>
          <dt>{t("finding.recommendation")}</dt>
          <dd>{finding.recommendation}</dd>
        </div>
        {finding.reproduction !== null ? (
          <div>
            <dt>{t("finding.reproduction")}</dt>
            <dd>{finding.reproduction}</dd>
          </div>
        ) : null}
      </dl>

      <section className="finding-detail__section">
        <h4>{t("finding.evidence")}</h4>
        <ul>
          {finding.evidence.map((item, index) => (
            <li key={`${finding.finding_id}-evidence-${index}`}>
              <strong>{item.kind}</strong> {item.description}
              {item.excerpt_hash !== null ? <span> · {item.excerpt_hash}</span> : null}
            </li>
          ))}
        </ul>
      </section>

      <section className="finding-detail__section">
        <h4>{t("finding.ruleSources")}</h4>
        <ul>
          {finding.rule_sources.length > 0 ? (
            finding.rule_sources.map((rule) => (
              <li key={`${finding.finding_id}-${rule.path}`}>
                {rule.path} · {rule.content_hash}
              </li>
            ))
          ) : (
            <li>{t("finding.noRules")}</li>
          )}
        </ul>
      </section>
    </article>
  );
}
