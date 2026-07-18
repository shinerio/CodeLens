import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { NewReviewPage } from "./NewReviewPage";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

it("creates a branch review with the default correctness agent", async () => {
  fetchMock
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          repository_id: "repository-1",
          repository_realpath_hash: "a".repeat(64),
          git_common_dir_hash: "b".repeat(64),
          display_path: "/srv/repos/app",
          head_oid: "c".repeat(40),
          current_branch: "feature",
          is_dirty: true,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ task_id: "review_1", status: "created" }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      ),
    );

  const user = userEvent.setup();

  render(<NewReviewPage />, { wrapper: TestProviders });

  expect(screen.getByLabelText("Correctness")).toBeChecked();

  await user.type(screen.getByLabelText("Repository path"), "/srv/repos/app");
  await user.click(screen.getByRole("button", { name: "Inspect" }));

  expect(screen.getByLabelText("Target branch")).toHaveValue("feature");

  await user.click(screen.getByRole("button", { name: /Branch diff/ }));
  await user.clear(screen.getByLabelText("Base branch"));
  await user.type(screen.getByLabelText("Base branch"), "origin/main");
  await user.click(screen.getByRole("button", { name: "Start review" }));

  const lastCall = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  expect(lastCall?.[0]).toBe("/api/reviews");
  expect(lastCall?.[1]).toMatchObject({ method: "POST" });

  const body = JSON.parse(String(lastCall?.[1]?.body)) as {
    mode: string;
    repository_path: string;
    scope: { base_ref: string; target_ref: string; type: string };
    selected_agents: string[];
  };

  expect(body).toEqual({
    mode: "review",
    repository_path: "/srv/repos/app",
    scope: {
      type: "branch",
      base_ref: "origin/main",
      target_ref: "feature",
      include_workspace_changes: false,
    },
    selected_agents: ["correctness:v1"],
  });
});
