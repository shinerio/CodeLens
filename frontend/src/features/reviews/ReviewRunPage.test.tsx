import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";

import { ReviewRunPage } from "./ReviewRunPage";
import { TestProviders } from "../../test/TestProviders";
import { FakeEventSource } from "../../test/FakeEventSource";

const fetchMock = vi.fn();

vi.mock("@monaco-editor/react", () => ({
  loader: { config: vi.fn() },
  DiffEditor: () => <div data-testid="review-diff-editor" />,
}));

vi.mock("monaco-editor", () => ({}));

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  FakeEventSource.latest = undefined;
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("EventSource", FakeEventSource);
});

it("shows the live run and refreshes findings after completion", async () => {
  fetchMock
    .mockResolvedValueOnce(
      jsonResponse({
        task_id: "review_1",
        status: "reviewing",
        scope_type: "branch",
        base_oid: "a".repeat(40),
        head_oid: "b".repeat(40),
        selected_agents: ["correctness:v1"],
        worktree_status: "pending",
        repository_id: "repository-1",
        repository_realpath_hash: "c".repeat(64),
        git_common_dir_hash: "d".repeat(64),
        cancellation_requested: false,
      }),
    )
    .mockResolvedValueOnce(jsonResponse([]))
    .mockResolvedValueOnce(jsonResponse([]))
    .mockResolvedValueOnce(
      jsonResponse([
        {
          finding_id: "finding_1",
          fingerprint: "e".repeat(64),
          reviewer_id: "correctness",
          category: "branching",
          title: "Wrong branch",
          severity: "medium",
          disposition: "non_blocking",
          confidence: 0.88,
          primary_location: {
            path: "feature.py",
            start_line: 1,
            end_line: 2,
            side: "new",
            excerpt_hash: "f".repeat(64),
            is_deleted: false,
          },
          related_locations: [],
          changed_hunk_id: null,
          change_origin: "introduced",
          evidence: [
            {
              kind: "excerpt",
              description: "Captured from the saved review output.",
              artifact_ref: null,
              excerpt_hash: "f".repeat(64),
            },
          ],
          impact: "The review pointed at the wrong branch.",
          explanation: "This is a stored contract fixture.",
          reproduction: null,
          recommendation: "Review the correct branch target.",
          rule_sources: [
            {
              path: "rules/review.md",
              content_hash: "1".repeat(64),
            },
          ],
        },
      ]),
    );

  render(<ReviewRunPage />, {
    wrapper: ({ children }) => (
      <TestProviders initialEntries={["/runs/review_1"]}>
        <Routes>
          <Route path="/runs/:taskId" element={children} />
        </Routes>
      </TestProviders>
    ),
  });

  expect(
    await screen.findByRole("heading", {
      name: "Correctness Reviewer",
      level: 1,
    }),
  ).toBeInTheDocument();
  expect(screen.getByText("Waiting for events.")).toBeInTheDocument();

  FakeEventSource.latest?.emit("review.completed", { finding_count: 1 }, "7");

  await waitFor(() => {
    expect(document.querySelector(".review-run-page__subtitle")).toHaveTextContent("completed");
  });
});

