import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";

import { ReviewConsole } from "./ReviewConsole";

it("renders final model output as Markdown after streaming completes", () => {
  render(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "model_output_delta",
          content: "# Partial result",
          created_at: "2026-07-22T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: { message_id: "message-1" },
        },
        {
          sequence: 2,
          kind: "model_output",
          content: "# Review summary\n\n- **Critical**: Check authentication\n- [Reference](https://example.com)",
          created_at: "2026-07-22T00:00:01Z",
          redacted: false,
          truncated: false,
          metadata: { message_id: "message-1" },
        },
      ]}
    />,
  );

  expect(screen.getByText("# Partial result").tagName).toBe("PRE");
  expect(screen.getByRole("heading", { name: "Review summary", level: 1 })).toBeInTheDocument();
  expect(screen.getByText("Critical").tagName).toBe("STRONG");
  expect(screen.getByRole("link", { name: "Reference" })).toHaveAttribute("href", "https://example.com");
});

it("renders completed streamed output as Markdown when the final payload is structured", () => {
  render(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "model_output_delta",
          content: "# Streamed summary\n\n```python\nresult = review()\n```",
          created_at: "2026-07-22T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "correctness:v1", message_id: "message-1" },
        },
        {
          sequence: 2,
          kind: "model_output_completed",
          content: "",
          created_at: "2026-07-22T00:00:01Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "correctness:v1", message_id: "message-1" },
        },
      ]}
    />,
  );

  expect(screen.getByRole("heading", { name: "Streamed summary", level: 1 })).toBeInTheDocument();
  expect(screen.getByText("result = review()").tagName).toBe("CODE");
  expect(screen.queryByText("```python")).not.toBeInTheDocument();
});

it("renders a completed thinking message as Markdown", () => {
  render(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "model_reasoning_delta",
          content: "## Investigation\n\n- Read `review.py`",
          created_at: "2026-07-22T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "correctness:v1", message_id: "reasoning-1" },
        },
        {
          sequence: 2,
          kind: "model_reasoning_completed",
          content: "",
          created_at: "2026-07-22T00:00:01Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "correctness:v1", message_id: "reasoning-1" },
        },
      ]}
    />,
  );

  expect(screen.getByRole("heading", { name: "Investigation", level: 2 })).toBeInTheDocument();
  expect(screen.getByText("review.py").tagName).toBe("CODE");
});

it("renders complete system instructions as Markdown", () => {
  render(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "prompt",
          content: JSON.stringify({ system_instructions: "# Review rules\n\n- Check `auth.py`", user_input: "{}" }),
          created_at: "2026-07-22T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "correctness:v1" },
        },
      ]}
    />,
  );

  expect(screen.getByRole("heading", { name: "Review rules", level: 1 })).toBeInTheDocument();
  expect(screen.getByText("auth.py").tagName).toBe("CODE");
});

it("hides tool calls and results again when the Tools filter is unchecked", () => {
  const { container, rerender } = render(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "tool_call",
          content: "get_diff",
          created_at: "2026-07-22T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: {},
        },
        {
          sequence: 2,
          kind: "tool_result",
          content: "diff output",
          created_at: "2026-07-22T00:00:01Z",
          redacted: false,
          truncated: false,
          metadata: {},
        },
      ]}
    />,
  );

  const consoleView = within(container);
  const tools = consoleView.getByRole("checkbox", { name: "Tools" });
  expect(consoleView.queryByText("get_diff")).not.toBeInTheDocument();
  fireEvent.click(tools);
  expect(consoleView.getByText("get_diff")).toBeInTheDocument();
  expect(consoleView.getByText("diff output")).toBeInTheDocument();
  fireEvent.click(tools);
  expect(consoleView.queryByText("get_diff")).not.toBeInTheDocument();
  expect(consoleView.queryByText("diff output")).not.toBeInTheDocument();

  rerender(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "tool_call",
          content: "get_diff",
          created_at: "2026-07-22T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: {},
        },
        {
          sequence: 2,
          kind: "tool_result",
          content: "diff output",
          created_at: "2026-07-22T00:00:01Z",
          redacted: false,
          truncated: false,
          metadata: {},
        },
        {
          sequence: 3,
          kind: "tool_result",
          content: "late tool output",
          created_at: "2026-07-22T00:00:02Z",
          redacted: false,
          truncated: false,
          metadata: {},
        },
      ]}
    />,
  );

  expect(consoleView.queryByText("get_diff")).not.toBeInTheDocument();
  expect(consoleView.queryByText("diff output")).not.toBeInTheDocument();
  expect(consoleView.queryByText("late tool output")).not.toBeInTheDocument();
});

