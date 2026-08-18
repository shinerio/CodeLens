import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  const normalized =
    typeof payload === "object" &&
    payload !== null &&
    "task_id" in payload &&
    "scope_type" in payload
      ? {
          base_ref: null,
          target_ref: null,
          selected_agents: ["correctness:v2"],
          worktree_status: "pending",
          repository_id: "repository-1",
          repository_realpath_hash: "c".repeat(64),
          git_common_dir_hash: "d".repeat(64),
          cancellation_requested: false,
          repository_name: "codelens",
          created_at: "2026-07-18T12:00:00Z",
          finding_count: 0,
          external_context: null,
          selection_request: {
            mode: "fixed",
            reviewer_versions: ["correctness:v2"],
          },
          profile_source: null,
          review_plan: null,
          coverage: { planned: [], completed: [], failed: [], omitted: [] },
          verdict_summary: { accept: 0, deny: 0, merge: 0 },
          ...payload,
        }
      : payload;
  return new Response(JSON.stringify(normalized), {
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
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/plugins")) return jsonResponse([]);
    if (url.endsWith("/exports")) return jsonResponse([]);
    if (url.endsWith("/source")) {
      return jsonResponse({
        path: "feature.py",
        base: null,
        target: {
          path: "feature.py",
          revision: "b".repeat(40),
          content: "if branch:\n    run()\n",
        },
        highlight_side: "new",
        highlight_start_line: 1,
        highlight_end_line: 2,
      });
    }
    if (url.endsWith("/findings"))
      return jsonResponse([
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
      ]);
    if (url.endsWith("/transcript")) return jsonResponse([]);
    if (url.endsWith("/process-report")) {
      return jsonResponse({ code: "process_report_not_ready", message: "not ready" }, 409);
    }
    return jsonResponse({
      task_id: "review_1",
      status: "reviewing",
      scope_type: "branch",
      base_oid: "a".repeat(40),
      head_oid: "b".repeat(40),
      selected_agents: ["correctness:v2"],
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

  expect(
    await screen.findByRole("heading", {
      name: "Correctness Reviewer",
      level: 1,
    }),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: /Logs/ }));
  expect(screen.getByText("Waiting for events.")).toBeInTheDocument();

  FakeEventSource.latest?.emit("review.completed.v2", { finding_count: 1 }, "7");

  await waitFor(() => {
    expect(document.querySelector(".review-run-page__subtitle")).toHaveTextContent("completed");
  });
});

