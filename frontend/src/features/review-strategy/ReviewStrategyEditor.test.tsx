import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, it } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import type { ReviewStrategySnapshot } from "../reviews/types";
import { ReviewStrategyEditor } from "./ReviewStrategyEditor";

const catalog = ["general", "security", "performance"].map((agentId) => ({
  reference: `${agentId}:v1`,
  agentId,
  version: 1,
  dimensions: [agentId],
  isPlannerEligible: true,
  isLegacy: false,
  capabilityStatus: "ready" as const,
}));

function Harness() {
  const [strategy, setStrategy] = useState<ReviewStrategySnapshot>({
    reviewerSelection: { mode: "fixed", reviewerVersions: [] },
  });
  return <ReviewStrategyEditor catalog={catalog} value={strategy} onChange={setStrategy} />;
}

it("keeps General mutually exclusive with specialist reviewers", async () => {
  const user = userEvent.setup();
  render(<Harness />, { wrapper: TestProviders });
  await user.click(screen.getByRole("checkbox", { name: /security/i }));
  await user.click(screen.getByRole("checkbox", { name: /general/i }));
  expect(screen.getByRole("checkbox", { name: /general/i })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: /security/i })).not.toBeChecked();
});

it("switches to Adaptive without retaining a hidden fixed selection", async () => {
  const user = userEvent.setup();
  render(<Harness />, { wrapper: TestProviders });
  await user.click(screen.getByRole("checkbox", { name: /security/i }));
  await user.click(screen.getByRole("radio", { name: /Adaptive/i }));
  expect(screen.queryByRole("checkbox", { name: /security/i })).not.toBeInTheDocument();
  expect(screen.getByText(/after the review task/i)).toBeVisible();
});

it("keeps a selected legacy snapshot valid while preventing new selection", () => {
  const legacyCatalog = [{
    ...catalog[0],
    reference: "correctness:v1",
    agentId: "correctness",
    isLegacy: true,
  }];
  render(
    <ReviewStrategyEditor
      catalog={legacyCatalog}
      value={{
        reviewerSelection: { mode: "fixed", reviewerVersions: ["correctness:v1"] },
      }}
      validationErrors={[]}
      onChange={() => undefined}
    />,
    { wrapper: TestProviders },
  );
  expect(screen.getByRole("checkbox", { name: /correctness/i })).toBeDisabled();
  expect(screen.getByText("retained snapshot")).toBeVisible();
});
