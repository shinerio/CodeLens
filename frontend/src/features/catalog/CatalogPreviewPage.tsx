import {
  Activity,
  Blocks,
  Bot,
  Bug,
  ClipboardList,
  Database,
  Gauge,
  Plus,
  RefreshCw,
  Scale,
  Search,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  TestTube2,
  Wrench,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";
import {
  getAgentPrompt,
  listAgentPromptCatalog,
  resetAgentPrompt,
  updateAgentPrompt,
} from "./api";
import type { AgentPromptCatalogEntry, ReviewerCapabilityStatus } from "./types";
import "./CatalogPreviewPage.css";

type PreviewKind = "agents" | "capabilities";

type AgentPresentation = {
  descriptionKey: TranslationKey;
  icon: typeof Bug;
  nameKey: TranslationKey;
};

const AGENT_PRESENTATIONS: Readonly<Record<string, AgentPresentation>> = {
  correctness: { nameKey: "catalog.correctnessReviewer", descriptionKey: "catalog.correctnessDescription", icon: Bug },
  security: { nameKey: "catalog.securityReviewer", descriptionKey: "catalog.securityDescription", icon: ShieldAlert },
  "reliability-concurrency": { nameKey: "catalog.reliabilityReviewer", descriptionKey: "catalog.reliabilityDescription", icon: Activity },
  "contract-data": { nameKey: "catalog.contractDataReviewer", descriptionKey: "catalog.contractDataDescription", icon: Database },
  architecture: { nameKey: "catalog.architectureReviewer", descriptionKey: "catalog.architectureDescription", icon: Blocks },
  performance: { nameKey: "catalog.performanceReviewer", descriptionKey: "catalog.performanceDescription", icon: Gauge },
  "test-regression": { nameKey: "catalog.testRegressionReviewer", descriptionKey: "catalog.testRegressionDescription", icon: TestTube2 },
  general: { nameKey: "catalog.generalReviewer", descriptionKey: "catalog.generalDescription", icon: Bot },
  "review-planner": { nameKey: "catalog.planner", descriptionKey: "catalog.plannerDescription", icon: ClipboardList },
  "review-verifier": { nameKey: "catalog.verifier", descriptionKey: "catalog.verifierDescription", icon: Scale },
};

const STATUS_KEYS: Readonly<Record<ReviewerCapabilityStatus, TranslationKey>> = {
  ready: "catalog.status.ready",
  degraded: "catalog.status.degraded",
  unavailable: "catalog.status.unavailable",
};

type CapabilityGroupKey = "catalog.skills" | "catalog.mcpServers" | "catalog.staticTools" | "catalog.contextProviders";

const capabilityGroups: Readonly<Record<CapabilityGroupKey, readonly TranslationKey[]>> = {
  "catalog.skills": ["catalog.changedCodeReasoning", "catalog.securityThreatAnalysis", "catalog.migrationSafety"],
  "catalog.mcpServers": ["catalog.codeGraphTools", "catalog.openApiTools", "catalog.issueTracker"],
  "catalog.staticTools": ["catalog.ruff", "catalog.mypy", "catalog.pytest"],
  "catalog.contextProviders": ["catalog.codeGraphProvider", "catalog.textFallback"],
};

function fallbackAgentName(agentId: string) {
  return agentId
    .split("-")
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function presentationFor(entry: AgentPromptCatalogEntry): AgentPresentation {
  return AGENT_PRESENTATIONS[entry.agentId] ?? {
    nameKey: "catalog.unknownReviewer",
    descriptionKey: "catalog.unknownDescription",
    icon: Bot,
  };
}

/** Render the immutable backend catalog and per-version prompt configuration. */
export function CatalogPreviewPage({ kind }: { kind: PreviewKind }) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const [activeGroup, setActiveGroup] = useState<keyof typeof capabilityGroups>("catalog.skills");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedReference, setSelectedReference] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const isAgents = kind === "agents";
  const catalogQuery = useQuery({
    queryKey: ["agent-prompt-catalog"],
    queryFn: listAgentPromptCatalog,
    enabled: isAgents,
  });
  const activeAgent = catalogQuery.data?.find((entry) => entry.reference === selectedReference)
    ?? catalogQuery.data?.[0];
  const promptQuery = useQuery({
    queryKey: ["agent-prompt", activeAgent?.reference, locale],
    queryFn: () => {
      if (activeAgent === undefined) throw new Error("Agent is not selected");
      return getAgentPrompt(activeAgent.agentId, activeAgent.version, locale);
    },
    enabled: isAgents && activeAgent !== undefined,
  });
  const savePrompt = useMutation({
    mutationFn: (prompt: string) => {
      if (activeAgent === undefined) throw new Error("Agent is not selected");
      return updateAgentPrompt(activeAgent.agentId, activeAgent.version, locale, prompt);
    },
    onSuccess: async () => {
      setDraft(null);
      await queryClient.invalidateQueries({ queryKey: ["agent-prompt", activeAgent?.reference, locale] });
    },
  });
  const resetPrompt = useMutation({
    mutationFn: () => {
      if (activeAgent === undefined) throw new Error("Agent is not selected");
      return resetAgentPrompt(activeAgent.agentId, activeAgent.version, locale);
    },
    onSuccess: async () => {
      setDraft(null);
      await queryClient.invalidateQueries({ queryKey: ["agent-prompt", activeAgent?.reference, locale] });
    },
  });

  useEffect(() => setDraft(null), [activeAgent?.reference, locale]);

  const { filteredReviewers, filteredInternals } = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    const matches = (entry: AgentPromptCatalogEntry) => {
      const presentation = presentationFor(entry);
      const translatedName = presentation.nameKey === "catalog.unknownReviewer"
        ? fallbackAgentName(entry.agentId)
        : t(presentation.nameKey);
      return `${translatedName} ${entry.reference} ${entry.dimensions.join(" ")}`
        .toLowerCase()
        .includes(normalizedQuery);
    };
    const all = catalogQuery.data ?? [];
    return {
      filteredReviewers: all.filter((entry) => entry.role === "reviewer" && matches(entry)),
      filteredInternals: all.filter((entry) => entry.role !== "reviewer" && matches(entry)),
    };
  }, [catalogQuery.data, searchQuery, t]);

  function handleUnsupported() {
    window.alert(t("common.notSupported"));
  }

  function selectAgent(reference: string) {
    setSelectedReference(reference);
    setDraft(null);
    savePrompt.reset();
    resetPrompt.reset();
  }

  const activePresentation = activeAgent === undefined ? undefined : presentationFor(activeAgent);
  const activeName = activeAgent === undefined || activePresentation === undefined
    ? ""
    : activePresentation.nameKey === "catalog.unknownReviewer"
      ? fallbackAgentName(activeAgent.agentId)
      : t(activePresentation.nameKey);
  const liveCount = filteredReviewers.length + filteredInternals.length;

  function renderAgentCard(entry: AgentPromptCatalogEntry) {
    const presentation = presentationFor(entry);
    const Icon = presentation.icon;
    const name = presentation.nameKey === "catalog.unknownReviewer" ? fallbackAgentName(entry.agentId) : t(presentation.nameKey);
    return (
      <article className={activeAgent?.reference === entry.reference ? "catalog-preview-card reviewer-card reviewer-card--active" : "catalog-preview-card reviewer-card"} data-testid="reviewer-card" key={entry.reference}>
        <header><span className="catalog-preview-card__icon"><Icon aria-hidden="true" /></span><div><h2>{name}</h2><small>{entry.reference}</small></div><b>{t(STATUS_KEYS[entry.capabilityStatus])}</b></header>
        <p>{t(presentation.descriptionKey)}</p>
        <div className="catalog-preview-card__meta">{entry.dimensions.map((dimension) => <span key={dimension}>{dimension}</span>)}</div>
        <footer><button type="button" onClick={() => selectAgent(entry.reference)}><Settings2 aria-hidden="true" /> {t("catalog.editPrompt")}</button></footer>
      </article>
    );
  }

  return (
    <section className="catalog-preview-page">
      <header className="catalog-preview-page__header">
        <div>
          <p>{t(isAgents ? "catalog.agentsEyebrow" : "catalog.capabilitiesEyebrow")}</p>
          <h1>{t(isAgents ? "catalog.agentsTitle" : "catalog.capabilitiesTitle")}</h1>
          <span>{t(isAgents ? "catalog.agentsSubtitle" : "catalog.capabilitiesSubtitle")}</span>
        </div>
        <div className="catalog-preview-page__actions">
          <button type="button" onClick={() => isAgents ? void catalogQuery.refetch() : handleUnsupported()}><RefreshCw aria-hidden="true" /> {t("catalog.refresh")}</button>
          {!isAgents ? <button className="catalog-preview-page__primary" type="button" onClick={handleUnsupported}><Plus aria-hidden="true" /> {t("catalog.addCapability")}</button> : null}
        </div>
      </header>

      {isAgents ? (
        <div className="agent-catalog-workbench">
          <div className="agent-catalog-list">
            <div className="catalog-preview-toolbar">
              <label><Search aria-hidden="true" /><input aria-label={t("catalog.searchAgents")} placeholder={t("catalog.searchAgents")} value={searchQuery} onChange={(event) => setSearchQuery(event.currentTarget.value)} /></label>
              <span>{t("catalog.liveEntries", { count: String(liveCount) })}</span>
            </div>
            {catalogQuery.isPending ? <p className="catalog-state">{t("catalog.loadingAgents")}</p> : null}
            {catalogQuery.isError ? <p className="catalog-state catalog-state--error" role="alert">{t("catalog.loadAgentsFailed")}</p> : null}
            <div className="catalog-preview-grid catalog-preview-grid--agents">
              {filteredReviewers.map(renderAgentCard)}
            </div>
            {filteredInternals.length > 0 ? (
              <div className="catalog-internal-section">
                <header className="catalog-internal-section__header">
                  <h2>{t("catalog.internalAgents")}</h2>
                  <span>{t("catalog.internalAgentsSubtitle")}</span>
                </header>
                <div className="catalog-preview-grid catalog-preview-grid--agents">
                  {filteredInternals.map(renderAgentCard)}
                </div>
              </div>
            ) : null}
          </div>

          <article className="catalog-preview-card prompt-editor">
            {activeAgent === undefined || activePresentation === undefined ? <p>{t("catalog.selectAgent")}</p> : (
              <>
                <header><span className="catalog-preview-card__icon"><ShieldCheck aria-hidden="true" /></span><div><h2>{activeName}</h2><small>{activeAgent.reference}</small></div><b>{promptQuery.data?.is_custom ? t("catalog.customPrompt") : t("catalog.systemDefault")}</b></header>
                <p>{t("catalog.promptScopeNote")}</p>
                {promptQuery.isError ? <p className="catalog-state catalog-state--error" role="alert">{t("catalog.promptLoadFailed")}</p> : null}
                <textarea aria-label={t("catalog.agentPrompt")} value={draft ?? promptQuery.data?.prompt ?? ""} onChange={(event) => setDraft(event.currentTarget.value)} rows={18} disabled={promptQuery.isPending || promptQuery.isError} />
                {savePrompt.isError || resetPrompt.isError ? <p className="catalog-state catalog-state--error" role="alert">{t("catalog.promptSaveFailed")}</p> : null}
                <footer className="prompt-editor__actions"><button className="prompt-editor__save" disabled={draft === null || draft.trim() === "" || savePrompt.isPending} type="button" onClick={() => savePrompt.mutate(draft ?? promptQuery.data?.prompt ?? "")}>{t("catalog.savePrompt")}</button><button type="button" onClick={() => resetPrompt.mutate()} disabled={!promptQuery.data?.is_custom || resetPrompt.isPending}>{t("catalog.resetPrompt")}</button></footer>
              </>
            )}
          </article>
        </div>
      ) : (
        <>
          <div className="catalog-preview-warning"><ShieldAlert aria-hidden="true" /> {t("catalog.untrustedWarning")}</div>
          <div className="catalog-preview-tabs" role="tablist" aria-label={t("catalog.capabilityTypes")}>{(Object.keys(capabilityGroups) as CapabilityGroupKey[]).map((group) => <button className={activeGroup === group ? "active" : ""} key={group} role="tab" type="button" onClick={() => setActiveGroup(group)}><Blocks aria-hidden="true" /> {t(group)}</button>)}</div>
          <div className="catalog-preview-grid">{capabilityGroups[activeGroup].map((entryKey) => <article className="catalog-preview-card" key={entryKey}><header><span className="catalog-preview-card__icon"><ShieldCheck aria-hidden="true" /></span><div><h2>{t(entryKey)}</h2><small>{t("catalog.readOnlyEntry")}</small></div><b>{t("catalog.trusted")}</b></header><p>{t("catalog.entryDescription")}</p><div className="catalog-preview-card__meta"><span>{t("catalog.local")}</span><span>{t("catalog.policyEnforced")}</span><span>{t("catalog.noMutation")}</span></div><footer><button type="button" onClick={handleUnsupported}><Wrench aria-hidden="true" /> {t("catalog.configure")}</button></footer></article>)}</div>
        </>
      )}
    </section>
  );
}