it("keeps polling an empty transcript after completion until the worker persists it", async () => {
  let transcriptRequests = 0;
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/process-report")) {
      return jsonResponse({ code: "process_report_not_ready", message: "not ready" }, 409);
    }
    if (url.endsWith("/transcript")) {
      transcriptRequests += 1;
      return jsonResponse(
        transcriptRequests === 1
          ? []
          : [
              {
                sequence: 1,
                kind: "model_output_delta",
                content: "The agent inspected the changed code.",
                created_at: "2026-07-24T00:00:00Z",
                redacted: false,
                truncated: false,
                metadata: { agent: "correctness:v1", message_id: "message-1" },
              },
            ],
      );
    }
    if (url.endsWith("/findings")) return jsonResponse([]);
    return jsonResponse({
      task_id: "review_1",
      status: "reviewing",
      scope_type: "branch",
      base_oid: "a".repeat(40),
      head_oid: "b".repeat(40),
      selected_agents: ["correctness:v1"],
      worktree_status: "pending",
      repository_id: "repository-1",
      repository_realpath_hash: "c".repeat(64),
      git_common_dir_hash: "d".repeat(64),
      cancellation_requested: false,
    });
  });

  render(<ReviewRunPage />, {
    wrapper: ({ children }) => (
      <TestProviders initialEntries={["/runs/review_1"]}>
        <Routes>
          <Route path="/runs/:taskId" element={children} />
        </Routes>
      </TestProviders>
    ),
  });

  expect(await screen.findAllByText("Waiting for events.")).not.toHaveLength(0);
  FakeEventSource.latest?.emit("review.completed", {}, "7");

  await waitFor(
    () => {
      expect(screen.getByText("The agent inspected the changed code.")).toBeInTheDocument();
    },
    { timeout: 2_000 },
  );
});

it("shows the actionable failure reason in the page banner", async () => {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/findings")) return jsonResponse([]);
    if (url.endsWith("/transcript")) {
      return jsonResponse([
        {
          sequence: 8,
          kind: "lifecycle",
          content: "CodeLens could not connect to the model gateway.",
          created_at: "2026-07-25T00:00:05Z",
          redacted: false,
          truncated: false,
          metadata: {
            error_code: "transient_agent_runtime_error",
            error_type: "TransientAgentRuntimeError",
            phase: "investigation",
            provider_status_code: "503",
            reason_code: "provider_connection_error",
            retryable: "true",
          },
        },
      ]);
    }
    if (url.endsWith("/process-report")) {
      return jsonResponse({ code: "process_report_not_ready", message: "not ready" }, 409);
    }
    return jsonResponse({
      task_id: "review_failed",
      status: "failed",
      scope_type: "commit",
      base_oid: "a".repeat(40),
      head_oid: "b".repeat(40),
      selected_agents: ["correctness:v1"],
      worktree_status: "pending",
      repository_id: "repository-1",
      repository_realpath_hash: "c".repeat(64),
      git_common_dir_hash: "d".repeat(64),
      cancellation_requested: false,
    });
  });

  render(<ReviewRunPage />, {
    wrapper: ({ children }) => (
      <TestProviders initialEntries={["/runs/review_failed"]}>
        <Routes>
          <Route path="/runs/:taskId" element={children} />
        </Routes>
      </TestProviders>
    ),
  });

  expect(await screen.findByRole("heading", { name: "Correctness Reviewer" })).toBeInTheDocument();
  FakeEventSource.latest?.emit("review.failed", {}, "9");

  expect(await screen.findByRole("alert")).toHaveTextContent("Cannot connect to the model gateway");
  expect(screen.getByRole("alert")).toHaveTextContent("Check the Base URL and network access");
  expect(screen.getByRole("alert")).toHaveTextContent("HTTP 503");
});

