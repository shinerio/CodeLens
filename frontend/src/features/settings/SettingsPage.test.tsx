import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { SettingsPage } from "./SettingsPage";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => cleanup());

it("creates the first persistent model gateway without retaining its API key", async () => {
  fetchMock
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ active_gateway_id: null, gateways: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ level: "info" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          active_gateway_id: "gateway_1",
          gateways: [
            {
              gateway_id: "gateway_1",
              name: "Primary gateway",
              model: "gpt-test",
              base_url: "http://model-gateway.example:8080",
              is_active: true,
            },
          ],
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      ),
    );
  const user = userEvent.setup();

  render(<SettingsPage />, { wrapper: TestProviders });

  expect(await screen.findByText("No gateways configured")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Gateway name"), "Primary gateway");
  await user.type(screen.getByLabelText("API Key"), "sk-ui-test-secret");
  await user.type(screen.getByLabelText("Model"), "gpt-test");
  await user.type(screen.getByLabelText("Base URL"), "http://model-gateway.example:8080");
  await user.click(screen.getByRole("button", { name: "Add gateway" }));

  const createCall = fetchMock.mock.calls.find(
    ([url, init]) => url === "/api/settings/model-gateways" && init?.method === "POST",
  );
  expect(createCall?.[0]).toBe("/api/settings/model-gateways");
  expect(createCall?.[1]).toMatchObject({ method: "POST" });
  expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
    name: "Primary gateway",
    api_key: "sk-ui-test-secret",
    model: "gpt-test",
    base_url: "http://model-gateway.example:8080",
    vendor: "openai",
    api_type: "chat_completions",
    max_tokens: 65536,
    thinking_level: "disabled",
    agent_timeout: 1800,
  });
  expect(await screen.findByText("Active gateway")).toBeInTheDocument();
  expect(screen.getByLabelText("API Key")).toHaveValue("");
});

it("switches the active gateway without asking for the stored key", async () => {
  const initialCatalog = {
    active_gateway_id: "gateway_primary",
    gateways: [
      {
        gateway_id: "gateway_primary",
        name: "Primary gateway",
        model: "gpt-primary",
        base_url: "https://primary.example/v1",
        is_active: true,
      },
      {
        gateway_id: "gateway_secondary",
        name: "Secondary gateway",
        model: "gpt-secondary",
        base_url: "https://secondary.example/v1",
        is_active: false,
      },
    ],
  };
  fetchMock
    .mockResolvedValueOnce(
      new Response(JSON.stringify(initialCatalog), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ level: "info" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ...initialCatalog,
          active_gateway_id: "gateway_secondary",
          gateways: initialCatalog.gateways.map((gateway) => ({
            ...gateway,
            is_active: gateway.gateway_id === "gateway_secondary",
          })),
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  const user = userEvent.setup();

  render(<SettingsPage />, { wrapper: TestProviders });

  const secondary = await screen.findByTestId("gateway-gateway_secondary");
  await user.click(within(secondary).getByRole("button", { name: "Activate" }));

  const activateCall = fetchMock.mock.calls.find(
    ([url]) => url === "/api/settings/active-model-gateway",
  );
  expect(JSON.parse(String(activateCall?.[1]?.body))).toEqual({
    gateway_id: "gateway_secondary",
  });
  expect(await within(secondary).findByText("Active gateway")).toBeInTheDocument();
});

it("sends a connectivity test request when the test connectivity button is clicked", async () => {
  const catalog = {
    active_gateway_id: "gateway_primary",
    gateways: [
      {
        gateway_id: "gateway_primary",
        name: "Primary gateway",
        model: "gpt-primary",
        base_url: "https://primary.example/v1",
        is_active: true,
      },
    ],
  };
  fetchMock
    .mockResolvedValueOnce(
      new Response(JSON.stringify(catalog), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ level: "info" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ ok: true, latency_ms: 42, detail: "TCP connection succeeded." }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  const user = userEvent.setup();

  render(<SettingsPage />, { wrapper: TestProviders });

  const card = await screen.findByTestId("gateway-gateway_primary");
  await user.click(
    within(card).getByRole("button", { name: "Test connectivity Primary gateway" }),
  );

  const connectivityCall = fetchMock.mock.calls.find(
    ([url]) =>
      url ===
      "/api/settings/model-gateways/gateway_primary/test-connectivity",
  );
  expect(connectivityCall?.[1]?.method).toBe("POST");
  expect(
    await within(card).findByText("Reachable (42ms)"),
  ).toBeInTheDocument();
});

it("sends an availability test request when the test availability button is clicked", async () => {
  const catalog = {
    active_gateway_id: "gateway_primary",
    gateways: [
      {
        gateway_id: "gateway_primary",
        name: "Primary gateway",
        model: "gpt-primary",
        base_url: "https://primary.example/v1",
        is_active: true,
      },
    ],
  };
  fetchMock
    .mockResolvedValueOnce(
      new Response(JSON.stringify(catalog), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ level: "info" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ ok: false, latency_ms: 100, detail: "Connection failed." }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  const user = userEvent.setup();

  render(<SettingsPage />, { wrapper: TestProviders });

  const card = await screen.findByTestId("gateway-gateway_primary");
  await user.click(
    within(card).getByRole("button", { name: "Test availability Primary gateway" }),
  );

  const availabilityCall = fetchMock.mock.calls.find(
    ([url]) =>
      url ===
      "/api/settings/model-gateways/gateway_primary/test-availability",
  );
  expect(availabilityCall?.[1]?.method).toBe("POST");
  expect(
    await within(card).findByText("LLM not responding"),
  ).toBeInTheDocument();
});
