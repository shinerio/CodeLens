import { Brain, ChevronDown, ChevronRight, Search, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";
import type { ReviewProcessReport as ProcessReport, TranscriptEntry } from "./api";
import { failureDetails } from "./failure-details";
import { ReviewProcessReport } from "./ReviewProcessReport";
import type { ReviewPlanNodeRole, ReviewPlanProjection } from "./types";

type ConsoleMessage = TranscriptEntry & { content: string; messageKey: string; sequence: number };
type StreamingTranscriptEntry = TranscriptEntry & {
  kind: "model_reasoning_delta" | "model_output_delta";
};
type ConsoleVisibility = {
  prompt: boolean;
  reasoning: boolean;
  output: boolean;
  tools: boolean;
  rawResponses: boolean;
};

const DEFAULT_VISIBILITY: ConsoleVisibility = {
  prompt: true,
  reasoning: true,
  output: true,
  tools: false,
  rawResponses: false,
};

type StageSelection = "all" | ReviewPlanNodeRole;

const STAGE_OPTIONS: ReadonlyArray<{
  id: ReviewPlanNodeRole;
  labelKey: TranslationKey;
}> = [
  { id: "planner", labelKey: "logs.stagePlanner" },
  { id: "reviewer", labelKey: "logs.stageReviewers" },
  { id: "verifier", labelKey: "logs.stageVerifier" },
];

/** Render the durable transcript as a lossless timeline scoped by Plan stage and Reviewer. */
export function ReviewConsole({
  entries,
  plan,
  processReport,
  reviewerReferences: fallbackReviewerReferences = [],
}: {
  entries: TranscriptEntry[];
  plan?: ReviewPlanProjection | null;
  processReport?: ProcessReport;
  reviewerReferences?: readonly string[];
}) {
  const { locale, t } = useI18n();
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [visibility, setVisibility] = useState<ConsoleVisibility>(DEFAULT_VISIBILITY);
  const [selectedStage, setSelectedStage] = useState<StageSelection>("all");
  const [selectedReviewer, setSelectedReviewer] = useState("all");
  const nodes = plan?.nodes ?? [];
  const reviewerReferences = Array.from(new Set([
    ...nodes
      .filter((node) => node.node_type === "reviewer")
      .map((node) => node.agent_reference),
    ...(plan === null || plan === undefined ? fallbackReviewerReferences : []),
  ]));
  const nodeRoleByAgent = useMemo(
    () => new Map<string, ReviewPlanNodeRole>([
      ...nodes.map((node): [string, ReviewPlanNodeRole] => [node.agent_reference, node.node_type]),
      ...(
        plan === null || plan === undefined
          ? fallbackReviewerReferences.map((reference): [string, ReviewPlanNodeRole] => [reference, "reviewer"])
          : []
      ),
    ]),
    [fallbackReviewerReferences, nodes, plan],
  );
  const availableStages = STAGE_OPTIONS.filter((stage) =>
    nodes.some((node) => node.node_type === stage.id)
      || (stage.id === "reviewer" && reviewerReferences.length > 0),
  );
  const allMessages = useMemo(() => coalesceDeltas(entries), [entries]);
  const messages = useMemo(
    () => allMessages.filter((entry) => {
      if (selectedStage === "all") return true;
      const agent = entry.metadata.agent;
      if (agent === undefined || nodeRoleByAgent.get(agent) !== selectedStage) return false;
      return selectedStage !== "reviewer" || selectedReviewer === "all" || agent === selectedReviewer;
    }),
    [allMessages, nodeRoleByAgent, selectedReviewer, selectedStage],
  );
  const scopedAgentReferences = selectedStage === "all"
    ? undefined
    : selectedStage === "reviewer" && selectedReviewer !== "all"
      ? [selectedReviewer]
      : Array.from(nodeRoleByAgent.entries())
        .filter(([, role]) => role === selectedStage)
        .map(([reference]) => reference);
  const scopeLabel = selectedStage === "all"
    ? t("logs.allStages")
    : selectedStage === "reviewer" && selectedReviewer !== "all"
      ? reviewerDisplayName(selectedReviewer, t)
      : t(STAGE_OPTIONS.find((stage) => stage.id === selectedStage)?.labelKey ?? "logs.allStages");
  const completedMessages = useMemo(() => completedMessageKeys(entries), [entries]);
  const filtered = messages.filter((entry) =>
    (isToolEntry(entry) || isVisible(entry, visibility)) &&
    entry.content.toLocaleLowerCase().includes(query.toLocaleLowerCase()) &&
    !(entry.kind === "model_output_delta" && !entry.content.trim()),
  );
  const visibleCount = filtered.filter((entry) => !isToolEntry(entry) || visibility.tools).length;
  const parseFailed = messages.some((e) => e.kind === "model_raw_output" && e.metadata?.parse_failed === "true");

  function toggle(messageKey: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(messageKey)) next.delete(messageKey); else next.add(messageKey);
      return next;
    });
  }

  return <section className="review-console" aria-label="Review execution console">
    {nodes.length > 0 || reviewerReferences.length > 1 ? (
      <div className="review-console__scope">
        <div className="review-console__stage-heading">
          <div>
            <span>{t("logs.timeline")}</span>
            <strong>{t("logs.chooseStage")}</strong>
          </div>
          <small>{t("logs.visibleEvents", { count: String(messages.length), total: String(allMessages.length) })}</small>
        </div>
        <nav className="review-console__stage-nav" aria-label={t("logs.stageNavigator")}>
          <ScopeButton
            count={allMessages.length}
            isActive={selectedStage === "all"}
            label={t("logs.allStages")}
            onClick={() => {
              setSelectedStage("all");
              setSelectedReviewer("all");
            }}
          />
          {availableStages.map((stage) => (
            <ScopeButton
              count={allMessages.filter((entry) => nodeRoleByAgent.get(entry.metadata.agent ?? "") === stage.id).length}
              isActive={selectedStage === stage.id}
              key={stage.id}
              label={t(stage.labelKey)}
              onClick={() => {
                setSelectedStage(stage.id);
                setSelectedReviewer("all");
              }}
            />
          ))}
        </nav>
        {selectedStage === "reviewer" && reviewerReferences.length > 1 ? (
          <nav className="review-console__reviewer-nav" aria-label={t("logs.reviewerNavigator")}>
            <ScopeButton
              count={allMessages.filter((entry) => nodeRoleByAgent.get(entry.metadata.agent ?? "") === "reviewer").length}
              isActive={selectedReviewer === "all"}
              label={t("logs.allReviewers")}
              onClick={() => setSelectedReviewer("all")}
            />
            {reviewerReferences.map((reference) => (
              <ScopeButton
                count={allMessages.filter((entry) => entry.metadata.agent === reference).length}
                isActive={selectedReviewer === reference}
                key={reference}
                label={reviewerDisplayName(reference, t)}
                onClick={() => setSelectedReviewer(reference)}
              />
            ))}
          </nav>
        ) : null}
      </div>
    ) : null}
    {processReport !== undefined ? (
      <ReviewProcessReport
        agentReferences={scopedAgentReferences}
        entries={entries}
        isEmbedded
        report={processReport}
        scopeLabel={scopeLabel}
      />
    ) : null}
    <div className="review-console__toolbar">
      <label className="review-console__search"><Search aria-hidden="true" /><span className="sr-only">Search console</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search complete execution output" /></label>
      <fieldset className="review-console__filters">
        <legend>Output</legend>
        <FilterOption label="Prompt" checked={visibility.prompt} onChange={(checked) => setVisibility((value) => ({ ...value, prompt: checked }))} />
        <FilterOption label="Thinking" checked={visibility.reasoning} onChange={(checked) => setVisibility((value) => ({ ...value, reasoning: checked }))} />
        <FilterOption label="Model output" checked={visibility.output} onChange={(checked) => setVisibility((value) => ({ ...value, output: checked }))} />
        <FilterOption label="Tools" checked={visibility.tools} onChange={(checked) => setVisibility((value) => ({ ...value, tools: checked }))} />
        <FilterOption label="Raw responses" checked={visibility.rawResponses} onChange={(checked) => setVisibility((value) => ({ ...value, rawResponses: checked }))} />
      </fieldset>
      <button type="button" onClick={() => setCollapsed(new Set(messages.map((entry) => entry.messageKey)))}>Collapse all</button>
      <button type="button" onClick={() => setCollapsed(new Set())}>Expand all</button>
    </div>
    <ol className="review-console__messages">
      {filtered.map((entry) => {
        const isCollapsed = collapsed.has(entry.messageKey);
        const isTool = isToolEntry(entry);
        const isReasoning = entry.kind === "model_reasoning_delta";
        const isModel = isReasoning || entry.kind === "model_output" || entry.kind === "model_output_delta" || entry.kind === "model_completed" || entry.kind === "model_raw_output";
        const isFinalizedStream = isDelta(entry)
          && entry.metadata.message_id !== undefined
          && completedMessages.has(entry.metadata.message_id);
        return <li className={`review-console__message review-console__message--${isTool ? "tool" : isModel ? "model" : "system"}`} hidden={isTool && !visibility.tools} key={entry.messageKey}>
          <button className="review-console__message-head" type="button" onClick={() => toggle(entry.messageKey)} aria-expanded={!isCollapsed}>
            {isCollapsed ? <ChevronRight aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
            {isTool ? <Wrench aria-hidden="true" /> : isReasoning ? <Brain aria-hidden="true" /> : <span className="review-console__avatar">{isModel ? "AI" : "SYS"}</span>}
            <span>{labelFor(entry)}</span><time dateTime={entry.created_at}>#{entry.sequence}</time>
          </button>
          {!isCollapsed ? <ConsoleContent entry={entry} locale={locale} isFinalizedStream={isFinalizedStream} parseFailed={parseFailed} /> : null}
          {entry.redacted ? <small>Credential redacted</small> : null}
        </li>;
      })}
      {visibleCount === 0 ? <li className="event-log__empty">No matching execution output.</li> : null}
    </ol>
  </section>;
}

