import { useMutation } from "@tanstack/react-query";
import {
  BookOpenText,
  CirclePlay,
  FileCode2,
  Gauge,
  GitBranch,
  GitCommitVertical,
  Lock,
  Search,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { inspectRepository } from "../repositories/api";
import { createReview } from "./api";
import type {
  BranchScopeRequest,
  CommitScopeRequest,
  CreateReviewRequest,
  FullRepositoryScopeRequest,
  RepositoryInspectionResponse,
  ReviewMode,
  ScopeRequest,
  UncommittedScopeRequest,
} from "./types";
import "./NewReviewPage.css";

const CORRECTNESS_AGENT_REFERENCE = "correctness:v1";

const REVIEWER_ROWS = [
  {
    reference: CORRECTNESS_AGENT_REFERENCE,
    label: "Correctness",
    description: "Logic, edge cases, concurrency, and state-machine correctness.",
    enabled: true,
    status: "Enabled now",
    icon: ShieldCheck,
  },
  {
    reference: "security:v1",
    label: "Security",
    description: "Auth, secrets, injection, data exposure, and supply chain.",
    enabled: false,
    status: "Available in Phase 3",
    icon: Lock,
  },
  {
    reference: "performance:v1",
    label: "Performance",
    description: "Complexity, blocking work, memory, and resource usage.",
    enabled: false,
    status: "Available in Phase 3",
    icon: Gauge,
  },
  {
    reference: "maintainability:v1",
    label: "Maintainability",
    description: "Responsibilities, coupling, repetition, and testability.",
    enabled: false,
    status: "Available in Phase 3",
    icon: Wrench,
  },
  {
    reference: "testing:v1",
    label: "Testing",
    description: "Regression risk, edge cases, failure paths, and coverage.",
    enabled: false,
    status: "Available in Phase 3",
    icon: FileCode2,
  },
  {
    reference: "docs_style:v1",
    label: "Docs & Style",
    description: "Public contracts, documentation, naming, and repo rules.",
    enabled: false,
    status: "Available in Phase 3",
    icon: BookOpenText,
  },
  {
    reference: "cross_file:v1",
    label: "Cross-file",
    description: "Call chains, imports, compatibility, and downstream impact.",
    enabled: false,
    status: "Available in Phase 3",
    icon: GitCommitVertical,
  },
] as const;

const DEFAULT_BRANCH_BASE = "origin/main";
const DEFAULT_TARGET_REF = "HEAD";
const MODE_OPTIONS: Array<{ value: ReviewMode; label: string; note: string }> = [
  { value: "review", label: "REVIEW", note: "Enabled now" },
  { value: "fix", label: "FIX", note: "Available in Phase 5" },
];

type ScopeType = ScopeRequest["type"];

function buildScope(
  scopeType: ScopeType,
  includeWorkspaceChanges: boolean,
  branchBaseRef: string,
  branchTargetRef: string,
  commitBaseRef: string,
  commitTargetRef: string,
  fullTargetRef: string,
): ScopeRequest {
  if (scopeType === "branch") {
    const scope: BranchScopeRequest = {
      type: "branch",
      base_ref: branchBaseRef.trim(),
      target_ref: branchTargetRef.trim() || DEFAULT_TARGET_REF,
      include_workspace_changes: includeWorkspaceChanges,
    };
    return scope;
  }
  if (scopeType === "commit") {
    const scope: CommitScopeRequest = {
      type: "commit",
      base_commit: commitBaseRef.trim(),
      target_ref: commitTargetRef.trim() || DEFAULT_TARGET_REF,
      include_workspace_changes: includeWorkspaceChanges,
    };
    return scope;
  }
  if (scopeType === "full") {
    const scope: FullRepositoryScopeRequest = {
      type: "full",
      target_ref: fullTargetRef.trim() || DEFAULT_TARGET_REF,
      include_workspace_changes: includeWorkspaceChanges,
    };
    return scope;
  }
  const scope: UncommittedScopeRequest = {
    type: "uncommitted",
  };
  return scope;
}

function ScopeToggle({
  active,
  children,
  description,
  onClick,
}: {
  active: boolean;
  children: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      className={active ? "scope-toggle scope-toggle--active" : "scope-toggle"}
      type="button"
      onClick={onClick}
    >
      <span className="scope-toggle__title">{children}</span>
      <span className="scope-toggle__description">{description}</span>
    </button>
  );
}

function AgentRow({
  enabled,
  checked,
  description,
  icon: Icon,
  label,
  onChange,
  reference,
  status,
}: {
  enabled: boolean;
  checked: boolean;
  description: string;
  icon: typeof ShieldCheck;
  label: string;
  onChange: (checked: boolean) => void;
  reference: string;
  status: string;
}) {
  return (
    <label className={enabled ? "agent-row" : "agent-row agent-row--disabled"}>
      <span className="agent-row__leading">
        <Icon aria-hidden="true" />
      </span>
      <span className="agent-row__content">
        <span className="agent-row__headline">
          <span>{label}</span>
          <span className="agent-row__reference">{reference}</span>
        </span>
        <span className="agent-row__description">{description}</span>
      </span>
      <span className="agent-row__status">{status}</span>
      <input
        checked={checked}
        disabled={!enabled}
        className="agent-row__input"
        aria-label={label}
        type="checkbox"
        onChange={(event) => onChange(event.currentTarget.checked)}
      />
    </label>
  );
}

function ModeToggle({
  active,
  disabled,
  label,
  note,
  onClick,
}: {
  active: boolean;
  disabled: boolean;
  label: string;
  note: string;
  onClick: () => void;
}) {
  return (
    <button
      className={active ? "mode-toggle mode-toggle--active" : "mode-toggle"}
      type="button"
      disabled={disabled}
      onClick={onClick}
    >
      <span className="mode-toggle__label">{label}</span>
      <span className="mode-toggle__note">{note}</span>
    </button>
  );
}

export function NewReviewPage() {
  const navigate = useNavigate();
  const [repositoryPath, setRepositoryPath] = useState("");
  const [inspection, setInspection] = useState<RepositoryInspectionResponse | null>(null);
  const [scopeType, setScopeType] = useState<ScopeType>("branch");
  const [includeWorkspaceChanges, setIncludeWorkspaceChanges] = useState(false);
  const [branchBaseRef, setBranchBaseRef] = useState(DEFAULT_BRANCH_BASE);
  const [branchTargetRef, setBranchTargetRef] = useState("");
  const [commitBaseRef, setCommitBaseRef] = useState("");
  const [commitTargetRef, setCommitTargetRef] = useState(DEFAULT_TARGET_REF);
  const [fullTargetRef, setFullTargetRef] = useState(DEFAULT_TARGET_REF);
  const [correctnessEnabled, setCorrectnessEnabled] = useState(true);
  const [mode, setMode] = useState<ReviewMode>("review");

  const inspectMutation = useMutation({
    mutationFn: async () => inspectRepository(repositoryPath.trim()),
    onSuccess: (result) => {
      setInspection(result);
      if (scopeType === "branch" && branchTargetRef.trim() === "") {
        setBranchTargetRef(result.current_branch ?? DEFAULT_TARGET_REF);
      }
      if (scopeType === "commit" && commitTargetRef.trim() === "") {
        setCommitTargetRef(DEFAULT_TARGET_REF);
      }
      if (scopeType === "full" && fullTargetRef.trim() === "") {
        setFullTargetRef(DEFAULT_TARGET_REF);
      }
    },
  });

  const createMutation = useMutation({
    mutationFn: async (request: CreateReviewRequest) => createReview(request),
    onSuccess: (result) => {
      navigate(`/runs/${result.task_id}`);
    },
  });

  useEffect(() => {
    if (inspection === null) {
      return;
    }
    if (scopeType === "branch" && branchTargetRef.trim() === "") {
      setBranchTargetRef(inspection.current_branch ?? DEFAULT_TARGET_REF);
    }
    if (scopeType === "commit" && commitTargetRef.trim() === "") {
      setCommitTargetRef(DEFAULT_TARGET_REF);
    }
    if (scopeType === "full" && fullTargetRef.trim() === "") {
      setFullTargetRef(DEFAULT_TARGET_REF);
    }
  }, [inspection, scopeType, branchTargetRef, commitTargetRef, fullTargetRef]);

  const selectedAgents = correctnessEnabled ? [CORRECTNESS_AGENT_REFERENCE] : [];
  const startDisabled =
    inspection === null ||
    selectedAgents.length === 0 ||
    inspectMutation.isPending ||
    createMutation.isPending;
  const errorMessage =
    createMutation.error instanceof Error
      ? createMutation.error.message
      : inspectMutation.error instanceof Error
        ? inspectMutation.error.message
        : null;

  function handleStartReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inspection === null || selectedAgents.length === 0) {
      return;
    }
    createMutation.mutate({
      repository_path: repositoryPath.trim(),
      scope: buildScope(
        scopeType,
        includeWorkspaceChanges,
        branchBaseRef,
        branchTargetRef,
        commitBaseRef,
        commitTargetRef,
        fullTargetRef,
      ),
      selected_agents: selectedAgents,
      mode,
    });
  }

  return (
    <section className="new-review-page">
      <header className="new-review-page__header">
        <div className="new-review-page__eyebrow">Phase 0-2 review creation</div>
        <h1>New Review</h1>
        <p>
          Inspect a repository, pin the review scope, and launch the first correctness-only run
          from one contained workbench.
        </p>
      </header>

      <div className="new-review-page__grid">
        <form className="new-review-page__form" onSubmit={handleStartReview}>
          <section className="panel panel--primary">
            <div className="panel__heading">
              <Search aria-hidden="true" />
              <h2>Repository inspection</h2>
            </div>
            <div className="field-row field-row--path">
              <label className="field">
                <span className="field__label">Repository path</span>
                <input
                  autoComplete="off"
                  className="field__control"
                  placeholder="/srv/repos/app"
                  value={repositoryPath}
                  onChange={(event) => setRepositoryPath(event.currentTarget.value)}
                />
              </label>
              <button
                className="action-button action-button--secondary"
                disabled={repositoryPath.trim() === "" || inspectMutation.isPending}
                type="button"
                onClick={() => inspectMutation.mutate()}
              >
                Inspect
              </button>
            </div>

            {inspection === null ? (
              <p className="hint">
                Inspect a local repository to lock the path, branch, and HEAD before creating the
                task.
              </p>
            ) : (
              <dl className="inspection-summary">
                <div>
                  <dt>Repository</dt>
                  <dd>{inspection.display_path}</dd>
                </div>
                <div>
                  <dt>HEAD</dt>
                  <dd>{inspection.head_oid}</dd>
                </div>
                <div>
                  <dt>Branch</dt>
                  <dd>{inspection.current_branch ?? "Detached HEAD"}</dd>
                </div>
                <div>
                  <dt>Dirty</dt>
                  <dd>{inspection.is_dirty ? "Dirty working tree" : "Clean working tree"}</dd>
                </div>
              </dl>
            )}
          </section>

          <section className="panel">
            <div className="panel__heading">
              <GitBranch aria-hidden="true" />
              <h2>Scope</h2>
            </div>
            <div className="scope-toggle-grid" role="radiogroup" aria-label="Review scope">
              <ScopeToggle
                active={scopeType === "branch"}
                description="Compare a named branch range."
                onClick={() => setScopeType("branch")}
              >
                Branch diff
              </ScopeToggle>
              <ScopeToggle
                active={scopeType === "commit"}
                description="Inspect one commit against a pinned base."
                onClick={() => setScopeType("commit")}
              >
                Commit diff
              </ScopeToggle>
              <ScopeToggle
                active={scopeType === "uncommitted"}
                description="Review the current workspace delta."
                onClick={() => setScopeType("uncommitted")}
              >
                Uncommitted
              </ScopeToggle>
              <ScopeToggle
                active={scopeType === "full"}
                description="Pin the repository without a narrower diff."
                onClick={() => setScopeType("full")}
              >
                Full repository
              </ScopeToggle>
            </div>

            <div className="scope-fields">
              {scopeType === "branch" ? (
                <>
                  <label className="field">
                    <span className="field__label">Base branch</span>
                    <input
                      className="field__control"
                      value={branchBaseRef}
                      onChange={(event) => setBranchBaseRef(event.currentTarget.value)}
                    />
                  </label>
                  <label className="field">
                    <span className="field__label">Target branch</span>
                    <input
                      className="field__control"
                      value={branchTargetRef}
                      onChange={(event) => setBranchTargetRef(event.currentTarget.value)}
                    />
                  </label>
                </>
              ) : null}

              {scopeType === "commit" ? (
                <>
                  <label className="field">
                    <span className="field__label">Base commit</span>
                    <input
                      className="field__control"
                      placeholder="HEAD~1"
                      value={commitBaseRef}
                      onChange={(event) => setCommitBaseRef(event.currentTarget.value)}
                    />
                  </label>
                  <label className="field">
                    <span className="field__label">Target ref</span>
                    <input
                      className="field__control"
                      value={commitTargetRef}
                      onChange={(event) => setCommitTargetRef(event.currentTarget.value)}
                    />
                  </label>
                </>
              ) : null}

              {scopeType === "full" ? (
                <label className="field">
                  <span className="field__label">Target ref</span>
                  <input
                    className="field__control"
                    value={fullTargetRef}
                    onChange={(event) => setFullTargetRef(event.currentTarget.value)}
                  />
                </label>
              ) : null}

              {scopeType !== "uncommitted" ? (
                <label className="field field--toggle">
                  <input
                    checked={includeWorkspaceChanges}
                    type="checkbox"
                    onChange={(event) => setIncludeWorkspaceChanges(event.currentTarget.checked)}
                  />
                  <span>Include workspace changes</span>
                </label>
              ) : null}
            </div>
          </section>

          <section className="panel">
            <div className="panel__heading">
              <CirclePlay aria-hidden="true" />
              <h2>Mode</h2>
            </div>
            <div className="mode-toggle-grid" role="radiogroup" aria-label="Review mode">
              {MODE_OPTIONS.map((option) => (
                <ModeToggle
                  active={mode === option.value}
                  disabled={option.value === "fix"}
                  key={option.value}
                  label={option.label}
                  note={option.note}
                  onClick={() => setMode(option.value)}
                />
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel__heading">
              <ShieldCheck aria-hidden="true" />
              <h2>Reviewers</h2>
            </div>
            <div className="reviewer-list">
              {REVIEWER_ROWS.map((row) => (
                <AgentRow
                  checked={row.enabled ? correctnessEnabled : false}
                  description={row.description}
                  enabled={row.enabled}
                  icon={row.icon}
                  key={row.reference}
                  label={row.label}
                  reference={row.reference}
                  status={row.status}
                  onChange={(checked) => {
                    if (row.reference === CORRECTNESS_AGENT_REFERENCE) {
                      setCorrectnessEnabled(checked);
                    }
                  }}
                />
              ))}
            </div>
          </section>

          {errorMessage !== null ? (
            <div className="alert" role="alert">
              {errorMessage}
            </div>
          ) : null}

          <div className="form-actions">
            <div className="form-actions__summary">
              <span>{inspection === null ? "Inspection required" : "Inspection ready"}</span>
              <span>{selectedAgents.length} enabled agent</span>
            </div>
            <button className="action-button" disabled={startDisabled} type="submit">
              Start review
            </button>
          </div>
        </form>

        <aside className="panel panel--aside">
          <div className="panel__heading">
            <FileCode2 aria-hidden="true" />
            <h2>Inspection summary</h2>
          </div>
          {inspection === null ? (
            <p className="hint">
              The summary appears after inspection and stays visible while you tune the scope.
            </p>
          ) : (
            <dl className="inspector-card">
              <div>
                <dt>Branch</dt>
                <dd>{inspection.current_branch ?? "Detached HEAD"}</dd>
              </div>
              <div>
                <dt>HEAD</dt>
                <dd>{inspection.head_oid}</dd>
              </div>
              <div>
                <dt>Dirty</dt>
                <dd>{inspection.is_dirty ? "Yes" : "No"}</dd>
              </div>
              <div>
                <dt>Repository ID</dt>
                <dd>{inspection.repository_id}</dd>
              </div>
              <div>
                <dt>Realpath hash</dt>
                <dd>{inspection.repository_realpath_hash}</dd>
              </div>
              <div>
                <dt>Common dir hash</dt>
                <dd>{inspection.git_common_dir_hash}</dd>
              </div>
            </dl>
          )}
          <div className="aside-note">
            Only the correctness reviewer is active in this phase. The rest of the catalog is shown
            so the form matches the future review topology.
          </div>
        </aside>
      </div>
    </section>
  );
}
