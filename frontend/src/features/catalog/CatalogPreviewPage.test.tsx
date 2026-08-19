import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { CatalogPreviewPage } from "./CatalogPreviewPage";

afterEach(() => vi.unstubAllGlobals());

it("renders reviewers and internal agents, and edits each versioned prompt", async () => {
  let updatedPromptUrl: string | undefined;
  const catalog = [
    {
      reference: "correctness:v2",
      agent_id: "correctness",
      version: 2,
      role: "reviewer",
      dimensions: ["correctness"],
      capability_readiness: "ready",
    },
    {
      reference: "security:v2",
      agent_id: "security",
      version: 1,
      role: "reviewer",
      dimensions: ["security"],
      capability_readiness: "ready",
    },
    {
      reference: "review-planner:v2",
      agent_id: "review-planner",
      version: 2,
      role: "planner",
      dimensions: [] as string[],
      capability_readiness: "ready",
    },
    {
      reference: "review-verifier:v2",
      agent_id: "review-verifier",
      version: 2,
      role: "verifier",
      dimensions: [] as string[],
      capability_readiness: "ready",
    },
  ];
  vi.stubGlobal("fetch", vi.fn().mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/agent-prompts")) {
        return Promise.resolve(new Response(JSON.stringify(catalog), { status: 200 }));
      }
      if (url.includes("/api/agent-prompts/")) {
        const agentId = url.includes("/review-planner?") ? "review-planner"
          : url.includes("/security?") ? "security"
          : "correctness";
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
  expect(cards).toHaveLength(4);
  expect(within(cards[0]).getByText("correctness:v2")).toBeVisible();
  expect(within(cards[1]).getByText("security:v2")).toBeVisible();
  // Internal DAG roles appear alongside reviewers and are editable.
  expect(screen.getByText("review-planner:v2")).toBeVisible();
  expect(screen.getByText("review-verifier:v2")).toBeVisible();

  const plannerCard = cards.find((card) => within(card).queryByText("review-planner:v2") !== null);
  if (plannerCard === undefined) throw new Error("Planner card missing");
  await user.click(within(plannerCard).getByRole("button", { name: "Edit prompt" }));
  expect(await screen.findByDisplayValue("review-planner prompt")).toBeVisible();
  await user.clear(screen.getByLabelText("Agent prompt"));
  await user.type(screen.getByLabelText("Agent prompt"), "custom planner prompt");
  await user.click(screen.getByRole("button", { name: "Save prompt" }));

  await waitFor(() => expect(updatedPromptUrl).toContain("/api/agent-prompts/review-planner?"));
  expect(updatedPromptUrl).toContain("version=2");
});
