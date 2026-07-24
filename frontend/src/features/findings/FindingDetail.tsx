import { ChevronRight, Lightbulb, MapPin } from "lucide-react";
import ReactMarkdown from "react-markdown";

import { useI18n } from "../../shared/i18n/i18n";
import type { FindingRecord, FindingSourcePreview } from "./types";

function formatLocation(finding: FindingRecord) {
  return `${finding.primary_location.path}:${finding.primary_location.start_line}-${finding.primary_location.end_line}`;
}

function normalizeText(content: string) {
  return content.replaceAll(/\s+/g, " ").trim();
}

function hasDistinctContent(content: string, comparedTo: readonly string[]) {
  const normalized = normalizeText(content);
  return normalized.length > 0 && !comparedTo.some((item) => normalizeText(item) === normalized);
}

function MarkdownContent({ content }: { content: string }) {
  return <div className="finding-markdown"><ReactMarkdown>{content}</ReactMarkdown></div>;
}

function SourceCode({ finding, source }: { finding: FindingRecord; source: FindingSourcePreview }) {
  const lines = source.content.split("\n");

  return (
    <div className="finding-code" aria-label="Pinned complete source">
      <div className="finding-code__toolbar">
        <span className="finding-code__path"><MapPin aria-hidden="true" /> {source.path}</span>
        <span className="finding-code__revision">{source.revision.slice(0, 12)}</span>
      </div>
      <div className="finding-code__scroll">
        <ol className="finding-code__lines" start={source.start_line}>
          {lines.map((line, index) => {
            const lineNumber = source.start_line + index;
            const isAnnotationAnchor = lineNumber === source.highlight_start_line;
            const isHighlighted =
              lineNumber >= source.highlight_start_line && lineNumber <= source.highlight_end_line;
            return (
              <li className="finding-code__line-group" key={`${lineNumber}-${line}`}>
                {isAnnotationAnchor ? (
                  <aside className="finding-annotation" aria-label="Review opinion">
                    <div className="finding-annotation__label">Review opinion</div>
                    <MarkdownContent content={finding.explanation} />
                    <div className="finding-annotation__recommendation">
                      <Lightbulb aria-hidden="true" />
                      <MarkdownContent content={finding.recommendation} />
                    </div>
                  </aside>
                ) : null}
                <div className={isHighlighted ? "finding-code__line finding-code__line--highlight" : "finding-code__line"}>
                  <span className="finding-code__number" aria-hidden="true">{lineNumber}</span>
                  <code>{line || " "}</code>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

export function FindingDetail({ finding, source }: { finding: FindingRecord | null; source: FindingSourcePreview | null }) {
  const { t } = useI18n();
  if (finding === null) {
    return <div className="finding-detail finding-detail--empty">{t("finding.select")}</div>;
  }
  const hasDistinctImpact = hasDistinctContent(finding.impact, [finding.explanation]);
  const uniqueEvidence = finding.evidence.filter((item, index, items) =>
    hasDistinctContent(item.description, [finding.explanation, finding.impact]) &&
    items.findIndex((candidate) => normalizeText(candidate.description) === normalizeText(item.description)) === index,
  );

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

      {hasDistinctImpact ? (
        <section className="finding-detail__summary" aria-label="Review summary">
          <div>
            <span>{t("finding.impact")}</span>
            <MarkdownContent content={finding.impact} />
          </div>
        </section>
      ) : null}

      <section className="finding-detail__source">
        <div className="finding-detail__section-heading">
          <div>
            <p>Source context</p>
            <h4>Complete pinned source</h4>
          </div>
          <ChevronRight aria-hidden="true" />
        </div>
        {source === null ? <p className="finding-detail__loading">Loading complete pinned source…</p> : <SourceCode finding={finding} source={source} />}
      </section>

      {uniqueEvidence.length > 0 ? (
        <section className="finding-detail__evidence">
          <h4>{t("finding.evidence")}</h4>
          <ul>
            {uniqueEvidence.map((item, index) => (
              <li key={`${finding.finding_id}-evidence-${index}`}>
                <strong>{item.kind}</strong>
                <MarkdownContent content={item.description} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}
