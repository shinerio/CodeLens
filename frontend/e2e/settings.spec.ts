import { expect, test } from "@playwright/test";

test("persists the recent repository limit without layout overflow", async ({ page }, testInfo) => {
  await page.goto("/settings");

  const limitInput = page.getByRole("spinbutton", {
    name: "Recent repository limit",
    exact: true,
  });
  const saveButton = page.getByRole("button", {
    name: "Save recent repository limit",
  });
  await expect(limitInput).toBeEnabled();
  if ((await limitInput.inputValue()) !== "12") {
    await limitInput.fill("12");
    await expect(saveButton).toBeEnabled();
    await saveButton.click();
  }

  await expect
    .poll(async () => {
      const response = await page.request.get("/api/settings/repositories");
      return response.json();
    })
    .toEqual({ recent_repository_limit: 12 });
  await expect(limitInput).toHaveValue("12");

  const rootLimitInput = page.getByRole("spinbutton", {
    name: "Root instruction file limit",
    exact: true,
  });
  const nestedLimitInput = page.getByRole("spinbutton", {
    name: "Nested instruction file limit",
    exact: true,
  });
  const saveInstructionLimits = page.getByRole("button", {
    name: "Save instruction file limits",
  });
  await expect(rootLimitInput).toBeEnabled();
  await expect(nestedLimitInput).toBeEnabled();
  if (
    (await rootLimitInput.inputValue()) !== "640" ||
    (await nestedLimitInput.inputValue()) !== "240"
  ) {
    await rootLimitInput.fill("640");
    await nestedLimitInput.fill("240");
    await expect(saveInstructionLimits).toBeEnabled();
    await saveInstructionLimits.click();
  }

  await expect
    .poll(async () => {
      const response = await page.request.get("/api/settings/instruction-files");
      return response.json();
    })
    .toEqual({ root_max_lines: 640, nested_max_lines: 240 });
  await expect(page.getByText("Credential handling")).toHaveCount(0);

  const executionLimits = [
    { name: "Agent Timeout (s)", min: "60", max: "7200" },
    { name: "Maximum agent turns", min: "1", max: "500" },
    { name: "Maximum tool calls", min: "1", max: "5000" },
    { name: "Identical result limit", min: "2", max: "20" },
    { name: "Tool timeout (s)", min: "1", max: "300" },
  ];
  for (const limit of executionLimits) {
    const input = page.getByRole("spinbutton", { name: limit.name, exact: true });
    await expect(input).toBeVisible();
    await expect(input).toHaveAttribute("min", limit.min);
    await expect(input).toHaveAttribute("max", limit.max);
  }

  const reviewSettingsField = limitInput.locator("xpath=..");
  await expect(reviewSettingsField).toContainText("Recent repository limit");
  await expect(reviewSettingsField.getByRole("button", {
    name: "Save recent repository limit",
  })).toHaveCount(1);

  const boxes = await Promise.all([
    reviewSettingsField.boundingBox(),
    limitInput.boundingBox(),
    saveButton.boundingBox(),
  ]);
  expect(boxes.every((box) => box !== null)).toBe(true);
  const [fieldBox, inputBox, buttonBox] = boxes;
  if (fieldBox !== null && inputBox !== null && buttonBox !== null) {
    expect(inputBox.x).toBeGreaterThanOrEqual(fieldBox.x);
    expect(inputBox.x + inputBox.width).toBeLessThanOrEqual(fieldBox.x + fieldBox.width + 1);
    expect(buttonBox.y).toBeGreaterThanOrEqual(inputBox.y + inputBox.height);
    expect(buttonBox.x).toBeGreaterThanOrEqual(fieldBox.x);
    expect(buttonBox.x + buttonBox.width).toBeLessThanOrEqual(fieldBox.x + fieldBox.width + 1);
  }
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();

  await page.screenshot({
    fullPage: true,
    path: testInfo.outputPath("settings.png"),
  });
});
