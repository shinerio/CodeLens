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
  expect(consoleView.getByText("get_diff").closest("li")).not.toBeVisible();
  fireEvent.click(tools);
  expect(consoleView.getByText("get_diff")).toBeInTheDocument();
  expect(consoleView.getByText("diff output")).toBeInTheDocument();
  fireEvent.click(tools);
  expect(consoleView.getByText("get_diff").closest("li")).not.toBeVisible();
  expect(consoleView.getByText("diff output").closest("li")).not.toBeVisible();

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

  expect(consoleView.getByText("get_diff").closest("li")).not.toBeVisible();
  expect(consoleView.getByText("diff output").closest("li")).not.toBeVisible();
  expect(consoleView.getByText("late tool output").closest("li")).not.toBeVisible();
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
        budget_profile: "deep",
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
            node_id: "resolver",
            node_type: "resolver",
            agent_reference: "review-resolver:v1",
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
          content: "Resolver timeline event",
          created_at: "2026-07-22T00:00:02Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "review-resolver:v1", message_id: "resolver-output" },
        },
      ]}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: /Reviewers/ }));
  expect(screen.getByText("Security timeline event")).toBeInTheDocument();
  expect(screen.getByText("Performance timeline event")).toBeInTheDocument();
  expect(screen.queryByText("Resolver timeline event")).not.toBeInTheDocument();

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
