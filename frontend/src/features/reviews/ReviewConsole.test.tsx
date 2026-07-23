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
  const { container } = render(
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
});

it("renders streamed Markdown after its agent has completed without a provider done event", () => {
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
