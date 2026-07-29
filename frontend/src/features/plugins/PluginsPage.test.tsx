import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { PluginsPage } from "./PluginsPage";

const fetchMock = vi.fn();
let hookIsInstalled = true;
let hookInstallFailures = 0;

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  hookIsInstalled = true;
  hookInstallFailures = 0;
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/plugins")) {
      return jsonResponse([
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
      ]);
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
