import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";

import { ReviewRunPage } from "./ReviewRunPage";
import { TestProviders } from "../../test/TestProviders";
import { FakeEventSource } from "../../test/FakeEventSource";

const fetchMock = vi.fn();

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
          suggested_patch: null,
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
  expect(screen.getByText("0 findings")).toBeInTheDocument();

  FakeEventSource.latest?.emit("review.completed", { finding_count: 1 }, "7");

  expect(await screen.findByText("Wrong branch")).toBeInTheDocument();
  expect(screen.getByText("1 finding")).toBeInTheDocument();
});
