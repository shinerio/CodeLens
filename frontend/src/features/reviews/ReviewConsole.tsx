import { Brain, ChevronDown, ChevronRight, Search, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

import type { TranscriptEntry } from "./api";

type ConsoleMessage = TranscriptEntry & { content: string; sequence: number };
type ConsoleVisibility = {
  prompt: boolean;
  reasoning: boolean;
  output: boolean;
  tools: boolean;
};

const DEFAULT_VISIBILITY: ConsoleVisibility = {
  prompt: true,
  reasoning: true,
  output: true,
  tools: false,
};

/** Render the durable execution transcript as a lossless, collapsible conversation. */
export function ReviewConsole({ entries }: { entries: TranscriptEntry[] }) {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [visibility, setVisibility] = useState<ConsoleVisibility>(DEFAULT_VISIBILITY);
  const messages = useMemo(() => coalesceDeltas(entries), [entries]);
  const completedMessages = useMemo(() => completedMessageKeys(entries), [entries]);
  const visible = messages.filter((entry) => isVisible(entry, visibility) && entry.content.toLocaleLowerCase().includes(query.toLocaleLowerCase()));

  function toggle(sequence: number) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(sequence)) next.delete(sequence); else next.add(sequence);
      return next;
    });
  }

  return <section className="review-console" aria-label="Review execution console">
    <div className="review-console__toolbar">
      <label className="review-console__search"><Search aria-hidden="true" /><span className="sr-only">Search console</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search complete execution output" /></label>
      <fieldset className="review-console__filters">
        <legend>Output</legend>
        <FilterOption label="Prompt" checked={visibility.prompt} onChange={() => setVisibility((value) => ({ ...value, prompt: !value.prompt }))} />
        <FilterOption label="Thinking" checked={visibility.reasoning} onChange={() => setVisibility((value) => ({ ...value, reasoning: !value.reasoning }))} />
        <FilterOption label="Model output" checked={visibility.output} onChange={() => setVisibility((value) => ({ ...value, output: !value.output }))} />
        <FilterOption label="Tools" checked={visibility.tools} onChange={() => setVisibility((value) => ({ ...value, tools: !value.tools }))} />
      </fieldset>
      <button type="button" onClick={() => setCollapsed(new Set(messages.map((entry) => entry.sequence)))}>Collapse all</button>
      <button type="button" onClick={() => setCollapsed(new Set())}>Expand all</button>
    </div>
    <ol className="review-console__messages">
      {visible.map((entry) => {
        const isCollapsed = collapsed.has(entry.sequence);
        const isTool = entry.kind === "tool_call" || entry.kind === "tool_result";
        const isReasoning = entry.kind === "model_reasoning_delta";
        const isModel = isReasoning || entry.kind === "model_output" || entry.kind === "model_output_delta" || entry.kind === "model_completed";
        const isFinalizedStream = isDelta(entry)
          && entry.metadata.message_id !== undefined
          && completedMessages.has(entry.metadata.message_id);
        return <li className={`review-console__message review-console__message--${isTool ? "tool" : isModel ? "model" : "system"}`} key={entry.sequence}>
          <button className="review-console__message-head" type="button" onClick={() => toggle(entry.sequence)} aria-expanded={!isCollapsed}>
            {isCollapsed ? <ChevronRight aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
            {isTool ? <Wrench aria-hidden="true" /> : isReasoning ? <Brain aria-hidden="true" /> : <span className="review-console__avatar">{isModel ? "AI" : "SYS"}</span>}
            <span>{labelFor(entry.kind)}</span><time dateTime={entry.created_at}>#{entry.sequence}</time>
          </button>
          {!isCollapsed ? <ConsoleContent entry={entry} isFinalizedStream={isFinalizedStream} /> : null}
          {entry.redacted ? <small>Credential redacted</small> : null}
        </li>;
      })}
      {visible.length === 0 ? <li className="event-log__empty">No matching execution output.</li> : null}
    </ol>
  </section>;
}

function coalesceDeltas(entries: TranscriptEntry[]): ConsoleMessage[] {
  const result: ConsoleMessage[] = [];
  for (const entry of entries) {
    const previous = result.at(-1);
    if (isDelta(entry) && previous !== undefined && previous.kind === entry.kind && previous.metadata.message_id === entry.metadata.message_id) {
      previous.content += entry.content;
      continue;
    }
    result.push({ ...entry });
  }
  return result;
}

function completedMessageKeys(entries: TranscriptEntry[]): Set<string> {
  return new Set(entries.flatMap((entry) => (
    (entry.kind === "model_reasoning_completed" || entry.kind === "model_output_completed") && entry.metadata.message_id
      ? [entry.metadata.message_id]
      : []
  )));
}

function isVisible(entry: ConsoleMessage, visibility: ConsoleVisibility) {
  if (entry.kind === "prompt") return visibility.prompt;
  if (entry.kind === "model_reasoning_delta") return visibility.reasoning;
  if (entry.kind === "model_output" || entry.kind === "model_output_delta") return visibility.output;
  if (entry.kind === "tool_call" || entry.kind === "tool_result") return visibility.tools;
  return false;
}

function isDelta(entry: TranscriptEntry) {
  return entry.kind === "model_reasoning_delta" || entry.kind === "model_output_delta";
}

function FilterOption({ label, checked, onChange }: { label: string; checked: boolean; onChange: () => void }) {
  return <label><input type="checkbox" checked={checked} onChange={onChange} />{label}</label>;
}

function labelFor(kind: TranscriptEntry["kind"]) {
  if (kind === "model_reasoning_delta") return "AI thinking summary";
  if (kind === "model_output_delta") return "AI output";
  if (kind === "model_output") return "Final structured output";
  return kind.replaceAll("_", " ");
}

function ConsoleContent({ entry, isFinalizedStream }: { entry: ConsoleMessage; isFinalizedStream: boolean }) {
  if (entry.kind === "prompt") return <PromptContent content={entry.content} />;
  if (entry.kind === "model_output") return <ModelOutputContent content={entry.content} />;
  if (isDelta(entry) && isFinalizedStream) return <MarkdownContent content={entry.content} />;
  return <pre className={entry.kind === "model_reasoning_delta" ? "review-console__content review-console__content--thinking" : "review-console__content"}>{entry.content}</pre>;
}

function PromptContent({ content }: { content: string }) {
  const prompt = objectValue(content);
  if (prompt === null) return <pre className="review-console__content">{content}</pre>;
  return <div className="review-console__prompt">
    <section><h3>System instructions</h3><MarkdownContent content={stringValue(prompt.system_instructions)} /></section>
    <section><h3>Review input</h3><StructuredValue value={parseNested(prompt.user_input)} /></section>
  </div>;
}

function ModelOutputContent({ content }: { content: string }) {
  const output = objectValue(content);
  const findings = Array.isArray(output?.findings) ? output.findings.filter(isRecord) : [];
  if (output === null) return <MarkdownContent content={content} />;
  return <div className="review-console__output">
    <p className="review-console__output-summary">Final structured result · {findings.length} finding{findings.length === 1 ? "" : "s"}</p>
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
