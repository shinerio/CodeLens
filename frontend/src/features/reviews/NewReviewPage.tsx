import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  FolderSearch,
  FolderGit2,
  GitBranch,
  History,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { Link, useNavigate } from "react-router-dom";

import { formatUserDateTime } from "../../shared/i18n/format-user-date-time";
import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";
import { listReviewerCatalog } from "../catalog/api";
import { createReviewProfile, listReviewProfiles } from "../review-profiles/api";
import { ReviewProfilePicker } from "../review-profiles/ReviewProfilePicker";
import { ReviewStrategyEditor } from "../review-strategy/ReviewStrategyEditor";
import { ReviewStrategySummary } from "../review-strategy/ReviewStrategySummary";
import { validateStrategy } from "../review-strategy/model";
import {
  deleteRecentRepository,
  getRepositoryCatalog,
  inspectRepository,
  listRecentRepositories,
} from "../repositories/api";
import { RepositoryBrowser } from "../repositories/RepositoryBrowser";
import type {
  RepositoryCatalog,
  RepositoryCommit,
  RepositoryInspectionResponse,
  RecentRepository,
} from "../repositories/types";
import { listModelGateways } from "../settings/api";
import { createReview, toCreateReviewRequest } from "./api";
import type { CreateReviewRequest, ReviewStrategySnapshot, ScopeRequest } from "./types";
import "./NewReviewPage.css";

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
  const { t, locale } = useI18n();
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
  const [commitBranchRef, setCommitBranchRef] = useState("");
  const [commitTargetRef, setCommitTargetRef] = useState("");
  const selectedCommitBranchRef = useRef("");
  const [fullTargetRef, setFullTargetRef] = useState("");
  const [strategy, setStrategy] = useState<ReviewStrategySnapshot>({
    reviewerSelection: { mode: "adaptive" },
  });
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [isCustomizingStrategy, setIsCustomizingStrategy] = useState(false);
  const [isStrategyCustomized, setIsStrategyCustomized] = useState(false);
  const [shouldSaveProfile, setShouldSaveProfile] = useState(false);
  const [newProfileName, setNewProfileName] = useState("");
  const profileInitialized = useRef(false);
  const [repositoryPendingDeletion, setRepositoryPendingDeletion] =
    useState<RecentRepository | null>(null);
  const deleteDialogConfirmRef = useRef<HTMLButtonElement>(null);
  const deleteDialogTriggerRef = useRef<HTMLButtonElement | null>(null);

  const gatewayQuery = useQuery({
    queryKey: ["model-gateways"],
    queryFn: listModelGateways,
  });
  const profilesQuery = useQuery({ queryKey: ["review-profiles"], queryFn: listReviewProfiles });
  const reviewerCatalogQuery = useQuery({ queryKey: ["reviewer-catalog"], queryFn: listReviewerCatalog });
  const recentRepositoriesQuery = useQuery({
    queryKey: ["recent-repositories"],
    queryFn: listRecentRepositories,
  });
  const deleteRecentRepositoryMutation = useMutation({
    mutationFn: deleteRecentRepository,
    onSuccess: (_result, deletedPath) => {
      queryClient.setQueryData<RecentRepository[]>(["recent-repositories"], (repositories) =>
        repositories?.filter((repository) => repository.repository_path !== deletedPath),
      );
      closeDeleteRecentRepositoryDialog();
    },
  });

  useEffect(() => {
    if (repositoryPendingDeletion === null) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    deleteDialogConfirmRef.current?.focus();

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape" && !deleteRecentRepositoryMutation.isPending) {
        closeDeleteRecentRepositoryDialog();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [repositoryPendingDeletion, deleteRecentRepositoryMutation.isPending]);

  const inspectMutation = useMutation({
    mutationFn: async (path: string) => {
      const repository = await inspectRepository(path);
      const repositoryCatalog = await getRepositoryCatalog(path, repository.current_branch);
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
      setCommitBranchRef(target);
      selectedCommitBranchRef.current = target;
      setFullTargetRef(target);
      setBranchBaseRef(preferredBase(branchNames, target));
      setCommitBaseRef(repositoryCatalog.commits[0]?.oid ?? "");
      setCommitTargetRef(
        repositoryCatalog.branches.find((branch) => branch.name === target)?.oid ?? "",
      );
    },
  });

  const commitCatalogMutation = useMutation({
    mutationFn: async ({
      targetRef,
      offset,
    }: {
      targetRef: string;
      offset: number;
      shouldReplace: boolean;
    }) => getRepositoryCatalog(repositoryPath, targetRef, offset),
    onSuccess: (nextCatalog, { targetRef, shouldReplace }) => {
      if (selectedCommitBranchRef.current !== targetRef) {
        return;
      }
      if (shouldReplace) {
        setCatalog(nextCatalog);
        setCommits(nextCatalog.commits);
        setCommitBaseRef(nextCatalog.commits[0]?.oid ?? "");
        setCommitTargetRef(
          nextCatalog.branches.find((branch) => branch.name === targetRef)?.oid ?? "",
        );
      } else {
        setCommits((current) => {
          const existing = new Set(current.map((commit) => commit.oid));
          return [
            ...current,
            ...nextCatalog.commits.filter((commit) => !existing.has(commit.oid)),
          ];
        });
      }
      setNextCommitOffset(nextCatalog.next_commit_offset);
    },
  });

  const createMutation = useMutation({
    mutationFn: async ({ request, saveProfileName }: { request: CreateReviewRequest; saveProfileName: string | null }) => {
      if (saveProfileName === null) return createReview(request);
      const profile = await createReviewProfile({ name: saveProfileName, isDefault: false, strategy });
      return createReview({ ...request, profile_source: { profile_id: profile.id, revision: profile.revision } });
    },
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
      navigate(`/runs/${result.task_id}`);
    },
  });

  useEffect(() => {
    if (profileInitialized.current || profilesQuery.data === undefined) return;
    const defaultProfile = profilesQuery.data.find((profile) => profile.isDefault);
    if (defaultProfile === undefined) return;
    profileInitialized.current = true;
    setSelectedProfileId(defaultProfile.id);
    setStrategy({
      reviewerSelection:
        defaultProfile.strategy.reviewerSelection.mode === "adaptive"
          ? { mode: "adaptive" }
          : { mode: "fixed", reviewerVersions: [...defaultProfile.strategy.reviewerSelection.reviewerVersions] },
    });
  }, [profilesQuery.data]);

  const branchNames = catalog?.branches.map((branch) => branch.name) ?? [];
  const strategyErrors = validateStrategy(strategy, reviewerCatalogQuery.data ?? []);
  const hasActiveGateway = gatewayQuery.data?.active_gateway_id != null;
  const selectedScopeIsValid =
    scopeType === "uncommitted" ||
    (scopeType === "branch" && branchBaseRef !== "" && branchTargetRef !== "") ||
    (scopeType === "commit" && commitBranchRef !== "" && commitBaseRef !== "" && commitTargetRef !== "") ||
    (scopeType === "full" && fullTargetRef !== "");
  const startDisabled =
    inspection === null ||
    profilesQuery.data === undefined ||
    profilesQuery.data.length === 0 ||
    reviewerCatalogQuery.isError ||
    strategyErrors.length > 0 ||
    (shouldSaveProfile && newProfileName.trim() === "") ||
    !hasActiveGateway ||
    !selectedScopeIsValid ||
    inspectMutation.isPending ||
    createMutation.isPending;
  const errorMessage = [
    inspectMutation.error,
    commitCatalogMutation.error,
    createMutation.error,
    gatewayQuery.error,
    profilesQuery.error,
    reviewerCatalogQuery.error,
    deleteRecentRepositoryMutation.error,
  ].find((error): error is Error => error instanceof Error)?.message;

  function selectRepository(path: string) {
    setBrowserOpen(false);
    setRepositoryPath(path);
    setInspection(null);
    setCatalog(null);
    inspectMutation.mutate(path);
  }

  function handleRepositoryPathChange(path: string) {
    setRepositoryPath(path);
    setInspection(null);
    setCatalog(null);
  }

  function handleRepositoryPathKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    const path = event.currentTarget.value.trim();
    if (path !== "") {
      selectRepository(path);
    }
  }

  function closeDeleteRecentRepositoryDialog() {
    setRepositoryPendingDeletion(null);
    queueMicrotask(() => deleteDialogTriggerRef.current?.focus());
  }

  function handleRequestDeleteRecentRepository(
    event: MouseEvent<HTMLButtonElement>,
    repository: RecentRepository,
  ) {
    deleteDialogTriggerRef.current = event.currentTarget;
    deleteRecentRepositoryMutation.reset();
    setRepositoryPendingDeletion(repository);
  }

  function handleDeleteDialogBackdrop(event: MouseEvent<HTMLDivElement>) {
    if (
      event.target === event.currentTarget &&
      !deleteRecentRepositoryMutation.isPending
    ) {
      closeDeleteRecentRepositoryDialog();
    }
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
    const selectedProfile = profilesQuery.data?.find((profile) => profile.id === selectedProfileId);
    const request = toCreateReviewRequest({
      repositoryPath,
      scope: buildScope(),
      strategy,
      promptLocale: locale,
      ...(selectedProfile === undefined || isStrategyCustomized
        ? {}
        : { profileSource: { id: selectedProfile.id, revision: selectedProfile.revision } }),
    });
    createMutation.mutate({
      request,
      saveProfileName: shouldSaveProfile ? newProfileName.trim() : null,
    });
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
                  value={repositoryPath}
                  onChange={(event) => handleRepositoryPathChange(event.currentTarget.value)}
                  onKeyDown={handleRepositoryPathKeyDown}
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
                    <span className="field__label">{t("review.targetBranch")}</span>
                    <select
                      aria-label={t("review.targetBranch")}
                      className="field__control"
                      disabled={branchNames.length === 0}
                      value={commitBranchRef}
                      onChange={(event) => {
                        const targetRef = event.currentTarget.value;
                        selectedCommitBranchRef.current = targetRef;
                        setCommitBranchRef(targetRef);
                        setCommitTargetRef("");
                        setCommitBaseRef("");
                        setCommits([]);
                        setNextCommitOffset(null);
                        commitCatalogMutation.mutate({
                          targetRef,
                          offset: 0,
                          shouldReplace: true,
                        });
                      }}
                    >
                      {branchNames.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
                    </select>
                  </label>
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
                      disabled={commitCatalogMutation.isPending}
                      type="button"
                      onClick={() => commitCatalogMutation.mutate({
                        targetRef: commitBranchRef,
                        offset: nextCommitOffset,
                        shouldReplace: false,
                      })}
                    >
                      {commitCatalogMutation.isPending ? t("common.loading") : t("review.moreCommits")}
                    </button>
                  ) : null}
                  <label className="field">
                    <span className="field__label">{t("review.targetCommit")}</span>
                    <input
                      aria-label={t("review.targetCommit")}
                      className="field__control"
                      readOnly
                      value={commitTargetRef}
                    />
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
              <ShieldCheck aria-hidden="true" />
              <h2>{t("review.strategyTitle")}</h2>
            </div>
            {profilesQuery.isPending || reviewerCatalogQuery.isPending ? <p className="hint">{t("common.loading")}</p> : null}
            {profilesQuery.data !== undefined && profilesQuery.data.length > 0 ? (
              <div className="review-profile-snapshot">
                <ReviewProfilePicker
                  profiles={profilesQuery.data}
                  value={selectedProfileId}
                  onChange={(profile) => {
                    setSelectedProfileId(profile.id);
                    setStrategy({
                      reviewerSelection:
                        profile.strategy.reviewerSelection.mode === "adaptive"
                          ? { mode: "adaptive" }
                          : { mode: "fixed", reviewerVersions: [...profile.strategy.reviewerSelection.reviewerVersions] },
                    });
                    setIsStrategyCustomized(false);
                  }}
                />
                <ReviewStrategySummary strategy={strategy} />
                <button className="load-more-button" type="button" onClick={() => setIsCustomizingStrategy((value) => !value)}>
                  {t(isCustomizingStrategy ? "review.hideCustomization" : "review.changeCustomization")}
                </button>
                {isStrategyCustomized ? <p className="hint">{t("review.strategyCustomized")}</p> : null}
              </div>
            ) : null}
            {isCustomizingStrategy ? (
              <ReviewStrategyEditor
                catalog={reviewerCatalogQuery.data ?? []}
                validationErrors={strategyErrors}
                value={strategy}
                onChange={(value) => { setStrategy(value); setIsStrategyCustomized(true); }}
              />
            ) : null}
            {isCustomizingStrategy ? (
              <div className="review-save-profile">
                <label><input checked={shouldSaveProfile} type="checkbox" onChange={(event) => setShouldSaveProfile(event.currentTarget.checked)} /> {t("review.saveAsProfile")}</label>
                {shouldSaveProfile ? <input aria-label={t("review.newProfileName")} maxLength={120} placeholder={t("review.newProfilePlaceholder")} value={newProfileName} onChange={(event) => setNewProfileName(event.currentTarget.value)} /> : null}
              </div>
            ) : null}
            {profilesQuery.data?.length === 0 ? (
              <div className="alert" role="alert">{t("review.noProfiles")}</div>
            ) : null}
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
              <span>{strategy.reviewerSelection.mode === "adaptive" ? t("review.adaptivePlan") : t("review.agentCount", { count: strategy.reviewerSelection.reviewerVersions.length })}</span>
              <span>{hasActiveGateway ? t("review.gatewayReady") : t("review.gatewayMissing")}</span>
            </div>
            <button className="action-button" disabled={startDisabled} type="submit">
              {createMutation.isPending ? t("review.starting") : t("review.start")}
            </button>
          </div>
        </form>

        <aside className="panel panel--aside">
          <div className="panel__heading">
            <History aria-hidden="true" />
            <h2>{t("review.recentRepositories")}</h2>
          </div>
          {recentRepositoriesQuery.isLoading ? (
            <p className="hint">{t("common.loading")}</p>
          ) : null}
          {recentRepositoriesQuery.isError ? (
            <p className="hint">{t("review.recentRepositoriesLoadError")}</p>
          ) : null}
          {!recentRepositoriesQuery.isLoading &&
          !recentRepositoriesQuery.isError &&
          recentRepositoriesQuery.data?.length === 0 ? (
            <p className="hint">{t("review.recentRepositoriesEmpty")}</p>
          ) : null}
          <div className="recent-repository-list">
            {recentRepositoriesQuery.data?.map((repository) => {
              const isSelected = repositoryPath === repository.repository_path;
              return (
                <div
                  className={
                    isSelected
                      ? "recent-repository recent-repository--selected"
                      : "recent-repository"
                  }
                  key={repository.repository_path}
                >
                  <button
                    aria-label={t("review.selectRecentRepository", {
                      name: repository.repository_name,
                    })}
                    className="recent-repository__select"
                    type="button"
                    onClick={() => selectRepository(repository.repository_path)}
                  >
                    <span className="recent-repository__icon">
                      <FolderGit2 aria-hidden="true" />
                    </span>
                    <span className="recent-repository__content">
                      <strong>{repository.repository_name}</strong>
                      <code title={repository.repository_path}>{repository.repository_path}</code>
                      <time dateTime={repository.last_reviewed_at}>
                        {formatUserDateTime(repository.last_reviewed_at, locale)}
                      </time>
                    </span>
                    {isSelected ? (
                      <Check aria-hidden="true" className="recent-repository__check" />
                    ) : null}
                  </button>
                  <button
                    aria-label={t("review.deleteRecentRepository", {
                      name: repository.repository_name,
                    })}
                    className="recent-repository__delete"
                    disabled={deleteRecentRepositoryMutation.isPending}
                    title={t("common.delete")}
                    type="button"
                    onClick={(event) => handleRequestDeleteRecentRepository(event, repository)}
                  >
                    <Trash2 aria-hidden="true" />
                  </button>
                </div>
              );
            })}
          </div>
        </aside>
      </div>
      {repositoryPendingDeletion !== null ? (
        <div
          className="recent-repository-dialog-backdrop"
          role="presentation"
          onMouseDown={handleDeleteDialogBackdrop}
        >
          <section
            aria-describedby="recent-repository-dialog-description"
            aria-labelledby="recent-repository-dialog-title"
            aria-modal="true"
            className="recent-repository-dialog"
            role="dialog"
          >
            <div className="recent-repository-dialog__icon" aria-hidden="true">
              <Trash2 />
            </div>
            <div className="recent-repository-dialog__content">
              <h2 id="recent-repository-dialog-title">
                {t("review.deleteRecentRepositoryTitle")}
              </h2>
              <p id="recent-repository-dialog-description">
                {t("review.deleteRecentRepositoryConfirm", {
                  name: repositoryPendingDeletion.repository_name,
                })}
              </p>
              <code title={repositoryPendingDeletion.repository_path}>
                {repositoryPendingDeletion.repository_path}
              </code>
              {deleteRecentRepositoryMutation.isError ? (
                <p className="recent-repository-dialog__error" role="alert">
                  {t("review.deleteRecentRepositoryError")}
                </p>
              ) : null}
            </div>
            <div className="recent-repository-dialog__actions">
              <button
                disabled={deleteRecentRepositoryMutation.isPending}
                type="button"
                onClick={closeDeleteRecentRepositoryDialog}
              >
                {t("common.cancel")}
              </button>
              <button
                className="recent-repository-dialog__confirm"
                disabled={deleteRecentRepositoryMutation.isPending}
                ref={deleteDialogConfirmRef}
                type="button"
                onClick={() =>
                  deleteRecentRepositoryMutation.mutate(
                    repositoryPendingDeletion.repository_path,
                  )
                }
              >
                <Trash2 aria-hidden="true" />
                {deleteRecentRepositoryMutation.isPending
                  ? t("review.deletingRecentRepository")
                  : t("review.confirmDeleteRecentRepository")}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}
