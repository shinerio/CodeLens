import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("shows the review workbench navigation", () => {
    render(<App />, { wrapper: MemoryRouter });

    expect(screen.getByText("CodeLens")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New review" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Runs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review agents" })).toBeInTheDocument();
  });
});
