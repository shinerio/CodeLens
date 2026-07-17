# CodeLens Phase 0-2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first vertical slice of CodeLens: pin a review target, create a task-owned detached worktree and immutable snapshot for all four scopes, resolve instructions, run one Correctness Reviewer in a restart-safe singleton worker, and display validated findings in the Web UI.

**Architecture:** The backend uses DDD-oriented packages with dependency inversion between domain/application code and Git, filesystem, SQLite, FastAPI, and OpenAI adapters. Every ReviewTask pins `base_oid/head_oid`, creates one application-owned detached worktree, and freezes its Snapshot there. FastAPI persists commands and streams outbox events; exactly one worker per data directory executes restart-safe DAG checkpoints, while multiple tasks may run concurrently in-process. The React frontend consumes REST and SSE APIs and never accesses repositories directly.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite WAL, OpenAI Agents SDK, React, TypeScript, Vite, TanStack Query, React Router, pytest, Vitest, Playwright.

## Global Constraints

- Python is exactly the 3.12 minor line: `>=3.12,<3.13`.
- The domain layer must not import FastAPI, SQLAlchemy, OpenAI Agents SDK, Git libraries, or MCP libraries.
- All Git and process calls use argument arrays; never use `shell=True`.
- Every ReviewTask creates one detached worktree under the application data directory; Reviewers read it through a read-only boundary and never access the user's working tree.
- Same-repository tasks may run concurrently in separate worktrees. Only worktree add/remove/repair uses a short per-repository lock.
- REVIEW may register/remove CodeLens-owned Git worktree metadata, but never writes the user's working tree, index, branches, tags, or other worktrees.
- All review scopes apply Git-native `.gitignore` semantics, including tracked files that match current ignore rules.
- `ReviewSnapshot` separates `target_paths` from read-only `context_paths`; both pass the same ignore and safety filters.
- Root `AGENTS.md`, applicable `REVIEW.md`, and `<full-filename>.review.md` are control inputs and load even when the rule file itself is ignored.
- Every model finding must pass Pydantic schema, repository path, line range, changed-hunk, and excerpt-hash validation.
- The main workflow is application-controlled; the model cannot decide which configured reviewer nodes run.
- API keys and secrets must not be stored in SQLite, events, logs, artifacts, or `RunContext`.
- `auth=none` binds only `127.0.0.1`; a non-loopback host or a second Worker for one data directory must fail at startup.
- Raw model output is persisted as a hash-verified opaque Artifact before validation; `SUCCEEDED` is committed atomically with Findings and outbox events.
- Use TDD for every behavior task: failing test, observed failure, minimal implementation, passing test, focused commit.
- The authoritative design is `docs/superpowers/specs/2026-07-17-codelens-review-app-design.md`.

## 2026-07-17 Correctness Amendment

This section is normative and replaces contradictory snippets later in this plan:

- Task 1 verifies the existing Git repository and `AGENTS.md`; it never initializes or replaces them.
- Task 3 rejects non-loopback unauthenticated binds and `max_workers != 1`.
- Tasks 4–9 add `ReviewTarget(base_oid, head_oid, overlay_hash)`, `TaskWorktree`, ownership records,
  a per-common-dir in-process lock, and `ReviewWorktreePort`. Every Snapshot is created from a newly created
  task worktree. The old direct-copy-from-source flow in Task 9 must not be implemented.
- Task 9 integration tests start two reviews for different refs in the same real repository, assert distinct
  worktree paths/OIDs, run both beyond provisioning concurrently, and prove cleanup never removes a user worktree.
- Task 11 stores jobs and DAG checkpoints but has no lease columns or claim/reclaim API. A singleton OS lock protects
  one Worker per data directory; startup deterministically requeues interrupted nodes.
- Tasks 13–14 introduce a hash-verified opaque run-output store before OpenAI execution. A run transitions
  `RUNNING -> OUTPUT_SAVED -> VALIDATING -> SUCCEEDED`; the final transition, Findings, and outbox event share one
  database transaction. Context is budgeted before file bodies are read.
- Task 14 runs multiple ReviewTasks and their Agent nodes through structured concurrency with global and per-task
  semaphores. Repository locks are never held during Snapshot analysis, model calls, validation, or SSE delivery.
- Task 17 includes crash injection immediately before and after `OUTPUT_SAVED`, worktree mutation detection,
  same-repository task concurrency, second-Worker rejection, and loopback bind rejection.

Do not execute a later code block that uses `SqlJobQueue.claim(... lease_duration ...)`, reads the mutable source
path after task creation, sets `AgentRun` directly to `SUCCEEDED` before Finding persistence, or enables
`0.0.0.0 + auth=none`; replace it with the contract above and the authoritative design.

---

## File And Module Map

```text
backend/
  pyproject.toml
  alembic.ini
  migrations/
  src/codelens/
    bootstrap/              # settings, CLI, application composition
    shared/domain/          # IDs, clock, base errors
    workspace/domain/       # scopes, snapshots, repository models, ports
    workspace/application/  # inspect, target pinning, worktree lifecycle, snapshot creation
    workspace/infrastructure/ # Git CLI, owned worktree, filesystem snapshot adapters
    instruction_policy/domain/
    instruction_policy/application/
    instruction_policy/infrastructure/
    reviewer_catalog/domain/
    findings/domain/
    review/domain/
    review/application/     # commands, queries, orchestrator
    review/infrastructure/  # OpenAI runtime, SQLAlchemy repositories
    interface/http/         # FastAPI app and routers
    worker/                 # singleton worker and durable DAG recovery
  tests/
    unit/
    integration/
    contract/
    fixtures/
frontend/
  src/
    app/                    # providers, router, shell
    features/repositories/
    features/reviews/
    features/findings/
    shared/api/
  e2e/
docs/
  superpowers/specs/
  superpowers/plans/
```

The package boundaries above are fixed for Phase 0-2. Do not create generic `utils.py`, `services.py`, or `models.py` at the package root.

---

### Task 1: Preserve Repository Governance And Add Backend Toolchain

**Files:**
- Create: `.gitignore`
- Create: `.python-version`
- Read only: `AGENTS.md`
- Create: `backend/pyproject.toml`
- Create: `backend/src/codelens/__init__.py`
- Create: `backend/tests/unit/test_package.py`

**Interfaces:**
- Consumes: the approved design document.
- Produces: importable `codelens` package and repeatable `uv` test commands used by every backend task.

- [ ] **Step 1: Verify and preserve the existing repository**

Run:

```bash
git rev-parse --show-toplevel
test -f AGENTS.md
git status --short --branch
```

Expected: the first command prints the current CodeLens root, `AGENTS.md` exists, and status is captured before edits. Do not run `git init`, rewrite Git history, or replace repository instructions.

- [ ] **Step 2: Add repository ignores and Python version**

Create `.gitignore`:

```gitignore
.DS_Store
.idea/
.vscode/
.superpowers/
.venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.py[cod]
*.sqlite3
.data/
artifacts/
backend/dist/
backend/*.egg-info/
frontend/node_modules/
frontend/dist/
frontend/playwright-report/
frontend/test-results/
```

Create `.python-version`:

```text
3.12
```

- [ ] **Step 3: Add backend package metadata**

Create `backend/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "codelens-review"
version = "0.1.0"
description = "Local multi-agent code review workbench"
requires-python = ">=3.12,<3.13"
dependencies = [
  "aiosqlite",
  "alembic",
  "fastapi",
  "markdown-it-py",
  "openai-agents",
  "pathspec",
  "pydantic>=2,<3",
  "pydantic-settings>=2,<3",
  "python-frontmatter",
  "sqlalchemy>=2,<3",
  "uvicorn[standard]",
]

[project.scripts]
codelens-review = "codelens.bootstrap.cli:main"

[dependency-groups]
dev = [
  "httpx",
  "mypy",
  "pytest",
  "pytest-asyncio",
  "pytest-cov",
  "ruff",
]

[tool.hatch.build.targets.wheel]
packages = ["src/codelens"]

[tool.pytest.ini_options]
addopts = "-q --strict-markers --strict-config"
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["codelens"]
```

Create `backend/src/codelens/__init__.py`:

```python
__all__ = ["__version__"]
__version__ = "0.1.0"
```

- [ ] **Step 4: Write and run the package smoke test**

Create `backend/tests/unit/test_package.py`:

```python
from codelens import __version__


def test_package_version() -> None:
    assert __version__ == "0.1.0"
```

Run:

```bash
uv sync --project backend
uv run --project backend pytest backend/tests/unit/test_package.py -v
uv run --project backend ruff check backend
```

Expected: one passing test and no Ruff violations.

- [ ] **Step 5: Verify durable repository instructions**

Run:

```bash
rg -n "uv run --project backend|pnpm --dir frontend|REVIEW 模式|shell=True" AGENTS.md
```

Expected: the existing repository instructions cover backend/frontend commands, REVIEW isolation, and safe subprocess use. If governance needs a future change, review it as a separate focused task; this bootstrap must not overwrite it.

- [ ] **Step 6: Commit the backend foundation**

```bash
git add .gitignore .python-version backend docs
git commit -m "chore: initialize codelens backend workspace"
```

Expected: first commit succeeds and `git status --short` is empty.

---

### Task 2: Initialize Frontend Toolchain And Application Shell

**Files:**
- Create: `pnpm-workspace.yaml`
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/App.test.tsx`
- Create: `frontend/src/app/styles.css`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/testSetup.ts`

**Interfaces:**
- Consumes: backend will eventually expose `/api/health` and review APIs.
- Produces: `App` component, frontend test/build commands, and navigation slots for later feature pages.

- [ ] **Step 1: Scaffold React TypeScript and install runtime dependencies**

Run:

```bash
corepack enable
pnpm create vite frontend --template react-ts
pnpm --dir frontend add @tanstack/react-query react-router-dom lucide-react
pnpm --dir frontend add -D @testing-library/jest-dom @testing-library/react @testing-library/user-event jsdom vitest
```

Expected: `frontend/package.json` and `pnpm-lock.yaml` exist.

- [ ] **Step 2: Configure workspace and Vitest**

Create `pnpm-workspace.yaml`:

```yaml
packages:
  - frontend
```

Replace `frontend/vite.config.ts` with:

```typescript
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/testSetup.ts",
  },
});
```

Create `frontend/src/testSetup.ts`:

```typescript
import "@testing-library/jest-dom/vitest";
```

Ensure `frontend/package.json` has these scripts:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "lint": "eslint ."
  }
}
```

- [ ] **Step 3: Write the failing application shell test**

Create `frontend/src/app/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("shows the review workbench navigation", () => {
    render(<App />, { wrapper: MemoryRouter });
    expect(screen.getByText("CodeLens")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New review" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Runs" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review agents" })).toBeInTheDocument();
  });
});
```

Run:

```bash
pnpm --dir frontend test
```

Expected: FAIL because `src/app/App.tsx` does not exist.

- [ ] **Step 4: Implement the minimal workbench shell**

Create `frontend/src/app/App.tsx`:

```tsx
import { NavLink, Outlet } from "react-router-dom";
import { Bot, History, PlayCircle, Settings } from "lucide-react";

import "./styles.css";

