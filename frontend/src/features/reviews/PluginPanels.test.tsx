import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import type { ExportResultResponse } from "./api";
import { PluginPanels } from "./PluginPanels";

function exportEntry(success: boolean): ExportResultResponse {
  return {
    plugin_id: "local",
    task_id: "review_1",
    success,
    output_path: null,
    error: success ? null : "boom",
    exported_at: "2026-08-18T08:00:00Z",
  };
}

it("renders report-complete status for a successful zero-findings export", () => {
  render(
    <PluginPanels
      externalContext={null}
      plugins={[]}
      exportHistory={[exportEntry(true)]}
      findingsCount={0}
    />,
    { wrapper: TestProviders },
  );

  expect(screen.getByText("Report complete · no findings")).toBeInTheDocument();
});

it("renders exported-successfully status when findings exist", () => {
  render(
    <PluginPanels
      externalContext={null}
      plugins={[]}
      exportHistory={[exportEntry(true)]}
      findingsCount={2}
    />,
    { wrapper: TestProviders },
  );

  expect(screen.getByText("Exported successfully")).toBeInTheDocument();
});

it("renders export-failed status when the export failed", () => {
  render(
    <PluginPanels
      externalContext={null}
      plugins={[]}
      exportHistory={[exportEntry(false)]}
      findingsCount={0}
    />,
    { wrapper: TestProviders },
  );

  expect(screen.getByText("Export failed")).toBeInTheDocument();
});
