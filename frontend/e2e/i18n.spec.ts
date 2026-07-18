import { expect, test } from "@playwright/test";

test.use({ locale: "zh-CN" });

test("detects the browser language and renders the Chinese interface", async ({ page }) => {
  await page.goto("/reviews/new");

  await expect(page.getByRole("heading", { name: "新建 Review", level: 1 })).toBeVisible();
  await expect(page.getByText("Review 列表", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "设置" })).toBeVisible();
  await expect(page.getByRole("button", { name: "浏览文件夹" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
});
