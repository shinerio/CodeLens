import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { FindingDetail } from "./FindingDetail";
import type { FindingRecord, FindingSourcePreview } from "./types";
import { TestProviders } from "../../test/TestProviders";

vi.mock("@monaco-editor/react", () => ({
  loader: { config: vi.fn() },
  DiffEditor: ({
    original,
    modified,
    options,
  }: {
    original: string;
    modified: string;
    options: { renderSideBySide?: boolean };
  }) => (
    <div
      data-testid="monaco-diff-editor"
      data-modified={modified}
      data-original={original}
      data-view={options.renderSideBySide === true ? "split" : "unified"}
    />
  ),
}));

vi.mock("monaco-editor", () => ({}));

const finding: FindingRecord = {
  finding_id: "finding-1",
  fingerprint: "a".repeat(64),
  reviewer_id: "correctness",
  category: "correctness",
  title: "Missing migration",
  severity: "high",
  disposition: "blocking",
  confidence: 0.9,
  primary_location: {
    path: "schema.sql",
    start_line: 3,
    end_line: 3,
    side: "new",
    excerpt_hash: "b".repeat(64),
    is_deleted: false,
  },
  related_locations: [],
  changed_hunk_id: "hunk-1",
  change_origin: "introduced",
  impact: "**Existing installations fail** when the new field is read.",
  explanation: "**Existing installations fail** when the new field is read.",
  reproduction: null,
  recommendation: "Add `ALTER TABLE` after the create statement.",
  evidence: [
    {
      kind: "excerpt",
      description: "**Existing installations fail** when the new field is read.",
      artifact_ref: null,
      excerpt_hash: "b".repeat(64),
    },
  ],
  rule_sources: [],
};

const source: FindingSourcePreview = {
  path: "schema.sql",
  base: {
    path: "schema.sql",
    revision: "b".repeat(40),
    content: "CREATE TABLE example (\n  id BIGINT,\n);\n",
  },
  target: {
    path: "schema.sql",
    revision: "c".repeat(40),
    content: "CREATE TABLE example (\n  id BIGINT,\n  lock_version BIGINT,\n);\n",
  },
  highlight_side: "new",
  highlight_start_line: 3,
  highlight_end_line: 3,
};

it("renders one equal-width side-by-side diff with side-aware comment placement", () => {
  render(<FindingDetail finding={finding} source={source} />, { wrapper: TestProviders });

  expect(screen.queryByRole("heading", { name: "Evidence" })).toBeNull();
  expect(screen.getByLabelText("Pinned source comparison")).toBeInTheDocument();
  const comparison = screen.getByTestId("monaco-diff-editor");
  expect(comparison).toHaveAttribute("data-original", source.base?.content);
  expect(comparison).toHaveAttribute("data-modified", source.target?.content);
  expect(comparison).toHaveAttribute("data-view", "split");
  expect(screen.queryByRole("button", { name: "Side by side" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Unified diff" })).not.toBeInTheDocument();
  expect(screen.queryByText("Add", { exact: false })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Pinned source comparison")).toHaveAttribute(
    "data-comment-side",
    "new",
  );
});
