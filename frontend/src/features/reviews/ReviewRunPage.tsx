import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  CircleCheckBig,
  CircleStop,
  Copy,
  Download,
  FileDigit,
  ListChecks,
  PanelTop,
  PlayCircle,
  RefreshCw,
  WandSparkles,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";
import { FindingDetail } from "../findings/FindingDetail";
import { FindingList } from "../findings/FindingList";
import type { FindingRecord } from "../findings/types";
import { cancelReview, getFindingSource, getReview, getTranscript, listFindings } from "./api";
import { ReviewConsole } from "./ReviewConsole";
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
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const params = useParams();
  const taskId = params.taskId;
  const [activeTab, setActiveTab] = useState<TabName>("agent_runs");
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const terminalRef = useRef<string | null>(null);
  const { status: eventStatus, events, connectionState } = useReviewEvents(taskId);
  function handleUnsupported() { window.alert(t("common.notSupported")); }

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
    refetchInterval: TERMINAL_STATUSES.has(eventStatus) ? false : 1_000,
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

  async function refreshProgress() {
    await Promise.all([reviewQuery.refetch(), findingsQuery.refetch(), transcriptQuery.refetch()]);
  }

  const currentStatus =
    eventStatus === "loading" ? reviewQuery.data?.status ?? eventStatus : eventStatus;
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
    void findingsQuery.refetch();
  }, [currentStatus, findingsQuery]);

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
          <button className="run-action" type="button" onClick={() => void refreshProgress()} disabled={reviewQuery.isFetching || transcriptQuery.isFetching}><RefreshCw aria-hidden="true" /> {t("runs.refresh")}</button>
          <button className="run-action run-action--cancel" type="button" disabled={TERMINAL_STATUSES.has(currentStatus) || cancelMutation.isPending} onClick={() => cancelMutation.mutate()}><CircleStop aria-hidden="true" /> {t("run.cancel")}</button>
          <button className="run-action" type="button" onClick={handleUnsupported}><Copy aria-hidden="true" /> {t("run.copyLink")}</button>
          <button className="run-action" type="button" onClick={handleUnsupported}><Download aria-hidden="true" /> {t("run.exportReport")}</button>
          <span className="run-chip">
            <PlayCircle aria-hidden="true" />
            {reviewQuery.data?.base_oid ?? t("run.waiting")}
          </span>
          <span className="run-chip">
            <CircleCheckBig aria-hidden="true" />
            {reviewQuery.data?.head_oid ?? t("run.waiting")}
          </span>
        </div>
      </header>

      {TERMINAL_STATUSES.has(currentStatus) && currentStatus !== "completed" ? (
        <div className={bannerClass(currentStatus)} role="status">
          {currentStatus === "partial" ? t("run.partial") : null}
          {currentStatus === "failed" ? t("run.failed") : null}
          {currentStatus === "canceled" ? t("run.canceled") : null}
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
                <span className="run-tab__note">{t(tab.noteKey)}</span>
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
        <section className="run-layout run-layout--findings">
          <article className="run-panel">
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
          </article>
          <article className="run-panel run-panel--detail">
            <FindingDetail finding={selectedFinding} source={sourceQuery.data ?? null} />
            {selectedFinding !== null ? <div className="run-preview-actions"><button type="button" onClick={handleUnsupported}>{t("run.suppress")}</button><button type="button" onClick={handleUnsupported}>{t("run.acknowledge")}</button><button type="button" onClick={handleUnsupported}><WandSparkles aria-hidden="true" /> {t("run.draftFix")}</button></div> : null}
          </article>
        </section>
      ) : null}

      {activeTab === "agent_runs" ? (
        <section className="run-layout">
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
