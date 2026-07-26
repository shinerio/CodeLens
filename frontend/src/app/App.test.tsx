import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TestProviders } from "../test/TestProviders";
import { App } from "./App";

afterEach(() => {
  cleanup();
});

describe("App", () => {
  it("links to runs from the workspace navigation", () => {
    render(<App />, { wrapper: TestProviders });

    expect(screen.getByText("CodeLens")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("href", "/runs");
  });
});
