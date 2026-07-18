import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { RepositoryBrowser } from "./RepositoryBrowser";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("browses from a system root and selects a discovered Git repository", async () => {
  fetchMock
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          current_path: null,
          parent_path: null,
          roots: ["/"],
          directories: [],
          current_is_git_repository: false,
          is_truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          current_path: "/",
          parent_path: null,
          roots: ["/"],
          directories: [
            { name: "app", path: "/app", is_git_repository: true },
            { name: "data", path: "/data", is_git_repository: false },
          ],
          current_is_git_repository: false,
          is_truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  const onSelect = vi.fn();
  const user = userEvent.setup();

  render(<RepositoryBrowser isOpen onClose={vi.fn()} onSelect={onSelect} />, {
    wrapper: TestProviders,
  });

  await user.click(await screen.findByRole("button", { name: "/" }));
  await user.click(await screen.findByRole("button", { name: "Select repository app" }));

  expect(onSelect).toHaveBeenCalledWith("/app");
  expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/repositories/browse");
});
