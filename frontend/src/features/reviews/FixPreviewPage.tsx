import { BadgeCheck, Download, FileDiff, RotateCcw, Stamp, Trash2 } from "lucide-react";
import { useState } from "react";

import { useI18n } from "../../shared/i18n/i18n";
import "./RunListPage.css";

type FixTab = "patch" | "validation" | "approval";

/** Provides the demo Fix workspace without claiming patch operations are implemented. */
export function FixPreviewPage() {
  const { t } = useI18n();
  const [tab, setTab] = useState<FixTab>("patch");
  function handleUnsupported() { window.alert(t("common.notSupported")); }
  return <section className="fix-preview-page"><header><div><p>Fix / isolated workspace preview</p><h1>billing-worker</h1><span>a18c70d → isolated worktree · patch_8d32a1f0</span></div><div><button type="button" onClick={handleUnsupported}><Trash2 aria-hidden="true" /> Discard</button><button type="button" onClick={handleUnsupported}><Download aria-hidden="true" /> Download patch</button></div></header><nav>{(["patch", "validation", "approval"] as FixTab[]).map((item) => <button className={tab === item ? "active" : ""} key={item} type="button" onClick={() => setTab(item)}>{item === "patch" ? <FileDiff aria-hidden="true" /> : item === "validation" ? <BadgeCheck aria-hidden="true" /> : <Stamp aria-hidden="true" />}{item === "validation" ? "Validation gates" : item[0].toUpperCase() + item.slice(1)}</button>)}</nav>{tab === "patch" ? <article><h2>Make settlement retries idempotent</h2><code><span>− result = await gateway.capture(command)</span><span>+ reservation = await keys.reserve(command.key)</span><span>+ if reservation.completed: return reservation.receipt</span><span>+ result = await gateway.capture(command)</span><span>+ await reservation.complete(result)</span></code><p>The reservation is established before the external side effect. This preview is read-only.</p></article> : null}{tab === "validation" ? <article><h2>Validation gates</h2><ul><li>Snapshot fingerprint <b>Passed</b></li><li>Conflict check <b>Passed</b></li><li>Focused tests <b>Passed</b></li><li>Policy verification <b>Passed</b></li></ul><button type="button" onClick={handleUnsupported}><RotateCcw aria-hidden="true" /> Re-run gates</button></article> : null}{tab === "approval" ? <article><h2>Approval decision</h2><p>Applying a patch writes only after each gate is rechecked. This operation is not connected yet.</p><label>Approval note<textarea placeholder="Optional audit note" /></label><button type="button" onClick={handleUnsupported}><Stamp aria-hidden="true" /> Approve and apply</button></article> : null}</section>;
}