it("keeps published findings visible while naming partial reviewer coverage", async () => {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/plugins") || url.endsWith("/exports") || url.endsWith("/transcript")) {
      return jsonResponse([]);
    }
    if (url.endsWith("/source")) {
      return jsonResponse({
        path: "src/auth.ts",
        base: null,
        target: { path: "src/auth.ts", revision: "b".repeat(40), content: "allow(principal);" },
        highlight_side: "new",
        highlight_start_line: 1,
        highlight_end_line: 1,
      });
    }
    if (url.endsWith("/findings")) {
      return jsonResponse([{
        finding_id: "finding_partial",
        fingerprint: "e".repeat(64),
        reviewer_id: "security",
        category: "security",
        title: "Published security finding",
        severity: "high",
        disposition: "blocking",
        confidence: null,
        evidence_strength: "strong",
        verification_state: "unresolved",
        primary_location: { path: "src/auth.ts", start_line: 8, end_line: 8, side: "new", excerpt_hash: "f".repeat(64), is_deleted: false },
        related_locations: [],
        changed_hunk_id: "hunk-1",
        change_origin: "introduced",
        evidence: [],
        impact: "Authorization can be bypassed.",
        explanation: "The condition accepts an invalid principal.",
        reproduction: null,
        recommendation: "Reject missing principals.",
        rule_sources: [],
      }]);
    }
    if (url.endsWith("/process-report")) {
      return jsonResponse({ code: "process_report_not_ready", message: "not ready" }, 409);
    }
    return jsonResponse({
      task_id: "review_partial",
      status: "partial",
      scope_type: "branch",
      base_oid: "a".repeat(40),
      head_oid: "b".repeat(40),
      base_ref: "main",
      target_ref: "feature",
      selected_agents: ["security:v2", "performance:v2"],
      worktree_status: "pending",
      repository_id: "repository-1",
      repository_realpath_hash: "c".repeat(64),
      git_common_dir_hash: "d".repeat(64),
      cancellation_requested: false,
      repository_name: "repository",
      created_at: "2026-08-02T00:00:00Z",
      finding_count: 1,
      external_context: null,
      selection_request: { mode: "adaptive" },
      profile_source: null,
      review_plan: {
        selection_mode: "adaptive",
        reviewer_references: ["security:v2", "performance:v2"],
        plan_hash: "1".repeat(64),
        planner_reason: "Risk-sensitive fan-out",
        nodes: [
          { node_id: "planner", node_type: "planner", agent_reference: "planner:v2", depends_on: [], pass_index: 0, shard_id: "all", logical_attempt_group: "planner", task_id: "review_partial" },
          { node_id: "security", node_type: "reviewer", agent_reference: "security:v2", depends_on: ["planner"], pass_index: 1, shard_id: "all", logical_attempt_group: "security", task_id: "review_partial" },
        ],
      },
      coverage: {
        planned: ["security:v2", "performance:v2"],
        completed: ["security:v2"],
        failed: ["performance:v2"],
        omitted: [],
      },
      verdict_summary: { accept: 1, deny: 0, merge: 1 },
    });
  });

  render(<ReviewRunPage />, {
    wrapper: ({ children }) => (
      <TestProviders initialEntries={["/runs/review_partial"]}>
        <Routes><Route path="/runs/:taskId" element={children} /></Routes>
      </TestProviders>
    ),
  });

  expect(await screen.findByRole("tab", { name: /Findings/ })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("Published security finding")).toBeVisible();
  expect(screen.getByRole("status", { name: "Reviewer coverage" })).toHaveTextContent("performance:v2");
  expect(screen.getByText("Risk-sensitive fan-out")).toBeVisible();
});

it("keeps polling an empty transcript after completion until the worker persists it", async () => {
  let transcriptRequests = 0;
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/plugins")) return jsonResponse([]);
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
                metadata: { agent: "correctness:v2", message_id: "message-1" },
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
      selected_agents: ["correctness:v2"],
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

  await userEvent.click(await screen.findByRole("tab", { name: /Logs/ }));
  expect(await screen.findAllByText("Waiting for events.")).not.toHaveLength(0);
  FakeEventSource.latest?.emit("review.completed.v2", {}, "7");

  await waitFor(
    () => {
      expect(screen.getByText("The agent inspected the changed code.")).toBeInTheDocument();
    },
    { timeout: 2_000 },
  );
});

it("shows timeline filters when the persisted review plan arrives", async () => {
  let reviewRequests = 0;
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/plugins") || url.endsWith("/exports")) return jsonResponse([]);
    if (url.endsWith("/findings")) return jsonResponse([]);
    if (url.endsWith("/transcript")) {
      return jsonResponse([{
        sequence: 1,
        kind: "model_output_delta",
        content: "Security timeline event",
        created_at: "2026-08-03T00:00:00Z",
        redacted: false,
        truncated: false,
        metadata: { agent: "security:v2", message_id: "security-output" },
      }]);
    }
    if (url.endsWith("/api/reviews/review_plan_refresh")) {
      reviewRequests += 1;
      return jsonResponse({
        task_id: "review_plan_refresh",
        status: "reviewing",
        scope_type: "branch",
        base_oid: "a".repeat(40),
        head_oid: "b".repeat(40),
        selected_agents: reviewRequests > 1 ? ["security:v2", "performance:v2"] : [],
        worktree_status: "pending",
        repository_id: "repository-1",
        repository_realpath_hash: "c".repeat(64),
        git_common_dir_hash: "d".repeat(64),
        cancellation_requested: false,
        review_plan: reviewRequests > 1 ? {
          selection_mode: "fixed",
          reviewer_references: ["security:v2", "performance:v2"],
          plan_hash: "1".repeat(64),
          planner_reason: null,
          nodes: [
            { node_id: "security", node_type: "reviewer", agent_reference: "security:v2", depends_on: [], pass_index: 1, shard_id: "all", logical_attempt_group: "security", task_id: "review_plan_refresh" },
            { node_id: "performance", node_type: "reviewer", agent_reference: "performance:v2", depends_on: [], pass_index: 1, shard_id: "all", logical_attempt_group: "performance", task_id: "review_plan_refresh" },
          ],
        } : null,
      });
    }
    return jsonResponse({ code: "not_found", message: "not found" }, 404);
  });

  render(<ReviewRunPage />, {
    wrapper: ({ children }) => (
      <TestProviders initialEntries={["/runs/review_plan_refresh"]}>
        <Routes><Route path="/runs/:taskId" element={children} /></Routes>
      </TestProviders>
    ),
  });

  await userEvent.click(await screen.findByRole("tab", { name: /Logs/ }));
  expect(screen.queryByRole("button", { name: /Reviewers/ })).not.toBeInTheDocument();

  FakeEventSource.latest?.emit("review.plan_created.v2", { reviewer_count: 2 }, "2");

  expect(await screen.findByRole("button", { name: /Reviewers/ })).toBeInTheDocument();
  expect(reviewRequests).toBe(2);
});