function ScopeButton({
  count,
  isActive,
  label,
  onClick,
}: {
  count: number;
  isActive: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      aria-pressed={isActive}
      className={isActive ? "review-console__scope-button review-console__scope-button--active" : "review-console__scope-button"}
      onClick={onClick}
      type="button"
    >
      <span>{label}</span>
      <small>{count}</small>
    </button>
  );
}

function reviewerDisplayName(
  reference: string,
  t: (key: TranslationKey, values?: Record<string, string>) => string,
) {
  const [agentId] = reference.split(":");
  if (agentId.length === 0) return reference;
  const name = `${agentId[0].toUpperCase()}${agentId.slice(1)}`;
  return t("run.reviewer", { name });
}

function coalesceDeltas(entries: TranscriptEntry[]): ConsoleMessage[] {
  const result: ConsoleMessage[] = [];
  const activeDeltaByAgent = new Map<
    string,
    Partial<Record<"model_reasoning_delta" | "model_output_delta", {
      messageId: string | undefined;
      resultIndex: number;
    }>>
  >();
  for (const [index, entry] of entries.entries()) {
    const agentKey = entry.metadata.agent ?? "<global>";
    if (isDelta(entry)) {
      const activeByKind = activeDeltaByAgent.get(agentKey) ?? {};
      const activeDelta = activeByKind[entry.kind];
      if (
        activeDelta !== undefined
        && activeDelta.messageId === entry.metadata.message_id
      ) {
        const activeMessage = result[activeDelta.resultIndex];
        if (activeMessage !== undefined) activeMessage.content += entry.content;
        continue;
      }
      result.push({
        ...entry,
        messageKey: `${entry.sequence}:${entry.created_at}:${entry.kind}:${index}`,
      });
      activeByKind[entry.kind] = {
        messageId: entry.metadata.message_id,
        resultIndex: result.length - 1,
      };
      activeDeltaByAgent.set(agentKey, activeByKind);
      continue;
    }
    activeDeltaByAgent.delete(agentKey);
    result.push({
      ...entry,
      messageKey: `${entry.sequence}:${entry.created_at}:${entry.kind}:${index}`,
    });
  }
  return result;
}

