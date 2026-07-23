import {
  Blocks,
  Bug,
  Database,
  Gauge,
  History,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  TestTube2,
  Wrench,
} from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";
import { getReviewerPrompt, resetReviewerPrompt, updateReviewerPrompt } from "./api";
import "./CatalogPreviewPage.css";

type PreviewKind = "agents" | "capabilities";

const reviewers: ReadonlyArray<readonly [TranslationKey, TranslationKey, string, typeof Bug]> = [
  ["catalog.correctnessReviewer", "catalog.correctnessDescription", "120k tokens", Bug],
  ["catalog.securityReviewer", "catalog.securityDescription", "140k tokens", ShieldAlert],
  ["catalog.performanceReviewer", "catalog.performanceDescription", "96k tokens", Gauge],
  ["catalog.dataApiReviewer", "catalog.dataApiDescription", "110k tokens", Database],
  ["catalog.testQualityReviewer", "catalog.testQualityDescription", "84k tokens", TestTube2],
  ["catalog.architectureReviewer", "catalog.architectureDescription", "150k tokens", Blocks],
  ["catalog.maintainabilityReviewer", "catalog.maintainabilityDescription", "72k tokens", Wrench],
  ["catalog.releaseRiskReviewer", "catalog.releaseRiskDescription", "Draft", History],
];

type CapabilityGroupKey = "catalog.skills" | "catalog.mcpServers" | "catalog.staticTools" | "catalog.contextProviders";

const capabilityGroups: Readonly<Record<CapabilityGroupKey, readonly TranslationKey[]>> = {
  "catalog.skills": ["catalog.changedCodeReasoning", "catalog.securityThreatAnalysis", "catalog.migrationSafety"],
  "catalog.mcpServers": ["catalog.codeGraphTools", "catalog.openApiTools", "catalog.issueTracker"],
  "catalog.staticTools": ["catalog.ruff", "catalog.mypy", "catalog.pytest"],
  "catalog.contextProviders": ["catalog.codeGraphProvider", "catalog.textFallback"],
};

