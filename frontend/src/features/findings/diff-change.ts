import type { editor as MonacoEditor } from "monaco-editor";

export type DiffChangeKind = "added" | "deleted" | "modified";

export function selectDiffSide<Value>(
  side: "old" | "new",
  originalValue: Value,
  modifiedValue: Value,
): Value {
  return side === "old" ? originalValue : modifiedValue;
}

export function classifyDiffChange(change: MonacoEditor.ILineChange): DiffChangeKind | null {
  const hasOriginalLines = change.originalEndLineNumber > 0;
  const hasModifiedLines = change.modifiedEndLineNumber > 0;
  if (hasOriginalLines && hasModifiedLines) {
    return "modified";
  }
  if (hasOriginalLines) {
    return "deleted";
  }
  if (hasModifiedLines) {
    return "added";
  }
  return null;
}
