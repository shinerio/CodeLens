import { expect, test } from "@playwright/test";

test("creates, edits, duplicates, and deletes a review profile", async ({ page }) => {
  const runSuffix = Date.now().toString(36);
  const profileName = `Adaptive deep E2E ${runSuffix}`;
  const renamedProfile = `${profileName} revised`;

  await page.goto("/agents");
  await page.getByRole("link", { name: "Review profiles" }).click();
  await expect(page).toHaveURL(/\/settings\/review-profiles$/);
  await expect(page.getByRole("heading", { name: "Review Profiles" })).toBeVisible();

  await page.getByRole("button", { name: "New profile" }).click();
  await page.getByLabel("Profile name").fill(profileName);
  await page.getByRole("radio", { name: /Deep/ }).click();
  await page.getByRole("button", { name: "Save profile" }).click();

  const createdCard = page.locator(".profile-card").filter({ hasText: profileName });
  await expect(createdCard).toBeVisible();
  await expect(createdCard).toContainText(/adaptive/i);
  await expect(createdCard).toContainText(/deep/i);

  await createdCard.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Profile name").fill(renamedProfile);
  await page.getByRole("button", { name: "Save profile" }).click();

  const renamedCard = page.locator(".profile-card").filter({ hasText: renamedProfile });
  await expect(renamedCard).toBeVisible();
  await renamedCard.getByRole("button", { name: `Duplicate ${renamedProfile}` }).click();
  const copiedName = `${renamedProfile} copy`;
  await page.getByLabel("New profile name").fill(copiedName);
  await page.getByRole("button", { name: "Create copy" }).click();
  const copiedCard = page.locator(".profile-card").filter({ hasText: copiedName });
  await expect(copiedCard).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await copiedCard.getByRole("button", { name: `Delete ${copiedName}` }).click();
  await expect(copiedCard).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
