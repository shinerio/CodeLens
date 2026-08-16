import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { SettingsPage } from "./SettingsPage";

const fetchMock = vi.fn();

function instructionSettingsResponse(rootMaxLines = 1000, nestedMaxLines = 500) {
  return new Response(JSON.stringify({
    root_max_lines: rootMaxLines,
    nested_max_lines: nestedMaxLines,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function reviewCompletionSettingsResponse(maxIncompleteReviewRetries = 3) {
  return new Response(JSON.stringify({
    max_incomplete_review_retries: maxIncompleteReviewRetries,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function triggerIdempotencySettingsResponse(enabled = false) {
  return new Response(JSON.stringify({ enabled }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function fileExclusionSettingsResponse(
  suffixes: string[] = [],
  pathRegexes: string[] = [],
) {
  return new Response(JSON.stringify({
    suffixes,
    path_regexes: pathRegexes,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function toolLimitsResponse() {
  return new Response(JSON.stringify({
    max_results: 200,
    max_read_bytes: 65536,
    max_scan_bytes: 1048576,
    max_source_bytes: 1048576,
    max_lines: 1000,
    max_path_chars: 1024,
    max_pattern_chars: 512,
    regex_timeout_seconds: 30.0,
    comment_batch_size: 20,
    short_text_max: 240,
    long_text_max: 8000,
    task_summary_max: 8000,
    context_compaction_enabled: true,
    context_compaction_trigger_bytes: 131072,
    context_compaction_keep_recent_evidence_results: 6,
    context_compaction_max_retries: 3,
    context_compaction_retry_backoff_base: 2.0,
    context_compaction_retry_max_delay: 30.0,
    context_compaction_max_consecutive_failures: 3,
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => cleanup());

it("shows context and tool sizes in KB with compaction enabled by default", async () => {
  fetchMock.mockImplementation((url: string) => {
    if (url === "/api/settings/model-gateways") {
      return Promise.resolve(new Response(JSON.stringify({ active_gateway_id: null, gateways: [] })));
    }
    if (url === "/api/settings/logging") {
      return Promise.resolve(new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true })));
    }
    if (url === "/api/settings/repositories") {
      return Promise.resolve(new Response(JSON.stringify({ recent_repository_limit: 10 })));
    }
    if (url === "/api/settings/instruction-files") {
      return Promise.resolve(instructionSettingsResponse());
    }
    if (url === "/api/settings/review-completion") {
      return Promise.resolve(reviewCompletionSettingsResponse());
    }
    if (url === "/api/settings/trigger-idempotency") {
      return Promise.resolve(triggerIdempotencySettingsResponse());
    }
    if (url === "/api/settings/tool-limits") {
      return Promise.resolve(toolLimitsResponse());
    }
    if (url === "/api/settings/file-exclusions") {
      return Promise.resolve(fileExclusionSettingsResponse());
    }
    throw new Error(`Unexpected request: ${url}`);
  });

  render(<SettingsPage />, { wrapper: TestProviders });

  expect(await screen.findByRole("spinbutton", { name: "Max read size (KB)" })).toHaveValue(64);
  expect(screen.getByRole("spinbutton", { name: "Max scan size (KB)" })).toHaveValue(1024);
  expect(screen.getByRole("spinbutton", { name: "Max source size (KB)" })).toHaveValue(1024);
  expect(screen.getByRole("spinbutton", { name: "Context compaction trigger (KB)" })).toHaveValue(128);
  expect(
    screen.getByRole("checkbox", { name: "Enable deterministic context compaction" }),
  ).toBeChecked();
  expect(screen.getByRole("spinbutton", { name: "Compaction max retries" })).toHaveValue(3);
  expect(screen.getByRole("spinbutton", { name: "Compaction retry backoff base (seconds)" })).toHaveValue(2);
  expect(screen.getByRole("spinbutton", { name: "Compaction retry max delay (seconds)" })).toHaveValue(30);
  expect(screen.getByRole("spinbutton", { name: "Compaction consecutive failure circuit breaker" })).toHaveValue(3);
});

it("creates the first persistent model gateway without retaining its API key", async () => {
  fetchMock
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ active_gateway_id: null, gateways: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ recent_repository_limit: 10 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(instructionSettingsResponse())
    .mockResolvedValueOnce(reviewCompletionSettingsResponse())
    .mockResolvedValueOnce(triggerIdempotencySettingsResponse())
    .mockResolvedValueOnce(toolLimitsResponse())
    .mockResolvedValueOnce(fileExclusionSettingsResponse())
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
  await user.click(screen.getByRole("button", { name: /add gateway/i }));
  const gatewayNameInput = await screen.findByLabelText("Gateway name");
  await user.type(gatewayNameInput, "Primary gateway");
  await user.type(screen.getByLabelText("API Key"), "sk-ui-test-secret");
  await user.type(screen.getByLabelText("Model"), "gpt-test");
  await user.type(screen.getByLabelText("Base URL"), "http://model-gateway.example:8080");
  const modal = document.querySelector(".gateway-modal") as HTMLElement;
  await user.click(within(modal).getByRole("button", { name: /^add gateway$/i }));

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
    agent_timeout: 3600,
    max_agent_turns: 500,
    max_tool_calls: 500,
    max_identical_tool_results: 3,
    tool_timeout_seconds: 30,
    max_retries: 10,
    retry_backoff_base: 1.0,
    retry_max_delay: 30.0,
  });
  expect(await screen.findByText("Active gateway")).toBeInTheDocument();
  expect(screen.getByLabelText("API Key")).toHaveValue("");
}, 10_000);

it("edits, deduplicates, validates, and saves Web file exclusion rules", async () => {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url === "/api/settings/file-exclusions" && init?.method === "PUT") {
      return Promise.resolve(fileExclusionSettingsResponse([".map"], ["^generated/"]));
    }
    if (url === "/api/settings/file-exclusions") {
      return Promise.resolve(fileExclusionSettingsResponse());
    }
    if (url === "/api/settings/model-gateways") {
      return Promise.resolve(new Response(JSON.stringify({ active_gateway_id: null, gateways: [] })));
    }
    if (url === "/api/settings/logging") {
      return Promise.resolve(new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true })));
    }
    if (url === "/api/settings/repositories") {
      return Promise.resolve(new Response(JSON.stringify({ recent_repository_limit: 10 })));
    }
    if (url === "/api/settings/instruction-files") {
      return Promise.resolve(instructionSettingsResponse());
    }
    if (url === "/api/settings/review-completion") {
      return Promise.resolve(reviewCompletionSettingsResponse());
    }
    if (url === "/api/settings/trigger-idempotency") {
      return Promise.resolve(triggerIdempotencySettingsResponse());
    }
    return Promise.resolve(toolLimitsResponse());
  });
  const user = userEvent.setup();
  render(<SettingsPage />, { wrapper: TestProviders });

  const suffixes = await screen.findByLabelText("Excluded literal suffixes");
  const regexes = screen.getByLabelText("Excluded path regular expressions");
  await user.type(suffixes, ".map\n.map");
  fireEvent.change(regexes, { target: { value: "[" } });
  expect(screen.getByRole("alert")).toHaveTextContent(
    "At least one path regular expression is invalid.",
  );
  await user.clear(regexes);
  await user.type(regexes, "^generated/");
  await user.click(screen.getByRole("button", { name: "Save file exclusions" }));

  await waitFor(() => {
    const updateCall = fetchMock.mock.calls.find(
      ([url, init]) => url === "/api/settings/file-exclusions" && init?.method === "PUT",
    );
    expect(JSON.parse(String(updateCall?.[1]?.body))).toEqual({
      suffixes: [".map"],
      path_regexes: ["^generated/"],
    });
  });
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
      new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ recent_repository_limit: 10 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(instructionSettingsResponse())
    .mockResolvedValueOnce(reviewCompletionSettingsResponse())
    .mockResolvedValueOnce(triggerIdempotencySettingsResponse())
    .mockResolvedValueOnce(toolLimitsResponse())
    .mockResolvedValueOnce(fileExclusionSettingsResponse())
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

it("updates the selected model execution limits from the runtime rail", async () => {
  const gateway = {
    gateway_id: "gateway_primary",
    name: "Primary gateway",
    model: "gpt-primary",
    base_url: "https://primary.example/v1",
    vendor: "openai",
    is_active: true,
    api_type: "responses",
    max_tokens: 65536,
    thinking_level: "medium",
    agent_timeout: 1800,
    max_agent_turns: 100,
    max_tool_calls: 300,
    max_identical_tool_results: 3,
    tool_timeout_seconds: 30,
    max_retries: 10,
    retry_backoff_base: 1.0,
    retry_max_delay: 30.0,
  };
  fetchMock
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ active_gateway_id: gateway.gateway_id, gateways: [gateway] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ recent_repository_limit: 10 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(instructionSettingsResponse())
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          active_gateway_id: gateway.gateway_id,
          gateways: [{ ...gateway, max_agent_turns: 80, max_tool_calls: 240 }],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  const user = userEvent.setup();

  render(<SettingsPage />, { wrapper: TestProviders });

  const maxTurnsInput = await screen.findByLabelText("Maximum agent turns");
  await waitFor(() => expect(maxTurnsInput).toBeEnabled());
  await user.clear(maxTurnsInput);
  await user.type(maxTurnsInput, "80");
  await user.clear(screen.getByLabelText("Maximum tool calls"));
  await user.type(screen.getByLabelText("Maximum tool calls"), "240");
  expect(screen.getByLabelText("Agent Timeout (s)")).toHaveValue(1800);
  expect(screen.getByLabelText("Maximum agent turns")).toHaveValue(80);
  expect(screen.getByLabelText("Maximum tool calls")).toHaveValue(240);
  expect(screen.getByLabelText("Identical result limit")).toHaveValue(3);
  expect(screen.getByLabelText("Tool timeout (s)")).toHaveValue(30);
  const saveButton = screen.getByRole("button", { name: "Save model execution limits" });
  expect(saveButton).toBeEnabled();
  await user.click(saveButton);

  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          url === "/api/settings/model-gateways/gateway_primary" && init?.method === "PUT",
      ),
    ).toBe(true),
  );
  const updateCall = fetchMock.mock.calls.find(
    ([url, init]) =>
      url === "/api/settings/model-gateways/gateway_primary" && init?.method === "PUT",
  );
  expect(JSON.parse(String(updateCall?.[1]?.body))).toEqual({
    name: "Primary gateway",
    model: "gpt-primary",
    base_url: "https://primary.example/v1",
    vendor: "openai",
    api_type: "responses",
    max_tokens: 65536,
    thinking_level: "medium",
    agent_timeout: 1800,
    max_agent_turns: 80,
    max_tool_calls: 240,
    max_identical_tool_results: 3,
    tool_timeout_seconds: 30,
    max_retries: 10,
    retry_backoff_base: 1.0,
    retry_max_delay: 30.0,
  });
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
      new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ recent_repository_limit: 10 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(instructionSettingsResponse())
    .mockResolvedValueOnce(reviewCompletionSettingsResponse())
    .mockResolvedValueOnce(triggerIdempotencySettingsResponse())
    .mockResolvedValueOnce(toolLimitsResponse())
    .mockResolvedValueOnce(fileExclusionSettingsResponse())
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
      new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ recent_repository_limit: 10 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(instructionSettingsResponse())
    .mockResolvedValueOnce(reviewCompletionSettingsResponse())
    .mockResolvedValueOnce(triggerIdempotencySettingsResponse())
    .mockResolvedValueOnce(toolLimitsResponse())
    .mockResolvedValueOnce(fileExclusionSettingsResponse())
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

it("updates the recent repository list limit", async () => {
  fetchMock
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ active_gateway_id: null, gateways: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ recent_repository_limit: 10 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(instructionSettingsResponse())
    .mockResolvedValueOnce(reviewCompletionSettingsResponse())
    .mockResolvedValueOnce(triggerIdempotencySettingsResponse())
    .mockResolvedValueOnce(toolLimitsResponse())
    .mockResolvedValueOnce(fileExclusionSettingsResponse())
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ recent_repository_limit: 15 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  const user = userEvent.setup();

  render(<SettingsPage />, { wrapper: TestProviders });

  const limitInput = await screen.findByLabelText("Recent repository limit");
  await waitFor(() => expect(limitInput).toBeEnabled());
  await user.clear(limitInput);
  await user.type(limitInput, "15");
  const saveButton = screen.getByRole("button", { name: "Save review settings" });
  await waitFor(() => expect(saveButton).toBeEnabled());
  await user.click(saveButton);

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/settings/repositories",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ recent_repository_limit: 15 }),
    }),
  );
});

it("updates instruction file limits and omits credential handling details", async () => {
  fetchMock
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ active_gateway_id: null, gateways: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ recent_repository_limit: 10 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(instructionSettingsResponse())
    .mockResolvedValueOnce(reviewCompletionSettingsResponse())
    .mockResolvedValueOnce(triggerIdempotencySettingsResponse())
    .mockResolvedValueOnce(toolLimitsResponse())
    .mockResolvedValueOnce(fileExclusionSettingsResponse())
    .mockResolvedValueOnce(instructionSettingsResponse(800, 240));
  const user = userEvent.setup();

  render(<SettingsPage />, { wrapper: TestProviders });

  expect(await screen.findByText("Recommended: 500 lines")).toBeInTheDocument();
  expect(screen.getByText("Recommended: 200 lines")).toBeInTheDocument();
  expect(screen.queryByText("Credential handling")).not.toBeInTheDocument();
  const rootLimit = screen.getByLabelText("Root instruction file limit");
  const nestedLimit = screen.getByLabelText("Nested instruction file limit");
  await waitFor(() => expect(rootLimit).toBeEnabled());
  await user.clear(rootLimit);
  await user.type(rootLimit, "800");
  await user.clear(nestedLimit);
  await user.type(nestedLimit, "240");
  await user.click(screen.getByRole("button", { name: "Save review settings" }));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/settings/instruction-files",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ root_max_lines: 800, nested_max_lines: 240 }),
    }),
  );
});

it("updates the maximum incomplete review retry count", async () => {
  fetchMock
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ active_gateway_id: null, gateways: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ recent_repository_limit: 10 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    .mockResolvedValueOnce(instructionSettingsResponse())
    .mockResolvedValueOnce(reviewCompletionSettingsResponse())
    .mockResolvedValueOnce(triggerIdempotencySettingsResponse())
    .mockResolvedValueOnce(toolLimitsResponse())
    .mockResolvedValueOnce(fileExclusionSettingsResponse())
    .mockResolvedValueOnce(reviewCompletionSettingsResponse(5));
  const user = userEvent.setup();

  render(<SettingsPage />, { wrapper: TestProviders });

  const retryLimit = await screen.findByLabelText("Incomplete review retry limit");
  await waitFor(() => expect(retryLimit).toBeEnabled());
  await user.clear(retryLimit);
  await user.type(retryLimit, "5");
  await user.click(screen.getByRole("button", { name: "Save review settings" }));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/settings/review-completion",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ max_incomplete_review_retries: 5 }),
    }),
  );
});

