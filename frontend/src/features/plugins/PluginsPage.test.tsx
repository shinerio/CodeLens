import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { PluginsPage } from "./PluginsPage";

const fetchMock = vi.fn();
let hookIsInstalled = true;
let hookInstallFailures = 0;
let installRequest: RequestInit | undefined;
let configRequest: RequestInit | undefined;
let pluginsPayload: Array<Record<string, unknown>>;

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  hookIsInstalled = true;
  hookInstallFailures = 0;
  installRequest = undefined;
  configRequest = undefined;
  pluginsPayload = [
    {
      plugin_id: "local",
      manifest: {
        plugin_id: "local",
        name: "Local Development Plugin",
        version: "1.0.0",
        description: "Local Git hook trigger",
        author: "CodeLens Team",
        platform: "local",
        capabilities: {
          trigger: {
            trigger_type: "local-hook",
            supported_events: ["post-commit"],
            entry_point: "local_hook_trigger:LocalHookTriggerAdapter",
            config_schema: {},
          },
        },
        min_codelens_version: null,
      },
      is_builtin: true,
      install_path: null,
      trigger_enabled: true,
      report_enabled: false,
      report_auto_export: false,
      trigger_config: {
        repository_paths: ["/workspace/repository"],
        events: ["post-commit"],
        scope_type: "commit",
        base_ref: null,
        target_ref: null,
        selected_agents: ["correctness:v1"],
        prompt_locale: "en",
        debounce_seconds: 10,
      },
      report_config: {},
    },
  ];
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/plugins/install")) {
      installRequest = init;
      return new Response(
        JSON.stringify({
          plugin_id: "external",
          install_path: "/data/plugins/external",
          installed_at: "2026-07-29T00:00:00Z",
        }),
        {
          status: 201,
          headers: { "Content-Type": "application/json" },
        },
      );
    }
    if (url.endsWith("/api/plugins")) {
      return jsonResponse(pluginsPayload);
    }
    if (url.endsWith("/api/review-profiles")) {
      return jsonResponse([
        {
          profile_id: "profile-adaptive",
          revision: 2,
          name: "Adaptive deep",
          is_default: true,
          reviewer_selection: { mode: "adaptive" },
          budget_profile: "deep",
          created_at: "2026-08-01T00:00:00Z",
          updated_at: "2026-08-02T00:00:00Z",
        },
      ]);
    }
    if (url.endsWith("/api/reviewer-catalog")) {
      return jsonResponse([
        {
          reference: "security:v1",
          agent_id: "security",
          version: 1,
          dimensions: ["security"],
          cost_class: "balanced",
          planner_eligible: true,
          capability_readiness: "ready",
          is_legacy: false,
        },
      ]);
    }
    if (url.endsWith("/api/plugins/local/trigger/config")) {
      configRequest = init;
      return jsonResponse(pluginsPayload[0]);
    }
    if (url.endsWith("/api/plugins/external/report/config")) {
      configRequest = init;
      return jsonResponse(pluginsPayload[0]);
    }
    if (url.endsWith("/api/plugins/local/trigger/hook-status")) {
      return jsonResponse({
        is_installed: hookIsInstalled,
        hook_path: hookIsInstalled
          ? "/workspace/repository/.git/hooks/post-commit"
          : null,
        repository_path: "/workspace/repository",
        repositories: [
          {
            repository_path: "/workspace/repository",
            hooks: { "post-commit": hookIsInstalled },
            is_installed: hookIsInstalled,
          },
        ],
      });
    }
    if (url.endsWith("/api/plugins/local/trigger/install-hooks")) {
      if (hookInstallFailures > 0) {
        hookInstallFailures -= 1;
        return new Response(JSON.stringify({ message: "Hook installation failed" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        });
      }
      hookIsInstalled = true;
      return jsonResponse({
        is_installed: true,
        hook_path: "/workspace/repository/.git/hooks/post-commit",
        repository_path: "/workspace/repository",
        repositories: [
          {
            repository_path: "/workspace/repository",
            hooks: { "post-commit": true },
            is_installed: true,
          },
        ],
      });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the installed hook state for each configured repository", async () => {
  const user = userEvent.setup();
  render(<PluginsPage />, { wrapper: TestProviders });

  expect(await screen.findByText("/workspace/repository")).toBeVisible();
  expect(await screen.findByText("Installed")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Reinstall Git hooks" }));
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/plugins/local/trigger/install-hooks",
    expect.objectContaining({ method: "POST" }),
  );
});

