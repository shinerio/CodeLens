import { expect, test } from "@playwright/test";

test("lists backend reviewers and edits a non-correctness prompt", async ({ page }) => {
  await page.goto("/agents");

  const reviewerCards = page.getByTestId("reviewer-card");
  await expect(reviewerCards).toHaveCount(10);
  await expect(reviewerCards.filter({ hasText: "correctness:v2" })).toHaveCount(1);
  await expect(reviewerCards.filter({ hasText: "security:v2" })).toHaveCount(1);
  await expect(reviewerCards.filter({ hasText: "general:v2" })).toHaveCount(1);
  // Internal DAG roles are listed so their prompts are editable too.
  await expect(reviewerCards.filter({ hasText: "review-planner:v2" })).toHaveCount(1);
  await expect(reviewerCards.filter({ hasText: "review-verifier:v2" })).toHaveCount(1);
  const securityCard = reviewerCards.filter({ hasText: "security:v2" });
  await securityCard.getByRole("button", { name: "Edit prompt" }).click();
  const promptEditor = page.getByLabel("Agent prompt");
  await expect(promptEditor).not.toHaveValue("");

  const customPrompt = `Security E2E override ${Date.now()}`;
  await promptEditor.fill(customPrompt);
  await page.getByRole("button", { name: "Save prompt" }).click();
  await expect(page.getByText("Custom", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Reset to default" }).click();
  await expect(page.getByText("System default", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
