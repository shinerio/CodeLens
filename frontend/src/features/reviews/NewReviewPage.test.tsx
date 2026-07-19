import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { NewReviewPage } from "./NewReviewPage";

const fetchMock = vi.fn();

const inspection = {
  repository_id: "repository-1",
  repository_realpath_hash: "a".repeat(64),
  git_common_dir_hash: "b".repeat(64),
  display_path: "/app",
  head_oid: "c".repeat(40),
  current_branch: "feature",
  is_dirty: true,
};

const branches = [
  { name: "feature", oid: "c".repeat(40), is_current: true, is_remote: false },
  { name: "main", oid: "d".repeat(40), is_current: false, is_remote: false },
  { name: "origin/main", oid: "d".repeat(40), is_current: false, is_remote: true },
];

function jsonResponse(value: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function commit(index: number) {
  const oid = index.toString(16).padStart(40, "0");
  return {
    oid,
    short_oid: oid.slice(-8),
    author: `Author ${index}`,
    message: `Commit message ${index}`,
    committed_at: "2026-07-18T12:00:00Z",
  };
}

function installApiMock({ configured = true, nextOffset = null as number | null } = {}) {
  fetchMock.mockImplementation((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/settings/model-gateways") {
      return jsonResponse({
        active_gateway_id: configured ? "gateway_primary" : null,
        gateways: configured
          ? [
              {
                gateway_id: "gateway_primary",
                name: "Primary",
                model: "gpt-test",
                base_url: "https://gateway.example/v1",
                is_active: true,
              },
            ]
          : [],
      });
    }
    if (url === "/api/repositories/browse") {
      const body = JSON.parse(String(init?.body)) as { path: string | null };
      if (body.path === null) {
        return jsonResponse({
          current_path: null,
          parent_path: null,
          roots: ["/"],
          directories: [],
          current_is_git_repository: false,
          is_truncated: false,
        });
      }
      return jsonResponse({
        current_path: "/",
        parent_path: null,
        roots: ["/"],
        directories: [{ name: "app", path: "/app", is_git_repository: true }],
        current_is_git_repository: false,
        is_truncated: false,
      });
    }
    if (url === "/api/repositories/inspect") {
      return jsonResponse(inspection);
    }
    if (url === "/api/repositories/catalog") {
      const body = JSON.parse(String(init?.body)) as { commit_offset: number };
      const offset = body.commit_offset;
      return jsonResponse({
        branches,
        commits: Array.from({ length: 10 }, (_, index) => commit(offset + index)),
        next_commit_offset: offset === 0 ? nextOffset : null,
      });
    }
    if (url === "/api/reviews") {
      return jsonResponse({ task_id: "review_1", status: "created" }, 202);
    }
    throw new Error(`Unexpected request: ${url}`);
  });
}

async function chooseRepository(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Browse folders" }));
  const dialog = await screen.findByRole("dialog", { name: "Repository browser" });
  await user.click(await within(dialog).findByRole("button", { name: "/" }));
  await user.click(
    await within(dialog).findByRole("button", { name: "Select repository app" }),
  );
  expect(await screen.findByText("Inspection ready")).toBeVisible();
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("creates a branch review using Git branch dropdowns", async () => {
  installApiMock();
  const user = userEvent.setup();

  render(<NewReviewPage />, { wrapper: TestProviders });

  expect(await screen.findByText("Gateway ready")).toBeInTheDocument();
  await chooseRepository(user);

  expect(screen.getByLabelText("Base branch")).toHaveValue("origin/main");
  expect(screen.getByLabelText("Target branch")).toHaveValue("feature");
  expect(screen.getByLabelText("Base branch")).toHaveRole("combobox");
  await user.click(screen.getByRole("button", { name: "Start review" }));

  const reviewCall = fetchMock.mock.calls.find(([url]) => url === "/api/reviews");
  const body = JSON.parse(String(reviewCall?.[1]?.body)) as {
    repository_path: string;
    scope: { base_ref: string; target_ref: string; type: string };
  };
  expect(body.repository_path).toBe("/app");
  expect(body.scope).toMatchObject({
    type: "branch",
    base_ref: "origin/main",
    target_ref: "feature",
  });
});

it("inspects a repository path entered directly into the path field", async () => {
  installApiMock();
  const user = userEvent.setup();

  render(<NewReviewPage />, { wrapper: TestProviders });

  const pathField = screen.getByLabelText("Repository path");
  await user.type(pathField, "  /app  ");
  await user.keyboard("{Enter}");

  expect(await screen.findByText("Inspection ready")).toBeVisible();
  expect(pathField).toHaveValue("/app");
  const inspectionCall = fetchMock.mock.calls.find(([url]) => url === "/api/repositories/inspect");
  expect(JSON.parse(String(inspectionCall?.[1]?.body))).toEqual({ path: "/app" });
});

it("requires an active gateway before a review can start", async () => {
  installApiMock({ configured: false });
  const user = userEvent.setup();

  render(<NewReviewPage />, { wrapper: TestProviders });
  await chooseRepository(user);

  expect(await screen.findByText("An active model gateway is required.")).toBeVisible();
  expect(screen.getByRole("link", { name: "Configure gateways" })).toHaveAttribute(
    "href",
    "/settings",
  );
  expect(screen.getByRole("button", { name: "Start review" })).toBeDisabled();
});

it("loads commits ten at a time into the commit selector", async () => {
  installApiMock({ nextOffset: 10 });
  const user = userEvent.setup();

  render(<NewReviewPage />, { wrapper: TestProviders });
  await chooseRepository(user);
  await user.click(screen.getByRole("button", { name: /Commit diff/ }));

  expect(screen.getByLabelText("Base commit")).toHaveRole("combobox");
  expect(screen.getByLabelText("Base commit")).toHaveValue("0".repeat(40));
  await user.click(screen.getByRole("button", { name: "Load more commits" }));

  expect(await screen.findByRole("option", { name: /Author 19 · Commit message 19/ })).toBeVisible();
  const catalogCalls = fetchMock.mock.calls.filter(([url]) => url === "/api/repositories/catalog");
  expect(JSON.parse(String(catalogCalls[1]?.[1]?.body))).toMatchObject({ commit_offset: 10 });
});