it("keeps installation retryable after an install failure", async () => {
  hookIsInstalled = false;
  hookInstallFailures = 1;
  const user = userEvent.setup();
  render(<PluginsPage />, { wrapper: TestProviders });

  const installButton = await screen.findByRole("button", { name: "Install Git hooks" });
  await user.click(installButton);

  expect(await screen.findByText("Hook installation failed")).toBeVisible();
  expect(installButton).toBeEnabled();

  await user.click(installButton);

  expect(await screen.findByRole("button", { name: "Reinstall Git hooks" })).toBeEnabled();
  expect(await screen.findByText("Installed")).toBeVisible();
});

it("omits an empty Git ref from the installation request", async () => {
  const user = userEvent.setup();
  render(<PluginsPage />, { wrapper: TestProviders });

  await user.type(screen.getByPlaceholderText("Git repository URL"), "https://example.invalid/plugin.git");
  await user.click(screen.getByRole("button", { name: "Install" }));

  await waitFor(() => expect(installRequest).toBeDefined());
  expect(JSON.parse(String(installRequest?.body))).toEqual({
    git_url: "https://example.invalid/plugin.git",
  });
});

it("submits numeric schema fields as numbers", async () => {
  pluginsPayload = [
    {
      plugin_id: "external",
      manifest: {
        plugin_id: "external",
        name: "External report",
        version: "1.0.0",
        description: "Report plugin",
        author: "Test",
        platform: "local",
        capabilities: {
          report: {
            entry_point: "sink:Sink",
            config_schema: {
              type: "object",
              properties: {
                retries: {
                  type: "integer",
                  description: "Retries",
                  default: 1,
                },
              },
            },
          },
        },
        min_codelens_version: null,
      },
      is_builtin: false,
      install_path: "/data/plugins/external",
      trigger_enabled: false,
      report_enabled: true,
      report_auto_export: false,
      trigger_config: {},
      report_config: { retries: 1 },
    },
  ];
  const user = userEvent.setup();
  render(<PluginsPage />, { wrapper: TestProviders });

  const retries = await screen.findByRole("spinbutton");
  await user.clear(retries);
  await user.type(retries, "3");
  await user.click(screen.getByRole("button", { name: "Save configuration" }));

  await waitFor(() => expect(configRequest).toBeDefined());
  expect(JSON.parse(String(configRequest?.body))).toEqual({
    config: { retries: 3 },
  });
});

it("copies a Profile into a v2 plugin policy with separate provenance", async () => {
  const localPlugin = pluginsPayload[0];
  if (localPlugin === undefined) throw new Error("Local plugin fixture missing");
  pluginsPayload = [
    {
      ...localPlugin,
      plugin_api_version: "2",
      profile_source: null,
      trigger_config: {
        repository_paths: ["/workspace/repository"],
        events: ["post-commit"],
        scope_type: "commit",
        reviewer_selection: {
          mode: "fixed",
          reviewer_versions: ["security:v1"],
        },
        budget_profile: "standard",
        supersede_policy: "latest_snapshot",
        prompt_locale: "en",
        debounce_seconds: 10,
      },
    },
  ];
  const user = userEvent.setup();
  render(<PluginsPage />, { wrapper: TestProviders });

  await user.click(await screen.findByRole("button", { name: "Reload from profile" }));
  await user.click(screen.getByRole("button", { name: "Save configuration" }));

  await waitFor(() => expect(configRequest).toBeDefined());
  const body = JSON.parse(String(configRequest?.body));
  expect(body.config.reviewer_selection).toEqual({ mode: "adaptive" });
  expect(body.config.budget_profile).toBe("deep");
  expect(body.config.profile_source).toBeUndefined();
  expect(body.profile_source).toEqual({
    profile_id: "profile-adaptive",
    profile_name: "Adaptive deep",
    profile_revision: 2,
  });
});
