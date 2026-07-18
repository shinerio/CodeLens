import { expect, test } from "@playwright/test";
import path from "node:path";

function fixtureRepositoryPath() {
  const dataDir = process.env.CODELENS_E2E_DATA_DIR ?? path.resolve(process.cwd(), ".tmp", "codelens-e2e");
  return path.join(dataDir, "e2e-fixture", "simple-branch");
}

async function assertNoOverlap(page: import("@playwright/test").Page) {
  const list = await page.locator(".run-panel").first().boundingBox();
  const detail = await page.locator(".run-panel--detail").boundingBox();
  if (list === null || detail === null) {
    throw new Error("run panels are not visible");
  }
  const intersects = !(
    list.x + list.width <= detail.x ||
    detail.x + detail.width <= list.x ||
    list.y + list.height <= detail.y ||
    detail.y + detail.height <= list.y
  );
  expect(intersects).toBeFalsy();
  const nav = await page.locator(".review-run-page__tabs").boundingBox();
  if (nav !== null) {
    const header = await page.locator(".review-run-page__header").boundingBox();
    if (header !== null) {
      expect(nav.y).toBeGreaterThanOrEqual(header.y + header.height - 1);
    }
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
}

test("streams the correctness fixture from inspect to validated findings", async ({ page }) => {
  const repository = fixtureRepositoryPath();
  await page.goto("/");

  await page.getByLabel("Repository path").fill(repository);
  await page.getByRole("button", { name: "Inspect", exact: true }).click();
  await expect(page.getByText("Inspection ready")).toBeVisible();

  await page.getByRole("button", { name: /Uncommitted/ }).click();
  await page.getByRole("button", { name: "Start review", exact: true }).click();

  await expect(page.getByText("Live review run")).toBeVisible();
  await expect(page.getByText("completed", { exact: true })).toBeVisible({
    timeout: 15000,
  });
  await expect(
    page.getByRole("heading", { name: "Inverted transition guard allows invalid states", level: 3 }),
  ).toBeVisible({
    timeout: 15000,
  });

  await page
    .getByRole("button", { name: /Inverted transition guard allows invalid states/ })
    .click();
  await expect(page.getByText("Invalid states can now enter the reviewing state.")).toBeVisible();
  await expect(
    page.getByText("Restore the draft-only guard before allowing the reviewing transition."),
  ).toBeVisible();
  await assertNoOverlap(page);
});
