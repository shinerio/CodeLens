import { DiffEditor, loader, type DiffOnMount } from "@monaco-editor/react";
import { GitCompareArrows, Lightbulb, MapPin, Maximize2, Minimize2 } from "lucide-react";
import CssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import HtmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import TypeScriptWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import * as localMonaco from "monaco-editor";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import type { editor as MonacoEditor, IDisposable, Range } from "monaco-editor";

import { useI18n } from "../../shared/i18n/i18n";
import { classifyDiffChange, selectDiffSide } from "./diff-change";
import type { FindingRecord, FindingSourcePreview } from "./types";

window.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === "json") {
      return new JsonWorker();
    }
    if (label === "css" || label === "scss" || label === "less") {
      return new CssWorker();
    }
    if (label === "html" || label === "handlebars" || label === "razor") {
      return new HtmlWorker();
    }
    if (label === "typescript" || label === "javascript") {
      return new TypeScriptWorker();
    }
    return new EditorWorker();
  },
};
loader.config({ monaco: localMonaco });

function formatLocation(finding: FindingRecord) {
  return `${finding.primary_location.path}:${finding.primary_location.start_line}-${finding.primary_location.end_line}`;
}

function formatConfidence(value: number) {
  return `${Math.round(value * 100)}%`;
}

function measureCommentHeight(host: HTMLElement, content: HTMLElement) {
  const styles = getComputedStyle(host);
  const paddingTop = Number.parseFloat(styles.paddingTop) || 0;
  const paddingBottom = Number.parseFloat(styles.paddingBottom) || 0;
  const contentHeight = Math.max(content.scrollHeight, content.getBoundingClientRect().height);
  return Math.max(132, Math.ceil(contentHeight + paddingTop + paddingBottom));
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

function languageFor(path: string) {
  const extension = path.split(".").at(-1)?.toLowerCase();
  const languages: Record<string, string> = {
    css: "css",
    go: "go",
    html: "html",
    java: "java",
    js: "javascript",
    json: "json",
    jsx: "javascript",
    md: "markdown",
    py: "python",
    rs: "rust",
    sql: "sql",
    ts: "typescript",
    tsx: "typescript",
    xml: "xml",
    yaml: "yaml",
    yml: "yaml",
  };
  return extension === undefined ? "plaintext" : languages[extension] ?? "plaintext";
}

interface FindingOpinionLabels {
  explanation: string;
  impact: string;
  recommendation: string;
}

function FindingOpinion({
  finding,
  labels,
}: {
  finding: FindingRecord;
  labels: FindingOpinionLabels;
}) {
  const hasDistinctImpact = hasDistinctContent(finding.impact, [finding.explanation]);
  return (
    <section
      className={`finding-detail__opinion finding-detail__opinion--${finding.severity}`}
      aria-label={labels.explanation}
      data-severity={finding.severity}
    >
      <div>
        <span>{labels.explanation}</span>
        <MarkdownContent content={finding.explanation} />
      </div>
      <div className="finding-detail__recommendation">
        <Lightbulb aria-hidden="true" />
        <div>
          <span>{labels.recommendation}</span>
          <MarkdownContent content={finding.recommendation} />
        </div>
      </div>
      {hasDistinctImpact ? (
        <div className="finding-detail__impact">
          <span>{labels.impact}</span>
          <MarkdownContent content={finding.impact} />
        </div>
      ) : null}
    </section>
  );
}

function SourceComparison({
  finding,
  source,
}: {
  finding: FindingRecord;
  source: FindingSourcePreview;
}) {
  const { t } = useI18n();
  const [isReadingAreaExpanded, setIsReadingAreaExpanded] = useState(false);
  const editorRef = useRef<MonacoEditor.IStandaloneDiffEditor | null>(null);
  const reviewRef = useRef<HTMLDivElement | null>(null);
  const decorationRefs = useRef<readonly MonacoEditor.IEditorDecorationsCollection[]>([]);
  const diffSubscriptionRef = useRef<IDisposable | null>(null);
  const modelRefs = useRef<readonly MonacoEditor.ITextModel[]>([]);
  const commentZoneRef = useRef<{
    editor: MonacoEditor.ICodeEditor;
    frameId: number;
    id: string;
    mutationObserver: MutationObserver;
    observer: ResizeObserver;
    root: Root;
    zone: MonacoEditor.IViewZone;
  } | null>(null);
  const rangeRef = useRef<(new (
    startLineNumber: number,
    startColumn: number,
    endLineNumber: number,
    endColumn: number,
  ) => Range) | null>(null);
  const opinionLabels = useMemo<FindingOpinionLabels>(() => ({
    explanation: t("finding.explanation"),
    impact: t("finding.impact"),
    recommendation: t("finding.recommendation"),
  }), [t]);
  const modelPaths = useMemo(() => ({
    base: `codelens-review://finding/${finding.finding_id}/base/${source.path}`,
    target: `codelens-review://finding/${finding.finding_id}/target/${source.path}`,
  }), [finding.finding_id, source.path]);

  const clearDecorations = useCallback(() => {
    for (const decorations of decorationRefs.current) {
      decorations.clear();
    }
    decorationRefs.current = [];
  }, []);

  const clearCommentZone = useCallback(() => {
    const commentZone = commentZoneRef.current;
    if (commentZone === null) {
      return;
    }
    commentZoneRef.current = null;
    cancelAnimationFrame(commentZone.frameId);
    commentZone.mutationObserver.disconnect();
    commentZone.observer.disconnect();
    commentZone.editor.changeViewZones((accessor) => accessor.removeZone(commentZone.id));
    queueMicrotask(() => commentZone.root.unmount());
  }, []);

  const decorateChanges = useCallback(() => {
    const editor = editorRef.current;
    const RangeConstructor = rangeRef.current;
    if (editor === null || RangeConstructor === null) {
      return;
    }
    clearDecorations();
    const originalDecorations: MonacoEditor.IModelDeltaDecoration[] = [];
    const modifiedDecorations: MonacoEditor.IModelDeltaDecoration[] = [];
    for (const change of editor.getLineChanges() ?? []) {
      const changeKind = classifyDiffChange(change);
      if (changeKind === "deleted" || changeKind === "modified") {
        originalDecorations.push({
          range: new RangeConstructor(
            change.originalStartLineNumber,
            1,
            change.originalEndLineNumber,
            1,
          ),
          options: {
            isWholeLine: true,
            className: `finding-diff__${changeKind}-line`,
            linesDecorationsClassName: `finding-diff__${changeKind}-gutter`,
          },
        });
      }
      if (changeKind === "added" || changeKind === "modified") {
        modifiedDecorations.push({
          range: new RangeConstructor(
            change.modifiedStartLineNumber,
            1,
            change.modifiedEndLineNumber,
            1,
          ),
          options: {
            isWholeLine: true,
            className: `finding-diff__${changeKind}-line`,
            linesDecorationsClassName: `finding-diff__${changeKind}-gutter`,
          },
        });
      }
    }

    const selectedDecorations = source.highlight_side === "old"
      ? originalDecorations
      : modifiedDecorations;
    selectedDecorations.push({
      range: new RangeConstructor(
        source.highlight_start_line,
        1,
        source.highlight_end_line,
        1,
      ),
      options: {
        isWholeLine: true,
        linesDecorationsClassName: "finding-diff__comment-anchor",
      },
    });
    decorationRefs.current = [
      editor.getOriginalEditor().createDecorationsCollection(originalDecorations),
      editor.getModifiedEditor().createDecorationsCollection(modifiedDecorations),
    ];
  }, [clearDecorations, source]);

  const placeComment = useCallback(() => {
    const editor = editorRef.current;
    if (editor === null) {
      return;
    }
    clearCommentZone();
    const selectedEditor = selectDiffSide(
      source.highlight_side,
      editor.getOriginalEditor(),
      editor.getModifiedEditor(),
    );
    const model = selectedEditor.getModel();
    if (model === null || model.getLineCount() === 0) {
      return;
    }
    const host = document.createElement("div");
    host.className = `finding-comment-zone finding-comment-zone--${source.highlight_side}`;
    const root = createRoot(host);
    root.render(<FindingOpinion finding={finding} labels={opinionLabels} />);
    const zone: MonacoEditor.IViewZone = {
      afterLineNumber: Math.min(source.highlight_end_line, model.getLineCount()),
      heightInPx: 132,
      domNode: host,
      showInHiddenAreas: true,
    };
    let zoneId = "";
    selectedEditor.changeViewZones((accessor) => {
      zoneId = accessor.addZone(zone);
    });
    let observedContent: HTMLElement | null = null;
    const layoutZone = () => {
      const content = host.firstElementChild;
      if (!(content instanceof HTMLElement)) {
        return;
      }
      if (content !== observedContent) {
        observer.disconnect();
        observer.observe(content);
        observedContent = content;
      }
      const height = measureCommentHeight(host, content);
      if (zone.heightInPx === height) {
        return;
      }
      zone.heightInPx = height;
      selectedEditor.changeViewZones((accessor) => accessor.layoutZone(zoneId));
    };
    const observer = new ResizeObserver(layoutZone);
    const mutationObserver = new MutationObserver(layoutZone);
    observer.observe(host);
    mutationObserver.observe(host, { childList: true });
    layoutZone();
    const frameId = requestAnimationFrame(layoutZone);
    commentZoneRef.current = {
      editor: selectedEditor,
      frameId,
      id: zoneId,
      mutationObserver,
      observer,
      root,
      zone,
    };
    selectedEditor.revealLineNearTop(Math.min(source.highlight_start_line, model.getLineCount()));
  }, [clearCommentZone, finding, opinionLabels, source]);

  const synchronizeEditor = useCallback(() => {
    decorateChanges();
    placeComment();
  }, [decorateChanges, placeComment]);

  const handleMount: DiffOnMount = (editor, monaco) => {
    editorRef.current = editor;
    const models = editor.getModel();
    modelRefs.current = models === null ? [] : [models.original, models.modified];
    rangeRef.current = monaco.Range;
    diffSubscriptionRef.current?.dispose();
    diffSubscriptionRef.current = editor.onDidUpdateDiff(decorateChanges);
    synchronizeEditor();
  };

  useEffect(() => {
    synchronizeEditor();
    return () => {
      clearCommentZone();
      clearDecorations();
    };
  }, [clearCommentZone, clearDecorations, synchronizeEditor]);

  useEffect(() => () => diffSubscriptionRef.current?.dispose(), []);

  useEffect(() => {
    if (!isReadingAreaExpanded) {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsReadingAreaExpanded(false);
      }
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isReadingAreaExpanded]);

  useEffect(() => {
    const frameId = requestAnimationFrame(() => {
      const viewport = reviewRef.current;
      const scroller = isReadingAreaExpanded ? viewport : viewport?.parentElement;
      if (scroller !== undefined && scroller !== null) {
        scroller.scrollLeft = source.highlight_side === "new"
          ? scroller.scrollWidth - scroller.clientWidth
          : 0;
      }
    });
    return () => cancelAnimationFrame(frameId);
  }, [isReadingAreaExpanded, source.highlight_side]);

  useEffect(() => () => {
    const models = modelRefs.current;
    modelRefs.current = [];
    queueMicrotask(() => {
      for (const model of models) {
        if (!model.isDisposed()) {
          model.dispose();
        }
      }
    });
  }, [modelPaths]);

  const baseContent = source.base?.content ?? "";
  const targetContent = source.target?.content ?? "";
  const language = languageFor(source.path);
  const readingAreaActionLabel = isReadingAreaExpanded
    ? t("finding.collapseReadingArea")
    : t("finding.expandReadingArea");
  const editorOptions = {
    automaticLayout: true,
    fontFamily: "SFMono-Regular, Consolas, 'Liberation Mono', monospace",
    fontSize: 13,
    glyphMargin: true,
    lineHeight: 20,
    minimap: { enabled: false },
    readOnly: true,
    scrollBeyondLastLine: false,
    smoothScrolling: true,
  } as const;

  return (
    <div
      aria-label="Pinned source comparison"
      className={`finding-review-viewport${isReadingAreaExpanded ? " finding-review-viewport--expanded" : ""}`}
      data-comment-side={source.highlight_side}
      ref={reviewRef}
    >
      <div className="finding-review">
        <header className="finding-review__pane-header finding-review__pane-header--base">
          <span className="finding-review__path">
            <MapPin aria-hidden="true" />
            <strong>{t("finding.baseVersion")}</strong>
            <code>{source.base?.path ?? source.path}</code>
          </span>
          <span className="finding-review__revision">
            {t("finding.readOnly")}
            <code>{source.base?.revision.slice(0, 12) ?? t("finding.fileAdded")}</code>
          </span>
        </header>
        <header className="finding-review__pane-header finding-review__pane-header--target">
          <span className="finding-review__path">
            <GitCompareArrows aria-hidden="true" />
            <strong>{t("finding.targetVersion")}</strong>
            <code>{source.target?.path ?? source.path}</code>
          </span>
          <div className="finding-review__toolbar">
            <span className="finding-review__revision">
              <code>{source.target?.revision.slice(0, 12) ?? t("finding.fileDeleted")}</code>
            </span>
            <button
              aria-label={readingAreaActionLabel}
              aria-pressed={isReadingAreaExpanded}
              className="finding-review__expand"
              onClick={() => setIsReadingAreaExpanded((isExpanded) => !isExpanded)}
              title={readingAreaActionLabel}
              type="button"
            >
              {isReadingAreaExpanded
                ? <Minimize2 aria-hidden="true" />
                : <Maximize2 aria-hidden="true" />}
            </button>
          </div>
        </header>
        <div className="finding-review__editor">
          <DiffEditor
            height="100%"
            keepCurrentModifiedModel
            keepCurrentOriginalModel
            language={language}
            modified={targetContent}
            modifiedLanguage={language}
            modifiedModelPath={modelPaths.target}
            onMount={handleMount}
            options={{
              ...editorOptions,
              diffWordWrap: "off",
              ignoreTrimWhitespace: false,
              originalEditable: false,
              renderSideBySide: true,
              useInlineViewWhenSpaceIsLimited: false,
            }}
            original={baseContent}
            originalLanguage={language}
            originalModelPath={modelPaths.base}
            theme="vs-dark"
          />
        </div>
      </div>
    </div>
  );
}

export function FindingDetail({ finding, source }: { finding: FindingRecord | null; source: FindingSourcePreview | null }) {
  const { t } = useI18n();
  if (finding === null) {
    return <div className="finding-detail finding-detail--empty">{t("finding.select")}</div>;
  }
  const uniqueEvidence = finding.evidence.filter((item, index, items) =>
    hasDistinctContent(item.description, [finding.explanation, finding.impact]) &&
    items.findIndex((candidate) => normalizeText(candidate.description) === normalizeText(item.description)) === index,
  );

  return (
    <article className="finding-detail" data-severity={finding.severity}>
      <header className="finding-detail__header">
        <div>
          <dl className="finding-detail__eyebrow">
            <div>
              <dt>{t("finding.severity")}</dt>
              <dd>{finding.severity}</dd>
            </div>
            <div>
              <dt>{t("finding.category")}</dt>
              <dd>{finding.category}</dd>
            </div>
            <div>
              <dt>{t("finding.confidence")}</dt>
              <dd>{formatConfidence(finding.confidence)}</dd>
            </div>
          </dl>
          <h3>{finding.title}</h3>
        </div>
        <div className="finding-detail__meta">
          <span>{finding.reviewer_id}</span>
          <span>{formatLocation(finding)}</span>
        </div>
      </header>

      <section className="finding-detail__source">
        {source === null
          ? <p className="finding-detail__loading">{t("finding.loadingSource")}</p>
          : <SourceComparison key={finding.finding_id} finding={finding} source={source} />}
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