function completedMessageKeys(entries: TranscriptEntry[]): Set<string> {
  const completedAgents = new Set(entries.flatMap((entry) => (
    entry.kind === "model_completed" && entry.metadata.agent !== undefined ? [entry.metadata.agent] : []
  )));
  return new Set(entries.flatMap((entry) => (
    (entry.kind === "model_reasoning_completed" || entry.kind === "model_output_completed") && entry.metadata.message_id
      ? [entry.metadata.message_id]
      : isDelta(entry) && entry.metadata.message_id && completedAgents.has(entry.metadata.agent)
        ? [entry.metadata.message_id]
        : []
  )));
}

function isVisible(entry: ConsoleMessage, visibility: ConsoleVisibility) {
  if (entry.kind === "prompt") return visibility.prompt;
  if (entry.kind === "model_reasoning_delta") return visibility.reasoning;
  if (entry.kind === "model_output" || entry.kind === "model_output_delta") return visibility.output;
  if (entry.kind === "model_raw_output") {
    return entry.metadata.parse_failed === "true" ? visibility.output : visibility.rawResponses;
  }
  return false;
}

function isToolEntry(entry: TranscriptEntry) {
  return entry.kind === "tool_call" || entry.kind === "tool_result";
}

function isDelta(entry: TranscriptEntry): entry is StreamingTranscriptEntry {
  return entry.kind === "model_reasoning_delta" || entry.kind === "model_output_delta";
}