it("hides successful provider responses by default without calling them parsing failures", () => {
  render(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "model_raw_output",
          content: '{"output":[]}',
          created_at: "2026-07-26T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: { response_index: "1" },
        },
      ]}
    />,
  );

  expect(screen.queryByText("Provider raw response")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Raw responses" }));
  expect(screen.getByText("Provider raw response")).toBeInTheDocument();
  expect(screen.queryByText(/parsing failed/i)).not.toBeInTheDocument();
});

it("labels a raw output as a parsing failure only when its metadata says so", () => {
  render(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "model_raw_output",
          content: "unparsed provider body",
          created_at: "2026-07-26T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: { parse_failed: "true" },
        },
      ]}
    />,
  );

  expect(screen.getByText("Model raw output (parsing failed)")).toBeInTheDocument();
  expect(screen.getByText("unparsed provider body")).toBeInTheDocument();
});

it("renders streamed Markdown when agent completion finalizes the message", () => {
  render(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "model_output_delta",
          content: "# Completed review",
          created_at: "2026-07-22T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "correctness:v1", message_id: "deepseek-output:0" },
        },
        {
          sequence: 2,
          kind: "model_completed",
          content: "",
          created_at: "2026-07-22T00:00:01Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "correctness:v1" },
        },
      ]}
    />,
  );

  expect(screen.getByRole("heading", { name: "Completed review", level: 1 })).toBeInTheDocument();
});

it("selects a stage and then one reviewer timeline", () => {
  render(
    <ReviewConsole
      plan={{
        selection_mode: "fixed",
        reviewer_references: ["security:v1", "performance:v1"],
        plan_hash: "plan-hash",
        planner_reason: null,
        nodes: [
          {
            node_id: "reviewer-security",
            node_type: "reviewer",
            agent_reference: "security:v1",
            depends_on: [],
            pass_index: 1,
            shard_id: "default",
            logical_attempt_group: "primary",
            task_id: "review-1",
          },
          {
            node_id: "reviewer-performance",
            node_type: "reviewer",
            agent_reference: "performance:v1",
            depends_on: [],
            pass_index: 1,
            shard_id: "default",
            logical_attempt_group: "primary",
            task_id: "review-1",
          },
          {
            node_id: "verifier",
            node_type: "verifier",
            agent_reference: "review-verifier:v1",
            depends_on: ["reviewer-security", "reviewer-performance"],
            pass_index: 2,
            shard_id: "default",
            logical_attempt_group: "primary",
            task_id: "review-1",
          },
        ],
      }}
      entries={[
        {
          sequence: 1,
          kind: "model_output_delta",
          content: "Security timeline event",
          created_at: "2026-07-22T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "security:v1", message_id: "security-output" },
        },
        {
          sequence: 2,
          kind: "model_output_delta",
          content: "Performance timeline event",
          created_at: "2026-07-22T00:00:01Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "performance:v1", message_id: "performance-output" },
        },
        {
          sequence: 3,
          kind: "model_output_delta",
          content: "Verifier timeline event",
          created_at: "2026-07-22T00:00:02Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "review-verifier:v1", message_id: "verifier-output" },
        },
      ]}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: /Reviewers/ }));
  expect(screen.getByText("Security timeline event")).toBeInTheDocument();
  expect(screen.getByText("Performance timeline event")).toBeInTheDocument();
  expect(screen.queryByText("Verifier timeline event")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Security Reviewer/ }));
  expect(screen.getByText("Security timeline event")).toBeInTheDocument();
  expect(screen.queryByText("Performance timeline event")).not.toBeInTheDocument();
});

it("selects reviewers for a fixed team whose legacy execution has no persisted plan", () => {
  render(
    <ReviewConsole
      entries={[
        {
          sequence: 1,
          kind: "model_output_delta",
          content: "Correctness legacy timeline",
          created_at: "2026-08-03T00:00:00Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "correctness:v2", message_id: "correctness-output" },
        },
        {
          sequence: 2,
          kind: "model_output_delta",
          content: "Security legacy timeline",
          created_at: "2026-08-03T00:00:01Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "security:v1", message_id: "security-output" },
        },
      ]}
      plan={null}
      reviewerReferences={["correctness:v2", "security:v1"]}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: /Reviewers/ }));
  fireEvent.click(screen.getByRole("button", { name: /Security Reviewer/ }));

  expect(screen.getByText("Security legacy timeline")).toBeInTheDocument();
  expect(screen.queryByText("Correctness legacy timeline")).not.toBeInTheDocument();
});

