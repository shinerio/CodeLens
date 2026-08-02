import type { FindingRecord } from "./types";
import { useI18n } from "../../shared/i18n/i18n";

function formatLocation(finding: FindingRecord) {
  return `${finding.primary_location.path}:${finding.primary_location.start_line}-${finding.primary_location.end_line}`;
}

function formatSeverity(value: string) {
  return value.replaceAll("_", " ");
}

export function FindingList({
  findings,
  selectedFindingId,
  onSelect,
}: {
  findings: FindingRecord[];
  selectedFindingId: string | null;
  onSelect: (findingId: string) => void;
}) {
  const { t } = useI18n();
  if (findings.length === 0) {
    return <p className="finding-list__empty">{t("finding.none")}</p>;
  }

  return (
    <nav aria-label={t("finding.navigation")}>
      <ul className="finding-list" aria-label={t("finding.list")}>
        {findings.map((finding) => {
          const isSelected = finding.finding_id === selectedFindingId;
          const itemClassName = [
            "finding-list__item",
            `finding-list__item--${finding.severity}`,
            isSelected ? "finding-list__item--active" : "",
          ].filter(Boolean).join(" ");
          return (
            <li key={finding.finding_id}>
              <button
                aria-current={isSelected ? "true" : undefined}
                className={itemClassName}
                data-severity={finding.severity}
                type="button"
                onClick={() => onSelect(finding.finding_id)}
              >
                <span className="finding-list__severity">{formatSeverity(finding.severity)}</span>
                <span className="finding-list__title">{finding.title}</span>
                <span className="finding-list__meta">
                  {finding.category} · {finding.confidence === null ? t("finding.evidenceBased") : `${Math.round(finding.confidence * 100)}%`} · {formatLocation(finding)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
