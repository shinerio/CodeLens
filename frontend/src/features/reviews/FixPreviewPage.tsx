import { BadgeCheck, Download, FileDiff, RotateCcw, Stamp, Trash2 } from "lucide-react";
import { useState } from "react";

import { useI18n } from "../../shared/i18n/i18n";
import "./RunListPage.css";

type FixTab = "patch" | "validation" | "approval";

const TAB_LABEL_KEYS = {
  patch: "fix.patch",
  validation: "fix.validation",
  approval: "fix.approval",
} as const;

/** Provides the demo Fix workspace without claiming patch operations are implemented. */
export function FixPreviewPage() {
  const { t } = useI18n();
  const [tab, setTab] = useState<FixTab>("patch");

  function handleUnsupported() {
    window.alert(t("common.notSupported"));
  }

  return (
    <section className="fix-preview-page">
      <header>
        <div>
          <p>{t("fix.eyebrow")}</p>
          <h1>billing-worker</h1>
          <span>{t("fix.metadata")}</span>
        </div>
        <div>
          <button type="button" onClick={handleUnsupported}><Trash2 aria-hidden="true" /> {t("fix.discard")}</button>
          <button type="button" onClick={handleUnsupported}><Download aria-hidden="true" /> {t("fix.download")}</button>
        </div>
      </header>
      <nav>
        {(["patch", "validation", "approval"] as FixTab[]).map((item) => (
          <button className={tab === item ? "active" : ""} key={item} type="button" onClick={() => setTab(item)}>
            {item === "patch" ? <FileDiff aria-hidden="true" /> : item === "validation" ? <BadgeCheck aria-hidden="true" /> : <Stamp aria-hidden="true" />}
            {t(TAB_LABEL_KEYS[item])}
          </button>
        ))}
      </nav>
      {tab === "patch" ? (
        <article>
          <h2>{t("fix.patchTitle")}</h2>
          <code><span>− result = await gateway.capture(command)</span><span>+ reservation = await keys.reserve(command.key)</span><span>+ if reservation.completed: return reservation.receipt</span><span>+ result = await gateway.capture(command)</span><span>+ await reservation.complete(result)</span></code>
          <p>{t("fix.patchDescription")}</p>
        </article>
      ) : null}
      {tab === "validation" ? (
        <article>
          <h2>{t("fix.validation")}</h2>
          <ul>
            {(["fix.snapshotFingerprint", "fix.conflictCheck", "fix.focusedTests", "fix.policyVerification"] as const).map((key) => <li key={key}>{t(key)} <b>{t("fix.passed")}</b></li>)}
          </ul>
          <button type="button" onClick={handleUnsupported}><RotateCcw aria-hidden="true" /> {t("fix.rerun")}</button>
        </article>
      ) : null}
      {tab === "approval" ? (
        <article>
          <h2>{t("fix.approvalDecision")}</h2>
          <p>{t("fix.approvalDescription")}</p>
          <label>{t("fix.approvalNote")}<textarea placeholder={t("fix.approvalPlaceholder")} /></label>
          <button type="button" onClick={handleUnsupported}><Stamp aria-hidden="true" /> {t("fix.approveApply")}</button>
        </article>
      ) : null}
    </section>
  );
}
