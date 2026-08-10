import { expect, test, type Page, type Route } from "@playwright/test";

const FAILED_TASK_ID = `review_${"1".repeat(32)}`;
const COMPLETED_TASK_ID = `review_${"2".repeat(32)}`;
const RETRY_TASK_ID = `review_${"3".repeat(32)}`;

function reviewResponse(taskId: string, repositoryName: string, status: string) {
  return {
    task_id: taskId,
    status,
    scope_type: "branch",
    base_oid: "a".repeat(40),
    head_oid: "b".repeat(40),
    selected_agents: ["correctness:v2"],
    worktree_status: "pending",
    repository_id: `repository_${"c".repeat(64)}`,
    repository_realpath_hash: "d".repeat(64),
    git_common_dir_hash: "e".repeat(64),
    cancellation_requested: false,
    repository_name: repositoryName,
    created_at: "2026-07-26T12:00:00Z",
    finding_count: 0,
    external_context: null,
    selection_request: { mode: "fixed", reviewer_versions: ["correctness:v2"] },
    profile_source: null,
    review_plan: null,
    coverage: { planned: [], completed: [], failed: [], omitted: [] },
    verdict_summary: { accept: 0, deny: 0, merge: 0 },
  };
}

async function mockReviewRequests(page: Page) {
  const failed = reviewResponse(
    FAILED_TASK_ID,
    "codelens-platform-with-a-very-long-repository-name",
    "failed",
  );
  const completed = reviewResponse(COMPLETED_TASK_ID, "stable-review", "completed");
  const retried = reviewResponse(RETRY_TASK_ID, failed.repository_name, "created");

  await page.route("**/api/reviews**", async (route: Route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === `/api/reviews/${FAILED_TASK_ID}/retry`) {
      await route.fulfill({ json: retried, status: 202 });
      return;
    }
    if (pathname === "/api/reviews") {
      await route.fulfill({ json: [failed, completed] });
      return;
    }
    if (pathname.endsWith("/findings") || pathname.endsWith("/transcript")) {
      await route.fulfill({ json: [] });
      return;
    }
    if (pathname.endsWith("/process-report")) {
      await route.fulfill({
        json: { code: "process_report_not_ready", message: "not ready" },
        status: 409,
      });
      return;
    }
    if (pathname.endsWith("/events")) {
      await route.fulfill({ body: "", contentType: "text/event-stream" });
      return;
    }
    await route.fulfill({ json: pathname.includes(RETRY_TASK_ID) ? retried : completed });
  });
}

test("review rows open details and failed reviews retry as independent tasks", async ({ page }, testInfo) => {
  await mockReviewRequests(page);
  await page.goto("/runs");

  const failedDetails = page.getByRole("link", {
    name: "Open codelens-platform-with-a-very-long-repository-name",
  });
  const retryButton = page.getByRole("button", {
    name: "Retry codelens-platform-with-a-very-long-repository-name",
  });
  await expect(failedDetails).toBeVisible();
  await expect(retryButton).toBeVisible();
  await expect(page.getByRole("link", { name: "Open stable-review" })).toHaveAttribute(
    "href",
    `/runs/${COMPLETED_TASK_ID}`,
  );

  const detailBounds = await failedDetails.boundingBox();
  const actionBounds = await retryButton.locator("xpath=..").boundingBox();
  expect(detailBounds).not.toBeNull();
  expect(actionBounds).not.toBeNull();
  expect((detailBounds?.x ?? 0) + (detailBounds?.width ?? 0)).toBeLessThanOrEqual(
    (actionBounds?.x ?? 0) + 1,
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ fullPage: true, path: testInfo.outputPath("run-list.png") });

  const retryRequest = page.waitForRequest(
    (request) => new URL(request.url()).pathname === `/api/reviews/${FAILED_TASK_ID}/retry`,
  );
  await retryButton.click();
  expect((await retryRequest).postDataJSON()).toEqual({});
  await expect(page).toHaveURL(`/runs/${RETRY_TASK_ID}`);
});
