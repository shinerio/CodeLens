import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TestProviders } from "../test/TestProviders";
import { App } from "./App";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("confirm", vi.fn(() => true));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("shows persistent reviews as nested workspaces and deletes one", async () => {
    fetchMock
      .mockResolvedValueOnce(
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
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    const user = userEvent.setup();

    render(<App />, { wrapper: TestProviders });

    expect(screen.getByText("CodeLens")).toBeInTheDocument();
    expect(screen.getAllByText("Reviews").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "New review" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /codelens/ })).toHaveAttribute(
      "href",
      "/reviews/review_1",
    );

    await user.click(screen.getByRole("button", { name: "Delete review codelens" }));

    expect(fetchMock.mock.calls).toContainEqual([
      "/api/reviews/review_1",
      expect.objectContaining({ method: "DELETE" }),
    ]);
  });
});
