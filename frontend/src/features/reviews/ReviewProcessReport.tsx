import { Bot, Clock3, Coins, Wrench } from "lucide-react";

import { useI18n } from "../../shared/i18n/i18n";
import type { ReviewProcessReport as ProcessReport } from "./api";

/** Present terminal execution metrics in a compact, comparison-oriented report. */
export function ReviewProcessReport({ report }: { report: ProcessReport }) {
  const { locale, t } = useI18n();
  const number = new Intl.NumberFormat(locale);

  return (
    <article className="run-panel run-panel--wide process-report">
      <div className="run-panel__heading">
        <div>
          <p className="run-panel__eyebrow">{t("run.processReportNote")}</p>
          <h2>{t("run.processReport")}</h2>
        </div>
        <span className="run-panel__status">{report.status}</span>
      </div>

      {!report.usage_is_complete ? (
        <p className="process-report__warning">{t("run.usageIncomplete")}</p>
      ) : null}

      <dl className="process-report__metrics">
        <Metric icon={Bot} label={t("run.llmCalls")} value={number.format(report.llm_call_count)} />
        <Metric icon={Coins} label={t("run.totalTokens")} value={number.format(report.total_tokens)} />
        <Metric icon={Wrench} label={t("run.toolCalls")} value={number.format(report.tool_call_count)} />
        <Metric icon={Clock3} label={t("run.duration")} value={formatDuration(report.duration_ms, locale)} />
        <Metric icon={Coins} label={t("run.inputTokens")} value={number.format(report.input_tokens)} />
        <Metric icon={Coins} label={t("run.outputTokens")} value={number.format(report.output_tokens)} />
        <Metric icon={Bot} label={t("run.agentRuns")} value={number.format(report.agent_run_count)} />
        <Metric icon={Wrench} label={t("run.findings")} value={number.format(report.finding_count)} />
      </dl>

      <div className="process-report__tables">
        <section aria-labelledby="tool-usage-heading">
          <h3 id="tool-usage-heading">{t("run.toolUsage")}</h3>
          {report.tools.length > 0 ? (
            <div className="process-report__table">
              <div className="process-report__row process-report__row--header">
                <span>{t("run.tool")}</span>
                <span>{t("run.calls")}</span>
                <span>{t("run.results")}</span>
              </div>
              {report.tools.map((tool) => (
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
            {report.agents.map((agent) => (
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