it("shows the actionable failure reason in the page banner", async () => {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/plugins")) return jsonResponse([]);
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
      selected_agents: ["correctness:v2"],
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
  FakeEventSource.latest?.emit("review.failed.v2", {}, "9");

  expect(await screen.findByRole("alert")).toHaveTextContent("Cannot connect to the model gateway");
  expect(screen.getByRole("alert")).toHaveTextContent("Check the Base URL and network access");
  expect(screen.getByRole("alert")).toHaveTextContent("HTTP 503");
});

it("shows candidate validation warnings without failing the completed review", async () => {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/plugins")) return jsonResponse([]);
    if (url.endsWith("/findings")) return jsonResponse([]);
    if (url.endsWith("/transcript")) {
      return jsonResponse([
        {
          sequence: 8,
          kind: "lifecycle",
          content: "Finding validation retained 3 and skipped 2 model candidates",
          created_at: "2026-07-26T00:00:05Z",
          redacted: false,
          truncated: false,
          metadata: {
            agent: "correctness:v2",
            warning_code: "finding_validation_partial",
            retained_count: "3",
            skipped_count: "2",
            duplicate_count: "1",
            invalid_count: "1",
          },
        },
      ]);
    }
    if (url.endsWith("/process-report")) {
      return jsonResponse({ code: "process_report_not_ready", message: "not ready" }, 409);
    }
    return jsonResponse({
      task_id: "review_completed",
      status: "completed",
      scope_type: "branch",
      base_oid: "a".repeat(40),
      head_oid: "b".repeat(40),
      selected_agents: ["correctness:v2"],
      worktree_status: "pending",
      repository_id: "repository-1",
      repository_realpath_hash: "c".repeat(64),
      git_common_dir_hash: "d".repeat(64),
      cancellation_requested: false,
    });
  });

  render(<ReviewRunPage />, {
    wrapper: ({ children }) => (
      <TestProviders initialEntries={["/runs/review_completed"]}>
        <Routes>
          <Route path="/runs/:taskId" element={children} />
        </Routes>
      </TestProviders>
    ),
  });

  expect(await screen.findByRole("heading", { name: "Correctness Reviewer" })).toBeInTheDocument();
  const warning = await screen.findByRole("status", {
    name: "Some model findings were skipped",
  });
  expect(warning).toHaveTextContent("3 findings kept");
  expect(warning).toHaveTextContent("2 skipped");
  expect(warning).toHaveTextContent("1 duplicate");
  expect(warning).toHaveTextContent("1 invalid");
  expect(document.querySelector(".review-run-page__subtitle")).toHaveTextContent("completed");
});

