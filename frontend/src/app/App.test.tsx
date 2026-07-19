import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TestProviders } from "../test/TestProviders";
import { App } from "./App";

afterEach(() => {
  cleanup();
});

describe("App", () => {
  it("uses runs as the only workspace list entry", () => {
    render(<App />, { wrapper: TestProviders });

    expect(screen.getByText("CodeLens")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("href", "/runs");
    expect(screen.queryByRole("button", { name: "New review" })).not.toBeInTheDocument();
  });
});
