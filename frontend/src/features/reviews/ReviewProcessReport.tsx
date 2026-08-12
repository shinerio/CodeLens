import { Bot, Clock3, Coins, Wrench } from "lucide-react";
import { useMemo, useState } from "react";

import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";
import type {
  AgentProcessSummary,
  ReviewProcessReport as ProcessReport,
  ToolUsageSummary,
  TranscriptEntry,
} from "./api";
import type { ReviewPlanNodeRole, ReviewPlanProjection } from "./types";

type StageSelection = "all" | ReviewPlanNodeRole;

const STAGE_OPTIONS: ReadonlyArray<{ id: ReviewPlanNodeRole; labelKey: TranslationKey }> = [
  { id: "planner", labelKey: "logs.stagePlanner" },
  { id: "reviewer", labelKey: "logs.stageReviewers" },
  { id: "verifier", labelKey: "logs.stageVerifier" },
];

/** Present live execution metrics in a compact, comparison-oriented report. */
export function ReviewProcessReport({
  report,
  entries = [],
  plan,
  reviewerReferences: fallbackReviewerReferences = [],
}: {
  report: ProcessReport;
  entries?: readonly TranscriptEntry[];
  plan?: ReviewPlanProjection | null;
  reviewerReferences?: readonly string[];
}) {
  const { locale, t } = useI18n();
  const [selectedStage, setSelectedStage] = useState<StageSelection>("all");
  const [selectedReviewer, setSelectedReviewer] = useState("all");
  const number = new Intl.NumberFormat(locale);
  const nodes = plan?.nodes ?? [];
  const reviewerReferences = Array.from(new Set([
    ...nodes.filter((node) => node.node_type === "reviewer").map((node) => node.agent_reference),
    ...(plan === null || plan === undefined ? fallbackReviewerReferences : []),
  ]));
  const nodeRoleByAgent = useMemo(
    () => new Map<string, ReviewPlanNodeRole>([
      ...nodes.map((node): [string, ReviewPlanNodeRole] => [node.agent_reference, node.node_type]),
      ...(plan === null || plan === undefined
        ? fallbackReviewerReferences.map((reference): [string, ReviewPlanNodeRole] => [reference, "reviewer"])
        : []),
    ]),
    [fallbackReviewerReferences, nodes, plan],
  );
  const selectedAgentReferences = selectedStage === "all"
    ? undefined
    : selectedStage === "reviewer" && selectedReviewer !== "all"
      ? [selectedReviewer]
      : Array.from(nodeRoleByAgent.entries())
        .filter(([, role]) => role === selectedStage)
        .map(([reference]) => reference);
  const isFiltered = selectedAgentReferences !== undefined;
  const selectedAgents = new Set(selectedAgentReferences);
  const agents = isFiltered
    ? report.agents.filter((agent) => selectedAgents.has(agent.agent))
    : report.agents;
  const totals = isFiltered ? summarizeAgents(agents) : report;
  const tools = isFiltered ? summarizeTools(entries, selectedAgents) : report.tools;
  const findingCount = isFiltered
    ? countCandidates(entries, selectedAgents)
    : report.finding_count;
  const invalidTools = report.invalid_tools ?? [];
  const rejectedToolCalls = isFiltered
    ? report.rejected_tool_calls.filter((call) => selectedAgents.has(call.agent))
    : report.rejected_tool_calls;
  const scopeLabel = selectedStage === "all"
    ? t("logs.allStages")
    : selectedStage === "reviewer" && selectedReviewer !== "all"
      ? reviewerDisplayName(selectedReviewer, t)
      : t(STAGE_OPTIONS.find((stage) => stage.id === selectedStage)?.labelKey ?? "logs.allStages");
  const availableStages = STAGE_OPTIONS.filter((stage) =>
    nodes.some((node) => node.node_type === stage.id)
      || (stage.id === "reviewer" && reviewerReferences.length > 0),
  );

  return (
    <article
      aria-label={t("run.processReport")}
      className="run-panel run-panel--wide process-report"
    >
      <div className="run-panel__heading">
        <div>
          <p className="run-panel__eyebrow">{t("run.processReportNote")}</p>
          <h2>{t("run.processReport")}</h2>
        </div>
        <span className="run-panel__status">{scopeLabel}</span>
      </div>

      {availableStages.length > 0 ? (
        <div className="review-console__scope">
          <nav className="review-console__stage-nav" aria-label={t("logs.stageNavigator")}>
            <ScopeButton isActive={selectedStage === "all"} label={t("logs.allStages")} onClick={() => {
              setSelectedStage("all");
              setSelectedReviewer("all");
            }} />
            {availableStages.map((stage) => (
              <ScopeButton isActive={selectedStage === stage.id} key={stage.id} label={t(stage.labelKey)} onClick={() => {
                setSelectedStage(stage.id);
                setSelectedReviewer("all");
              }} />
            ))}
          </nav>
          {selectedStage === "reviewer" && reviewerReferences.length > 1 ? (
            <nav className="review-console__reviewer-nav" aria-label={t("logs.reviewerNavigator")}>
              <ScopeButton isActive={selectedReviewer === "all"} label={t("logs.allReviewers")} onClick={() => setSelectedReviewer("all")} />
              {reviewerReferences.map((reference) => (
                <ScopeButton isActive={selectedReviewer === reference} key={reference} label={reviewerDisplayName(reference, t)} onClick={() => setSelectedReviewer(reference)} />
              ))}
            </nav>
          ) : null}
        </div>
      ) : null}

      {!report.usage_is_complete ? (
        <p className="process-report__warning">{t("run.usageIncomplete")}</p>
      ) : null}

      <dl className="process-report__metrics">
        <Metric icon={Bot} label={t("run.llmCalls")} value={number.format(totals.llm_call_count)} />
        <Metric icon={Coins} label={t("run.totalTokens")} value={number.format(totals.total_tokens)} />
        <Metric icon={Wrench} label={t("run.toolCalls")} value={number.format(totals.tool_call_count)} />
        <Metric icon={Wrench} label={t("run.acceptedToolCalls")} value={number.format(totals.accepted_tool_call_count)} />
        <Metric icon={Wrench} label={t("run.rejectedToolCalls")} value={number.format(totals.rejected_tool_call_count)} />
        <Metric icon={Wrench} label={t("run.unclassifiedToolCalls")} value={number.format(totals.unclassified_tool_call_count)} />
        <Metric icon={Clock3} label={t("run.duration")} value={formatDuration(totals.duration_ms, locale)} />
        <Metric icon={Coins} label={t("run.inputTokens")} value={number.format(totals.input_tokens)} />
        <Metric icon={Coins} label={t("run.cachedInputTokens")} value={number.format(totals.cached_input_tokens ?? 0)} />
        <Metric icon={Coins} label={t("run.cacheWriteInputTokens")} value={number.format(totals.cache_write_input_tokens ?? 0)} />
        <Metric icon={Wrench} label={t("run.contextCompactions")} value={number.format(totals.context_compaction_count ?? 0)} />
        <Metric icon={Wrench} label={t("run.contextCompactedResults")} value={number.format(totals.context_compacted_result_count ?? 0)} />
        <Metric icon={Coins} label={t("run.contextCompactionOriginalBytes")} value={number.format(totals.context_compaction_original_bytes ?? 0)} />
        <Metric icon={Coins} label={t("run.contextCompactionCompressedBytes")} value={number.format(totals.context_compaction_compressed_bytes ?? 0)} />
        <Metric icon={Coins} label={t("run.outputTokens")} value={number.format(totals.output_tokens)} />
        <Metric icon={Bot} label={t("run.agentRuns")} value={number.format(agents.length)} />
        <Metric icon={Wrench} label={t("run.findings")} value={number.format(findingCount)} />
      </dl>

      {invalidTools.length > 0 ? (
        <section aria-labelledby="invalid-tool-usage-heading">
          <h3 id="invalid-tool-usage-heading">{t("run.invalidToolUsage")}</h3>
          <p className="process-report__warning">{t("run.invalidToolUsageNote")}</p>
          <div className="process-report__table">
            <div className="process-report__row process-report__row--invalid process-report__row--header">
              <span>{t("run.invalidToolName")}</span>
              <span>{t("run.calls")}</span>
              <span>{t("run.results")}</span>
            </div>
            {invalidTools.map((tool) => (
              <div className="process-report__row process-report__row--invalid" key={tool.tool_name}>
                <code>{tool.tool_name}</code>
                <span>{number.format(tool.call_count)}</span>
                <span>0</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className="process-report__tables">
        <section aria-labelledby="tool-usage-heading">
          <h3 id="tool-usage-heading">{t("run.toolUsage")}</h3>
          {tools.length > 0 ? (
            <div className="process-report__table">
              <div className="process-report__row process-report__row--header">
                <span>{t("run.tool")}</span>
                <span>{t("run.attempts")}</span>
                <span>{t("run.accepted")}</span>
                <span>{t("run.rejected")}</span>
                <span>{t("run.unclassified")}</span>
              </div>
              {tools.map((tool) => (
                <div className="process-report__row" key={tool.tool_name}>
                  <code>{tool.tool_name}</code>
                  <span>{number.format(tool.call_count)}</span>
                  <span>{number.format(tool.accepted_call_count)}</span>
                  <span>{number.format(tool.rejected_call_count)}</span>
                  <span>{number.format(tool.unclassified_call_count)}</span>
                </div>
              ))}
            </div>
          ) : <p className="run-muted">{t("run.noToolCalls")}</p>}
        </section>

        <section aria-labelledby="agent-usage-heading">
          <h3 id="agent-usage-heading">{t("run.agentUsage")}</h3>
          <div className="process-report__table process-report__table--agents">
            <div className="process-report__row process-report__row--agent process-report__row--header">
              <span>{t("run.agent")}</span>
              <span>{t("run.model")}</span>
              <span>{t("run.llmCalls")}</span>
              <span>{t("run.tokens")}</span>
            </div>
            {agents.map((agent) => (
              <div className="process-report__row process-report__row--agent" key={agent.agent}>
                <code>{agent.agent}</code>
                <span>{agent.model_name ?? "-"}</span>
                <span>{number.format(agent.llm_call_count)}</span>
                <span>{number.format(agent.total_tokens)}</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      {rejectedToolCalls.length > 0 ? (
        <section aria-labelledby="rejected-tool-calls-heading">
          <h3 id="rejected-tool-calls-heading">{t("run.rejectedToolCallReasons")}</h3>
          <div className="process-report__rejections">
            {rejectedToolCalls.map((call, index) => (
              <div className="process-report__rejection" key={`${call.agent}:${call.tool_call_id ?? index}`}>
                <code>{call.tool_name}</code>
                <strong>{call.reason_code}</strong>
                <span>{call.reason}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </article>
  );
}

type UsageTotals = Pick<
  ProcessReport,
  "llm_call_count" | "input_tokens" | "cached_input_tokens" | "cache_write_input_tokens" | "context_compaction_count" | "context_compacted_result_count" | "context_compaction_original_bytes" | "context_compaction_compressed_bytes" | "output_tokens" | "total_tokens" | "tool_call_count" | "accepted_tool_call_count" | "rejected_tool_call_count" | "unclassified_tool_call_count" | "duration_ms"
>;

function summarizeAgents(agents: readonly AgentProcessSummary[]): UsageTotals {
  const startedAt = agents
    .map((agent) => agent.started_at)
    .filter((value): value is string => value !== null)
    .sort()[0];
  const completedAt = agents
    .map((agent) => agent.completed_at)
    .filter((value): value is string => value !== null)
    .sort()
    .at(-1);
  return {
    llm_call_count: agents.reduce((total, agent) => total + agent.llm_call_count, 0),
    input_tokens: agents.reduce((total, agent) => total + agent.input_tokens, 0),
    cached_input_tokens: agents.reduce((total, agent) => total + (agent.cached_input_tokens ?? 0), 0),
    cache_write_input_tokens: agents.reduce((total, agent) => total + (agent.cache_write_input_tokens ?? 0), 0),
    context_compaction_count: agents.reduce((total, agent) => total + (agent.context_compaction_count ?? 0), 0),
    context_compacted_result_count: agents.reduce((total, agent) => total + (agent.context_compacted_result_count ?? 0), 0),
    context_compaction_original_bytes: agents.reduce((total, agent) => total + (agent.context_compaction_original_bytes ?? 0), 0),
    context_compaction_compressed_bytes: agents.reduce((total, agent) => total + (agent.context_compaction_compressed_bytes ?? 0), 0),
    output_tokens: agents.reduce((total, agent) => total + agent.output_tokens, 0),
    total_tokens: agents.reduce((total, agent) => total + agent.total_tokens, 0),
    tool_call_count: agents.reduce((total, agent) => total + agent.tool_call_count, 0),
    accepted_tool_call_count: agents.reduce((total, agent) => total + agent.accepted_tool_call_count, 0),
    rejected_tool_call_count: agents.reduce((total, agent) => total + agent.rejected_tool_call_count, 0),
    unclassified_tool_call_count: agents.reduce((total, agent) => total + agent.unclassified_tool_call_count, 0),
    duration_ms: startedAt === undefined || completedAt === undefined
      ? null
      : Math.max(0, Date.parse(completedAt) - Date.parse(startedAt)),
  };
}

function summarizeTools(
  entries: readonly TranscriptEntry[],
  selectedAgents: ReadonlySet<string>,
): ToolUsageSummary[] {
  const callsById = new Map<string, string>();
  const pendingCalls = new Map<string, string[]>();
  const totals = new Map<string, ToolUsageSummary>();
  for (const entry of entries) {
    const agent = entry.metadata.agent ?? "";
    if (entry.kind !== "tool_call" || !selectedAgents.has(agent)) continue;
    const toolName = entry.metadata.tool_name;
    if (toolName === undefined) continue;
    const current = totals.get(toolName) ?? { tool_name: toolName, call_count: 0, result_count: 0, accepted_call_count: 0, rejected_call_count: 0, unclassified_call_count: 0 };
    current.call_count += 1;
    totals.set(toolName, current);
    pendingCalls.set(agent, [...(pendingCalls.get(agent) ?? []), toolName]);
    if (entry.metadata.tool_call_id !== undefined) {
      callsById.set(`${agent}\u0000${entry.metadata.tool_call_id}`, toolName);
    }
  }
  for (const entry of entries) {
    if (entry.kind !== "tool_result") continue;
    const agent = entry.metadata.agent ?? "";
    if (!selectedAgents.has(agent)) continue;
    const callId = entry.metadata.tool_call_id;
    let toolName = callId === undefined ? undefined : callsById.get(`${agent}\u0000${callId}`);
    const agentPendingCalls = pendingCalls.get(agent) ?? [];
    if (toolName !== undefined) {
      const pendingIndex = agentPendingCalls.indexOf(toolName);
      if (pendingIndex >= 0) agentPendingCalls.splice(pendingIndex, 1);
    } else {
      toolName = agentPendingCalls.shift();
    }
    if (toolName === undefined) continue;
    const current = totals.get(toolName);
    if (current !== undefined) {
      current.result_count += 1;
      const outcome = toolOutcome(entry);
      if (outcome === "accepted") current.accepted_call_count += 1;
      else if (outcome === "rejected") current.rejected_call_count += 1;
      else current.unclassified_call_count += 1;
    }
  }
  return Array.from(totals.values()).sort((left, right) =>
    right.call_count - left.call_count || left.tool_name.localeCompare(right.tool_name),
  );
}

function toolOutcome(entry: TranscriptEntry): "accepted" | "rejected" | undefined {
  const recordedOutcome = entry.metadata.tool_outcome;
  if (recordedOutcome === "accepted" || recordedOutcome === "rejected") return recordedOutcome;
  let value: unknown = entry.content;
  for (let depth = 0; depth < 2 && typeof value === "string"; depth += 1) {
    try {
      value = JSON.parse(value) as unknown;
    } catch {
      break;
    }
  }
  if (typeof value === "string") {
    const normalized = value.toLocaleLowerCase();
    if (normalized.includes("invalid json input for tool") || normalized.includes("validation error for") || normalized.includes("an error occurred while running the tool")) return "rejected";
    return "accepted";
  }
  if (typeof value === "object" && value !== null) {
    const result = value as Record<string, unknown>;
    if (result.accepted === false || result.success === false || result.ok === false) return "rejected";
  }
  return "accepted";
}

function ScopeButton({ isActive, label, onClick }: { isActive: boolean; label: string; onClick: () => void }) {
  return <button aria-pressed={isActive} className={isActive ? "review-console__scope-button review-console__scope-button--active" : "review-console__scope-button"} onClick={onClick} type="button"><span>{label}</span></button>;
}

function reviewerDisplayName(
  reference: string,
  t: (key: TranslationKey, values?: Record<string, string>) => string,
): string {
  const [agentId] = reference.split(":");
  if (agentId.length === 0) return reference;
  const name = `${agentId[0].toUpperCase()}${agentId.slice(1)}`;
  return t("run.reviewer", { name });
}

/** Count model-output candidates emitted by the selected agents. */
function countCandidates(
  entries: readonly TranscriptEntry[],
  selectedAgents: ReadonlySet<string>,
): number {
  let total = 0;
  for (const entry of entries) {
    if (entry.kind !== "model_output") continue;
    const agent = entry.metadata.agent ?? "";
    if (!selectedAgents.has(agent)) continue;
    try {
      const parsed = JSON.parse(entry.content) as unknown;
      if (typeof parsed === "object" && parsed !== null && Array.isArray((parsed as Record<string, unknown>).candidates)) {
        total += (parsed as Record<string, unknown[]>).candidates.length;
      }
    } catch {
      // Skip unparseable entries — they don't contribute to the candidate count.
    }
  }
  return total;
}

function Metric({ icon: Icon, label, value }: { icon: typeof Bot; label: string; value: string }) {
  return <div><dt><Icon aria-hidden="true" />{label}</dt><dd>{value}</dd></div>;
}

function formatDuration(durationMs: number | null, locale: "en" | "zh-CN") {
  if (durationMs === null) return "-";
  const seconds = durationMs / 1_000;
  if (seconds < 60) return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return `${minutes}m ${remainingSeconds}s`;
}