function FilterOption({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label><input type="checkbox" checked={checked} onChange={(event) => onChange(event.currentTarget.checked)} />{label}</label>;
}

function labelFor(entry: TranscriptEntry) {
  if (entry.kind === "model_reasoning_delta") return "AI thinking summary";
  if (entry.kind === "model_output_delta") return "AI output";
  if (entry.kind === "model_output") return "Final structured output";
  if (entry.kind === "model_raw_output") {
    return entry.metadata.parse_failed === "true"
      ? "Model raw output (parsing failed)"
      : "Provider raw response";
  }
  return entry.kind.replaceAll("_", " ");
}

function ConsoleContent({ entry, locale, isFinalizedStream, parseFailed }: { entry: ConsoleMessage; locale: "en" | "zh-CN"; isFinalizedStream: boolean; parseFailed: boolean }) {
  if (entry.kind === "lifecycle" && entry.metadata.error_code !== undefined) return <FailureContent metadata={entry.metadata} fallback={entry.content} locale={locale} />;
  if (entry.kind === "prompt") return <PromptContent content={entry.content} />;
  if (entry.kind === "model_output") return <ModelOutputContent content={entry.content} parseFailed={parseFailed} />;
  if (isDelta(entry) && isFinalizedStream) return <MarkdownContent content={entry.content} />;
  return <pre className={entry.kind === "model_reasoning_delta" ? "review-console__content review-console__content--thinking" : "review-console__content"}>{entry.content}</pre>;
}

function FailureContent({ metadata, fallback, locale }: { metadata: Record<string, string>; fallback: string; locale: "en" | "zh-CN" }) {
  const details = failureDetails(metadata, locale);
  return <div className="review-console__failure"><strong>{details.title}</strong><p>{details.description}</p><small>{details.action}</small>{metadata.provider_status_code !== undefined ? <code>HTTP {metadata.provider_status_code}</code> : null}{details.isUnknown ? <pre className="review-console__content">{fallback}</pre> : null}</div>;
}

function PromptContent({ content }: { content: string }) {
  const prompt = objectValue(content);
  if (prompt === null) return <pre className="review-console__content">{content}</pre>;
  return <div className="review-console__prompt">
    <section><h3>System instructions</h3><MarkdownContent content={stringValue(prompt.system_instructions)} /></section>
    <section><h3>Review input</h3><StructuredValue value={parseNested(prompt.user_input)} /></section>
  </div>;
}

function ModelOutputContent({ content, parseFailed }: { content: string; parseFailed: boolean }) {
  const output = objectValue(content);
  const findings = Array.isArray(output?.findings) ? output.findings.filter(isRecord) : [];
  if (output === null) return <MarkdownContent content={content} />;
  return <div className="review-console__output">
    {parseFailed ? (
      <p className="review-console__output-summary">Review completed but model output parsing failed. Check "Model raw output" above for the model's review opinions.</p>
    ) : (
      <p className="review-console__output-summary">Final structured result · {findings.length} finding{findings.length === 1 ? "" : "s"}</p>
    )}
    {findings.map((finding, index) => <article className="review-console__finding" key={`${stringValue(finding.title)}-${index}`}>
      <header><span>{stringValue(finding.severity).toUpperCase() || "UNSPECIFIED"}</span><strong>{stringValue(finding.title) || "Untitled finding"}</strong></header>
      <p>{stringValue(finding.explanation) || stringValue(finding.impact)}</p>
      <dl><div><dt>Location</dt><dd>{locationLabel(finding.primary_location)}</dd></div><div><dt>Recommendation</dt><dd>{stringValue(finding.recommendation) || "—"}</dd></div></dl>
      <details><summary>Evidence and complete finding</summary><StructuredValue value={finding} /></details>
    </article>)}
    <details className="review-console__raw"><summary>Complete structured payload</summary><StructuredValue value={output} /></details>
  </div>;
}

/** Render completed model text as safe Markdown while streamed deltas remain plain text. */
function MarkdownContent({ content }: { content: string }) {
  return <div className="review-console__markdown"><ReactMarkdown>{content}</ReactMarkdown></div>;
}

function StructuredValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) return <ol className="review-console__structured-list">{value.map((item, index) => <li key={index}><StructuredValue value={item} /></li>)}</ol>;
  if (isRecord(value)) return <dl className="review-console__structured">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd><StructuredValue value={parseNested(item)} /></dd></div>)}</dl>;
  return <span>{String(value ?? "—")}</span>;
}

function objectValue(content: string): Record<string, unknown> | null {
  const parsed = parseNested(content);
  return isRecord(parsed) ? parsed : null;
}

function parseNested(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try { return JSON.parse(value) as unknown; } catch { return value; }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function locationLabel(value: unknown): string {
  if (!isRecord(value)) return "—";
  const path = stringValue(value.path);
  const start = typeof value.start_line === "number" ? value.start_line : undefined;
  const end = typeof value.end_line === "number" ? value.end_line : undefined;
  return path && start !== undefined ? `${path}:${start}${end !== undefined && end !== start ? `–${end}` : ""}` : path || "—";
}
