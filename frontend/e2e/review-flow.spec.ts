import { expect, test } from "@playwright/test";
import path from "node:path";

function fixtureRepositoryPath() {
  const dataDir = process.env.CODELENS_E2E_DATA_DIR ?? path.resolve(process.cwd(), ".tmp", "codelens-e2e");
  return path.join(dataDir, "e2e-fixture", "simple-branch");
}

async function assertFindingWorkspaceLayout(page: import("@playwright/test").Page) {
  const navigation = await page.locator(".finding-workspace__navigation").boundingBox();
  const detail = await page.locator(".finding-workspace__detail").boundingBox();
  const baseHeader = await page.locator(".finding-review__pane-header--base").boundingBox();
  const targetHeader = await page.locator(".finding-review__pane-header--target").boundingBox();
  if (navigation === null || detail === null || baseHeader === null || targetHeader === null) {
    throw new Error("finding workspace is not visible");
  }

  expect(detail.y).toBeGreaterThanOrEqual(navigation.y + navigation.height - 1);
  expect(Math.abs(detail.width - navigation.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(baseHeader.width - targetHeader.width)).toBeLessThanOrEqual(1);
  expect(targetHeader.x).toBeGreaterThanOrEqual(baseHeader.x + baseHeader.width - 1);
  expect(Math.abs(targetHeader.y - baseHeader.y)).toBeLessThanOrEqual(1);
  await expect(page.locator(".finding-review__editor .monaco-diff-editor")).toBeVisible({
    timeout: 15000,
  });
  const originalEditor = await page.locator(".monaco-diff-editor .editor.original").boundingBox();
  const modifiedEditor = await page.locator(".monaco-diff-editor .editor.modified").boundingBox();
  if (originalEditor === null || modifiedEditor === null) {
    throw new Error("side-by-side source editors are not visible");
  }
  expect(Math.abs(originalEditor.width - modifiedEditor.width)).toBeLessThanOrEqual(2);
  const comment = page.locator(".finding-comment-zone--new");
  await expect(comment).toBeVisible();
  expect(
    await comment.evaluate((element) => element.scrollHeight <= element.clientHeight + 1),
  ).toBeTruthy();
  const collapsedSidebar = await page.locator(".sidebar").boundingBox();
  expect(collapsedSidebar?.width).toBeLessThanOrEqual(55);
  await page.locator(".sidebar").hover();
  await expect(page.locator(".sidebar")).toHaveCSS("width", "216px");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
}

async function chooseRepository(
  page: import("@playwright/test").Page,
  repository: string,
) {
  await page.getByRole("button", { name: "Browse folders" }).click();
  const dialog = page.getByRole("dialog", { name: "Repository browser" });
  const root = path.parse(repository).root;
  await dialog.getByRole("button", { name: root, exact: true }).click();

  const parts = repository.slice(root.length).split(path.sep).filter(Boolean);
  for (const [index, part] of parts.entries()) {
    const row = dialog.locator(".directory-row").filter({ hasText: part }).first();
    await expect(row).toBeVisible();
    if (index === parts.length - 1) {
      await row.getByRole("button", { name: `Select repository ${part}` }).click();
    } else {
      await row.locator(":scope > button").first().click();
    }
  }
}

test("streams the correctness fixture from inspect to validated findings", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  const repository = fixtureRepositoryPath();
  await page.goto("/settings");

  await page.getByRole("button", { name: "Add gateway", exact: true }).click();
  const gatewayModal = page.locator(".gateway-modal");
  await expect(gatewayModal.getByRole("heading", { name: "Add gateway" })).toBeVisible();
  await gatewayModal.getByLabel("Gateway name").fill("E2E fixture");
  await gatewayModal.getByLabel("API Key").fill("sk-e2e-fixture-secret");
  await gatewayModal.getByLabel("Base URL").fill("http://127.0.0.1:9999");
  await gatewayModal
    .getByRole("textbox", { name: "Model", exact: true })
    .fill("fixture-model");
  await gatewayModal.getByRole("button", { name: "Add gateway", exact: true }).click();
  await expect(page.getByText("Active gateway", { exact: true }).first()).toBeVisible();

  await page.goto("/reviews/new");

  await chooseRepository(page, repository);
  await expect(page.getByText("Inspection ready")).toBeVisible();

  await page.getByLabel("Base branch").selectOption("main");
  await page.getByLabel("Target branch").selectOption("fixture-change");
  await page.getByRole("button", { name: "Start review", exact: true }).click();

  await expect(page.getByText("Live review run")).toBeVisible();
  await expect(page.locator(".review-run-page__subtitle")).toContainText("completed", {
    timeout: 15000,
  });
  await page.getByRole("tab", { name: /Execution/ }).click();
  await expect(page.getByRole("heading", { name: "Process report" })).toBeVisible({
    timeout: 15000,
  });
  await page.getByRole("tab", { name: /Logs/ }).click();
  const console = page.getByRole("region", { name: "Review execution console" });
  const embeddedProcessReport = console.getByRole("article", { name: "Process report" });
  await expect(embeddedProcessReport).toBeVisible();
  await expect(embeddedProcessReport).toHaveCSS("color", "rgb(241, 247, 246)");
  await expect(embeddedProcessReport).toHaveCSS("background-color", "rgb(21, 33, 38)");
  await console.getByRole("button", { name: /Reviewers/ }).click();
  await expect(console.getByText("correctness:v2", { exact: true }).first()).toBeVisible();
  await expect(console.getByRole("button", { name: /Verifier/ })).toHaveCount(0);
  await expect(console.getByText("review-verifier:v2", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();

  await page.getByRole("tab", { name: /Findings/ }).click();
  await page
    .getByRole("button", { name: /Inverted transition guard allows invalid states/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "Inverted transition guard allows invalid states", level: 3 }),
  ).toBeVisible({
    timeout: 15000,
  });
  await expect(
    page.getByText("The guard now allows every non-draft state to reach reviewing."),
  ).toBeVisible();
  await expect(
    page.getByText("Restore the draft-only guard before allowing the reviewing transition."),
  ).toBeVisible();
  await assertFindingWorkspaceLayout(page);

  await page.goto("/reviews/new");
  const recentRepository = page.locator(".recent-repository").filter({ hasText: "simple-branch" });
  await expect(recentRepository).toBeVisible();
  const timestamp = recentRepository.locator("time");
  const apiTimestamp = await timestamp.getAttribute("datetime");
  expect(apiTimestamp).toMatch(/(?:Z|\+00:00)$/);
  expect(await timestamp.textContent()).toBe(
    await timestamp.evaluate((element) => {
      const value = element.getAttribute("datetime");
      if (value === null) {
        throw new Error("recent repository timestamp is missing");
      }
      return new Intl.DateTimeFormat("en", {
        dateStyle: "medium",
        timeStyle: "medium",
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }).format(new Date(value));
    }),
  );
  const deleteButton = recentRepository.getByRole("button", {
    name: "Remove recent repository simple-branch",
  });
  await expect(deleteButton).toBeVisible();
  await deleteButton.click();
  const deleteDialog = page.getByRole("dialog", { name: "Remove recent repository?" });
  await expect(deleteDialog).toBeVisible();
  await page.screenshot({
    fullPage: true,
    path: testInfo.outputPath("recent-repository-delete-dialog.png"),
  });
  await deleteDialog.getByRole("button", { name: "Remove repository" }).click();
  await expect(page.getByText("Recent Review repositories will appear here.")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});