it("shows the process report after a review has completed", async () => {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/findings")) return jsonResponse([]);
    if (url.endsWith("/transcript")) {
      return jsonResponse([
        {
          sequence: 1,
          kind: "model_output",
          content: "{}",
          created_at: "2026-07-25T00:00:05Z",
          redacted: false,
          truncated: false,
          metadata: { agent: "correctness:v1" },
        },
      ]);
    }
    if (url.endsWith("/process-report")) {
      return jsonResponse({
        task_id: "review_1",
        status: "completed",
        usage_is_complete: true,
        agent_run_count: 1,
        llm_call_count: 3,
        input_tokens: 120,
        output_tokens: 30,
        total_tokens: 150,
        tool_call_count: 2,
        tool_result_count: 2,
        unmatched_tool_result_count: 0,
        finding_count: 0,
        transcript_entry_count: 7,
        started_at: "2026-07-25T00:00:00Z",
        completed_at: "2026-07-25T00:00:06Z",
        duration_ms: 6000,
        tools: [
          { tool_name: "get_diff", call_count: 1, result_count: 1 },
          { tool_name: "grep", call_count: 1, result_count: 1 },
        ],
        agents: [
          {
            agent: "correctness:v1",
            model_name: "gpt-5.1",
            llm_call_count: 3,
            input_tokens: 120,
            output_tokens: 30,
            total_tokens: 150,
            tool_call_count: 2,
            started_at: "2026-07-25T00:00:00Z",
            completed_at: "2026-07-25T00:00:06Z",
            duration_ms: 6000,
          },
        ],
      });
    }
    return jsonResponse({
      task_id: "review_1",
      status: "completed",
      scope_type: "branch",
      base_oid: "a".repeat(40),
      head_oid: "b".repeat(40),
      selected_agents: ["correctness:v1"],
      worktree_status: "pending",
      repository_id: "repository-1",
      repository_realpath_hash: "c".repeat(64),
      git_common_dir_hash: "d".repeat(64),
      cancellation_requested: false,
    });
  });

  render(<ReviewRunPage />, {
    wrapper: ({ children }) => (
      <TestProviders initialEntries={["/runs/review_1"]}>
        <Routes>
          <Route path="/runs/:taskId" element={children} />
        </Routes>
      </TestProviders>
    ),
  });

  expect(await screen.findByRole("heading", { name: "Process report" })).toBeInTheDocument();
  expect(screen.getAllByText("150")).toHaveLength(2);
  expect(screen.getByText("get_diff")).toBeInTheDocument();
  expect(screen.getByText("gpt-5.1")).toBeInTheDocument();
});

it("places finding navigation above a full-width source comparison", async () => {
  const finding = {
    finding_id: "finding_1",
    fingerprint: "e".repeat(64),
    reviewer_id: "correctness",
    category: "logic",
    title: "Removed guard",
    severity: "high",
    disposition: "blocking",
    confidence: 0.98,
    primary_location: {
      path: "feature.py",
      start_line: 2,
      end_line: 2,
      side: "old",
      excerpt_hash: "f".repeat(64),
      is_deleted: false,
    },
    related_locations: [],
    changed_hunk_id: "hunk-1",
    change_origin: "introduced",
    evidence: [],
    impact: "Requests can bypass the guard.",
    explanation: "The target revision removes the required guard.",
    reproduction: null,
    recommendation: "Keep the guard.",
    rule_sources: [],
  };
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/findings/finding_1/source")) {
      return jsonResponse({
        path: "feature.py",
        base: { path: "feature.py", revision: "a".repeat(40), content: "guard()\nrun()\n" },
        target: { path: "feature.py", revision: "b".repeat(40), content: "run()\n" },
        highlight_side: "old",
        highlight_start_line: 2,
        highlight_end_line: 2,
      });
    }
    if (url.endsWith("/findings")) return jsonResponse([finding]);
    if (url.endsWith("/transcript")) return jsonResponse([]);
    return jsonResponse({
      task_id: "review_1",
      status: "completed",
      scope_type: "branch",
      base_oid: "a".repeat(40),
      head_oid: "b".repeat(40),
      selected_agents: ["correctness:v1"],
      worktree_status: "pending",
      repository_id: "repository-1",
      repository_realpath_hash: "c".repeat(64),
      git_common_dir_hash: "d".repeat(64),
      cancellation_requested: false,
    });
  });

  render(<ReviewRunPage />, {
    wrapper: ({ children }) => (
      <TestProviders initialEntries={["/runs/review_1"]}>
        <Routes>
          <Route path="/runs/:taskId" element={children} />
        </Routes>
      </TestProviders>
    ),
  });

  await screen.findByRole("heading", { name: "Correctness Reviewer" });
  screen.getByRole("button", { name: /Findings/ }).click();

  expect(await screen.findByRole("navigation", { name: "Finding navigation" })).toBeVisible();
  expect(screen.getByTestId("review-diff-editor")).toBeVisible();
  expect(document.querySelector(".finding-workspace__detail")).not.toBeNull();
});
