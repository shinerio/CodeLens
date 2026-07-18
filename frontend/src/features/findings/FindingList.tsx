import type { FindingRecord } from "./types";
import { useI18n } from "../../shared/i18n/i18n";

function formatLocation(finding: FindingRecord) {
  return `${finding.primary_location.path}:${finding.primary_location.start_line}-${finding.primary_location.end_line}`;
}

function formatConfidence(value: number) {
  return `${Math.round(value * 100)}%`;
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
    <ul className="finding-list" aria-label={t("finding.list")}>
      {findings.map((finding) => {
        const isSelected = finding.finding_id === selectedFindingId;
        return (
          <li key={finding.finding_id}>
            <button
              className={isSelected ? "finding-list__item finding-list__item--active" : "finding-list__item"}
              type="button"
              onClick={() => onSelect(finding.finding_id)}
            >
              <span className="finding-list__severity">{formatSeverity(finding.severity)}</span>
              <span className="finding-list__title">{finding.title}</span>
              <span className="finding-list__meta">
                {formatLocation(finding)} · {formatConfidence(finding.confidence)} ·{" "}
                {finding.reviewer_id}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