it("restores usage metrics and filters tokens and tools with the selected stage and reviewer", () => {
  render(
    <ReviewConsole
      processReport={{
        task_id: "review-1",
        status: "completed",
        usage_is_complete: true,
        agent_run_count: 3,
        llm_call_count: 9,
        input_tokens: 480,
        output_tokens: 120,
        total_tokens: 600,
        tool_call_count: 3,
        tool_result_count: 3,
        unmatched_tool_result_count: 0,
        finding_count: 1,
        transcript_entry_count: 6,
        started_at: "2026-08-03T00:00:00Z",
        completed_at: "2026-08-03T00:00:06Z",
        duration_ms: 6_000,
        tools: [
          { tool_name: "read_file", call_count: 1, result_count: 1 },
          { tool_name: "get_diff", call_count: 1, result_count: 1 },
          { tool_name: "verdict", call_count: 1, result_count: 1 },
        ],
        agents: [
          { agent: "security:v1", model_name: "model", llm_call_count: 2, input_tokens: 80, output_tokens: 20, total_tokens: 100, tool_call_count: 1, started_at: "2026-08-03T00:00:00Z", completed_at: "2026-08-03T00:00:02Z", duration_ms: 2_000 },
          { agent: "performance:v1", model_name: "model", llm_call_count: 3, input_tokens: 160, output_tokens: 40, total_tokens: 200, tool_call_count: 1, started_at: "2026-08-03T00:00:00Z", completed_at: "2026-08-03T00:00:03Z", duration_ms: 3_000 },
          { agent: "review-verifier:v1", model_name: "model", llm_call_count: 4, input_tokens: 240, output_tokens: 60, total_tokens: 300, tool_call_count: 1, started_at: "2026-08-03T00:00:03Z", completed_at: "2026-08-03T00:00:06Z", duration_ms: 3_000 },
        ],
      }}
      plan={{
        selection_mode: "fixed",
        reviewer_references: ["security:v1", "performance:v1"],
        plan_hash: "plan-hash",
        planner_reason: null,
        nodes: [
          { node_id: "security", node_type: "reviewer", agent_reference: "security:v1", depends_on: [], pass_index: 1, shard_id: "default", logical_attempt_group: "primary", task_id: "review-1" },
          { node_id: "performance", node_type: "reviewer", agent_reference: "performance:v1", depends_on: [], pass_index: 1, shard_id: "default", logical_attempt_group: "primary", task_id: "review-1" },
          { node_id: "verifier", node_type: "verifier", agent_reference: "review-verifier:v1", depends_on: ["security", "performance"], pass_index: 2, shard_id: "default", logical_attempt_group: "primary", task_id: "review-1" },
        ],
      }}
      entries={[
        { sequence: 1, kind: "tool_call", content: "{}", created_at: "2026-08-03T00:00:00Z", redacted: false, truncated: false, metadata: { agent: "security:v1", tool_name: "read_file", tool_call_id: "security-tool" } },
        { sequence: 2, kind: "tool_result", content: "{}", created_at: "2026-08-03T00:00:01Z", redacted: false, truncated: false, metadata: { agent: "security:v1" } },
        { sequence: 3, kind: "tool_call", content: "{}", created_at: "2026-08-03T00:00:02Z", redacted: false, truncated: false, metadata: { agent: "performance:v1", tool_name: "get_diff", tool_call_id: "performance-tool" } },
        { sequence: 4, kind: "tool_result", content: "{}", created_at: "2026-08-03T00:00:03Z", redacted: false, truncated: false, metadata: { agent: "performance:v1" } },
        { sequence: 5, kind: "tool_call", content: "{}", created_at: "2026-08-03T00:00:04Z", redacted: false, truncated: false, metadata: { agent: "review-verifier:v1", tool_name: "verdict", tool_call_id: "verifier-tool" } },
        { sequence: 6, kind: "tool_result", content: "{}", created_at: "2026-08-03T00:00:05Z", redacted: false, truncated: false, metadata: { agent: "review-verifier:v1" } },
      ]}
    />,
  );

  const report = screen.getByRole("article", { name: "Process report" });
  expect(within(report).getByText("600")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Reviewers/ }));
  expect(within(report).getByText("300")).toBeInTheDocument();
  expect(within(report).getByText("read_file")).toBeInTheDocument();
  expect(within(report).getByText("get_diff")).toBeInTheDocument();
  expect(within(report).queryByText("verdict")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Security Reviewer/ }));
  const totalTokensMetric = within(report).getByText("Total tokens").closest("div");
  expect(totalTokensMetric).not.toBeNull();
  if (totalTokensMetric === null) throw new Error("Total tokens metric is missing");
  expect(within(totalTokensMetric).getByText("100")).toBeInTheDocument();
  expect(within(report).getByText("read_file")).toBeInTheDocument();
  expect(within(report).queryByText("get_diff")).not.toBeInTheDocument();
});

