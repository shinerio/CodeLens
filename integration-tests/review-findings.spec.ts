import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

function fixtureRepositoryPath() {
  const dataDir = process.env.CODELENS_INTEGRATION_DATA_DIR
    ?? path.resolve(process.cwd(), ".tmp", "codelens-integration");
  return path.join(dataDir, "e2e-fixture", "simple-branch");
}

async function chooseRepository(page: Page, repository: string) {
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

const FINDING_CASES = [
  {
    title: "Shared cache key leaks data between users",
    severity: "critical",
    category: "data-isolation",
    confidence: "99%",
    explanation: "Every user is assigned the same cache key.",
    recommendation: "Include the user identifier in the cache key.",
    pathState: "file added",
    side: "new",
  },
  {
    title: "Deleting the authorization guard permits every role",
    severity: "high",
    category: "authorization",
    confidence: "98%",
    explanation: "Removing this file removes the only admin-role check.",
    recommendation: "Keep an authorization guard in the target revision.",
    pathState: "file deleted",
    side: "old",
  },
  {
    title: "Inverted transition guard allows invalid states",
    severity: "medium",
    category: "state-machine",
    confidence: "97%",
    explanation: "The guard now allows every non-draft state to reach reviewing.",
    recommendation: "Restore the draft-only guard before allowing the reviewing transition.",
    pathState: null,
    side: "new",
  },
] as const;

async function assertThreeFindings(page: Page) {
  const findingButtons = page.locator(".finding-list__item");
  await expect(findingButtons).toHaveCount(3);
  const backgrounds = await findingButtons.evaluateAll((elements) =>
    elements.map((element) => getComputedStyle(element).backgroundColor),
  );
  expect(new Set(backgrounds).size).toBe(3);

  const commentBackgrounds: string[] = [];
  for (const expected of FINDING_CASES) {
    await page.getByRole("button", { name: new RegExp(expected.title) }).click();
    const detail = page.locator(".finding-detail");
    await expect(detail.getByRole("heading", { name: expected.title, level: 3 })).toBeVisible();
    await expect(detail).toContainText(expected.severity);
    await expect(detail).toContainText(expected.category);
    await expect(detail).toContainText(expected.confidence);
    await expect(detail).toContainText(expected.explanation);
    await expect(detail).toContainText(expected.recommendation);
    const commentZone = page.locator(`.finding-comment-zone--${expected.side}`);
    await expect(commentZone).toBeVisible({ timeout: 15_000 });
    commentBackgrounds.push(await commentZone.locator(".finding-detail__opinion").evaluate(
      (element) => getComputedStyle(element).backgroundColor,
    ));
    const opinionBox = await commentZone.locator(".finding-detail__opinion").boundingBox();
    expect(opinionBox?.width).toBeGreaterThanOrEqual(280);
    const commentZoneBox = await commentZone.boundingBox();
    expect(commentZoneBox?.height).toBeGreaterThanOrEqual(
      opinionBox?.height ?? Number.POSITIVE_INFINITY,
    );
    if (expected.pathState !== null) {
      await expect(detail).toContainText(expected.pathState);
    }
  }
  expect(new Set(commentBackgrounds).size).toBe(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBeTruthy();
}

test("creates one review with three findings on desktop", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add gateway", exact: true }).click();
  const gatewayModal = page.locator(".gateway-modal");
  await gatewayModal.getByLabel("Gateway name").fill("Integration fixture");
  await gatewayModal.getByLabel("API Key").fill("sk-integration-fixture-secret");
  await gatewayModal.getByLabel("Base URL").fill("http://127.0.0.1:9999");
  await gatewayModal
    .getByRole("textbox", { name: "Model", exact: true })
    .fill("fixture-model");
  await gatewayModal.getByRole("button", { name: "Add gateway", exact: true }).click();

  await page.goto("/reviews/new");
  await chooseRepository(page, fixtureRepositoryPath());
  await expect(page.getByText("Inspection ready")).toBeVisible();
  await page.getByLabel("Base branch").selectOption("main");
  await page.getByLabel("Target branch").selectOption("fixture-change");
  await page.getByRole("button", { name: "Start review", exact: true }).click();

  await expect(page.locator(".review-run-page__subtitle")).toContainText("completed", {
    timeout: 20_000,
  });
  const report = page.locator(".process-report");
  await expect(report).toBeVisible();
  await expect(report.locator(".process-report__metrics > div").filter({ hasText: "LLM calls" }))
    .toContainText("2");
  await expect(report.locator(".process-report__metrics > div").filter({ hasText: "Findings" }))
    .toContainText("3");
  await expect(report).toContainText("comment");
  await expect(report).toContainText("task_done");
  await expect(page.getByRole("status", { name: "Some model findings were skipped" }))
    .toContainText("1 duplicate");

  await page.getByRole("button", { name: /Findings/ }).click();
  await assertThreeFindings(page);

  const reviewsResponse = await page.request.get("/api/reviews");
  expect(reviewsResponse.ok()).toBeTruthy();
  const reviews: unknown = await reviewsResponse.json();
  if (!Array.isArray(reviews)) {
    throw new Error("Review list response must be an array");
  }
  expect(reviews).toHaveLength(1);
  expect(pageErrors).toEqual([]);
});