it("shows files that were not verified before forced successful completion", async () => {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/plugins")) return jsonResponse([]);
    if (url.endsWith("/findings")) return jsonResponse([]);
    if (url.endsWith("/transcript")) {
      return jsonResponse([
        {
          sequence: 8,
          kind: "lifecycle",
          content: "Review completed after the incomplete-review retry limit",
          created_at: "2026-07-26T00:00:05Z",
          redacted: false,
          truncated: false,
          metadata: {
            agent: "correctness:v2",
            warning_code: "review_coverage_incomplete",
            incomplete_file_count: "2",
            incomplete_files: '["src/missed.py","src/unread.py"]',
          },
        },
        {
          sequence: 9,
          kind: "lifecycle",
          content: "Review completed after the incomplete-review retry limit",
          created_at: "2026-07-26T00:00:06Z",
          redacted: false,
          truncated: false,
          metadata: {
            agent: "security:v2",
            warning_code: "review_coverage_incomplete",
            incomplete_file_count: "2",
            incomplete_files: '["src/unread.py","src/security.py"]',
          },
        },
      ]);
    }
    if (url.endsWith("/process-report")) {
      return jsonResponse({ code: "process_report_not_ready", message: "not ready" }, 409);
    }
    return jsonResponse({
      task_id: "review_completed",
      status: "completed",
      scope_type: "branch",
      base_oid: "a".repeat(40),
      head_oid: "b".repeat(40),
      selected_agents: ["correctness:v2"],
      worktree_status: "pending",
      repository_id: "repository-1",
      repository_realpath_hash: "c".repeat(64),
      git_common_dir_hash: "d".repeat(64),
      cancellation_requested: false,
    });
  });

  render(<ReviewRunPage />, {
    wrapper: ({ children }) => (
      <TestProviders initialEntries={["/runs/review_completed"]}>
        <Routes>
          <Route path="/runs/:taskId" element={children} />
        </Routes>
      </TestProviders>
    ),
  });

  const warning = await screen.findByRole("status", {
    name: "Some files were not fully reviewed",
  });
  expect(warning).toHaveTextContent("src/missed.py");
  expect(warning).toHaveTextContent("src/security.py");
  expect(warning).toHaveTextContent("src/unread.py");
  expect(screen.getAllByText("src/unread.py")).toHaveLength(1);
  expect(document.querySelector(".review-run-page__subtitle")).toHaveTextContent("completed");
});

it("shows the process report after a review has completed", async () => {
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/plugins")) return jsonResponse([]);
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
          metadata: { agent: "correctness:v2" },
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
        accepted_tool_call_count: 2,
        rejected_tool_call_count: 0,
        unclassified_tool_call_count: 0,
        tool_result_count: 2,
        unmatched_tool_result_count: 0,
        finding_count: 0,
        transcript_entry_count: 7,
        started_at: "2026-07-25T00:00:00Z",
        completed_at: "2026-07-25T00:00:06Z",
        duration_ms: 6000,
        tools: [
          { tool_name: "get_diff", call_count: 1, result_count: 1, accepted_call_count: 1, rejected_call_count: 0, unclassified_call_count: 0 },
          { tool_name: "grep", call_count: 1, result_count: 1, accepted_call_count: 1, rejected_call_count: 0, unclassified_call_count: 0 },
        ],
        rejected_tool_calls: [],
        agents: [
          {
            agent: "correctness:v2",
            model_name: "gpt-5.1",
            llm_call_count: 3,
            input_tokens: 120,
            output_tokens: 30,
            total_tokens: 150,
            tool_call_count: 2,
            accepted_tool_call_count: 2,
            rejected_tool_call_count: 0,
            unclassified_tool_call_count: 0,
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
      selected_agents: ["correctness:v2"],
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

  await userEvent.click(await screen.findByRole("tab", { name: /Runtime overview/ }));
  expect(await screen.findByRole("heading", { name: "Process report" })).toBeInTheDocument();
  expect(screen.getAllByText("150")).toHaveLength(2);
  expect(screen.getByText("get_diff")).toBeInTheDocument();
  expect(screen.getByText("gpt-5.1")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: /Logs/ }));
  expect(screen.queryByRole("heading", { name: "Process report" })).not.toBeInTheDocument();
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
    if (url.endsWith("/api/plugins")) return jsonResponse([]);
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
      selected_agents: ["correctness:v2"],
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
  expect(screen.getByRole("tab", { name: /Findings/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  expect(await screen.findByRole("navigation", { name: "Finding navigation" })).toBeVisible();
  expect(screen.getByTestId("review-diff-editor")).toBeVisible();
  expect(document.querySelector(".finding-workspace__detail")).not.toBeNull();
});