it("uses one panel-level action for all review settings", async () => {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (url === "/api/settings/model-gateways") {
      return Promise.resolve(new Response(JSON.stringify({ active_gateway_id: null, gateways: [] })));
    }
    if (url === "/api/settings/logging") {
      return Promise.resolve(new Response(JSON.stringify({ default_level: "info", level: "info", model_output_enabled: true })));
    }
    if (url === "/api/settings/repositories") {
      return Promise.resolve(new Response(JSON.stringify({ recent_repository_limit: 10 })));
    }
    if (url === "/api/settings/instruction-files") {
      return Promise.resolve(instructionSettingsResponse());
    }
    if (url === "/api/settings/review-completion") {
      return Promise.resolve(reviewCompletionSettingsResponse());
    }
    if (url === "/api/settings/trigger-idempotency" && init?.method === "PUT") {
      return Promise.resolve(triggerIdempotencySettingsResponse(true));
    }
    if (url === "/api/settings/trigger-idempotency") {
      return Promise.resolve(triggerIdempotencySettingsResponse());
    }
    if (url === "/api/settings/file-exclusions") {
      return Promise.resolve(fileExclusionSettingsResponse());
    }
    return Promise.resolve(toolLimitsResponse());
  });
  const user = userEvent.setup();

  render(<SettingsPage />, { wrapper: TestProviders });

  const reviewHeading = await screen.findByRole("heading", { name: "Review Settings" });
  const reviewPanel = reviewHeading.closest("section");
  expect(reviewPanel).not.toBeNull();
  const panel = within(reviewPanel as HTMLElement);
  expect(panel.getAllByRole("button")).toHaveLength(1);

  const idempotencyCheckbox = panel.getByRole("checkbox", { name: "Trigger idempotency" });
  expect(idempotencyCheckbox.closest("label")).toHaveClass("settings-field--checkbox");
  await user.click(idempotencyCheckbox);
  await user.click(panel.getByRole("button", { name: "Save review settings" }));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/settings/trigger-idempotency",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ enabled: true }),
    }),
  );
});
