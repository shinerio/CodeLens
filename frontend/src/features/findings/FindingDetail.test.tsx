import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { FindingDetail } from "./FindingDetail";
import type { FindingRecord, FindingSourcePreview } from "./types";
import { TestProviders } from "../../test/TestProviders";

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
  revision: "c".repeat(40),
  start_line: 1,
  end_line: 4,
  highlight_start_line: 3,
  highlight_end_line: 3,
  content: "CREATE TABLE example (\n  id BIGINT,\n  lock_version BIGINT,\n);\n",
};

it("renders Markdown in the inline review card and omits duplicate detail sections", () => {
  render(<FindingDetail finding={finding} source={source} />, { wrapper: TestProviders });

  expect(screen.getByText("Existing installations fail", { exact: false }).tagName).toBe("STRONG");
  expect(screen.getByText("Add", { exact: false }).closest(".finding-annotation")).not.toBeNull();
  expect(screen.queryByRole("heading", { name: "Evidence" })).toBeNull();
  expect(screen.getByText("lock_version BIGINT,")).toBeInTheDocument();
});