/** Renders a read-only catalog preview until catalog APIs and mutations are available. */
export function CatalogPreviewPage({ kind }: { kind: PreviewKind }) {
  const { t, locale } = useI18n();
  const [activeGroup, setActiveGroup] = useState<keyof typeof capabilityGroups>("catalog.skills");
  const [searchQuery, setSearchQuery] = useState("");
  const isAgents = kind === "agents";
  const promptQuery = useQuery({ queryKey: ["reviewer-prompt", locale], queryFn: () => getReviewerPrompt(locale), enabled: isAgents });
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const savePrompt = useMutation({ mutationFn: (prompt: string) => updateReviewerPrompt(locale, prompt), onSuccess: () => { setDraft(null); void queryClient.invalidateQueries({ queryKey: ["reviewer-prompt", locale] }); } });
  const resetPrompt = useMutation({ mutationFn: () => resetReviewerPrompt(locale), onSuccess: () => { setDraft(null); void queryClient.invalidateQueries({ queryKey: ["reviewer-prompt", locale] }); } });

  function handleUnsupported() {
    window.alert(t("common.notSupported"));
  }

  return (
    <section className="catalog-preview-page">
      <header className="catalog-preview-page__header">
        <div>
          <p>{t(isAgents ? "catalog.agentsEyebrow" : "catalog.capabilitiesEyebrow")}</p>
          <h1>{t(isAgents ? "catalog.agentsTitle" : "catalog.capabilitiesTitle")}</h1>
          <span>
            {isAgents
              ? t("catalog.agentsSubtitle")
              : t("catalog.capabilitiesSubtitle")}
          </span>
        </div>
        <div className="catalog-preview-page__actions">
          <button type="button" onClick={handleUnsupported}><RefreshCw aria-hidden="true" /> {t("catalog.refresh")}</button>
          <button className="catalog-preview-page__primary" type="button" onClick={handleUnsupported}><Plus aria-hidden="true" /> {t(isAgents ? "catalog.newAgent" : "catalog.addCapability")}</button>
        </div>
      </header>

      {isAgents ? (
        <>
          <article className="catalog-preview-card prompt-editor">
            <header><span className="catalog-preview-card__icon"><ShieldCheck aria-hidden="true" /></span><div><h2>{t("review.correctness")}</h2><small>correctness:v1</small></div><b>{promptQuery.data?.is_custom ? "Custom" : "System default"}</b></header>
            <p>{locale === "zh-CN" ? "提示词会随当前桌面语言用于新建评审。" : "This prompt follows the current desktop language for new reviews."}</p>
            <textarea aria-label="Reviewer prompt" value={draft ?? promptQuery.data?.prompt ?? ""} onChange={(event) => setDraft(event.currentTarget.value)} rows={14} disabled={promptQuery.isLoading} />
            <footer className="prompt-editor__actions"><button className="prompt-editor__save" type="button" onClick={() => savePrompt.mutate(draft ?? promptQuery.data?.prompt ?? "")}>{locale === "zh-CN" ? "保存" : "Save"}</button><button type="button" onClick={() => resetPrompt.mutate()} disabled={!promptQuery.data?.is_custom}>{locale === "zh-CN" ? "重置" : "Reset"}</button></footer>
          </article>
          <div className="catalog-preview-toolbar">
            <label><Search aria-hidden="true" /><input aria-label={t("catalog.searchAgents")} placeholder={t("catalog.searchAgents")} value={searchQuery} onChange={(event) => setSearchQuery(event.currentTarget.value)} /></label>
            <button type="button" onClick={handleUnsupported}>{t("catalog.allSources")}</button>
            <span>{t("catalog.previewEntries", { count: reviewers.filter(([nameKey]) => t(nameKey).toLowerCase().includes(searchQuery.toLowerCase())).length })}</span>
          </div>
          <div className="catalog-preview-grid">
            {reviewers.filter(([nameKey, descriptionKey]) => `${t(nameKey)} ${t(descriptionKey)}`.toLowerCase().includes(searchQuery.toLowerCase())).map(([nameKey, descriptionKey, budget, Icon]) => (
              <article className="catalog-preview-card" key={nameKey}>
                <header><span className="catalog-preview-card__icon"><Icon aria-hidden="true" /></span><div><h2>{t(nameKey)}</h2><small>{t("catalog.builtInPreview")}</small></div><b>{t("catalog.enabled")}</b></header>
                <p>{t(descriptionKey)}</p>
                <div className="catalog-preview-card__meta"><span>{budget}</span><span>{t("catalog.readOnly")}</span><span>{t("catalog.policyBound")}</span></div>
                <footer><button type="button" onClick={handleUnsupported}><Settings2 aria-hidden="true" /> {t("catalog.configure")}</button><button aria-label={t("catalog.versionHistory", { name: t(nameKey) })} type="button" onClick={handleUnsupported}><History aria-hidden="true" /></button></footer>
              </article>
            ))}
          </div>
        </>
      ) : (
        <>
          <div className="catalog-preview-warning"><ShieldAlert aria-hidden="true" /> {t("catalog.untrustedWarning")}</div>
          <div className="catalog-preview-tabs" role="tablist" aria-label={t("catalog.capabilityTypes")}>
            {(Object.keys(capabilityGroups) as CapabilityGroupKey[]).map((group) => (
              <button className={activeGroup === group ? "active" : ""} key={group} role="tab" type="button" onClick={() => setActiveGroup(group)}>
                <Blocks aria-hidden="true" /> {t(group)}
              </button>
            ))}
          </div>
          <div className="catalog-preview-grid">
            {capabilityGroups[activeGroup].map((entryKey) => (
              <article className="catalog-preview-card" key={entryKey}>
                <header><span className="catalog-preview-card__icon"><ShieldCheck aria-hidden="true" /></span><div><h2>{t(entryKey)}</h2><small>{t("catalog.readOnlyEntry")}</small></div><b>{t("catalog.trusted")}</b></header>
                <p>{t("catalog.entryDescription")}</p>
                <div className="catalog-preview-card__meta"><span>{t("catalog.local")}</span><span>{t("catalog.policyEnforced")}</span><span>{t("catalog.noMutation")}</span></div>
                <footer><button type="button" onClick={handleUnsupported}><Wrench aria-hidden="true" /> {t("catalog.configure")}</button></footer>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
