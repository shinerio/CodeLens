import { expect, test } from "@playwright/test";

const LOCAL_PLUGIN = {
  plugin_id: "local",
  manifest: {
    plugin_id: "local",
    name: "Local Development Plugin",
    version: "1.0.0",
    description: "Local Git hook trigger and file report export",
    author: "CodeLens Team",
    platform: "local",
    capabilities: {
      trigger: {
        trigger_type: "local-hook",
        supported_events: ["post-commit", "pre-push"],
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
    repository_paths: ["/workspace/repository-with-a-long-name"],
    events: ["post-commit", "pre-push"],
    scope_type: "commit",
    base_ref: null,
    target_ref: null,
    selected_agents: ["correctness:v1"],
    prompt_locale: "en",
    debounce_seconds: 10,
  },
  report_config: {},
};

const INSTALLED_HOOK_STATUS = {
  is_installed: true,
  hook_path: null,
  repository_path: "/workspace/repository-with-a-long-name",
  repositories: [
    {
      repository_path: "/workspace/repository-with-a-long-name",
      hooks: { "post-commit": true, "pre-push": true },
      is_installed: true,
    },
  ],
};

test("installs plugins without a null ref and reinstalls configured hooks", async ({
  page,
}, testInfo) => {
  let installPayload: unknown;
  let hookInstallCount = 0;
  await page.route("**/api/plugins/install", async (route) => {
    installPayload = route.request().postDataJSON();
    await route.fulfill({
      json: {
        plugin_id: "external",
        install_path: "/data/plugins/external",
        installed_at: "2026-07-29T00:00:00Z",
      },
      status: 201,
    });
  });
  await page.route("**/api/plugins/local/trigger/install-hooks", async (route) => {
    hookInstallCount += 1;
    await route.fulfill({ json: INSTALLED_HOOK_STATUS });
  });
  await page.route("**/api/plugins/local/trigger/hook-status", async (route) => {
    await route.fulfill({ json: INSTALLED_HOOK_STATUS });
  });
  await page.route("**/api/plugins", async (route) => {
    await route.fulfill({ json: [LOCAL_PLUGIN] });
  });

  await page.goto("/plugins");

  await expect(
    page.getByRole("code").filter({ hasText: "/workspace/repository-with-a-long-name" }),
  ).toBeVisible();
  await expect(page.getByText("Installed")).toBeVisible();
  await page.getByRole("button", { name: "Reinstall Git hooks" }).click();
  await expect.poll(() => hookInstallCount).toBe(1);

  await page.getByPlaceholder("Git repository URL").fill(
    "https://example.invalid/plugin.git",
  );
  await page.getByRole("button", { name: "Install", exact: true }).click();
  await expect.poll(() => installPayload).toEqual({
    git_url: "https://example.invalid/plugin.git",
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("plugins-page.png"),
    fullPage: true,
  });
});

test("keeps a copied v2 profile snapshot until reload is explicit", async ({ page }) => {
  let configPayload: Record<string, unknown> | undefined;
  const plugin = {
    ...LOCAL_PLUGIN,
    plugin_api_version: "2",
    profile_source: {
      profile_id: "profile-adaptive",
      profile_name: "Adaptive deep",
      profile_revision: 1,
      copied_at: "2026-08-01T00:00:00Z",
    },
    trigger_config: {
      ...LOCAL_PLUGIN.trigger_config,
      reviewer_selection: { mode: "fixed", reviewer_versions: ["security:v1"] },
      budget_profile: "standard",
      supersede_policy: "latest_snapshot",
    },
  };
  await page.route("**/api/review-profiles", async (route) => {
    await route.fulfill({ json: [{
      profile_id: "profile-adaptive",
      revision: 2,
      name: "Adaptive deep",
      is_default: true,
      reviewer_selection: { mode: "adaptive" },
      budget_profile: "deep",
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-02T00:00:00Z",
    }] });
  });
  await page.route("**/api/reviewer-catalog", async (route) => {
    await route.fulfill({ json: [{
      reference: "security:v1",
      agent_id: "security",
      version: 1,
      dimensions: ["security"],
      cost_class: "balanced",
      planner_eligible: true,
      capability_readiness: "ready",
      is_legacy: false,
    }] });
  });
  await page.route("**/api/plugins/local/trigger/config", async (route) => {
    configPayload = route.request().postDataJSON();
    await route.fulfill({ json: plugin });
  });
  await page.route("**/api/plugins/local/trigger/hook-status", async (route) => {
    await route.fulfill({ json: INSTALLED_HOOK_STATUS });
  });
  await page.route("**/api/plugins", async (route) => {
    await route.fulfill({ json: [plugin] });
  });

  await page.goto("/plugins");
  await expect(page.getByText("The source Profile has changed. The saved plugin snapshot is unchanged.")).toBeVisible();
  await expect(page.getByRole("radio", { name: /Fixed/ })).toBeChecked();
  expect(configPayload).toBeUndefined();

  await page.getByRole("button", { name: "Reload from profile" }).click();
  await expect(page.getByRole("radio", { name: /Adaptive/ })).toBeChecked();
  await page.getByRole("button", { name: "Save configuration" }).click();
  await expect.poll(() => configPayload).toMatchObject({
    config: {
      reviewer_selection: { mode: "adaptive" },
      budget_profile: "deep",
    },
    profile_source: {
      profile_id: "profile-adaptive",
      profile_name: "Adaptive deep",
      profile_revision: 2,
    },
  });
});
