import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ChevronDown,
  CircleAlert,
  CircleStop,
  Copy,
  Download,
  FileDigit,
  ListChecks,
  PanelTop,
  RefreshCw,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";
import { FindingDetail } from "../findings/FindingDetail";
import { FindingList } from "../findings/FindingList";
import type { FindingRecord } from "../findings/types";
import { listPlugins, PLUGIN_QUERY_KEY } from "../report-plugins/api";
import { cancelReview, exportFindings, getFindingSource, getProcessReport, getReview, getTranscript, listFindings, type ExportResultResponse, type TranscriptEntry } from "./api";
import { failureDetails } from "./failure-details";
import { ReviewConsole } from "./ReviewConsole";
import { ReviewProcessReport } from "./ReviewProcessReport";
import { useReviewEvents } from "./useReviewEvents";
import "./ReviewRunPage.css";

type TabName = "overview" | "findings" | "agent_runs" | "artifacts";

const TERMINAL_STATUSES = new Set(["completed", "partial", "failed", "canceled"]);

const TAB_OPTIONS: Array<{
  icon: typeof PanelTop;
  id: TabName;
  labelKey: TranslationKey;
  noteKey: TranslationKey;
}> = [
  { id: "overview", labelKey: "run.overview", noteKey: "run.overviewNote", icon: PanelTop },
  { id: "findings", labelKey: "run.findings", noteKey: "run.findingsNote", icon: ListChecks },
  { id: "agent_runs", labelKey: "run.agentRuns", noteKey: "run.agentRunsNote", icon: Activity },
  { id: "artifacts", labelKey: "run.artifacts", noteKey: "run.artifactsNote", icon: FileDigit },
];

const STATUS_KEYS: Readonly<Record<string, TranslationKey>> = {
  loading: "status.loading",
  created: "status.created",
  queued: "status.queued",
  running: "status.running",
  completed: "status.completed",
  partial: "status.partial",
  failed: "status.failed",
  canceled: "status.canceled",
  cancellation_requested: "status.cancelRequested",
};

function reviewerLabel(reference: string, t: (key: TranslationKey, values?: Record<string, string>) => string) {
  const [agentId] = reference.split(":");
  if (agentId.length === 0) {
    return reference;
  }
  const name = `${agentId[0].toUpperCase()}${agentId.slice(1)}`;
  return t("run.reviewer", { name });
}

function statusLabel(status: string, t: (key: TranslationKey) => string) {
  const key = STATUS_KEYS[status];
  return key === undefined ? status.replaceAll("_", " ") : t(key);
}

const SCOPE_KEYS: Readonly<Record<string, TranslationKey>> = {
  branch: "run.scopeBranch",
  commit: "run.scopeCommit",
  uncommitted: "run.scopeUncommitted",
  full: "run.scopeFull",
};

function scopeLabel(scopeType: string, t: (key: TranslationKey) => string) {
  const key = SCOPE_KEYS[scopeType];
  return key === undefined ? scopeType.replaceAll("_", " ") : t(key);
}

function bannerClass(status: string) {
  if (status === "partial") {
    return "run-banner run-banner--partial";
  }
  if (status === "failed") {
    return "run-banner run-banner--failed";
  }
  if (status === "canceled") {
    return "run-banner run-banner--canceled";
  }
  return "run-banner";
}

