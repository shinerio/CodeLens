import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { ReviewProfilesPage } from "./ReviewProfilesPage";

afterEach(() => vi.unstubAllGlobals());

it("creates an Adaptive Deep profile", async () => {
  let createRequest: RequestInit | undefined;
  vi.stubGlobal("fetch", vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/reviewer-catalog")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (url.endsWith("/api/review-profiles") && init?.method === "POST") {
        createRequest = init;
        return Promise.resolve(new Response(JSON.stringify({
          profile_id: "profile-new",
          revision: 1,
          name: "Adaptive deep",
          is_default: false,
          reviewer_selection: { mode: "adaptive" },
          created_at: "2026-08-02T00:00:00Z",
          updated_at: "2026-08-02T00:00:00Z",
        }), { status: 201, headers: { "Content-Type": "application/json" } }));
      }
      if (url.endsWith("/api/review-profiles")) {
        return Promise.resolve(new Response(JSON.stringify([{
          profile_id: "profile-default",
          revision: 1,
          name: "Default",
          is_default: true,
          reviewer_selection: { mode: "adaptive" },
          created_at: "2026-08-01T00:00:00Z",
          updated_at: "2026-08-01T00:00:00Z",
        }]), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      throw new Error(`Unexpected request: ${url}`);
    },
  ));
  const user = userEvent.setup();
  render(<ReviewProfilesPage />, { wrapper: TestProviders });

  await user.click(await screen.findByRole("button", { name: "New profile" }));
  await user.type(screen.getByLabelText("Profile name"), "Adaptive deep");
  await user.click(screen.getByRole("button", { name: "Save profile" }));

  await waitFor(() => expect(createRequest).toBeDefined());
  expect(JSON.parse(String(createRequest?.body))).toMatchObject({
    name: "Adaptive deep",
    reviewer_selection: { mode: "adaptive" },
  });
});

it("preserves edits on a revision conflict and offers an explicit reload", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/reviewer-catalog")) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }));
      }
      if (url.endsWith("/api/review-profiles/profile-edit") && init?.method === "PUT") {
        return Promise.resolve(new Response(JSON.stringify({
          code: "review_profile_revision_conflict",
          message: "review profile revision conflict",
        }), { status: 409, headers: { "Content-Type": "application/json" } }));
      }
      if (url.endsWith("/api/review-profiles")) {
        return Promise.resolve(new Response(JSON.stringify([
          {
            profile_id: "profile-default",
            revision: 1,
            name: "Default",
            is_default: true,
            reviewer_selection: { mode: "adaptive" },
            created_at: "2026-08-01T00:00:00Z",
            updated_at: "2026-08-01T00:00:00Z",
          },
          {
            profile_id: "profile-edit",
            revision: 3,
            name: "Editable",
            is_default: false,
            reviewer_selection: { mode: "adaptive" },
            created_at: "2026-08-01T00:00:00Z",
            updated_at: "2026-08-02T00:00:00Z",
          },
        ]), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      throw new Error(`Unexpected request: ${url}`);
    },
  ));
  const user = userEvent.setup();
  render(<ReviewProfilesPage />, { wrapper: TestProviders });

  const editableCard = (await screen.findByText("Editable")).closest("article");
  if (!(editableCard instanceof HTMLElement)) throw new Error("Editable profile card missing");
  await user.click(within(editableCard).getByRole("button", { name: "Edit" }));
  const nameInput = screen.getByLabelText("Profile name");
  await user.clear(nameInput);
  await user.type(nameInput, "My edited name");
  await user.click(screen.getByRole("button", { name: "Save profile" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("changed elsewhere");
  expect(screen.getByDisplayValue("My edited name")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reload server version" })).toBeVisible();
});
