import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { ReviewPlanSummary } from "./ReviewPlanSummary";

it("does not claim a completed adaptive review is still waiting for Planner", () => {
  render(
    <ReviewPlanSummary
      plan={null}
      selection={{ mode: "adaptive" }}
      status="completed"
    />,
    { wrapper: TestProviders },
  );

  expect(screen.getByText("Plan was not generated")).toBeInTheDocument();
  expect(screen.queryByText("Pending Planner")).not.toBeInTheDocument();
});