it("coalesces interleaved streaming deltas independently for each reviewer", () => {
  render(
    <ReviewConsole
      entries={[
        { sequence: 1, kind: "model_output_delta", content: "Security ", created_at: "2026-08-03T00:00:00Z", redacted: false, truncated: false, metadata: { agent: "security:v1", message_id: "provider-message" } },
        { sequence: 2, kind: "model_output_delta", content: "Correctness ", created_at: "2026-08-03T00:00:01Z", redacted: false, truncated: false, metadata: { agent: "correctness:v2", message_id: "provider-message" } },
        { sequence: 3, kind: "model_output_delta", content: "complete", created_at: "2026-08-03T00:00:02Z", redacted: false, truncated: false, metadata: { agent: "security:v1", message_id: "provider-message" } },
        { sequence: 4, kind: "model_output_delta", content: "complete", created_at: "2026-08-03T00:00:03Z", redacted: false, truncated: false, metadata: { agent: "correctness:v2", message_id: "provider-message" } },
      ]}
      plan={null}
      reviewerReferences={["correctness:v2", "security:v1"]}
    />,
  );

  expect(screen.getByText("Security complete")).toBeInTheDocument();
  expect(screen.getByText("Correctness complete")).toBeInTheDocument();
  expect(screen.getAllByText("AI output")).toHaveLength(2);
  expect(screen.getByText("2 of 2 events")).toBeInTheDocument();
});

it("shows first 10 and last 10 events with a load-more gap in between", () => {
  const entries = Array.from({ length: 25 }, (_, i) => ({
    sequence: i + 1,
    kind: "model_output_delta" as const,
    content: `Event ${i + 1}`,
    created_at: `2026-08-05T00:00:${String(i).padStart(2, "0")}Z`,
    redacted: false,
    truncated: false,
    metadata: { message_id: `message-${i + 1}` },
  }));

  render(<ReviewConsole entries={entries} />);

  expect(screen.getByText("Event 1")).toBeInTheDocument();
  expect(screen.getByText("Event 10")).toBeInTheDocument();
  expect(screen.queryByText("Event 11")).not.toBeInTheDocument();
  expect(screen.queryByText("Event 15")).not.toBeInTheDocument();
  expect(screen.getByText("Event 16")).toBeInTheDocument();
  expect(screen.getByText("Event 25")).toBeInTheDocument();
  expect(screen.getByText("5 events hidden")).toBeInTheDocument();
});

it("loads more events from start and end independently", () => {
  const entries = Array.from({ length: 50 }, (_, i) => ({
    sequence: i + 1,
    kind: "model_output_delta" as const,
    content: `Event ${i + 1}`,
    created_at: `2026-08-05T00:00:${String(i).padStart(2, "0")}Z`,
    redacted: false,
    truncated: false,
    metadata: { message_id: `message-${i + 1}` },
  }));

  render(<ReviewConsole entries={entries} />);

  expect(screen.getByText("Event 10")).toBeInTheDocument();
  expect(screen.queryByText("Event 11")).not.toBeInTheDocument();
  expect(screen.queryByText("Event 40")).not.toBeInTheDocument();
  expect(screen.getByText("Event 41")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Load earlier/ }));
  expect(screen.getByText("Event 20")).toBeInTheDocument();
  expect(screen.queryByText("Event 21")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Load later/ }));
  expect(screen.getByText("Event 31")).toBeInTheDocument();
  expect(screen.queryByText("Event 21")).not.toBeInTheDocument();
});
