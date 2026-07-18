import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpenText,
  CirclePlay,
  FileCode2,
  FolderSearch,
  Gauge,
  GitBranch,
  GitCommitVertical,
  Lock,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";
import { getRepositoryCatalog, inspectRepository } from "../repositories/api";
import { RepositoryBrowser } from "../repositories/RepositoryBrowser";
import type {
  RepositoryCatalog,
  RepositoryCommit,
  RepositoryInspectionResponse,
} from "../repositories/types";
import { listModelGateways } from "../settings/api";
import { createReview } from "./api";
import type { CreateReviewRequest, ReviewMode, ScopeRequest } from "./types";
import "./NewReviewPage.css";

const CORRECTNESS_AGENT_REFERENCE = "correctness:v1";
const REVIEWER_ROWS: Array<{
  reference: string;
  labelKey: TranslationKey;
  noteKey: TranslationKey;
  enabled: boolean;
  statusKey: TranslationKey;
  icon: typeof ShieldCheck;
}> = [
  {
    reference: CORRECTNESS_AGENT_REFERENCE,
    labelKey: "review.correctness",
    noteKey: "review.correctnessNote",
    enabled: true,
    statusKey: "review.enabledNow",
    icon: ShieldCheck,
  },
  {
    reference: "security:v1",
    labelKey: "review.security",
    noteKey: "review.securityNote",
    enabled: false,
    statusKey: "review.availablePhase3",
    icon: Lock,
  },
  {
    reference: "performance:v1",
    labelKey: "review.performance",
    noteKey: "review.performanceNote",
    enabled: false,
    statusKey: "review.availablePhase3",
    icon: Gauge,
  },
  {
    reference: "maintainability:v1",
    labelKey: "review.maintainability",
    noteKey: "review.maintainabilityNote",
    enabled: false,
    statusKey: "review.availablePhase3",
    icon: Wrench,
  },
  {
    reference: "testing:v1",
    labelKey: "review.testing",
    noteKey: "review.testingNote",
    enabled: false,
    statusKey: "review.availablePhase3",
    icon: FileCode2,
  },
  {
    reference: "docs_style:v1",
    labelKey: "review.docsStyle",
    noteKey: "review.docsStyleNote",
    enabled: false,
    statusKey: "review.availablePhase3",
    icon: BookOpenText,
  },
  {
    reference: "cross_file:v1",
    labelKey: "review.crossFile",
    noteKey: "review.crossFileNote",
    enabled: false,
    statusKey: "review.availablePhase3",
    icon: GitCommitVertical,
  },
];

type ScopeType = ScopeRequest["type"];

function preferredBase(branchNames: string[], target: string): string {
  for (const candidate of ["origin/main", "main", "origin/master", "master"]) {
    if (candidate !== target && branchNames.includes(candidate)) {
      return candidate;
    }
  }
  return branchNames.find((branch) => branch !== target) ?? branchNames[0] ?? "";
}

function commitLabel(commit: RepositoryCommit): string {
  return `${commit.short_oid} · ${commit.author} · ${commit.message}`;
}

