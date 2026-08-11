import { Bot, Clock3, Coins, Wrench } from "lucide-react";

import { useI18n } from "../../shared/i18n/i18n";
import type {
  AgentProcessSummary,
  ReviewProcessReport as ProcessReport,
  ToolUsageSummary,
  TranscriptEntry,
} from "./api";

/** Present terminal execution metrics in a compact, comparison-oriented report. */
export function ReviewProcessReport({
  report,
  agentReferences,
  entries = [],
  scopeLabel,
  isEmbedded = false,
}: {
  report: ProcessReport;
  agentReferences?: readonly string[];
  entries?: readonly TranscriptEntry[];
  scopeLabel?: string;
  isEmbedded?: boolean;
}) {
  const { locale, t } = useI18n();
  const number = new Intl.NumberFormat(locale);
  const isFiltered = agentReferences !== undefined;
  const selectedAgents = new Set(agentReferences);
  const agents = isFiltered
    ? report.agents.filter((agent) => selectedAgents.has(agent.agent))
    : report.agents;
  const totals = isFiltered ? summarizeAgents(agents) : report;
  const tools = isFiltered ? summarizeTools(entries, selectedAgents) : report.tools;
  const findingCount = isFiltered
    ? countCandidates(entries, selectedAgents)
    : report.finding_count;
  const invalidTools = report.invalid_tools ?? [];

  return (
    <article
      aria-label={t("run.processReport")}
      className={`run-panel run-panel--wide process-report${isEmbedded ? " process-report--embedded" : ""}`}
    >
      <div className="run-panel__heading">
        <div>
          <p className="run-panel__eyebrow">{t("run.processReportNote")}</p>
          <h2>{t("run.processReport")}</h2>
        </div>
        <span className="run-panel__status">{scopeLabel ?? report.status}</span>
      </div>

      {!report.usage_is_complete ? (
        <p className="process-report__warning">{t("run.usageIncomplete")}</p>
      ) : null}

      <dl className="process-report__metrics">
        <Metric icon={Bot} label={t("run.llmCalls")} value={number.format(totals.llm_call_count)} />
        <Metric icon={Coins} label={t("run.totalTokens")} value={number.format(totals.total_tokens)} />
        <Metric icon={Wrench} label={t("run.toolCalls")} value={number.format(totals.tool_call_count)} />
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
            <div className="process-report__row process-report__row--header">
              <span>{t("run.invalidToolName")}</span>
              <span>{t("run.calls")}</span>
              <span>{t("run.results")}</span>
            </div>
            {invalidTools.map((tool) => (
              <div className="process-report__row" key={tool.tool_name}>
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
                <span>{t("run.calls")}</span>
                <span>{t("run.results")}</span>
              </div>
              {tools.map((tool) => (
                <div className="process-report__row" key={tool.tool_name}>
                  <code>{tool.tool_name}</code>
                  <span>{number.format(tool.call_count)}</span>
                  <span>{number.format(tool.result_count)}</span>
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
    </article>
  );
}

type UsageTotals = Pick<
  ProcessReport,
  "llm_call_count" | "input_tokens" | "cached_input_tokens" | "cache_write_input_tokens" | "context_compaction_count" | "context_compacted_result_count" | "context_compaction_original_bytes" | "context_compaction_compressed_bytes" | "output_tokens" | "total_tokens" | "tool_call_count" | "duration_ms"
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
    const current = totals.get(toolName) ?? { tool_name: toolName, call_count: 0, result_count: 0 };
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
    if (current !== undefined) current.result_count += 1;
  }
  return Array.from(totals.values()).sort((left, right) =>
    right.call_count - left.call_count || left.tool_name.localeCompare(right.tool_name),
  );
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