export function App() {
  return (
    <div className="app-shell">
      <header className="topbar"><strong>CodeLens</strong></header>
      <aside className="sidebar" aria-label="Primary navigation">
        <NavLink to="/"><PlayCircle aria-hidden />New review</NavLink>
        <NavLink to="/runs"><History aria-hidden />Runs</NavLink>
        <NavLink to="/agents"><Bot aria-hidden />Review agents</NavLink>
        <NavLink to="/settings"><Settings aria-hidden />Settings</NavLink>
      </aside>
      <main className="main-content"><Outlet /></main>
    </div>
  );
}
```

Create `frontend/src/app/styles.css`:

```css
:root { font-family: Inter, ui-sans-serif, system-ui, sans-serif; color: #17212b; }
* { box-sizing: border-box; }
body { margin: 0; background: #f7f9fb; }
.app-shell { min-height: 100vh; display: grid; grid-template: 48px 1fr / 176px 1fr; }
.topbar { grid-column: 1 / -1; display: flex; align-items: center; padding: 0 16px; background: #17212b; color: white; }
.sidebar { padding: 12px 8px; border-right: 1px solid #d8e0e7; background: #eef2f5; }
.sidebar a { display: flex; gap: 8px; align-items: center; padding: 9px 10px; color: #4d5b67; text-decoration: none; }
.sidebar a.active { background: white; color: #17212b; font-weight: 700; }
.sidebar svg { width: 16px; height: 16px; }
.main-content { min-width: 0; background: white; }
@media (max-width: 760px) { .app-shell { grid-template: 48px auto 1fr / 1fr; } .sidebar { display: flex; overflow-x: auto; border-right: 0; border-bottom: 1px solid #d8e0e7; } }
```

Create `frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import { App } from "./app/App";

const router = createBrowserRouter([{ path: "/", element: <App />, children: [{ index: true, element: <h1>New review</h1> }] }]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><RouterProvider router={router} /></React.StrictMode>,
);
```

- [ ] **Step 5: Verify and commit the frontend foundation**

Run:

```bash
pnpm --dir frontend test
pnpm --dir frontend build
```

Expected: tests pass and Vite creates `frontend/dist/`.

```bash
git add pnpm-workspace.yaml pnpm-lock.yaml frontend
git commit -m "feat: add frontend workbench shell"
```

---

### Task 3: Add Settings, CLI, And Health Endpoint

**Files:**
- Create: `backend/src/codelens/bootstrap/settings.py`
- Create: `backend/src/codelens/bootstrap/cli.py`
- Create: `backend/src/codelens/interface/http/app.py`
- Create: `backend/tests/unit/bootstrap/test_settings.py`
- Create: `backend/tests/contract/http/test_health.py`

**Interfaces:**
- Consumes: `codelens` package and FastAPI dependency.
- Produces: `Settings`, `create_app(settings: Settings) -> FastAPI`, and `codelens-review start` entry point.

- [ ] **Step 1: Write failing settings tests**

Create `backend/tests/unit/bootstrap/test_settings.py`:

```python
from pathlib import Path

import pytest
from pydantic import ValidationError

from codelens.bootstrap.settings import Settings


def test_local_settings_allow_empty_repository_roots(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, host="127.0.0.1")
    assert settings.repository_roots == ()


def test_unauthenticated_remote_bind_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        Settings(data_dir=tmp_path, host="0.0.0.0", repository_roots=(tmp_path,))


def test_local_bind_normalizes_repository_roots(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    settings = Settings(data_dir=tmp_path, host="127.0.0.1", repository_roots=(root,))
    assert settings.repository_roots == (root.resolve(),)


def test_multiple_workers_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="one Worker"):
        Settings(data_dir=tmp_path, max_workers=2)
```

Run:

```bash
uv run --project backend pytest backend/tests/unit/bootstrap/test_settings.py -v
```

Expected: FAIL because `Settings` does not exist.

- [ ] **Step 2: Implement validated settings**

Create `backend/src/codelens/bootstrap/settings.py`:

```python
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODELENS_", env_nested_delimiter="__")

    data_dir: Path = Path.home() / ".local" / "share" / "codelens-review"
    host: str = "127.0.0.1"
    port: int = 8765
    auth: Literal["none"] = "none"
    max_workers: int = 1
    max_active_reviews: int = 4
    max_active_agent_runs: int = 8
    max_agent_runs_per_review: int = 4
    repository_roots: tuple[Path, ...] = ()
    database_url: str | None = None
    openai_model: str = ""
    initialize_schema: bool = True

    @field_validator("repository_roots")
    @classmethod
    def normalize_roots(cls, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        return tuple(root.expanduser().resolve() for root in roots)

    @model_validator(mode="after")
    def validate_single_user_runtime(self) -> "Settings":
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("auth=none requires a loopback host")
        if self.max_workers != 1:
            raise ValueError("the first release supports exactly one Worker")
        if self.max_active_reviews < 1 or self.max_active_agent_runs < 1:
            raise ValueError("review and Agent concurrency limits must be positive")
        if not 1 <= self.max_agent_runs_per_review <= self.max_active_agent_runs:
            raise ValueError("per-review Agent limit must not exceed the global limit")
        return self

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{self.data_dir / 'codelens.sqlite3'}"
```

- [ ] **Step 3: Write the failing health contract test**

Create `backend/tests/contract/http/test_health.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def test_health_reports_ready(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "auth": "none"}
```

Run:

```bash
uv run --project backend pytest backend/tests/contract/http/test_health.py -v
```

Expected: FAIL because `create_app` does not exist.

- [ ] **Step 4: Implement FastAPI composition and CLI**

Create `backend/src/codelens/interface/http/app.py`:

```python
from fastapi import FastAPI

from codelens.bootstrap.settings import Settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="CodeLens Review API", version="0.1.0")
    app.state.settings = settings

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ready", "auth": settings.auth}

    return app
```

Create `backend/src/codelens/bootstrap/cli.py`:

```python
import argparse

import uvicorn

from codelens.bootstrap.settings import Settings
from codelens.interface.http.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="codelens-review")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("repository_root", nargs="*")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    settings = Settings(
        host=args.host,
        port=args.port,
        repository_roots=tuple(args.repository_root),
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
```

- [ ] **Step 5: Verify and commit settings/bootstrap**

Run:

```bash
uv run --project backend pytest backend/tests/unit/bootstrap backend/tests/contract/http/test_health.py -v
uv run --project backend mypy backend/src
uv run --project backend ruff check backend
```

Expected: all tests pass, mypy and Ruff report no errors.

```bash
git add backend
git commit -m "feat: add validated server bootstrap"
```

---

### Task 4: Define Workspace Domain Models And Ports

**Files:**
- Create: `backend/src/codelens/shared/domain/errors.py`
- Create: `backend/src/codelens/workspace/domain/models.py`
- Create: `backend/src/codelens/workspace/domain/ports.py`
- Create: `backend/tests/unit/workspace/test_models.py`

**Interfaces:**
- Consumes: Python standard library only.
- Produces: `ReviewScope`, `ReviewTarget`, `TaskWorktree`, `ReviewSnapshot`, `SnapshotManifest`, `ChangeIndex`, `RepositoryFingerprint`, `WorkspaceGitPort`, and `ReviewWorktreePort`.

- [ ] **Step 1: Write failing scope and manifest tests**

Create `backend/tests/unit/workspace/test_models.py`:

```python
from codelens.workspace.domain.models import (
    BranchScope,
    ReviewMode,
    SnapshotManifest,
)


def test_branch_scope_carries_base_and_target_refs() -> None:
    scope = BranchScope(
        base_ref="origin/main",
        target_ref="feature/invoice-rounding",
        include_workspace_changes=False,
    )
    assert scope.base_ref == "origin/main"
    assert scope.target_ref == "feature/invoice-rounding"


def test_manifest_separates_targets_from_context() -> None:
    manifest = SnapshotManifest(
        target_paths=("src/payment.py",),
        context_paths=("src/payment.py", "tests/test_payment.py"),
        excluded_paths=(),
    )
    assert manifest.is_target("src/payment.py")
    assert not manifest.is_target("tests/test_payment.py")
    assert manifest.is_context("tests/test_payment.py")


def test_review_mode_value_is_stable() -> None:
    assert ReviewMode.REVIEW.value == "review"
```

Run:

```bash
uv run --project backend pytest backend/tests/unit/workspace/test_models.py -v
```

Expected: FAIL because the domain models do not exist.

- [ ] **Step 2: Implement immutable domain models**

Create `backend/src/codelens/shared/domain/errors.py`:

```python
class DomainError(Exception):
    code = "domain_error"


class InvalidRepositoryError(DomainError):
    code = "invalid_repository"


class SnapshotStaleError(DomainError):
    code = "snapshot_stale"


class WorktreeOwnershipError(DomainError):
    code = "worktree_ownership"


class WorktreeMutatedError(DomainError):
    code = "worktree_mutated"
```

Create `backend/src/codelens/workspace/domain/models.py`:

```python
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, TypeAlias


class ReviewMode(str, Enum):
    REVIEW = "review"
    FIX = "fix"


@dataclass(frozen=True)
class BranchScope:
    base_ref: str
    target_ref: str
    include_workspace_changes: bool = False
    type: Literal["branch"] = "branch"


@dataclass(frozen=True)
class CommitScope:
    base_commit: str
    target_ref: str = "HEAD"
    include_workspace_changes: bool = False
    type: Literal["commit"] = "commit"


@dataclass(frozen=True)
class UncommittedScope:
    type: Literal["uncommitted"] = "uncommitted"


@dataclass(frozen=True)
class FullRepositoryScope:
    target_ref: str = "HEAD"
    include_workspace_changes: bool = False
    type: Literal["full"] = "full"


ReviewScope: TypeAlias = BranchScope | CommitScope | UncommittedScope | FullRepositoryScope


@dataclass(frozen=True)
class ExcludedPath:
    path: str
    reason: str
    source: str | None = None


@dataclass(frozen=True)
class SnapshotManifest:
    target_paths: tuple[str, ...]
    context_paths: tuple[str, ...]
    excluded_paths: tuple[ExcludedPath, ...]
    instruction_paths: tuple[str, ...] = ()

    def is_target(self, path: str) -> bool:
        return path in self.target_paths

    def is_context(self, path: str) -> bool:
        return path in self.context_paths


@dataclass(frozen=True)
class RepositoryFingerprint:
    head_sha: str
    index_hash: str
    worktree_hash: str


@dataclass(frozen=True)
class ReviewTarget:
    base_oid: str
    head_oid: str
    overlay_hash: str | None


@dataclass(frozen=True)
class TaskWorktree:
    worktree_id: str
    task_id: str
    repository_common_dir_hash: str
    root: Path
    head_oid: str
    ownership_token_hash: str


@dataclass(frozen=True)
class ChangedHunk:
    hunk_id: str
    path: str
    start_line: int
    end_line: int
    side: Literal["old", "new"]
    excerpt_hash: str


@dataclass(frozen=True)
class ChangeIndex:
    hunks: tuple[ChangedHunk, ...]

    def contains(self, path: str, start_line: int, end_line: int, side: str) -> bool:
        return any(
            hunk.path == path
            and hunk.side == side
            and start_line >= hunk.start_line
            and end_line <= hunk.end_line
            for hunk in self.hunks
        )


@dataclass(frozen=True)
class ReviewSnapshot:
    snapshot_id: str
    worktree: TaskWorktree
    target: ReviewTarget
    fingerprint: RepositoryFingerprint
    manifest: SnapshotManifest
    change_index: ChangeIndex
```

- [ ] **Step 3: Define the Git-facing port**

Create `backend/src/codelens/workspace/domain/ports.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from codelens.workspace.domain.models import RepositoryFingerprint, ReviewScope, TaskWorktree


@dataclass(frozen=True)
class RepositoryInfo:
    path: Path
    head_sha: str
    current_branch: str | None
    is_dirty: bool


@dataclass(frozen=True)
class ScopePlan:
    base_oid: str
    head_oid: str
    target_paths: tuple[str, ...]
    capture_workspace_overlay: bool
    warnings: tuple[str, ...] = ()


class WorkspaceGitPort(Protocol):
    async def inspect(self, repository: Path) -> RepositoryInfo:
        raise NotImplementedError

    async def plan_scope(self, repository: Path, scope: ReviewScope) -> ScopePlan:
        raise NotImplementedError

    async def list_context_paths(self, repository: Path) -> tuple[str, ...]:
        raise NotImplementedError

    async def fingerprint(self, repository: Path) -> RepositoryFingerprint:
        raise NotImplementedError

    async def read_file_at_revision(self, repository: Path, revision: str, path: str) -> bytes:
        raise NotImplementedError

    async def unified_diff(self, worktree: Path, base_oid: str) -> str:
        raise NotImplementedError


class ReviewWorktreePort(Protocol):
    async def create(
        self,
        task_id: str,
        repository: Path,
        head_oid: str,
    ) -> TaskWorktree:
        raise NotImplementedError

    async def verify_ownership(self, worktree: TaskWorktree) -> None:
        raise NotImplementedError

    async def remove_owned(self, worktree: TaskWorktree) -> None:
        raise NotImplementedError
```

- [ ] **Step 4: Run domain checks and commit**

```bash
uv run --project backend pytest backend/tests/unit/workspace/test_models.py -v
uv run --project backend mypy backend/src/codelens/workspace backend/src/codelens/shared
git add backend/src/codelens/shared backend/src/codelens/workspace backend/tests/unit/workspace
git commit -m "feat: define workspace domain contracts"
```

Expected: tests and mypy pass; commit succeeds.

---

### Task 5: Implement Safe Git CLI And Repository Inspection

**Files:**
- Create: `backend/src/codelens/workspace/infrastructure/git_cli.py`
- Create: `backend/src/codelens/workspace/application/inspect_repository.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/fixtures/__init__.py`
- Create: `backend/tests/fixtures/git_repository.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/integration/workspace/test_repository_inspection.py`

**Interfaces:**
- Consumes: `RepositoryInfo`, `InvalidRepositoryError`, `Settings.repository_roots`.
- Produces: `GitCli.run(repository, *args, stdin=None, ok_codes=(0,))` and `RepositoryInspector.inspect(path)`.

- [ ] **Step 1: Create a real Git repository fixture**

Create empty `backend/tests/__init__.py` and `backend/tests/fixtures/__init__.py` files so the
fixture plugin has a stable import path.

Create `backend/tests/fixtures/git_repository.py`:

```python
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    return repo
```

Add this to `backend/tests/conftest.py`:

```python
pytest_plugins = ["tests.fixtures.git_repository"]
```

- [ ] **Step 2: Write failing repository inspection tests**

Create `backend/tests/integration/workspace/test_repository_inspection.py`:

```python
from pathlib import Path

import pytest

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.application.inspect_repository import RepositoryInspector
from codelens.workspace.infrastructure.git_cli import GitCli


async def test_inspects_repository(git_repository: Path) -> None:
    inspector = RepositoryInspector(GitCli(), repository_roots=(git_repository.parent,))
    info = await inspector.inspect(git_repository)
    assert info.path == git_repository.resolve()
    assert info.current_branch == "main"
    assert len(info.head_sha) == 40
    assert not info.is_dirty


async def test_rejects_path_outside_repository_roots(git_repository: Path) -> None:
    inspector = RepositoryInspector(GitCli(), repository_roots=(git_repository / "nested",))
    with pytest.raises(InvalidRepositoryError, match="outside configured repository roots"):
        await inspector.inspect(git_repository)
```

Run:

```bash
uv run --project backend pytest backend/tests/integration/workspace/test_repository_inspection.py -v
```

Expected: FAIL because `GitCli` and `RepositoryInspector` do not exist.

- [ ] **Step 3: Implement the subprocess adapter**

Create `backend/src/codelens/workspace/infrastructure/git_cli.py`:

```python
import asyncio
from dataclasses import dataclass
from pathlib import Path

from codelens.shared.domain.errors import InvalidRepositoryError


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class GitCli:
    async def run(
        self,
        repository: Path,
        *args: str,
        stdin: bytes | None = None,
        ok_codes: tuple[int, ...] = (0,),
    ) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repository),
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(stdin)
        if process.returncode not in ok_codes:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise InvalidRepositoryError(message or "git command failed")
        return CommandResult(process.returncode, stdout, stderr)
```

- [ ] **Step 4: Implement repository root containment and inspection**

Create `backend/src/codelens/workspace/application/inspect_repository.py`:

```python
from pathlib import Path

from codelens.shared.domain.errors import InvalidRepositoryError
from codelens.workspace.domain.ports import RepositoryInfo
from codelens.workspace.infrastructure.git_cli import GitCli


class RepositoryInspector:
    def __init__(self, git: GitCli, repository_roots: tuple[Path, ...]) -> None:
        self._git = git
        self._roots = tuple(root.resolve() for root in repository_roots)

    async def inspect(self, path: Path) -> RepositoryInfo:
        repository = path.expanduser().resolve()
        if self._roots and not any(repository.is_relative_to(root) for root in self._roots):
            raise InvalidRepositoryError("repository is outside configured repository roots")
        if not repository.is_dir():
            raise InvalidRepositoryError("repository directory does not exist")

        top = await self._git.run(repository, "rev-parse", "--show-toplevel")
        top_path = Path(top.stdout.decode().strip()).resolve()
        if top_path != repository:
            raise InvalidRepositoryError("path must be a Git repository root")

        head = await self._git.run(repository, "rev-parse", "HEAD")
        branch = await self._git.run(
            repository, "symbolic-ref", "--short", "-q", "HEAD", ok_codes=(0, 1)
        )
        status = await self._git.run(repository, "status", "--porcelain=v1", "-z")
        return RepositoryInfo(
            path=repository,
            head_sha=head.stdout.decode().strip(),
            current_branch=branch.stdout.decode().strip() or None,
            is_dirty=bool(status.stdout),
        )
```

- [ ] **Step 5: Verify safety and commit**

Run:

```bash
uv run --project backend pytest backend/tests/integration/workspace/test_repository_inspection.py -v
uv run --project backend ruff check backend/src/codelens/workspace backend/tests/integration/workspace
git add backend
git commit -m "feat: inspect repositories through safe git adapter"
```

Expected: both integration tests pass; no command uses a shell string.

---

### Task 6: Apply Git-Native Ignore Rules To Tracked And Untracked Files

**Files:**
- Modify: `backend/src/codelens/workspace/domain/models.py`
- Create: `backend/src/codelens/workspace/infrastructure/git_ignore.py`
- Create: `backend/tests/integration/workspace/test_git_ignore.py`

**Interfaces:**
- Consumes: `GitCli.run(..., ok_codes=(0, 1))`.
- Produces: `GitIgnoreResolver.resolve(repository: Path, paths: tuple[str, ...]) -> IgnoreResolution`.

- [ ] **Step 1: Write failing ignore tests, including a tracked ignored file**

Create `backend/tests/integration/workspace/test_git_ignore.py`:

```python
import subprocess
from pathlib import Path

from codelens.workspace.infrastructure.git_cli import GitCli
from codelens.workspace.infrastructure.git_ignore import GitIgnoreResolver


async def test_excludes_tracked_file_matching_current_gitignore(git_repository: Path) -> None:
    tracked = git_repository / "tracked.log"
    tracked.write_text("old log\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repository), "add", "tracked.log"], check=True)
    subprocess.run(
        ["git", "-C", str(git_repository), "commit", "-m", "track log"],
        check=True,
        capture_output=True,
    )
    (git_repository / ".gitignore").write_text("*.log\n!important.log\n", encoding="utf-8")
    (git_repository / "important.log").write_text("keep\n", encoding="utf-8")

    result = await GitIgnoreResolver(GitCli()).resolve(
        git_repository,
        ("tracked.log", "important.log", "README.md"),
    )

    assert result.included == ("README.md", "important.log")
    assert result.excluded[0].path == "tracked.log"
    assert result.excluded[0].source == ".gitignore:1:*.log"


async def test_honors_nested_gitignore(git_repository: Path) -> None:
    generated = git_repository / "src" / "generated"
    generated.mkdir(parents=True)
    (git_repository / "src" / ".gitignore").write_text("generated/\n", encoding="utf-8")
    (generated / "api.py").write_text("value = 1\n", encoding="utf-8")

    result = await GitIgnoreResolver(GitCli()).resolve(
        git_repository, ("src/generated/api.py",)
    )
    assert result.included == ()
    assert result.excluded[0].source.startswith("src/.gitignore:1:")
```

Run:

```bash
uv run --project backend pytest backend/tests/integration/workspace/test_git_ignore.py -v
```

Expected: FAIL because `GitIgnoreResolver` does not exist.

- [ ] **Step 2: Add ignore result types**

Append to `backend/src/codelens/workspace/domain/models.py`:

```python
@dataclass(frozen=True)
class IgnoreResolution:
    included: tuple[str, ...]
    excluded: tuple[ExcludedPath, ...]
```

- [ ] **Step 3: Implement NUL-safe Git ignore parsing**

Create `backend/src/codelens/workspace/infrastructure/git_ignore.py`:

```python
from pathlib import Path

from codelens.workspace.domain.models import ExcludedPath, IgnoreResolution
from codelens.workspace.infrastructure.git_cli import GitCli


class GitIgnoreResolver:
    def __init__(self, git: GitCli) -> None:
        self._git = git

    async def resolve(self, repository: Path, paths: tuple[str, ...]) -> IgnoreResolution:
        normalized = tuple(sorted(dict.fromkeys(path.replace("\\", "/") for path in paths)))
        if not normalized:
            return IgnoreResolution((), ())
        stdin = b"\0".join(path.encode("utf-8") for path in normalized) + b"\0"
        result = await self._git.run(
            repository,
            "check-ignore",
            "--no-index",
            "-v",
            "-z",
            "--stdin",
            stdin=stdin,
            ok_codes=(0, 1),
        )
        fields = result.stdout.split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        if len(fields) % 4 != 0:
            raise ValueError("unexpected git check-ignore -z output")

        matches: dict[str, ExcludedPath] = {}
        for offset in range(0, len(fields), 4):
            source, line, pattern, path = (field.decode("utf-8") for field in fields[offset : offset + 4])
            matches[path] = ExcludedPath(path=path, reason="gitignore", source=f"{source}:{line}:{pattern}")

        return IgnoreResolution(
            included=tuple(path for path in normalized if path not in matches),
            excluded=tuple(matches[path] for path in normalized if path in matches),
        )
```

- [ ] **Step 4: Verify tracked, nested, and negated semantics**

Run:

```bash
uv run --project backend pytest backend/tests/integration/workspace/test_git_ignore.py -v
uv run --project backend pytest backend/tests/unit/workspace -v
```

Expected: all tests pass; `tracked.log` is excluded despite being tracked and `important.log` is included by negation.

- [ ] **Step 5: Commit ignore behavior**

```bash
git add backend/src/codelens/workspace backend/tests/integration/workspace
git commit -m "feat: enforce git-native review ignores"
```

---

### Task 7: Pin Review Targets And Plan All Four Scopes

**Files:**
- Create: <code>backend/src/codelens/workspace/application/plan_scope.py</code>
- Create: <code>backend/src/codelens/workspace/infrastructure/git_workspace.py</code>
- Test: <code>backend/tests/integration/workspace/test_scope_planner.py</code>

**Interfaces:**
- Consumes: <code>WorkspaceGitPort</code>, <code>ReviewScope</code>, and a contained repository.
- Produces: <code>ScopePlanner.plan(repository, scope) -&gt; ScopePlan</code> with full base/head OIDs, target path/change metadata, overlay eligibility, and warnings.

- [ ] **Step 1: Write failing real-Git scope tests**

Use one repository with main plus two feature branches. Cover:

- branch scope: base is merge-base of <code>base_ref</code> and <code>target_ref</code>, head is target OID;
- commit-baseline scope: selected base commit to selected target OID, including non-ancestor warning;
- uncommitted scope: base/head are current HEAD and overlay is required;
- full scope: target is the selected head OID and all eligible paths are candidates;
- a non-current target branch never receives current checkout dirty files;
- <code>include_workspace_changes=true</code> on a target OID different from current HEAD is rejected;
- ref movement after planning does not change the returned OIDs.

Run:

~~~bash
uv run --project backend pytest backend/tests/integration/workspace/test_scope_planner.py -v
~~~

Expected: FAIL because the planner does not exist.

- [ ] **Step 2: Implement safe full-OID resolution**

Use argument-array Git calls with <code>--end-of-options</code> where supported and reject option-like refs before invocation. Resolve commits with <code>rev-parse --verify &lt;ref&gt;^{commit}</code>, then store only full OIDs in executable task state. Preserve labels separately for UI.

For branch scope, compute merge-base from the two OIDs. For a commit baseline, check ancestry and emit a stable warning for direct diff. For uncommitted scope, require the current checkout HEAD. For full scope, enumerate from the target tree; overlay candidates are added only during Task 9 capture.

- [ ] **Step 3: Build deterministic change metadata**

Return tracked add/modify/delete/rename paths from <code>git diff --name-status -z</code> between pinned OIDs. If overlay is eligible, include staged/unstaged tracked paths plus allowed untracked candidates, but do not read their bodies yet. Sort normalized repository-relative POSIX paths and reject absolute, parent-traversal, NUL, or symlink-escape inputs.

- [ ] **Step 4: Verify and commit**

Run:

~~~bash
uv run --project backend pytest backend/tests/integration/workspace/test_scope_planner.py -v
uv run --project backend mypy backend/src/codelens/workspace
uv run --project backend ruff check backend
git add backend
git commit -m "feat: pin review targets and scopes"
~~~

Expected: all scopes are reproducible from OIDs and cannot mix dirty state from another checkout.

---

### Task 8: Resolve Root, Directory, And File Review Instructions

**Files:**
- Create: `backend/src/codelens/instruction_policy/domain/models.py`
- Create: `backend/src/codelens/instruction_policy/infrastructure/markdown_parser.py`
- Create: `backend/src/codelens/instruction_policy/application/resolver.py`
- Create: `backend/tests/unit/instruction_policy/test_resolver.py`

**Interfaces:**
- Consumes: repository root and target path.
- Produces: `InstructionResolver.resolve(repository, target_path) -> ResolvedInstructionSet` with ordered documents and structured excludes.

- [ ] **Step 1: Write failing precedence and control-input tests**

Create `backend/tests/unit/instruction_policy/test_resolver.py`:

```python
from pathlib import Path

from codelens.instruction_policy.application.resolver import InstructionResolver
from codelens.instruction_policy.infrastructure.markdown_parser import MarkdownInstructionParser


def test_resolves_ordered_instruction_chain_even_when_rule_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("REVIEW.md\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Repository conventions", encoding="utf-8")
    (tmp_path / "REVIEW.md").write_text("Root review", encoding="utf-8")
    target_dir = tmp_path / "src" / "payments"
    target_dir.mkdir(parents=True)
    (tmp_path / "src" / "REVIEW.md").write_text("Source rules", encoding="utf-8")
    (target_dir / "REVIEW.md").write_text("Payment rules", encoding="utf-8")
    (target_dir / "payment.py.review.md").write_text("File rules", encoding="utf-8")
    (target_dir / "payment.py").write_text("pass\n", encoding="utf-8")

    resolved = InstructionResolver(MarkdownInstructionParser()).resolve(
        tmp_path, "src/payments/payment.py"
    )
    assert [document.relative_path for document in resolved.documents] == [
        "AGENTS.md",
        "REVIEW.md",
        "src/REVIEW.md",
        "src/payments/REVIEW.md",
        "src/payments/payment.py.review.md",
    ]


def test_parses_frontmatter_and_skip_heading(tmp_path: Path) -> None:
    (tmp_path / "REVIEW.md").write_text(
        "---\nexclude:\n  - generated/**\n---\n## Skip\n- vendor/**\n- Explain why generated clients are noisy\n",
        encoding="utf-8",
    )
    resolved = InstructionResolver(MarkdownInstructionParser()).resolve(tmp_path, "src/app.py")
    assert resolved.excludes == ("generated/**", "vendor/**")
    assert len(resolved.warnings) == 1


def test_scopes_nested_excludes_to_rule_directory(tmp_path: Path) -> None:
    rule_dir = tmp_path / "src" / "payments"
    rule_dir.mkdir(parents=True)
    (rule_dir / "REVIEW.md").write_text(
        "---\nexclude:\n  - generated/**\n---\nPayment rules\n", encoding="utf-8"
    )
    resolved = InstructionResolver(MarkdownInstructionParser()).resolve(
        tmp_path, "src/payments/api.py"
    )
    assert resolved.excludes == ("src/payments/generated/**",)
```

Run:

```bash
uv run --project backend pytest backend/tests/unit/instruction_policy/test_resolver.py -v
```

Expected: FAIL because instruction policy modules do not exist.

- [ ] **Step 2: Define immutable instruction models**

Create `backend/src/codelens/instruction_policy/domain/models.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class InstructionDocument:
    relative_path: str
    content: str
    content_hash: str


@dataclass(frozen=True)
class ParsedInstruction:
    content: str
    excludes: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedInstructionSet:
    documents: tuple[InstructionDocument, ...]
    excludes: tuple[str, ...]
    warnings: tuple[str, ...]
```

- [ ] **Step 3: Implement structured Markdown parsing**

Create `backend/src/codelens/instruction_policy/infrastructure/markdown_parser.py`:

```python
from fnmatch import translate
from re import compile as compile_pattern

import frontmatter
from markdown_it import MarkdownIt

from codelens.instruction_policy.domain.models import ParsedInstruction


def _is_path_rule(value: str) -> bool:
    candidate = value.strip()
    if not candidate or " " in candidate or candidate.startswith(("/", "..")):
        return False
    compile_pattern(translate(candidate))
    return True


class MarkdownInstructionParser:
    def __init__(self) -> None:
        self._markdown = MarkdownIt()

    def parse(self, text: str) -> ParsedInstruction:
        post = frontmatter.loads(text)
        excludes = [str(value) for value in post.metadata.get("exclude", [])]
        warnings: list[str] = []
        tokens = self._markdown.parse(post.content)
        in_skip = False
        for index, token in enumerate(tokens):
            if token.type == "heading_open":
                inline = tokens[index + 1] if index + 1 < len(tokens) else None
                in_skip = bool(inline and inline.type == "inline" and inline.content.strip().lower() == "skip")
            elif in_skip and token.type == "inline" and token.level >= 2:
                value = token.content.strip()
                if _is_path_rule(value):
                    excludes.append(value)
                elif value:
                    warnings.append(f"non-path Skip entry kept as prompt only: {value}")
        valid = tuple(dict.fromkeys(value for value in excludes if _is_path_rule(value)))
        return ParsedInstruction(post.content, valid, tuple(warnings))
```

- [ ] **Step 4: Implement the exact resolution order**

Create `backend/src/codelens/instruction_policy/application/resolver.py`:

```python
import hashlib
from pathlib import Path

from pathspec import PathSpec

from codelens.instruction_policy.domain.models import (
    InstructionDocument,
    ResolvedInstructionSet,
)
from codelens.instruction_policy.infrastructure.markdown_parser import MarkdownInstructionParser


class InstructionResolver:
    def __init__(self, parser: MarkdownInstructionParser) -> None:
        self._parser = parser

    def resolve(self, repository: Path, target_path: str) -> ResolvedInstructionSet:
        target = PurePosixPath(target_path)
        candidates = [Path("AGENTS.md"), Path("REVIEW.md")]
        current = Path()
        for part in target.parent.parts:
            current /= part
            candidates.append(current / "REVIEW.md")
        candidates.append(Path(target_path + ".review.md"))

        documents: list[InstructionDocument] = []
        excludes: list[str] = []
        warnings: list[str] = []
        for relative in dict.fromkeys(candidates):
            absolute = repository / relative
            if not absolute.is_file():
                continue
            text = absolute.read_text(encoding="utf-8")
            parsed = self._parser.parse(text)
            documents.append(
                InstructionDocument(
                    relative_path=relative.as_posix(),
                    content=text,
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                )
            )
            base = relative.parent.as_posix()
            excludes.extend(
                pattern if base == "." else f"{base}/{pattern}"
                for pattern in parsed.excludes
            )
            warnings.extend(parsed.warnings)
        return ResolvedInstructionSet(
            documents=tuple(documents),
            excludes=tuple(dict.fromkeys(excludes)),
            warnings=tuple(warnings),
        )


class StructuredSkipMatcher:
    def excludes(self, path: str, instructions: ResolvedInstructionSet) -> bool:
        if not instructions.excludes:
            return False
        spec = PathSpec.from_lines("gitwildmatch", instructions.excludes)
        return spec.match_file(path)
```

- [ ] **Step 5: Verify precedence and commit**

```bash
uv run --project backend pytest backend/tests/unit/instruction_policy/test_resolver.py -v
uv run --project backend mypy backend/src/codelens/instruction_policy
git add backend/src/codelens/instruction_policy backend/tests/unit/instruction_policy
git commit -m "feat: resolve hierarchical review instructions"
```

Expected: ignored control files still load, file-specific rules are last, and only valid path entries become deterministic excludes.

---

### Task 9: Provision Owned Review Worktrees And Freeze Snapshots

**Files:**
- Create: <code>backend/src/codelens/workspace/application/worktree_lifecycle.py</code>
- Create: <code>backend/src/codelens/workspace/application/capture_overlay.py</code>
- Modify: <code>backend/src/codelens/workspace/domain/ports.py</code>
- Create: <code>backend/src/codelens/workspace/application/create_snapshot.py</code>
- Create: <code>backend/src/codelens/workspace/infrastructure/git_worktrees.py</code>
- Create: <code>backend/src/codelens/workspace/infrastructure/worktree_ownership.py</code>
- Create: <code>backend/src/codelens/workspace/infrastructure/input_artifacts.py</code>
- Create: <code>backend/src/codelens/workspace/infrastructure/change_index.py</code>
- Create: <code>backend/src/codelens/workspace/infrastructure/filesystem_snapshot.py</code>
- Test: <code>backend/tests/integration/workspace/test_review_worktrees.py</code>
- Test: <code>backend/tests/integration/workspace/test_snapshot_creation.py</code>

**Interfaces:**
- Consumes: the already pinned <code>ScopePlan</code>, <code>WorkspaceGitPort</code>, <code>GitCli</code>,
  <code>GitIgnoreResolver</code>, and <code>InstructionResolver</code>.
- Produces: <code>InputArtifactPort</code>,
  <code>ReviewInputCaptureService.capture(...) -&gt; ReviewTarget</code> with an immutable overlay Artifact,
  <code>WorktreeRegistryPort</code>, <code>GitReviewWorktreeManager.create/remove_owned</code>, and
  <code>SnapshotService.create(task_id, repository, target, scope_plan) -&gt; ReviewSnapshot</code>.

- [ ] **Step 1: Write failing real-Git worktree isolation tests**

Create two feature branches with distinct committed files in one temporary repository. Start both provisioning calls behind an event and assert:

~~~python
first, second = await asyncio.gather(
    service.create("review_a", repository, branch_scope_a),
    service.create("review_b", repository, branch_scope_b),
)
assert first.worktree.root != second.worktree.root
assert first.target.head_oid == feature_a_oid
assert second.target.head_oid == feature_b_oid
assert (first.worktree.root / "feature_a.py").exists()
assert not (first.worktree.root / "feature_b.py").exists()
assert (second.worktree.root / "feature_b.py").exists()
~~~

Add a user-created worktree before the test. After removing both CodeLens worktrees, assert the user worktree still exists and <code>git worktree list --porcelain</code> still lists it. Record Git arguments and assert no invocation contains <code>worktree prune</code>.

For an uncommitted scope, modify tracked files, add an allowed untracked file, and change an ignored file. Capture
the input, then modify the source again before worktree provisioning. Assert the task worktree contains the captured
tracked/untracked state, not the later edit, while the ignored file is absent. Mutate the source during capture and
assert <code>SNAPSHOT_STALE</code> with no durable task/job or Agent event.

Add an ignored, untracked <code>REVIEW.md</code> and an ignored file-level rule. Assert both are captured as control
inputs and affect instruction resolution even though an ordinary ignored source file is excluded.

Run:

~~~bash
uv run --project backend pytest backend/tests/integration/workspace/test_review_worktrees.py -v
~~~

Expected: FAIL because target resolution, ownership, and worktree lifecycle do not exist.

- [ ] **Step 2: Validate the pinned target and capture its eligible overlay**

Never resolve mutable display refs in the Worker. Before task/job creation, verify the full base/head OIDs and that
<code>include_workspace_changes</code> targets the source checkout HEAD. Capture the overlay with a fingerprint
before and after and persist it through the opaque Artifact port:

~~~text
tracked overlay = git diff --binary HEAD
untracked paths = git ls-files --others --exclude-standard -z
overlay hash = sha256(patch bytes + sorted(path, mode, content hash))
~~~

Define <code>InputArtifactPort</code> in the Workspace domain and an app-data-contained filesystem adapter returning
an opaque reference plus SHA-256. Task 11 makes the same adapter database-backed without changing references.
The Artifact contains the binary tracked patch plus contained, size-capped untracked entries and a canonical manifest.
Discover applicable root/directory/file control-rule candidates independently and include their current bytes even
when Git ignore excludes them; label them control inputs so they never become target paths by accident.
If before/after fingerprint differs, discard the staging Artifact and retry once; a second mismatch raises
<code>SnapshotStaleError</code>. After the task/job transaction, the source is never read again. Worker provisioning
rereads the Artifact by opaque reference, verifies its hash, then uses bounded stdin to apply the tracked patch and
materialize verified untracked entries in the task worktree.

- [ ] **Step 3: Implement scoped ownership and short repository locking**

Store each checkout under <code>&lt;data_dir&gt;/worktrees/&lt;task_id&gt;/checkout</code> and an ownership marker beside it.
Define <code>WorktreeRegistryPort</code> for the authoritative record; Task 9 tests inject an in-memory registry and
Task 11 supplies the SQLite adapter. Registry and marker contain task ID, checkout realpath hash, canonical Git
common-dir hash, head OID, random ownership-token hash, and creation time. Creation is not considered successful
until both records exist; compensation removes only the just-created owned checkout.

Key the in-process lock by canonical common-dir hash. Hold it only around target revalidation plus:

~~~text
git worktree add --detach <exact-checkout-path> <head_oid>
git worktree remove --force <exact-checkout-path>
~~~

Before removal, require the database record, ownership record, realpath, common-dir hash, and token hash to match. A mismatch quarantines the directory and raises <code>WorktreeOwnershipError</code>. Never call global prune or mutate another worktree.

- [ ] **Step 4: Build Manifest and ChangeIndex from the task worktree**

All ignore checks, instruction discovery, file reads, context discovery, and diff indexing use
<code>TaskWorktree.root</code>. The source repository path is available only to the pre-task target/overlay capture;
it is absent from the durable Review execution context. File tools expose a content view that rejects <code>.git</code>
and Manifest-external paths; later container mounts mask the worktree's <code>.git</code> administrative file.

Each Manifest entry records path, kind, mode, size, SHA-256, symlink target, and origin. The Snapshot records worktree ID/path hash, common-dir hash, base/head OIDs, overlay hash, instruction/profile hashes, ChangeIndex hash, included/context/excluded entries, and exclusion reasons. Persist a content-addressed Snapshot Artifact for history/recovery before the worktree becomes eligible for cleanup.

- [ ] **Step 5: Detect reviewer mutation and recover owned worktrees**

Before and after each Reviewer node, hash the Manifest entries visible to that node. A change raises <code>WORKTREE_MUTATED</code>, stops that run, and quarantines the worktree. On Worker startup:

1. keep a complete owned worktree referenced by a non-terminal task;
2. reconstruct a missing checkout from pinned OIDs plus the persisted overlay when hashes match;
3. quarantine mismatched ownership;
4. remove an unreferenced owned checkout through the scoped remove operation;
5. ignore every user-created worktree.

- [ ] **Step 6: Verify the worktree and Snapshot gate**

Run:

~~~bash
uv run --project backend pytest backend/tests/integration/workspace -v
uv run --project backend mypy backend/src/codelens/workspace
uv run --project backend ruff check backend
~~~

Expected: all four scopes use owned worktrees; same-repository feature reviews overlap after provisioning; dirty capture is stable; mutation is detected; cleanup is ownership-scoped.

- [ ] **Step 7: Commit the worktree Snapshot slice**

~~~bash
git add backend
git commit -m "feat: isolate every review in an owned worktree"
~~~

---

### Task 10: Define Review, AgentRun, And Finding Contracts

**Files:**
- Create: <code>backend/src/codelens/reviewer_catalog/domain/models.py</code>
- Create: <code>backend/src/codelens/findings/domain/models.py</code>
- Create: <code>backend/src/codelens/review/domain/models.py</code>
- Create: <code>backend/src/codelens/review/domain/agent_run.py</code>
- Create: <code>backend/src/codelens/review/domain/ports.py</code>
- Test: <code>backend/tests/unit/review/test_review_task.py</code>
- Test: <code>backend/tests/unit/review/test_agent_run.py</code>
- Test: <code>backend/tests/unit/findings/test_finding_schema.py</code>

**Interfaces:**
- Consumes: <code>ReviewScope</code> and <code>ReviewTarget</code>.
- Produces: immutable <code>AgentVersion</code>, <code>FindingBatch</code>, <code>ReviewTask</code>, <code>AgentRun</code>, <code>UnvalidatedAgentOutput</code>, and domain ports.

- [ ] **Step 1: Write failing state and identity tests**

Assert the ReviewTask state sequence includes <code>PROVISIONING_WORKTREE</code> before Snapshot. Assert terminal states cannot reopen and cancellation is valid from every non-terminal state.

AgentRun identity is derived from task ID, Agent version, pass index, shard ID, and logical attempt group. Even in the single-Agent MVP, include all dimensions so Phase 3/4 do not require a destructive uniqueness migration. Assert two shards or passes never share a run ID.

Assert allowed run transitions are:

~~~text
PENDING -> RUNNING -> OUTPUT_SAVED -> VALIDATING -> SUCCEEDED
PENDING/RUNNING -> CANCELED
RUNNING -> FAILED/TIMED_OUT
FAILED/TIMED_OUT -> PENDING only when retry policy allows
~~~

- [ ] **Step 2: Implement standard-library Review domain models**

<code>ReviewTask</code> stores repository ID/hash, original display refs, pinned base/head OIDs, scope, selected immutable Agent references, worktree/snapshot IDs, status, timestamps, and cancellation request. Domain methods enforce forward transitions; infrastructure cannot assign arbitrary status strings.

<code>AgentRun</code> stores the complete node identity, logical attempt group, execution attempt count, output Artifact reference/hash, usage, error code, and status. <code>succeed()</code> is not a public entity method; success is produced by the atomic persistence boundary after Finding validation.

- [ ] **Step 3: Implement strict Pydantic Finding contracts**

Keep SourceLocation, Evidence, Finding, and FindingBatch frozen and versioned. Validate confidence, line ranges, enums, nonempty evidence, and bounded text fields at the boundary. Finding IDs and fingerprints are application-derived from normalized validated content, not trusted from model output. Allow deleted-file old-side locations and require changed-hunk or explicit change-propagation evidence.

- [ ] **Step 4: Define runtime and persistence ports**

The domain-facing runtime port returns:

~~~python
@dataclass(frozen=True)
class UnvalidatedAgentOutput:
    canonical_bytes: bytes
    response_ids: tuple[str, ...]
    model_name: str
    input_tokens: int
    output_tokens: int
~~~

It does not return trusted Findings. Separate ports persist the unvalidated Artifact, load it by opaque reference, validate a batch, and atomically complete an AgentRun with Findings/outbox. Domain modules import only standard library and domain value objects.

- [ ] **Step 5: Verify contracts**

Run:

~~~bash
uv run --project backend pytest backend/tests/unit/review backend/tests/unit/findings -v
uv run --project backend mypy backend/src/codelens/review/domain backend/src/codelens/findings/domain backend/src/codelens/reviewer_catalog/domain
uv run --project backend ruff check backend
~~~

Expected: transition, identity, schema, and dependency-boundary tests pass.

- [ ] **Step 6: Commit the domain contracts**

~~~bash
git add backend
git commit -m "feat: define restart-safe review contracts"
~~~

---

### Task 11: Persist Tasks, Worktrees, Checkpoints, Events, Artifacts, And Findings

**Files:**
- Create: <code>backend/src/codelens/review/infrastructure/database.py</code>
- Create: <code>backend/src/codelens/review/infrastructure/tables.py</code>
- Create: <code>backend/src/codelens/review/infrastructure/repositories.py</code>
- Create: <code>backend/src/codelens/review/infrastructure/run_artifacts.py</code>
- Create: <code>backend/migrations/versions/0001_review_mvp.py</code>
- Test: <code>backend/tests/integration/review/test_sqlite_store.py</code>

**Interfaces:**
- Consumes: <code>ReviewTask</code>, <code>TaskWorktree</code>, <code>AgentRun</code>, <code>FindingBatch</code>, and domain repository ports.
- Produces: <code>SqlReviewStore</code>, <code>SqlWorktreeRegistry</code>, <code>SqlJobQueue</code>,
  <code>SqlCheckpointStore</code>, <code>SqlEventOutbox</code>, and <code>FilesystemRunArtifactStore</code>.

- [ ] **Step 1: Write failing atomicity and restart tests**

Use a real temporary SQLite database and assert that task, queued job, and <code>review.created</code> event are inserted in one transaction. Add tests for:

- a uniqueness constraint allowing one job per task;
- two different tasks for the same repository and different worktree IDs;
- ordered SSE events after a supplied event ID;
- a failed transaction leaving no partial Finding or success event;
- startup recovery changing interrupted jobs/nodes to resumable states without touching terminal nodes;
- output Artifact bytes surviving database/process reopen and failing closed on hash mismatch.

Representative recovery assertions:

~~~python
await checkpoints.mark_running("review_1", "correctness:v1:0:root")
await checkpoints.mark_output_saved("review_2", node_key, artifact_ref, artifact_hash)
await store.recover_after_singleton_restart()

assert (await checkpoints.get("review_1", node_key)).status == "pending"
assert (await checkpoints.get("review_2", node_key)).status == "output_saved"
~~~

Run:

~~~bash
uv run --project backend pytest backend/tests/integration/review/test_sqlite_store.py -v
~~~

Expected: FAIL because the stores and migration do not exist.

- [ ] **Step 2: Create the schema without Worker leases**

The migration creates:

- <code>review_tasks</code> with repository/common-dir hashes, scope JSON, pinned base/head OIDs, status, selected immutable versions, timestamps, and cancellation flag;
- <code>task_worktrees</code> with task ID, owned path hash, common-dir hash, head OID, ownership-token hash, lifecycle status, and timestamps;
- <code>jobs</code> with task ID, <code>queued/running/completed/failed/canceled</code> status and timestamps;
- <code>dag_checkpoints</code> keyed by task ID plus node key, with status, logical attempt group, artifact reference/hash, error code, and timestamps;
- <code>events</code>, <code>artifacts</code>, and <code>findings</code> with foreign keys and deterministic uniqueness.

Do not add lease owner, lease expiry, heartbeat, generation, or fencing columns. Enable SQLite WAL, foreign keys, a bounded busy timeout, and retry only whole idempotent transactions on <code>SQLITE_BUSY</code>.

- [ ] **Step 3: Implement singleton queue and checkpoint transitions**

<code>create_with_job</code> inserts the task, worktree placeholder, job, and first event atomically. Since the runtime enforces one Worker, <code>next_queued</code> only performs an atomic queued-to-running transition. On singleton Worker startup, <code>recover_after_singleton_restart</code>:

- returns interrupted task/job states to queued;
- returns <code>RUNNING</code> nodes without an output Artifact to pending;
- retains <code>OUTPUT_SAVED</code> and <code>VALIDATING</code> nodes for replay from Artifact;
- never reopens <code>SUCCEEDED</code> or another terminal state.

All state transitions validate the expected prior state in the update predicate.

- [ ] **Step 4: Implement opaque hash-verified Artifacts**

<code>FilesystemRunArtifactStore.write_bytes</code> allocates a random opaque ID, writes through a contained staging path, fsyncs, atomically renames, and stores SHA-256 metadata. <code>read_bytes</code> resolves only through the database mapping and verifies size/hash before returning bytes. API routes never accept paths. Redact log/event summaries before persistence; raw model output is not logged or returned by default.

- [ ] **Step 5: Implement the atomic success boundary**

Provide one repository method that, in a single transaction, validates an <code>OUTPUT_SAVED</code> or <code>VALIDATING</code> checkpoint, inserts deterministic Finding IDs with conflict rejection, marks the node <code>SUCCEEDED</code>, and appends <code>agent.succeeded</code>. There is no public sequence that can mark success before Finding persistence.

- [ ] **Step 6: Verify migration and recovery**

Run:

~~~bash
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend pytest backend/tests/integration/review/test_sqlite_store.py -v
uv run --project backend mypy backend/src/codelens/review
uv run --project backend ruff check backend
~~~

Expected: atomicity, Artifact verification, duplicate suppression, and singleton restart recovery tests pass; schema inspection finds no lease columns.

- [ ] **Step 7: Commit durable singleton persistence**

~~~bash
git add backend
git commit -m "feat: persist restart-safe review checkpoints"
~~~

---

### Task 12: Expose Pinned-Target Review APIs And SSE

**Files:**
- Create: <code>backend/src/codelens/review/application/commands.py</code>
- Create: <code>backend/src/codelens/interface/http/dto.py</code>
- Create: <code>backend/src/codelens/interface/http/dependencies.py</code>
- Create: <code>backend/src/codelens/interface/http/routers/repositories.py</code>
- Create: <code>backend/src/codelens/interface/http/routers/reviews.py</code>
- Modify: <code>backend/src/codelens/interface/http/app.py</code>
- Test: <code>backend/tests/contract/http/test_reviews_api.py</code>

**Interfaces:**
- Consumes: <code>RepositoryInspector</code>, <code>ReviewTargetResolver</code>, <code>SqlReviewStore</code>, and <code>SqlEventOutbox</code>.
- Produces: repository inspection, review creation/query/cancel/report endpoints, and resumable SSE.

- [ ] **Step 1: Write failing API contract tests**

Cover all four discriminated scopes. A branch request contains <code>base_ref</code>, <code>target_ref</code>, and <code>include_workspace_changes</code>. Assert a 202 response includes full pinned <code>base_oid/head_oid</code>, selected immutable Agent references, and <code>created</code> status.

Create two requests against different feature refs in the same repository and assert both are accepted with different head OIDs; the API must not reject a task merely because another task uses the same repository.

Reject:

- no selected Agent;
- a missing/non-Git/out-of-root path;
- an unknown or ambiguous ref;
- workspace overlay when target OID is not the current checkout HEAD;
- Fix mode before Phase 5;
- filesystem paths supplied as Artifact/worktree IDs.

Run:

~~~bash
uv run --project backend pytest backend/tests/contract/http/test_reviews_api.py -v
~~~

Expected: FAIL because DTOs and routes do not exist.

- [ ] **Step 2: Define validated request and response DTOs**

Use Pydantic discriminated unions. Resolve filesystem input to a contained repository before the application command. DTOs expose repository ID/hash and display path but never the owned worktree realpath or Artifact backing path. The create response includes:

~~~json
{
  "task_id": "review_...",
  "status": "created",
  "scope_type": "branch",
  "base_oid": "<40-or-64-hex>",
  "head_oid": "<40-or-64-hex>",
  "selected_agents": ["correctness:v1"],
  "worktree_status": "pending"
}
~~~

- [ ] **Step 3: Pin refs and create the durable command atomically**

<code>CreateReviewHandler</code> resolves refs once, invokes <code>ReviewInputCaptureService</code> when an overlay is
needed, then atomically creates ReviewTask/job with full OIDs plus overlay Artifact reference/hash. If capture is
stale, no task/job is created. The Worker never resolves a mutable branch name or rereads the user workspace to
decide what code to review. Store original ref labels only for display/audit. If the database transaction fails,
delete the unreferenced just-created input Artifact; startup retention also removes verified orphan staging inputs.

- [ ] **Step 4: Implement cancellation and resumable SSE**

Cancellation sets a durable flag and appends an event atomically. SSE validates <code>Last-Event-ID</code>, emits ordered outbox rows, sends bounded keep-alives, and stops after a terminal task event. It sends summaries plus opaque Artifact IDs, never raw output or storage paths.

- [ ] **Step 5: Apply local HTTP safety defaults**

The application accepts command requests only with JSON content type, validates Host and Origin against loopback defaults, disables broad CORS, and rejects state-changing cross-origin form requests. These controls are active before model configuration routes exist.

- [ ] **Step 6: Verify API and SSE contracts**

Run:

~~~bash
uv run --project backend pytest backend/tests/contract/http -v
uv run --project backend mypy backend/src/codelens/interface backend/src/codelens/review/application
uv run --project backend ruff check backend
~~~

Expected: pinned-target, same-repository multi-task, cancellation, SSE resume, content-type, Host, and Origin tests pass.

- [ ] **Step 7: Commit the API slice**

~~~bash
git add backend
git commit -m "feat: expose pinned review task APIs"
~~~

---

### Task 13: Build Bounded Context And A Checkpointable Correctness Reviewer

**Files:**
- Create: <code>backend/src/codelens/review/application/context_builder.py</code>
- Create: <code>backend/src/codelens/review/infrastructure/openai_runtime.py</code>
- Create: <code>backend/src/codelens/review/infrastructure/agent_output_codec.py</code>
- Create: <code>backend/src/codelens/reviewer_catalog/infrastructure/builtin_agents.py</code>
- Test: <code>backend/tests/unit/review/test_context_builder.py</code>
- Test: <code>backend/tests/contract/review/test_openai_runtime.py</code>

**Interfaces:**
- Consumes: <code>ReviewSnapshot</code>, <code>ResolvedInstructionSet</code>, <code>AgentVersion</code>, and <code>RunArtifactPort</code>.
- Produces: <code>ContextBuilder.build(...) -&gt; AgentInput</code> and <code>OpenAIAgentRuntime.invoke(...) -&gt; UnvalidatedAgentOutput</code>.

**Official SDK contracts:**
- Results and <code>raw_responses</code>: <https://openai.github.io/openai-agents-python/results/>
- Typed <code>output_type</code>: <https://openai.github.io/openai-agents-python/agents/>
- Tracing/log privacy: <https://openai.github.io/openai-agents-python/tracing/> and <https://openai.github.io/openai-agents-python/config/>

- [ ] **Step 1: Write failing hard-budget context tests**

Use a file reader fake that records every path opened. Give the planner ten candidate files and a budget that fits two. Assert it reads bodies for only the two selected candidates, includes changed hunks/rules first, and records considered/included/omitted/truncated paths with token estimates and reasons.

Add tests for long lines, oversized files, binary files, deleted files, Unicode, instruction budget reservation, and a ContextPlan whose visible paths are all contained by the verified task worktree. Assert no source repository path appears in <code>AgentInput</code>.

Run:

~~~bash
uv run --project backend pytest backend/tests/unit/review/test_context_builder.py -v
~~~

Expected: FAIL because the builder does not exist.

- [ ] **Step 2: Implement plan-before-read context selection**

Build metadata candidates from ChangeIndex and CodeContextProvider summaries. Reserve fixed budgets for platform policy, applicable instructions, output schema, and changed hunks. Rank remaining candidates deterministically, then read file bodies only while their estimated budget fits. Return coverage metadata even when nothing beyond hunks fits.

Do not join all target file bodies and truncate afterward. Every included excerpt carries snapshot ID, path, line range, content hash, selection reason, and trust label.

- [ ] **Step 3: Write failing SDK adapter and privacy tests**

Inject a fake Runner result with a typed <code>final_output</code>, multiple public <code>raw_responses</code>, usage, and response IDs. Assert the adapter:

- invokes one Agent with a Pydantic <code>FindingBatch</code> output type;
- sets <code>RunConfig(trace_include_sensitive_data=False)</code>;
- does not enable verbose SDK logging;
- returns canonical unvalidated final-output bytes plus response IDs/usage;
- does not place API keys, Prompt text, source bodies, or full provider payloads in logs/events;
- maps transient network/rate-limit/server errors separately from permanent invalid output.

- [ ] **Step 4: Implement the public-SDK adapter boundary**

Use <code>Runner.run</code> and public result properties only. Treat <code>final_output</code> as untrusted even when the SDK constructed the declared output type. Serialize it through a versioned <code>AgentOutputCodec</code> to canonical JSON bytes for the recovery checkpoint.

By default, persist response IDs, usage, model name, and a redacted diagnostic envelope from <code>raw_responses</code>; full provider payload retention is off. The pre-validation Artifact needed for restart is the canonical unvalidated final output, not an assumption that provider internals expose a stable JSON dump.

Set both SDK log privacy environment defaults in the launch path and configure each run with sensitive tracing disabled.

- [ ] **Step 5: Validate OpenAI and fake adapter behavior**

Run:

~~~bash
uv run --project backend pytest backend/tests/unit/review/test_context_builder.py backend/tests/contract/review/test_openai_runtime.py -v
uv run --project backend mypy backend/src/codelens/review backend/src/codelens/reviewer_catalog
uv run --project backend ruff check backend
~~~

Expected: bounded reads, canonical output encoding, public SDK result use, transient/permanent error mapping, and privacy assertions pass without a network key.

- [ ] **Step 6: Commit bounded context and runtime**

~~~bash
git add backend
git commit -m "feat: add bounded correctness reviewer runtime"
~~~

---

### Task 14: Execute The Restart-Safe Singleton Review Workflow

**Files:**
- Create: <code>backend/src/codelens/review/application/orchestrator.py</code>
- Create: <code>backend/src/codelens/review/application/validate_findings.py</code>
- Create: <code>backend/src/codelens/worker/scheduler.py</code>
- Create: <code>backend/src/codelens/worker/singleton.py</code>
- Create: <code>backend/src/codelens/worker/main.py</code>
- Modify: <code>backend/src/codelens/bootstrap/cli.py</code>
- Test: <code>backend/tests/unit/review/test_orchestrator.py</code>
- Test: <code>backend/tests/integration/worker/test_restart_recovery.py</code>
- Test: <code>backend/tests/integration/worker/test_concurrent_tasks.py</code>

**Interfaces:**
- Consumes: worktree/Snapshot lifecycle, instruction/context builder, Agent runtime, checkpoint/artifact stores, Finding validator, and outbox.
- Produces: <code>ReviewOrchestrator.execute(task_id)</code>, <code>ReviewScheduler.run()</code>, and independent <code>api</code>, <code>worker</code>, and supervised <code>start</code> commands.

- [ ] **Step 1: Write failing state, crash, and concurrency tests**

Assert the happy-path state sequence:

~~~text
CREATED -> PROVISIONING_WORKTREE -> SNAPSHOTTING -> PREPARING
-> REVIEWING -> VALIDATING -> SYNTHESIZING -> COMPLETED
~~~

Inject crashes at these boundaries:

1. before model invocation;
2. after model return but before Artifact write;
3. after Artifact write but before <code>OUTPUT_SAVED</code>;
4. after <code>OUTPUT_SAVED</code> but before validation;
5. after Finding insert attempt but before transaction commit;
6. after atomic success but before task aggregation.

After reopening SQLite and the Worker, assert only cases without a durable output invoke the model again; output-saved cases validate from Artifact; terminal node findings/events remain singletons.

Start two tasks for different refs in the same real repository. Gate both fake model calls and assert both reach REVIEWING concurrently with distinct worktrees. Add cancellation propagation and task-level/global semaphore assertions.

- [ ] **Step 2: Implement deterministic orchestration checkpoints**

The orchestrator performs each transition through application services and checks cancellation before every new node. For the Reviewer node:

1. mark the stable node key RUNNING;
2. invoke the runtime under the Agent semaphore;
3. encode and atomically persist the unvalidated output Artifact;
4. transition the checkpoint to OUTPUT_SAVED with Artifact reference/hash;
5. reread and hash-verify the Artifact;
6. transition to VALIDATING and perform schema/path/line/hunk/evidence checks;
7. atomically insert Findings, mark SUCCEEDED, and append the success event.

A schema repair is a distinct attempt under the same logical node and preserves the first Artifact. No domain entity imports SQLite, OpenAI, Git, or filesystem adapters.

- [ ] **Step 3: Implement one-Worker structured concurrency**

Acquire an OS-released singleton file lock under the data directory before recovery or job execution. Failure to acquire returns a stable <code>worker_already_running</code> error and exits nonzero.

Implement <code>WorkerSingletonPort</code> with stdlib platform adapters: <code>fcntl.flock</code> on Unix and
<code>msvcrt.locking</code> on Windows. Hold the file descriptor for the Worker lifetime, store only diagnostic PID/
start-time text in the file, and treat that text as informational rather than ownership proof. Process exit releases
the kernel lock; stale text never blocks startup. Contract-test both adapters and run native coverage in Phase 6 CI.

The scheduler owns a task group, a <code>max_active_reviews</code> semaphore, and shared Agent/model/tool semaphores. It polls queued jobs with bounded backoff, starts each task independently, and never holds a repository worktree lock beyond provisioning/cleanup. One task failure is recorded and does not cancel unrelated tasks.

- [ ] **Step 4: Implement restart and shutdown behavior**

At startup, validate/reconcile owned worktrees and execute the Task 11 recovery transaction before accepting jobs. At shutdown, stop claiming jobs, signal cancellation to active task groups, terminate child process groups with a bounded grace period, persist interrupted checkpoints, and release the singleton lock only after database/artifact handles close.

- [ ] **Step 5: Make API, Worker, And start independently runnable**

Provide:

~~~bash
uv run --project backend codelens-review api .
uv run --project backend codelens-review worker .
uv run --project backend codelens-review start .
~~~

<code>start</code> supervises exactly one API process and one Worker process, propagates termination, and exits nonzero if either child fails unexpectedly. It must not merely start Uvicorn. All commands apply loopback, one-Worker, SDK log privacy, and data-directory validation before spawning work.

- [ ] **Step 6: Verify workflow correctness**

Run:

~~~bash
uv run --project backend pytest backend/tests/unit/review backend/tests/integration/worker -v
uv run --project backend mypy backend/src
uv run --project backend ruff check backend
~~~

Expected: crash matrix, same-repository concurrent tasks, cancellation, singleton rejection, state transitions, and atomic Finding success all pass.

- [ ] **Step 7: Commit the durable worker**

~~~bash
git add backend
git commit -m "feat: execute restart-safe review workflows"
~~~

---

### Task 15: Build The New Review Form And Repository Inspection UI

**Files:**
- Create: `frontend/src/shared/api/client.ts`
- Create: `frontend/src/features/repositories/api.ts`
- Create: `frontend/src/features/reviews/types.ts`
- Create: `frontend/src/features/reviews/api.ts`
- Create: `frontend/src/features/reviews/NewReviewPage.tsx`
- Create: `frontend/src/features/reviews/NewReviewPage.test.tsx`
- Create: `frontend/src/test/TestProviders.tsx`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: repository inspect and create-review HTTP contracts from Task 12.
- Produces: route `/`, `NewReviewPage`, and navigation to `/runs/{taskId}` after successful creation.

- [ ] **Step 1: Write the failing form test**

Create `frontend/src/test/TestProviders.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { MemoryRouter } from "react-router-dom";

export function TestProviders({ children }: { children: ReactNode }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}
```

Create `frontend/src/features/reviews/NewReviewPage.test.tsx` with deterministic fetch stubs:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { TestProviders } from "../../test/TestProviders";
import { NewReviewPage } from "./NewReviewPage";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

it("creates a branch review with the default correctness agent", async () => {
  fetchMock
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          path: "/srv/repos/app",
          head_sha: "a".repeat(40),
          current_branch: "feature",
          is_dirty: true,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({ task_id: "review_1", status: "created" }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      ),
    );
  const user = userEvent.setup();
  render(<NewReviewPage />, { wrapper: TestProviders });
  await user.type(screen.getByLabelText("Repository path"), "/srv/repos/app");
  await user.click(screen.getByRole("button", { name: "Inspect" }));
  await user.click(screen.getByLabelText("Branch diff"));
  await user.type(screen.getByLabelText("Base branch"), "origin/main");
  await user.click(screen.getByRole("button", { name: "Start review" }));
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/reviews",
    expect.objectContaining({
      body: expect.stringContaining('"type":"branch"'),
      method: "POST",
    }),
  );
});
```

Run:

```bash
pnpm --dir frontend test -- NewReviewPage.test.tsx
```

Expected: FAIL because `NewReviewPage` does not exist.

- [ ] **Step 2: Implement typed API clients**

Create `client.ts` with:

```typescript
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error((await response.json()).detail ?? `HTTP ${response.status}`);
  return response.json() as Promise<T>;
}
```

Define exact discriminated TypeScript scope types matching the backend DTO in `types.ts`, and implement `inspectRepository(path)` plus `createReview(request)` in the feature API files.

- [ ] **Step 3: Implement the operational form**

`NewReviewPage` must include:

- Server repository path + Inspect button.
- Segmented controls for `branch`, `commit`, `uncommitted`, and `full`.
- Conditional base branch/commit input.
- REVIEW/FIX control, with FIX disabled and labeled `Available in Phase 5` for this plan.
- Seven reviewer rows; Correctness is enabled and selected by default, while the other six are disabled, unchecked, and show `Available in Phase 3`.
- Inspection summary: branch, HEAD, dirty state.
- Start Review button disabled until inspection succeeds and at least one enabled Agent is selected.
- Visible error region with `role="alert"`.

Use semantic form controls and Lucide icons. Do not implement decorative cards or a marketing header.

- [ ] **Step 4: Register routes and QueryClient**

Update `main.tsx` so `/` renders `NewReviewPage`, `/runs` renders a temporary `Runs` heading, and `/runs/:taskId` is reserved for Task 16. Wrap the router with a single `QueryClientProvider`.

- [ ] **Step 5: Verify and commit the creation UI**

```bash
pnpm --dir frontend test -- NewReviewPage.test.tsx
pnpm --dir frontend build
git add frontend pnpm-lock.yaml
git commit -m "feat: add review creation workbench"
```

Expected: form tests pass, types match backend discriminators, and production build succeeds.

---

### Task 16: Stream Review Progress And Display Findings

**Files:**
- Modify: `backend/src/codelens/interface/http/routers/reviews.py`
- Modify: `backend/tests/contract/http/test_reviews_api.py`
- Create: `frontend/src/features/reviews/useReviewEvents.ts`
- Create: `frontend/src/features/reviews/ReviewRunPage.tsx`
- Create: `frontend/src/features/reviews/ReviewRunPage.test.tsx`
- Create: `frontend/src/features/findings/FindingList.tsx`
- Create: `frontend/src/features/findings/FindingDetail.tsx`
- Create: `frontend/src/test/FakeEventSource.ts`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `GET /api/reviews/{id}`, SSE events, and persisted findings from Tasks 11-14.
- Produces: `/runs/:taskId` live run view with status, events, findings, and partial/failed states.

- [ ] **Step 1: Add the missing findings query contract to the backend**

Extend Task 12's reviews router with:

```python
@router.get("/{task_id}/findings")
async def list_findings(task_id: str, request: Request) -> list[dict[str, object]]:
    await load_task(request, task_id)
    items = await request.app.state.components.review_store.list_findings(task_id)
    return [item.model_dump(mode="json") for item in items]
```

It returns the stored `Finding` payloads in stable severity/confidence/path order. Add a contract test asserting an empty list before the Worker finishes and one typed finding after `save_findings`.

- [ ] **Step 2: Write the failing live-run test**

Create `frontend/src/test/FakeEventSource.ts`:

```typescript
type Listener = (event: MessageEvent<string>) => void;

export class FakeEventSource {
  static latest: FakeEventSource | undefined;
  readonly url: string;
  private readonly listeners = new Map<string, Listener[]>();

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.latest = this;
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback = listener as Listener;
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback]);
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    const callback = listener as Listener;
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((item) => item !== callback),
    );
  }

  emit(type: string, payload: object, lastEventId: string) {
    const event = new MessageEvent("message", {
      data: JSON.stringify(payload),
      lastEventId,
    });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  close() {}
}
```

Create `ReviewRunPage.test.tsx`, stub `globalThis.EventSource` with `FakeEventSource`, and stub
`GET /api/reviews/review_1` plus both pre-completion and post-completion findings responses. Render
the page at `/runs/review_1`, then assert:

```tsx
expect(await screen.findByText("Correctness Reviewer")).toBeInTheDocument();
FakeEventSource.latest!.emit("review.completed", { finding_count: 1 }, "7");
expect(await screen.findByText("1 finding")).toBeInTheDocument();
expect(screen.getByText("Wrong branch")).toBeInTheDocument();
```

Run:

```bash
pnpm --dir frontend test -- ReviewRunPage.test.tsx
```

Expected: FAIL because the run page and EventSource hook do not exist.

- [ ] **Step 3: Implement resumable event handling**

`useReviewEvents(taskId)` must:

- Construct `new EventSource('/api/reviews/' + taskId + '/events')`.
- Record the last event ID from `event.lastEventId` in component state.
- Update status for known event names.
- Close EventSource on unmount and after terminal events.
- On browser reconnect, rely on native EventSource reconnect; the server reads `Last-Event-ID`.
- Return `{status, events, connectionState}` without storing raw prompts or model output.

- [ ] **Step 4: Implement findings workspace**

`ReviewRunPage` must provide tabs for Overview, Findings, Agent Runs, and Artifacts; only Findings and Agent Runs require content in this phase. `FindingList` displays severity, title, file:line, confidence, and reviewer. Selecting a finding opens `FindingDetail` with impact, explanation, evidence reference, rules, and recommendation. Include explicit banners for `partial`, `failed`, and `canceled` statuses.

- [ ] **Step 5: Register route, verify, and commit**

```bash
pnpm --dir frontend test
pnpm --dir frontend build
git add backend frontend
git commit -m "feat: stream review progress and findings"
```

Expected: event-driven test passes, all frontend tests pass, and no layout element overlaps at 1280x800 or 390x844 test viewports.

---

### Task 17: Add Acceptance Fixtures, Live Smoke Command, And Phase Gate

**Files:**
- Create: `backend/tests/evals/fixtures/correctness/simple_branch/initial/src/state.py`
- Create: `backend/tests/evals/fixtures/correctness/simple_branch/changed/src/state.py`
- Create: `backend/tests/evals/fixtures/correctness/simple_branch/REVIEW.md`
- Create: `backend/tests/evals/fixtures/correctness/simple_branch/golden.json`
- Create: `backend/tests/evals/test_correctness_fixture.py`
- Create: `backend/scripts/run_live_smoke.py`
- Create: `backend/scripts/run_fake_server.py`
- Create: `frontend/e2e/review-flow.spec.ts`
- Create: `frontend/playwright.config.ts`
- Modify: `README.md`

**Interfaces:**
- Consumes: the complete Phase 0-2 vertical slice.
- Produces: deterministic fake-runtime acceptance test, opt-in OpenAI live smoke, browser E2E, and documented startup commands.

- [ ] **Step 1: Create a deterministic correctness fixture**

The test copies `initial/` into a temporary repository, initializes `main`, commits it, then
overwrites the worktree from `changed/`. The change inverts a guard in `src/state.py`;
`REVIEW.md` requires state-transition validation; `golden.json` contains one Finding with the
runtime-computed hunk ID and excerpt hash inserted by the fixture loader. The fake runtime returns
that batch, and the test asserts task-owned worktree creation, Snapshot, output checkpoint,
validation, persistence, terminal event, and user working-tree/index/ref immutability.

Add acceptance cases for two feature refs reviewed concurrently from the same repository, a
Reviewer mutation attempt, Worker restart at every output checkpoint, a user-created worktree that
survives cleanup, and a second Worker that fails to acquire the data-directory singleton lock.

Run:

```bash
uv run --project backend pytest backend/tests/evals/test_correctness_fixture.py -v
```

Expected: PASS without network access.

- [ ] **Step 2: Add an opt-in live OpenAI smoke script**

Create `backend/scripts/run_live_smoke.py` that:

- Exits with a clear message unless `OPENAI_API_KEY` and `CODELENS_OPENAI_MODEL` are set.
- Creates a temporary copy of the correctness fixture.
- Runs one Correctness Reviewer with `trace_include_sensitive_data=False`.
- Prints task ID, model, elapsed time, token usage when available, and validated Finding count.
- Returns exit code 1 if the task is not `COMPLETED` or no validated Finding is produced.

Run only when credentials are explicitly available:

```bash
: "${OPENAI_API_KEY:?set OPENAI_API_KEY before running the live smoke test}"
: "${CODELENS_OPENAI_MODEL:?set CODELENS_OPENAI_MODEL to the evaluated release model ID}"
uv run --project backend python backend/scripts/run_live_smoke.py
```

Expected: one completed review with at least one validated finding. The model ID is supplied by release configuration, not hard-coded in source.

- [ ] **Step 3: Add Playwright review-flow E2E**

Install and pin the Playwright test runner:

```bash
pnpm --dir frontend add -D @playwright/test
```

Create `backend/scripts/run_fake_server.py` using the same composition root as the real worker,
but inject the deterministic golden-fixture runtime; it must accept `--repository-root` and bind
only `127.0.0.1:8765`. Create `frontend/playwright.config.ts`:

```typescript
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "uv run --project backend python backend/scripts/run_fake_server.py",
      cwd: "..",
      port: 8765,
      reuseExistingServer: false,
    },
    {
      command: "pnpm --dir frontend dev --host 127.0.0.1",
      cwd: "..",
      port: 5173,
      reuseExistingServer: false,
    },
  ],
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 } } },
  ],
});
```

The E2E test must:

1. Open `/`.
2. Inspect the fixture repository.
3. Choose Uncommitted scope.
4. Start Correctness review.
5. Observe agent-running status.
6. Observe completed status.
7. Select the golden Finding and verify its evidence and recommendation.

At each viewport, assert `document.documentElement.scrollWidth <= window.innerWidth`, capture the
run page, and fail when the primary navigation, status strip, Finding list, or detail panel boxes
intersect unexpectedly.

Run:

```bash
pnpm --dir frontend exec playwright install chromium
pnpm --dir frontend exec playwright test
```

Expected: one passing Chromium flow with screenshots retained only on failure.

- [ ] **Step 4: Document exact development and verification commands**

Update `README.md` with:

```bash
# Backend API
uv run --project backend codelens-review start .

# Frontend development server
pnpm --dir frontend dev

# Backend verification
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src

# Frontend verification
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

Document that Phase 0-2 supports only the Correctness Reviewer and REVIEW mode; the UI visibly marks later capabilities as unavailable rather than silently ignoring them.

- [ ] **Step 5: Run the complete phase gate**

```bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
git status --short
```

Expected: all checks pass and `git status --short` shows only the intentional README/test changes before the final commit.

- [ ] **Step 6: Commit the Phase 0-2 acceptance gate**

```bash
git add README.md backend frontend
git commit -m "test: add phase zero to two acceptance gate"
git status --short --branch
```

Expected: the implementation branch contains a working single-Agent review vertical slice and no unexpected files.

---

## Phase 0-2 Acceptance Checklist

- [ ] A clean machine can install backend and frontend dependencies from lockfiles.
- [ ] Local server binds `127.0.0.1`; any non-loopback unauthenticated bind fails closed.
- [ ] Repository inspect rejects paths outside configured roots.
- [ ] Branch, commit, uncommitted, and full scopes pass real Git fixtures.
- [ ] `.gitignore` excludes tracked, untracked, and nested matches while respecting `!` rules.
- [ ] Every ReviewTask pins base/head OIDs, creates an independent owned worktree, distinguishes
  target/context paths, hides Git metadata from the Agent, and rejects escaping symlinks.
- [ ] Same-repository tasks for different feature refs and their Reviewer nodes execute concurrently without input crossover.
- [ ] Root and hierarchical review rules resolve in the approved order even when the control file is ignored.
- [ ] Review creation, worktree ownership, DAG/output checkpoints, outbox events, and Finding persistence survive restart.
- [ ] Unvalidated Correctness output is saved before schema/path/line/hash validation; success and Findings commit atomically.
- [ ] The Worker never writes the user's working tree, index, refs, or another task/user worktree.
- [ ] A second Worker for the same data directory is rejected.
- [ ] Browser can create a review, follow SSE status, and inspect a validated Finding.
- [ ] Full backend, frontend, and Playwright verification commands pass.

## Deferred To Later Plans

- [Phase 3](2026-07-17-codelens-phase-3-multi-agent-reporting.md): six additional Reviewer agents, parallel fan-out/fan-in, verification, deduplication, suppression, and constrained synthesis.
- [Phase 4](2026-07-17-codelens-phase-4-capabilities-context.md): Skill catalog, MCP registry, capability allowlists, repository trust, command profiles, and CodeGraph/context adapters.
- [Phase 5](2026-07-17-codelens-phase-5-fix-workflow.md): Fix mode, a separate owned Fix worktree, Snapshot-based PatchSet, validation gates, manual-default apply, and conflict handling.
- [Phase 6](2026-07-17-codelens-phase-6-deployment-security.md): container sandbox hardening, SecretStore provider upgrades, Artifact retention, and packaged static frontend.
- [Phase 7](2026-07-17-codelens-phase-7-evaluation-release-gates.md): golden datasets, prompt/model comparisons, release thresholds, rollback, and quality dashboards.
