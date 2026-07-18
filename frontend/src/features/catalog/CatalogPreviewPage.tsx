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

import { useI18n } from "../../shared/i18n/i18n";
import "./CatalogPreviewPage.css";

type PreviewKind = "agents" | "capabilities";

const reviewers = [
  ["Correctness Reviewer", "Logic, state transitions, edge cases, and changed-hunk behavior.", "120k tokens", Bug],
  ["Security Reviewer", "Trust boundaries, authorization, injection paths, and secret handling.", "140k tokens", ShieldAlert],
  ["Performance Reviewer", "Latency, resource growth, contention, and scaling risks.", "96k tokens", Gauge],
  ["Data & API Reviewer", "Schema evolution, API compatibility, and migration safety.", "110k tokens", Database],
  ["Test Quality Reviewer", "Assertions, failure modes, fixtures, and coverage gaps.", "84k tokens", TestTube2],
  ["Architecture Reviewer", "Dependency direction, ownership, and public contracts.", "150k tokens", Blocks],
  ["Maintainability Reviewer", "Control flow, operational ambiguity, and design clarity.", "72k tokens", Wrench],
  ["Release Risk Reviewer", "Rollout hazards, migration sequencing, and rollback readiness.", "Draft", History],
] as const;

const capabilityGroups = {
  Skills: ["Changed-code reasoning", "Security threat analysis", "Migration safety"],
  "MCP servers": ["CodeGraph · 5 read-only tools", "OpenAPI Catalog · 3 read-only tools", "Issue Tracker · restricted"],
  "Static tools": ["Ruff · Python lint", "mypy · type analysis", "pytest · focused tests"],
  "Context providers": ["CodeGraph provider · active", "Text fallback · active"],
} as const;

/** Renders a read-only catalog preview until catalog APIs and mutations are available. */
export function CatalogPreviewPage({ kind }: { kind: PreviewKind }) {
  const { t } = useI18n();
  const [activeGroup, setActiveGroup] = useState<keyof typeof capabilityGroups>("Skills");
  const [searchQuery, setSearchQuery] = useState("");
  const isAgents = kind === "agents";

  function handleUnsupported() {
    window.alert(t("common.notSupported"));
  }

  return (
    <section className="catalog-preview-page">
      <header className="catalog-preview-page__header">
        <div>
          <p>{isAgents ? "Configuration / catalog preview" : "Configuration / policy preview"}</p>
          <h1>{isAgents ? "Review agents" : "Capabilities"}</h1>
          <span>
            {isAgents
              ? "Versioned reviewer definitions, budgets, and bound capabilities."
              : "Skills, MCP servers, static tools, and read-only context providers."}
          </span>
        </div>
        <div className="catalog-preview-page__actions">
          <button type="button" onClick={handleUnsupported}><RefreshCw aria-hidden="true" /> Refresh</button>
          <button className="catalog-preview-page__primary" type="button" onClick={handleUnsupported}><Plus aria-hidden="true" /> {isAgents ? "New agent" : "Add capability"}</button>
        </div>
      </header>

      {isAgents ? (
        <>
          <div className="catalog-preview-toolbar">
            <label><Search aria-hidden="true" /><input aria-label="Search agents" placeholder="Search agents" value={searchQuery} onChange={(event) => setSearchQuery(event.currentTarget.value)} /></label>
            <button type="button" onClick={handleUnsupported}>All sources</button>
            <span>Preview · {reviewers.filter(([name]) => name.toLowerCase().includes(searchQuery.toLowerCase())).length} entries</span>
          </div>
          <div className="catalog-preview-grid">
            {reviewers.filter(([name, description]) => `${name} ${description}`.toLowerCase().includes(searchQuery.toLowerCase())).map(([name, description, budget, Icon]) => (
              <article className="catalog-preview-card" key={name}>
                <header><span className="catalog-preview-card__icon"><Icon aria-hidden="true" /></span><div><h2>{name}</h2><small>Built-in · catalog preview</small></div><b>Enabled</b></header>
                <p>{description}</p>
                <div className="catalog-preview-card__meta"><span>{budget}</span><span>Read-only</span><span>Policy-bound</span></div>
                <footer><button type="button" onClick={handleUnsupported}><Settings2 aria-hidden="true" /> Configure</button><button aria-label={`${name} version history`} type="button" onClick={handleUnsupported}><History aria-hidden="true" /></button></footer>
              </article>
            ))}
          </div>
        </>
      ) : (
        <>
          <div className="catalog-preview-warning"><ShieldAlert aria-hidden="true" /> Repository capabilities are untrusted until explicitly approved. Preview data cannot change permissions.</div>
          <div className="catalog-preview-tabs" role="tablist" aria-label="Capability types">
            {Object.keys(capabilityGroups).map((group) => (
              <button className={activeGroup === group ? "active" : ""} key={group} role="tab" type="button" onClick={() => setActiveGroup(group as keyof typeof capabilityGroups)}>
                <Blocks aria-hidden="true" /> {group}
              </button>
            ))}
          </div>
          <div className="catalog-preview-grid">
            {capabilityGroups[activeGroup].map((entry) => (
              <article className="catalog-preview-card" key={entry}>
                <header><span className="catalog-preview-card__icon"><ShieldCheck aria-hidden="true" /></span><div><h2>{entry}</h2><small>Read-only catalog entry</small></div><b>Trusted</b></header>
                <p>Preview the declared trust boundary and bound agent access before this catalog is connected to the runtime.</p>
                <div className="catalog-preview-card__meta"><span>Local</span><span>Policy enforced</span><span>No mutation</span></div>
                <footer><button type="button" onClick={handleUnsupported}><Wrench aria-hidden="true" /> Configure</button></footer>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
