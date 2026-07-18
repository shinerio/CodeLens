import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  CircleCheckBig,
  FileDigit,
  ListChecks,
  PanelTop,
  PlayCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { FindingDetail } from "../findings/FindingDetail";
import { FindingList } from "../findings/FindingList";
import type { FindingRecord } from "../findings/types";
import { getReview, listFindings } from "./api";
import { useReviewEvents } from "./useReviewEvents";
import "./ReviewRunPage.css";

type TabName = "overview" | "findings" | "agent_runs" | "artifacts";

const TERMINAL_STATUSES = new Set(["completed", "partial", "failed", "canceled"]);

const TAB_OPTIONS: Array<{
  icon: typeof PanelTop;
  id: TabName;
  label: string;
  note: string;
}> = [
  { id: "overview", label: "Overview", note: "Live status and summary", icon: PanelTop },
  { id: "findings", label: "Findings", note: "Validated output", icon: ListChecks },
  { id: "agent_runs", label: "Agent Runs", note: "Event stream", icon: Activity },
  { id: "artifacts", label: "Artifacts", note: "Locked for now", icon: FileDigit },
];

function reviewerLabel(reference: string) {
  const [agentId] = reference.split(":");
  if (agentId.length === 0) {
    return reference;
  }
  return `${agentId[0].toUpperCase()}${agentId.slice(1)} Reviewer`;
}

function statusLabel(status: string) {
  return status.replaceAll("_", " ");
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
  const params = useParams();
  const taskId = params.taskId;
  const [activeTab, setActiveTab] = useState<TabName>("findings");
  const [selectedFindingId, setSelectedFindingId] = useState<string | null>(null);
  const terminalRef = useRef<string | null>(null);
  const { status: eventStatus, events, connectionState } = useReviewEvents(taskId);

  const reviewQuery = useQuery({
    queryKey: ["review", taskId],
    queryFn: async () => {
      if (taskId === undefined) {
        throw new Error("Missing task id");
      }
      return getReview(taskId);
    },
    enabled: taskId !== undefined,
  });

  const findingsQuery = useQuery({
    queryKey: ["review-findings", taskId],
    queryFn: async () => {
      if (taskId === undefined) {
        throw new Error("Missing task id");
      }
      return listFindings(taskId);
    },
    enabled: taskId !== undefined,
    initialData: [] as FindingRecord[],
  });

  const currentStatus =
    eventStatus === "loading" ? reviewQuery.data?.status ?? eventStatus : eventStatus;
  const reviewTitle = useMemo(() => {
    const selectedAgents = reviewQuery.data?.selected_agents ?? [];
    if (selectedAgents.length === 0) {
      return "Review";
    }
    return selectedAgents.map(reviewerLabel).join(" · ");
  }, [reviewQuery.data?.selected_agents]);

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

  if (taskId === undefined) {
    return <div className="run-empty">Missing task id.</div>;
  }

  if (reviewQuery.isError) {
    return (
      <div className="run-empty" role="alert">
        {reviewQuery.error instanceof Error ? reviewQuery.error.message : "Unable to load run."}
      </div>
    );
  }

  const selectedFinding =
    findingsQuery.data.find((finding) => finding.finding_id === selectedFindingId) ?? null;

  return (
    <section className="review-run-page">
      <header className="review-run-page__header">
        <div>
          <p className="review-run-page__eyebrow">Live review run</p>
          <h1>{reviewTitle}</h1>
          <p className="review-run-page__subtitle">
            Task <span>{taskId}</span> · {statusLabel(currentStatus)} · connection{" "}
            {connectionState}
          </p>
        </div>
        <div className="review-run-page__chips">
          <span className="run-chip">
            <PlayCircle aria-hidden="true" />
            {reviewQuery.data?.base_oid ?? "Waiting for review"}
          </span>
          <span className="run-chip">
            <CircleCheckBig aria-hidden="true" />
            {reviewQuery.data?.head_oid ?? "Waiting for review"}
          </span>
        </div>
      </header>

      {TERMINAL_STATUSES.has(currentStatus) && currentStatus !== "completed" ? (
        <div className={bannerClass(currentStatus)} role="status">
          {currentStatus === "partial" ? "The run finished with partial output." : null}
          {currentStatus === "failed" ? "The run failed before synthesis completed." : null}
          {currentStatus === "canceled" ? "The run was canceled and preserved its checkpoint." : null}
        </div>
      ) : null}

      <nav className="review-run-page__tabs" aria-label="Run sections">
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
                <span className="run-tab__label">{tab.label}</span>
                <span className="run-tab__note">{tab.note}</span>
              </span>
            </button>
          );
        })}
      </nav>

      {activeTab === "overview" ? (
        <section className="run-layout">
          <article className="run-panel">
            <h2>Overview</h2>
            <dl className="run-summary">
              <div>
                <dt>Status</dt>
                <dd>{statusLabel(currentStatus)}</dd>
              </div>
              <div>
                <dt>Connection</dt>
                <dd>{connectionState}</dd>
              </div>
              <div>
                <dt>Events</dt>
                <dd>{events.length}</dd>
              </div>
              <div>
                <dt>Findings</dt>
                <dd>{findingsQuery.data.length}</dd>
              </div>
            </dl>
          </article>

          <article className="run-panel">
            <h2>Reviewers</h2>
            <div className="run-reviewer-stack">
              {(reviewQuery.data?.selected_agents ?? []).map((reference) => (
                <div className="run-reviewer" key={reference}>
                  <strong>{reviewerLabel(reference)}</strong>
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
                <h2>{findingsQuery.data.length} finding{findingsQuery.data.length === 1 ? "" : "s"}</h2>
              </div>
              <span className="run-panel__status">{statusLabel(currentStatus)}</span>
            </div>
            <FindingList
              findings={findingsQuery.data}
              selectedFindingId={selectedFindingId}
              onSelect={setSelectedFindingId}
            />
          </article>
          <article className="run-panel run-panel--detail">
            <FindingDetail finding={selectedFinding} />
          </article>
        </section>
      ) : null}

      {activeTab === "agent_runs" ? (
        <section className="run-layout">
          <article className="run-panel run-panel--wide">
            <div className="run-panel__heading">
              <div>
                <p className="run-panel__eyebrow">Agent runs</p>
                <h2>Event stream</h2>
              </div>
              <span className="run-panel__status">{connectionState}</span>
            </div>
            <ul className="event-log">
              {events.length > 0 ? (
                events.map((event) => (
                  <li className="event-log__item" key={`${event.type}-${event.id}`}>
                    <span className="event-log__type">{event.type}</span>
                    <span className="event-log__id">#{event.id}</span>
                    <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                  </li>
                ))
              ) : (
                <li className="event-log__empty">Waiting for events.</li>
              )}
            </ul>
          </article>
        </section>
      ) : null}

      {activeTab === "artifacts" ? (
        <section className="run-layout">
          <article className="run-panel run-panel--wide">
            <h2>Artifacts</h2>
            <p className="run-muted">
              Artifact browsing lands in Phase 6. The run already persists findings and events.
            </p>
          </article>
        </section>
      ) : null}
    </section>
  );
}
