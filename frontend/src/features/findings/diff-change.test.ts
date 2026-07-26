import { describe, expect, it } from "vitest";

import { classifyDiffChange, selectDiffSide } from "./diff-change";

function change(originalEndLineNumber: number, modifiedEndLineNumber: number) {
  return {
    originalStartLineNumber: 2,
    originalEndLineNumber,
    modifiedStartLineNumber: 2,
    modifiedEndLineNumber,
    charChanges: undefined,
  };
}

describe("classifyDiffChange", () => {
  it("distinguishes additions, deletions, and replacements", () => {
    expect(classifyDiffChange(change(0, 2))).toBe("added");
    expect(classifyDiffChange(change(2, 0))).toBe("deleted");
    expect(classifyDiffChange(change(2, 2))).toBe("modified");
    expect(classifyDiffChange(change(0, 0))).toBeNull();
  });
});

describe("selectDiffSide", () => {
  it("places old comments on original and new comments on modified", () => {
    expect(selectDiffSide("old", "original", "modified")).toBe("original");
    expect(selectDiffSide("new", "original", "modified")).toBe("modified");
  });
});