export function ReviewRunPage() {
  const { locale, t } = useI18n();
  const queryClient = useQueryClient();
  const params = useParams();
  const taskId = params.taskId;
  const [activeTab, setActiveTab] = useState<TabName>("agent_runs");
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const terminalRef = useRef<string | null>(null);
  const { status: eventStatus, events, connectionState } = useReviewEvents(taskId);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [exportResult, setExportResult] = useState<ExportResultResponse | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);

  const pluginsQuery = useQuery({
    queryKey: PLUGIN_QUERY_KEY,
    queryFn: listPlugins,
  });

  const exportMutation = useMutation({
    mutationFn: ({ pluginId }: { pluginId: string }) => {
      if (taskId === undefined) {
        throw new Error(t("run.missingTask"));
      }
      return exportFindings(taskId, pluginId);
    },
    onSuccess: (result) => {
      setExportResult(result);
      setExportMenuOpen(false);
    },
    onError: (error: Error) => {
      setExportResult({
        plugin_id: "",
        task_id: taskId ?? "",
        success: false,
        output_path: null,
        error: error.message,
        exported_at: new Date().toISOString(),
      });
      setExportMenuOpen(false);
    },
  });

  const enabledPlugins = (pluginsQuery.data ?? []).filter((p) => p.is_enabled);

  function handleUnsupported() { window.alert(t("common.notSupported")); }

  async function handleCopyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
    } catch {
      window.alert(t("run.copyLinkFailed"));
    }
  }

  const reviewQuery = useQuery({
    queryKey: ["review", taskId],
    queryFn: async () => {
      if (taskId === undefined) {
        throw new Error(t("run.missingTask"));
      }
      return getReview(taskId);
    },
    enabled: taskId !== undefined,
  });

  const findingsQuery = useQuery({
    queryKey: ["review-findings", taskId],
    queryFn: async () => {
      if (taskId === undefined) {
        throw new Error(t("run.missingTask"));
      }
      return listFindings(taskId);
    },
    enabled: taskId !== undefined,
    initialData: [] as FindingRecord[],
  });
  const transcriptQuery = useQuery({
    queryKey: ["review-transcript", taskId],
    queryFn: () => getTranscript(taskId ?? ""),
    enabled: taskId !== undefined,
    // The terminal SSE event can arrive before the Worker has atomically persisted
    // its in-memory transcript. Keep polling an empty terminal transcript until it is
    // available, then stop to avoid polling completed Reviews indefinitely.
    refetchInterval: (query) =>
      TERMINAL_STATUSES.has(eventStatus) && (query.state.data?.length ?? 0) > 0 ? false : 1_000,
    initialData: [],
  });
  const cancelMutation = useMutation({
    mutationFn: () => cancelReview(taskId ?? ""),
    onSuccess: async () => {
      await Promise.all([
        reviewQuery.refetch(),
        transcriptQuery.refetch(),
        queryClient.invalidateQueries({ queryKey: ["reviews"] }),
      ]);
    },
  });

  const [isRefreshing, setIsRefreshing] = useState(false);

  async function refreshProgress() {
    setIsRefreshing(true);
    const start = Date.now();
    await Promise.all([reviewQuery.refetch(), findingsQuery.refetch(), transcriptQuery.refetch()]);
    const elapsed = Date.now() - start;
    if (elapsed < 500) {
      await new Promise((resolve) => setTimeout(resolve, 500 - elapsed));
    }
    setIsRefreshing(false);
  }

  const currentStatus =
    eventStatus === "loading" ? reviewQuery.data?.status ?? eventStatus : eventStatus;
  const failureEntry = latestFailureEntry(transcriptQuery.data);
  const failure = failureEntry === undefined ? undefined : failureDetails(failureEntry.metadata, locale);
  const validationWarning = latestValidationWarningEntry(transcriptQuery.data);
  const coverageWarnings = coverageWarningEntries(transcriptQuery.data);
  const incompleteReviewFiles = Array.from(
    new Set(
      coverageWarnings.flatMap((entry) =>
        parseIncompleteReviewFiles(entry.metadata.incomplete_files),
      ),
    ),
  ).sort();
  const processReportQuery = useQuery({
    queryKey: ["review-process-report", taskId],
    queryFn: () => getProcessReport(taskId ?? ""),
    enabled:
      taskId !== undefined &&
      TERMINAL_STATUSES.has(currentStatus) &&
      transcriptQuery.data.length > 0,
    retry: 5,
    retryDelay: 1_000,
  });
  const reviewTitle = useMemo(() => {
    const selectedAgents = reviewQuery.data?.selected_agents ?? [];
    if (selectedAgents.length === 0) {
      return t("run.review");
    }
    return selectedAgents.map((reference) => reviewerLabel(reference, t)).join(" · ");
  }, [reviewQuery.data?.selected_agents, t]);

  useEffect(() => {
    if (!TERMINAL_STATUSES.has(currentStatus)) {
      return;
    }
    if (terminalRef.current === currentStatus) {
      return;
    }
    terminalRef.current = currentStatus;
    void Promise.all([findingsQuery.refetch(), transcriptQuery.refetch()]);
  }, [currentStatus, findingsQuery, transcriptQuery]);

  useEffect(() => {
    if (findingsQuery.data.length > 0 && selectedFindingId === null) {
      setSelectedFindingId(findingsQuery.data[0].finding_id);
      return;
    }
    if (
      selectedFindingId !== null &&
      findingsQuery.data.every((finding) => finding.finding_id !== selectedFindingId)
    ) {
      setSelectedFindingId(findingsQuery.data[0]?.finding_id ?? null);
    }
  }, [findingsQuery.data, selectedFindingId]);

  const selectedFinding =
    findingsQuery.data.find((finding) => finding.finding_id === selectedFindingId) ?? null;
  const sourceQuery = useQuery({
    queryKey: ["review-finding-source", taskId, selectedFinding?.finding_id],
    queryFn: () => getFindingSource(taskId ?? "", selectedFinding?.finding_id ?? ""),
    enabled: taskId !== undefined && selectedFinding !== null,
  });

  if (taskId === undefined) {
    return <div className="run-empty">{t("run.missingTask")}</div>;
  }

  if (reviewQuery.isError) {
    return (
      <div className="run-empty" role="alert">
        {reviewQuery.error instanceof Error ? reviewQuery.error.message : t("run.unableLoad")}
      </div>
    );
  }

  return (
    <section className="review-run-page">
      <header className="review-run-page__header">
        <div>
          <p className="review-run-page__eyebrow">{t("run.live")}</p>
          <h1>{reviewTitle}</h1>
          <p className="review-run-page__subtitle">
            {t("run.task")} <span>{taskId}</span> · {statusLabel(currentStatus, t)} · {t("run.connection")}{" "}
            {connectionState}
          </p>
        </div>
        <div className="review-run-page__chips">
          <button className="run-action" type="button" onClick={() => void refreshProgress()} disabled={isRefreshing}><RefreshCw aria-hidden="true" className={isRefreshing ? "run-action__spin" : undefined} /> {t("runs.refresh")}</button>
          <button className="run-action run-action--cancel" type="button" disabled={TERMINAL_STATUSES.has(currentStatus) || cancelMutation.isPending} onClick={() => cancelMutation.mutate()}><CircleStop aria-hidden="true" className={cancelMutation.isPending ? "run-action__spin" : undefined} /> {cancelMutation.isPending ? t("run.canceling") : t("run.cancel")}</button>
          <button className="run-action" type="button" onClick={handleCopyLink}><Copy aria-hidden="true" /> {linkCopied ? t("run.linkCopied") : t("run.copyLink")}</button>
          <div className="run-export-menu">
            <button
              className="run-action"
              type="button"
              disabled={!TERMINAL_STATUSES.has(currentStatus) || enabledPlugins.length === 0 || exportMutation.isPending}
              onClick={() => setExportMenuOpen(!exportMenuOpen)}
            >
              <Download aria-hidden="true" /> {t("run.exportReport")} <ChevronDown aria-hidden="true" />
            </button>
            {exportMenuOpen && (
              <div className="run-export-dropdown">
                {enabledPlugins.map((plugin) => (
                  <button
                    key={plugin.plugin_id}
                    className="run-export-option"
                    type="button"
                    disabled={exportMutation.isPending}
                    onClick={() => exportMutation.mutate({ pluginId: plugin.plugin_id })}
                  >
                    {plugin.manifest.name}
                  </button>
                ))}
              </div>
            )}
          </div>
          {exportResult && (
            <span className={`run-export-result ${exportResult.success ? "run-export-result--ok" : "run-export-result--err"}`}>
              {exportResult.success
                ? t("reportPlugins.exportSuccess")
                : t("reportPlugins.exportFailed")}
            </span>
          )}
        </div>
      </header>

      {TERMINAL_STATUSES.has(currentStatus) && currentStatus !== "completed" ? (
        <div className={bannerClass(currentStatus)} role={currentStatus === "failed" ? "alert" : "status"}>
          {currentStatus === "partial" ? t("run.partial") : null}
          {currentStatus === "failed" && failure !== undefined && failureEntry !== undefined ? (
            <>
              <div className="run-failure__summary">
                <CircleAlert aria-hidden="true" />
                <div><strong>{failure.title}</strong><p>{failure.description}</p></div>
              </div>
              <div className="run-failure__action">
                <span>{locale === "zh-CN" ? "下一步" : "Next step"}</span>
                <p>{failure.action}</p>
              </div>
              <div className="run-failure__metadata">
                <code>{failureEntry.metadata.reason_code ?? failureEntry.metadata.error_type ?? "unknown"}</code>
                {failureEntry.metadata.phase !== undefined ? <code>{failureEntry.metadata.phase}</code> : null}
                {failureEntry.metadata.provider_status_code !== undefined ? <code>HTTP {failureEntry.metadata.provider_status_code}</code> : null}
              </div>
            </>
          ) : null}
          {currentStatus === "failed" && failure === undefined ? t("run.failed") : null}
          {currentStatus === "canceled" ? t("run.canceled") : null}
        </div>
      ) : null}
      {validationWarning !== undefined ? (
        <div
          aria-label={t("run.validationWarningTitle")}
          className="run-validation-warning"
          role="status"
        >
          <CircleAlert aria-hidden="true" />
          <div>
            <strong>{t("run.validationWarningTitle")}</strong>
            <p>{t("run.validationWarningSummary", {
              retained: validationWarning.metadata.retained_count ?? "0",
              skipped: validationWarning.metadata.skipped_count ?? "0",
              duplicates: validationWarning.metadata.duplicate_count ?? "0",
              invalid: validationWarning.metadata.invalid_count ?? "0",
            })}</p>
          </div>
        </div>
      ) : null}
      {coverageWarnings.length > 0 ? (
        <div
          aria-label={t("run.coverageWarningTitle")}
          className="run-validation-warning"
          role="status"
        >
          <CircleAlert aria-hidden="true" />
          <div>
            <strong>{t("run.coverageWarningTitle")}</strong>
            <p>{t("run.coverageWarningSummary")}</p>
            <ul className="run-coverage-warning__files">
              {incompleteReviewFiles.map((path) => <li key={path}><code>{path}</code></li>)}
            </ul>
          </div>
        </div>
      ) : null}
      {cancelMutation.isError ? <p className="run-action-error" role="alert">{cancelMutation.error instanceof Error ? cancelMutation.error.message : t("run.unableLoad")}</p> : null}

      <nav className="review-run-page__tabs" aria-label={t("run.sections")}>
        {TAB_OPTIONS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              className={activeTab === tab.id ? "run-tab run-tab--active" : "run-tab"}
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
            >
              <Icon aria-hidden="true" />
              <span className="run-tab__copy">
                <span className="run-tab__label">{t(tab.labelKey)}</span>
                <span className="run-tab__note">
                  {tab.id === "findings" && !TERMINAL_STATUSES.has(currentStatus)
                    ? t("run.findingsNotePending")
                    : t(tab.noteKey)}
                </span>
              </span>
            </button>
          );
        })}
      </nav>

      {activeTab === "overview" ? (
        <section className="run-layout">
          <article className="run-panel">
            <h2>{t("run.overview")}</h2>
            <dl className="run-summary">
              <div>
                <dt>{t("run.status")}</dt>
                <dd>{statusLabel(currentStatus, t)}</dd>
              </div>
              <div>
                <dt>{t("run.connection")}</dt>
                <dd>{connectionState}</dd>
              </div>
              <div>
                <dt>{t("run.events")}</dt>
                <dd>{events.length}</dd>
              </div>
              <div>
                <dt>{t("run.findings")}</dt>
                <dd>{findingsQuery.data.length}</dd>
              </div>
            </dl>
          </article>

          <article className="run-panel">
            <h2>{t("run.scope")}</h2>
            <dl className="run-summary">
              <div>
                <dt>{t("run.repository")}</dt>
                <dd>{reviewQuery.data?.repository_name ?? "-"}</dd>
              </div>
              <div>
                <dt>{t("run.scope")}</dt>
                <dd>{scopeLabel(reviewQuery.data?.scope_type ?? "", t)}</dd>
              </div>
              {reviewQuery.data?.base_oid ? (
                <div>
                  <dt>{t("run.baseCommit")}</dt>
                  <dd><code>{reviewQuery.data.base_oid.slice(0, 7)}</code></dd>
                </div>
              ) : null}
              {reviewQuery.data?.head_oid ? (
                <div>
                  <dt>{t("run.headCommit")}</dt>
                  <dd><code>{reviewQuery.data.head_oid.slice(0, 7)}</code></dd>
                </div>
              ) : null}
            </dl>
          </article>

          <article className="run-panel">
            <h2>{t("run.reviewers")}</h2>
            <div className="run-reviewer-stack">
              {(reviewQuery.data?.selected_agents ?? []).map((reference) => (
                <div className="run-reviewer" key={reference}>
                  <strong>{reviewerLabel(reference, t)}</strong>
                  <span>{reference}</span>
                </div>
              ))}
            </div>
          </article>
        </section>
      ) : null}

      {activeTab === "findings" ? (
        <section className="finding-workspace">
          <header className="finding-workspace__navigation">
            <div className="run-panel__heading">
              <div>
                <p className="run-panel__eyebrow">{reviewTitle}</p>
                <h2>
                  {t(findingsQuery.data.length === 1 ? "run.findingCount" : "run.findingCountPlural", { count: findingsQuery.data.length })}
                </h2>
              </div>
              <span className="run-panel__status">{statusLabel(currentStatus, t)}</span>
            </div>
            <FindingList
              findings={findingsQuery.data}
              selectedFindingId={selectedFindingId}
              onSelect={setSelectedFindingId}
            />
          </header>
          <article className="finding-workspace__detail">
            <FindingDetail finding={selectedFinding} source={sourceQuery.data ?? null} />
            {selectedFinding !== null ? <div className="run-preview-actions"><button type="button" onClick={handleUnsupported}>{t("run.suppress")}</button><button type="button" onClick={handleUnsupported}>{t("run.acknowledge")}</button></div> : null}
          </article>
        </section>
      ) : null}

      {activeTab === "agent_runs" ? (
        <section className="run-layout">
          {processReportQuery.data !== undefined ? (
            <ReviewProcessReport report={processReportQuery.data} />
          ) : TERMINAL_STATUSES.has(currentStatus) && transcriptQuery.data.length > 0 ? (
            <article className="run-panel run-panel--wide process-report__state" role={processReportQuery.isError ? "alert" : "status"}>
              {processReportQuery.isError ? t("run.processReportError") : t("run.processReportLoading")}
            </article>
          ) : null}
          <article className="run-panel run-panel--wide">
            <div className="run-panel__heading">
              <div>
                <p className="run-panel__eyebrow">{t("run.agentRuns")}</p>
                <h2>{t("run.eventStream")}</h2>
              </div>
              <span className="run-panel__status">{connectionState}</span>
            </div>
            {transcriptQuery.data.length > 0 ? <ReviewConsole entries={transcriptQuery.data} /> : <p className="event-log__empty">{t("run.waitingEvents")}</p>}
          </article>
        </section>
      ) : null}

      {activeTab === "artifacts" ? (
        <section className="run-layout">
          <article className="run-panel run-panel--wide">
            <h2>{t("run.artifacts")}</h2>
            <p className="run-muted">{t("run.artifactPlaceholder")}</p>
          </article>
        </section>
      ) : null}
    </section>
  );
}

function latestFailureEntry(entries: TranscriptEntry[]): TranscriptEntry | undefined {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index];
    if (entry?.kind === "lifecycle" && (entry.metadata.error_code !== undefined || entry.metadata.error_type !== undefined)) {
      return entry;
    }
  }
  return undefined;
}

function latestValidationWarningEntry(entries: TranscriptEntry[]): TranscriptEntry | undefined {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index];
    if (
      entry?.kind === "lifecycle" &&
      entry.metadata.warning_code === "finding_validation_partial"
    ) {
      return entry;
    }
  }
  return undefined;
}

function coverageWarningEntries(entries: TranscriptEntry[]): TranscriptEntry[] {
  return entries.filter(
    (entry) =>
      entry.kind === "lifecycle" &&
      entry.metadata.warning_code === "review_coverage_incomplete",
  );
}

function parseIncompleteReviewFiles(value: string | undefined): string[] {
  if (value === undefined) return [];
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed) || !parsed.every((item) => typeof item === "string")) return [];
    return parsed;
  } catch {
    return [];
  }
}
