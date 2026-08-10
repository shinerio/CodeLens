import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { CatalogPreviewPage } from "./CatalogPreviewPage";

afterEach(() => vi.unstubAllGlobals());

it("renders exactly the public backend reviewers and edits each versioned prompt", async () => {
  let updatedPromptUrl: string | undefined;
  const catalog = [
    {
      reference: "correctness:v2",
      agent_id: "correctness",
      version: 2,
      dimensions: ["correctness"],
      planner_eligible: true,
      capability_readiness: "ready",
    },
    {
      reference: "security:v2",
      agent_id: "security",
      version: 1,
      dimensions: ["security"],
      planner_eligible: true,
      capability_readiness: "ready",
    },
  ];
  vi.stubGlobal("fetch", vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/reviewer-catalog")) {
        return Promise.resolve(new Response(JSON.stringify(catalog), { status: 200 }));
      }
      if (url.includes("/api/reviewer-prompts/")) {
        const agentId = url.includes("/security?") ? "security" : "correctness";
        const version = agentId === "security" ? 1 : 2;
        if (init?.method === "PUT") updatedPromptUrl = url;
        return Promise.resolve(new Response(JSON.stringify({
          agent_id: agentId,
          version,
          locale: "en",
          system_prompt: "System boundary",
          prompt: `${agentId} prompt`,
          is_custom: init?.method === "PUT",
        }), { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      throw new Error(`Unexpected request: ${url}`);
    },
  ));
  const user = userEvent.setup();
  render(<CatalogPreviewPage kind="agents" />, { wrapper: TestProviders });

  const cards = await screen.findAllByTestId("reviewer-card");
  expect(cards).toHaveLength(2);
  expect(within(cards[0]).getByText("correctness:v2")).toBeVisible();
  expect(within(cards[1]).getByText("security:v2")).toBeVisible();
  expect(screen.queryByText("release-risk:v2")).not.toBeInTheDocument();

  const securityCard = cards.find((card) => within(card).queryByText("security:v2") !== null);
  if (securityCard === undefined) throw new Error("Security reviewer card missing");
  await user.click(within(securityCard).getByRole("button", { name: "Edit prompt" }));
  expect(await screen.findByDisplayValue("security prompt")).toBeVisible();
  await user.clear(screen.getByLabelText("Reviewer prompt"));
  await user.type(screen.getByLabelText("Reviewer prompt"), "custom security prompt");
  await user.click(screen.getByRole("button", { name: "Save prompt" }));

  await waitFor(() => expect(updatedPromptUrl).toContain("/api/reviewer-prompts/security?"));
  expect(updatedPromptUrl).toContain("version=1");
});
