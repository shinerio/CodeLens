import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";

import { TestProviders } from "../../test/TestProviders";
import { RunListPage } from "./RunListPage";

const fetchMock = vi.fn();

function reviewResponse(overrides: Record<string, unknown>) {
  return {
    task_id: "review_1",
    repository_name: "codelens",
    created_at: "2026-07-18T12:00:00Z",
    status: "completed",
    scope_type: "branch",
    base_oid: "a".repeat(40),
    head_oid: "b".repeat(40),
    base_ref: "main",
    target_ref: "feature",
    selected_agents: ["correctness:v2"],
    worktree_status: "pending",
    repository_id: "repository-1",
    repository_realpath_hash: "c".repeat(64),
    git_common_dir_hash: "d".repeat(64),
    cancellation_requested: false,
    finding_count: 0,
    external_context: null,
    selection_request: { mode: "fixed", reviewer_versions: ["correctness:v2"] },
    profile_source: null,
    review_plan: null,
    coverage: { planned: [], completed: [], failed: [], omitted: [] },
    verdict_summary: { accept: 0, deny: 0, merge: 0 },
    ...overrides,
  };
}

beforeEach(() => {
  fetchMock.mockResolvedValue(
    new Response(
      JSON.stringify([
        reviewResponse({}),
      ]),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("confirm", vi.fn(() => true));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderRunsPage() {
  render(
    <Routes>
      <Route path="/runs" element={<RunListPage />} />
      <Route path="/runs/:taskId" element={<p>Review details</p>} />
    </Routes>,
    {
      wrapper: ({ children }) => (
        <TestProviders initialEntries={["/runs"]}>{children}</TestProviders>
      ),
    },
  );
}

it("opens review details by clicking the list item without a separate open link", async () => {
  const user = userEvent.setup();
  renderRunsPage();

  expect(screen.getByRole("link", { name: "New review" })).toHaveAttribute("href", "/reviews/new");
  const detailsLinks = await screen.findAllByRole("link", { name: "Open codelens" });
  expect(detailsLinks).toHaveLength(1);

  await user.click(screen.getByText("codelens"));

  expect(screen.getByText("Review details")).toBeInTheDocument();
});

it("soft-deletes a review from the runs page after confirmation", async () => {
  const user = userEvent.setup();
  render(<RunListPage />, { wrapper: TestProviders });

  await user.click(await screen.findByRole("button", { name: "Delete review codelens" }));

  expect(fetchMock.mock.calls).toContainEqual([
    "/api/reviews/review_1",
    expect.objectContaining({ method: "DELETE" }),
  ]);
});

it("retries a failed review as a new task and opens its details", async () => {
  fetchMock.mockImplementation(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      return new Response(
        JSON.stringify(reviewResponse({
          task_id: "review_2",
          created_at: "2026-07-18T12:05:00Z",
          status: "created",
        })),
        { status: 202, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response(
      JSON.stringify([
        reviewResponse({ status: "failed" }),
      ]),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  });
  const user = userEvent.setup();
  renderRunsPage();

  await user.click(await screen.findByRole("button", { name: "Retry codelens" }));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/reviews/review_1/retry",
    expect.objectContaining({ method: "POST", body: "{}" }),
  );
  expect(screen.getByText("Review details")).toBeInTheDocument();
});