export function NewReviewPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { t } = useI18n();
  const [browserOpen, setBrowserOpen] = useState(false);
  const [repositoryPath, setRepositoryPath] = useState("");
  const [inspection, setInspection] = useState<RepositoryInspectionResponse | null>(null);
  const [catalog, setCatalog] = useState<RepositoryCatalog | null>(null);
  const [commits, setCommits] = useState<RepositoryCommit[]>([]);
  const [nextCommitOffset, setNextCommitOffset] = useState<number | null>(null);
  const [scopeType, setScopeType] = useState<ScopeType>("branch");
  const [includeWorkspaceChanges, setIncludeWorkspaceChanges] = useState(false);
  const [branchBaseRef, setBranchBaseRef] = useState("");
  const [branchTargetRef, setBranchTargetRef] = useState("");
  const [commitBaseRef, setCommitBaseRef] = useState("");
  const [commitTargetRef, setCommitTargetRef] = useState("");
  const [fullTargetRef, setFullTargetRef] = useState("");
  const [correctnessEnabled, setCorrectnessEnabled] = useState(true);
  const [mode, setMode] = useState<ReviewMode>("review");

  const gatewayQuery = useQuery({
    queryKey: ["model-gateways"],
    queryFn: listModelGateways,
  });

  const inspectMutation = useMutation({
    mutationFn: async (path: string) => {
      const [repository, repositoryCatalog] = await Promise.all([
        inspectRepository(path),
        getRepositoryCatalog(path),
      ]);
      return { repository, repositoryCatalog };
    },
    onSuccess: ({ repository, repositoryCatalog }) => {
      const branchNames = repositoryCatalog.branches.map((branch) => branch.name);
      const target =
        repositoryCatalog.branches.find((branch) => branch.is_current)?.name ??
        branchNames[0] ??
        "";
      setInspection(repository);
      setCatalog(repositoryCatalog);
      setCommits(repositoryCatalog.commits);
      setNextCommitOffset(repositoryCatalog.next_commit_offset);
      setBranchTargetRef(target);
      setCommitTargetRef(target);
      setFullTargetRef(target);
      setBranchBaseRef(preferredBase(branchNames, target));
      setCommitBaseRef(repositoryCatalog.commits[0]?.oid ?? "");
    },
  });

  const loadMoreMutation = useMutation({
    mutationFn: async (offset: number) => getRepositoryCatalog(repositoryPath, offset),
    onSuccess: (nextCatalog) => {
      setCommits((current) => {
        const existing = new Set(current.map((commit) => commit.oid));
        return [...current, ...nextCatalog.commits.filter((commit) => !existing.has(commit.oid))];
      });
      setNextCommitOffset(nextCatalog.next_commit_offset);
    },
  });

  const createMutation = useMutation({
    mutationFn: createReview,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
      navigate(`/reviews/${result.task_id}`);
    },
  });

  const branchNames = catalog?.branches.map((branch) => branch.name) ?? [];
  const selectedAgents = correctnessEnabled ? [CORRECTNESS_AGENT_REFERENCE] : [];
  const hasActiveGateway = gatewayQuery.data?.active_gateway_id != null;
  const selectedScopeIsValid =
    scopeType === "uncommitted" ||
    (scopeType === "branch" && branchBaseRef !== "" && branchTargetRef !== "") ||
    (scopeType === "commit" && commitBaseRef !== "" && commitTargetRef !== "") ||
    (scopeType === "full" && fullTargetRef !== "");
  const startDisabled =
    inspection === null ||
    selectedAgents.length === 0 ||
    !hasActiveGateway ||
    !selectedScopeIsValid ||
    inspectMutation.isPending ||
    createMutation.isPending;
  const errorMessage = [
    inspectMutation.error,
    loadMoreMutation.error,
    createMutation.error,
    gatewayQuery.error,
  ].find((error): error is Error => error instanceof Error)?.message;

  function selectRepository(path: string) {
    setBrowserOpen(false);
    setRepositoryPath(path);
    setInspection(null);
    setCatalog(null);
    inspectMutation.mutate(path);
  }

  function buildScope(): ScopeRequest {
    if (scopeType === "branch") {
      return {
        type: "branch",
        base_ref: branchBaseRef,
        target_ref: branchTargetRef,
        include_workspace_changes: includeWorkspaceChanges,
      };
    }
    if (scopeType === "commit") {
      return {
        type: "commit",
        base_commit: commitBaseRef,
        target_ref: commitTargetRef,
        include_workspace_changes: includeWorkspaceChanges,
      };
    }
    if (scopeType === "full") {
      return {
        type: "full",
        target_ref: fullTargetRef,
        include_workspace_changes: includeWorkspaceChanges,
      };
    }
    return { type: "uncommitted" };
  }

  function handleStartReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (startDisabled) {
      return;
    }
    const request: CreateReviewRequest = {
      repository_path: repositoryPath,
      scope: buildScope(),
      selected_agents: selectedAgents,
      mode,
    };
    createMutation.mutate(request);
  }

  function scopeToggle(type: ScopeType, title: TranslationKey, note: TranslationKey) {
    return (
      <button
        className={scopeType === type ? "scope-toggle scope-toggle--active" : "scope-toggle"}
        type="button"
        onClick={() => setScopeType(type)}
      >
        <span className="scope-toggle__title">{t(title)}</span>
        <span className="scope-toggle__description">{t(note)}</span>
      </button>
    );
  }

  return (
    <section className="new-review-page">
      <RepositoryBrowser
        isOpen={browserOpen}
        onClose={() => setBrowserOpen(false)}
        onSelect={selectRepository}
      />
      <header className="new-review-page__header">
        <div className="new-review-page__eyebrow">{t("review.newEyebrow")}</div>
        <h1>{t("review.newTitle")}</h1>
        <p>{t("review.newSubtitle")}</p>
      </header>

      <div className="new-review-page__grid">
        <form className="new-review-page__form" onSubmit={handleStartReview}>
          <section className="panel panel--primary">
            <div className="panel__heading">
              <FolderSearch aria-hidden="true" />
              <h2>{t("repository.inspection")}</h2>
            </div>
            <div className="field-row field-row--path">
              <label className="field">
                <span className="field__label">{t("repository.path")}</span>
                <input
                  aria-label={t("repository.path")}
                  className="field__control repository-path-control"
                  readOnly
                  value={repositoryPath}
                />
              </label>
              <button
                className="action-button action-button--secondary"
                disabled={inspectMutation.isPending}
                type="button"
                onClick={() => setBrowserOpen(true)}
              >
                {repositoryPath === "" ? t("repository.browse") : t("repository.change")}
              </button>
            </div>

            {inspectMutation.isPending ? <p className="hint">{t("repository.inspecting")}</p> : null}
            {!inspectMutation.isPending && inspection === null ? (
              <p className="hint">{t("repository.required")}</p>
            ) : null}
            {inspection !== null ? (
              <dl className="inspection-summary">
                <div>
                  <dt>{t("repository.repository")}</dt>
                  <dd>{inspection.display_path}</dd>
                </div>
                <div>
                  <dt>{t("repository.head")}</dt>
                  <dd>{inspection.head_oid}</dd>
                </div>
                <div>
                  <dt>{t("repository.branch")}</dt>
                  <dd>{inspection.current_branch ?? t("repository.detached")}</dd>
                </div>
                <div>
                  <dt>{t("repository.dirty")}</dt>
                  <dd>
                    {inspection.is_dirty
                      ? t("repository.dirtyTree")
                      : t("repository.cleanTree")}
                  </dd>
                </div>
              </dl>
            ) : null}
          </section>

          <section className="panel">
            <div className="panel__heading">
              <GitBranch aria-hidden="true" />
              <h2>{t("review.scope")}</h2>
            </div>
            <div className="scope-toggle-grid" role="radiogroup" aria-label={t("review.scopeGroup")}>
              {scopeToggle("branch", "review.branchDiff", "review.branchDiffNote")}
              {scopeToggle("commit", "review.commitDiff", "review.commitDiffNote")}
              {scopeToggle("uncommitted", "review.uncommitted", "review.uncommittedNote")}
              {scopeToggle("full", "review.fullRepository", "review.fullRepositoryNote")}
            </div>

            <div className="scope-fields">
              {scopeType === "branch" ? (
                <>
                  <label className="field">
                    <span className="field__label">{t("review.baseBranch")}</span>
                    <select
                      aria-label={t("review.baseBranch")}
                      className="field__control"
                      disabled={branchNames.length === 0}
                      value={branchBaseRef}
                      onChange={(event) => setBranchBaseRef(event.currentTarget.value)}
                    >
                      {branchNames.length === 0 ? <option value="">{t("review.noBranches")}</option> : null}
                      {branchNames.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
                    </select>
                  </label>
                  <label className="field">
                    <span className="field__label">{t("review.targetBranch")}</span>
                    <select
                      aria-label={t("review.targetBranch")}
                      className="field__control"
                      disabled={branchNames.length === 0}
                      value={branchTargetRef}
                      onChange={(event) => setBranchTargetRef(event.currentTarget.value)}
                    >
                      {branchNames.length === 0 ? <option value="">{t("review.noBranches")}</option> : null}
                      {branchNames.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
                    </select>
                  </label>
                </>
              ) : null}

              {scopeType === "commit" ? (
                <>
                  <label className="field">
                    <span className="field__label">{t("review.baseCommit")}</span>
                    <select
                      aria-label={t("review.baseCommit")}
                      className="field__control commit-select"
                      disabled={commits.length === 0}
                      value={commitBaseRef}
                      onChange={(event) => setCommitBaseRef(event.currentTarget.value)}
                    >
                      {commits.length === 0 ? <option value="">{t("review.noCommits")}</option> : null}
                      {commits.map((commit) => (
                        <option key={commit.oid} value={commit.oid}>{commitLabel(commit)}</option>
                      ))}
                    </select>
                  </label>
                  {nextCommitOffset !== null ? (
                    <button
                      className="load-more-button"
                      disabled={loadMoreMutation.isPending}
                      type="button"
                      onClick={() => loadMoreMutation.mutate(nextCommitOffset)}
                    >
                      {loadMoreMutation.isPending ? t("common.loading") : t("review.moreCommits")}
                    </button>
                  ) : null}
                  <label className="field">
                    <span className="field__label">{t("review.targetBranch")}</span>
                    <select
                      aria-label={t("review.targetBranch")}
                      className="field__control"
                      value={commitTargetRef}
                      onChange={(event) => setCommitTargetRef(event.currentTarget.value)}
                    >
                      {branchNames.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
                    </select>
                  </label>
                </>
              ) : null}

              {scopeType === "full" ? (
                <label className="field">
                  <span className="field__label">{t("review.targetBranch")}</span>
                  <select
                    aria-label={t("review.targetBranch")}
                    className="field__control"
                    value={fullTargetRef}
                    onChange={(event) => setFullTargetRef(event.currentTarget.value)}
                  >
                    {branchNames.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
                  </select>
                </label>
              ) : null}

              {scopeType !== "uncommitted" ? (
                <label className="field field--toggle">
                  <input
                    checked={includeWorkspaceChanges}
                    type="checkbox"
                    onChange={(event) => setIncludeWorkspaceChanges(event.currentTarget.checked)}
                  />
                  <span>{t("review.includeWorkspace")}</span>
                </label>
              ) : null}
            </div>
          </section>

          <section className="panel">
            <div className="panel__heading">
              <CirclePlay aria-hidden="true" />
              <h2>{t("review.mode")}</h2>
            </div>
            <div className="mode-toggle-grid" role="radiogroup" aria-label={t("review.mode")}>
              <button
                className={mode === "review" ? "mode-toggle mode-toggle--active" : "mode-toggle"}
                type="button"
                onClick={() => setMode("review")}
              >
                <span className="mode-toggle__label">REVIEW</span>
                <span className="mode-toggle__note">{t("review.enabledNow")}</span>
              </button>
              <button className="mode-toggle" disabled type="button">
                <span className="mode-toggle__label">FIX</span>
                <span className="mode-toggle__note">{t("review.availablePhase5")}</span>
              </button>
            </div>
          </section>

          <section className="panel">
            <div className="panel__heading">
              <ShieldCheck aria-hidden="true" />
              <h2>{t("review.reviewers")}</h2>
            </div>
            <div className="reviewer-list">
              {REVIEWER_ROWS.map((row) => {
                const Icon = row.icon;
                return (
                  <label className={row.enabled ? "agent-row" : "agent-row agent-row--disabled"} key={row.reference}>
                    <span className="agent-row__leading"><Icon aria-hidden="true" /></span>
                    <span className="agent-row__content">
                      <span className="agent-row__headline">
                        <span>{t(row.labelKey)}</span>
                        <span className="agent-row__reference">{row.reference}</span>
                      </span>
                      <span className="agent-row__description">{t(row.noteKey)}</span>
                    </span>
                    <span className="agent-row__status">{t(row.statusKey)}</span>
                    <input
                      aria-label={t(row.labelKey)}
                      checked={row.enabled ? correctnessEnabled : false}
                      className="agent-row__input"
                      disabled={!row.enabled}
                      type="checkbox"
                      onChange={(event) => setCorrectnessEnabled(event.currentTarget.checked)}
                    />
                  </label>
                );
              })}
            </div>
          </section>

          {errorMessage !== undefined ? <div className="alert" role="alert">{errorMessage}</div> : null}
          {gatewayQuery.data?.active_gateway_id === null ? (
            <div className="provider-required" role="status">
              <span>{t("review.providerRequired")}</span>
              <Link to="/settings">{t("review.configureGateway")}</Link>
            </div>
          ) : null}

          <div className="form-actions">
            <div className="form-actions__summary">
              <span>{inspection === null ? t("repository.notReady") : t("repository.ready")}</span>
              <span>{t("review.agentCount", { count: selectedAgents.length })}</span>
              <span>{hasActiveGateway ? t("review.gatewayReady") : t("review.gatewayMissing")}</span>
            </div>
            <button className="action-button" disabled={startDisabled} type="submit">
              {createMutation.isPending ? t("review.starting") : t("review.start")}
            </button>
          </div>
        </form>

        <aside className="panel panel--aside">
          <div className="panel__heading">
            <FileCode2 aria-hidden="true" />
            <h2>{t("review.summary")}</h2>
          </div>
          {inspection === null ? <p className="hint">{t("review.summaryEmpty")}</p> : (
            <dl className="inspector-card">
              <div><dt>{t("repository.branch")}</dt><dd>{inspection.current_branch ?? t("repository.detached")}</dd></div>
              <div><dt>{t("repository.head")}</dt><dd>{inspection.head_oid}</dd></div>
              <div><dt>{t("repository.dirty")}</dt><dd>{inspection.is_dirty ? t("common.yes") : t("common.no")}</dd></div>
              <div><dt>{t("review.repositoryId")}</dt><dd>{inspection.repository_id}</dd></div>
              <div><dt>{t("review.realpathHash")}</dt><dd>{inspection.repository_realpath_hash}</dd></div>
              <div><dt>{t("review.commonDirHash")}</dt><dd>{inspection.git_common_dir_hash}</dd></div>
            </dl>
          )}
          <div className="aside-note">{t("review.phaseNote")}</div>
        </aside>
      </div>
    </section>
  );
}
