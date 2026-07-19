import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { RunListPage } from "./RunListPage";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockResolvedValue(
    new Response(
      JSON.stringify([
        {
          task_id: "review_1",
          repository_name: "codelens",
          created_at: "2026-07-18T12:00:00Z",
          status: "completed",
          scope_type: "branch",
        },
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

it("provides new review creation from the runs page", async () => {
  render(<RunListPage />, { wrapper: TestProviders });

  expect(screen.getByRole("link", { name: "New review" })).toHaveAttribute("href", "/reviews/new");
  expect(await screen.findByRole("link", { name: "Open codelens" })).toHaveAttribute(
    "href",
    "/runs/review_1",
  );
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
