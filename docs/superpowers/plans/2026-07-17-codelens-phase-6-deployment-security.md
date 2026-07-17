# CodeLens Phase 6 Deployment And Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make the single-user loopback release installable with hardened Docker/Podman execution, server-side secret providers, redacted tracing/logging, safe Artifact retention, static frontend packaging, and fail-closed protocol security.

**Architecture:** Sandbox, SecretStore, ArtifactStore, AuditSink, and TracePolicy remain ports selected at bootstrap. Docker or Podman is the only repository-process provider in the first release; missing sandbox disables stdio/commands/CodeGraph instead of falling back to host execution. API and the singleton Worker run independently, while start supervises exactly one of each and serves packaged frontend assets.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite WAL, OpenAI Agents SDK tracing controls, Docker/Podman CLI, system keyring through a pinned Python adapter, React/Vite static assets, uv/pipx, pytest, Playwright.

## Global Constraints

- Phase 0-5 acceptance gates pass before this plan starts.
- Local bind defaults to 127.0.0.1.
- With auth none, any non-loopback bind (including 0.0.0.0) is rejected at startup.
- Repository inspect, Snapshot, Fix, and apply accept only realpaths under configured roots.
- Browser requests never supply Artifact filesystem paths.
- Container receives no host Docker socket, Git/SSH/cloud/OpenAI/MCP credentials, or source-repository path.
- Container network is off by default and cannot be enabled by repository content.
- Container limits cover CPU, memory, PIDs, disk/tmp, timeout, output, capabilities, user, and read-only root filesystem.
- There is no LocalExecutor host fallback; unavailable container execution is visible and fails required gates closed.
- Secret values never enter SQLite, events, RunContext, prompts, logs, traces, Artifact metadata, or browser responses.
- Agents SDK tracing uses sensitive-data exclusion; ZDR/disabled tracing never sends traces to the OpenAI trace backend.
- Deleting retained data is explicit, scoped, auditable, and idempotent.
- API, Worker, and frontend support independent startup without implicit in-process shared state.
- Release artifacts include frontend assets and lockfiles; running the package does not require Node/pnpm.

## 2026-07-17 Correctness Amendment

- Phase 0–2 already supplies minimum Secret references, redaction, opaque Artifacts, sensitive tracing defaults,
  Host/Origin checks, and singleton Worker enforcement. This phase upgrades providers/retention and must preserve
  existing opaque IDs and restart checkpoints during migration.
- Docker/Podman enables the Phase 4 stdio MCP, command, Skill-script, and CodeGraph adapters. REVIEW mounts the
  task-owned worktree read-only; FIX mounts only its owned Fix worktree writable. Git common-dir and user workspaces
  are never mounted.
- Exactly one Worker may use a data directory. API/Worker independence is supported, not multi-Worker execution.
- Remote/trusted-network deployment is deferred until authentication/RBAC has its own approved design. There is no
  warning-only route that permits <code>0.0.0.0 + auth=none</code>.

---

## File And Module Map

~~~text
backend/src/codelens/
  bootstrap/
    settings.py                       # host/origin/root/security settings
    components.py                     # adapter selection and health
    cli.py                            # api, worker, start, doctor commands
    supervisor.py                     # separate process lifecycle
  interface/http/
    app.py
    security.py                       # host/origin/content-type middleware
    artifacts.py                      # opaque-ID download
    data_management.py                # retention/delete commands
    audit.py                          # audit queries if enabled
  sandbox/
    domain/models.py                  # limits, mounts, execution request/result
    domain/ports.py                   # SandboxProvider
    application/selector.py           # container default/local degradation
    infrastructure/container_cli.py   # Docker/Podman argv adapter
  secrets/
    domain/ports.py                   # SecretStore
    application/redaction.py          # value fingerprints and streaming redact
    infrastructure/keyring_store.py
    infrastructure/environment_store.py
  artifacts/
    domain/models.py
    domain/ports.py
    application/retention.py
    infrastructure/filesystem.py
  governance/
    domain/audit.py
    application/audit.py
    infrastructure/sql_audit.py
  observability/
    trace_policy.py
    logging.py
backend/src/codelens/interface/static/    # copied frontend/dist
backend/migrations/versions/
  0005_security_artifacts_audit.py
frontend/src/features/
  settings/SecuritySettingsPage.tsx
  settings/DataManagementPage.tsx
  shared/LocalOnlyBanner.tsx
scripts/
  build-release.sh
.github/workflows/release.yml
backend/tests/
  unit/sandbox/
  unit/secrets/
  unit/artifacts/
  contract/http/test_protocol_security.py
  integration/sandbox/
  integration/security/
frontend/e2e/security-disclosure.spec.ts
~~~

### Task 1: Enforce Loopback, Repository Roots, Host, Origin, And Content Type

**Files:**
- Modify: <code>backend/src/codelens/bootstrap/settings.py</code>
- Modify: <code>backend/src/codelens/interface/http/security.py</code>
- Modify: <code>backend/src/codelens/interface/http/app.py</code>
- Test: <code>backend/tests/unit/bootstrap/test_security_settings.py</code>
- Test: <code>backend/tests/contract/http/test_protocol_security.py</code>

**Interfaces:**
- Consumes: environment/config values.
- Produces: normalized loopback <code>NetworkSecurityPolicy</code> and middleware enforcement.

- [ ] **Step 1: Write the fail-closed configuration matrix**

Accept 127.0.0.1, ::1, and localhost with exact loopback Host/Origin values. Reject 0.0.0.0, non-loopback IPv4/IPv6, wildcard Host/CORS, external origins, forwarded-host trust, filesystem root as repository root, relative/missing roots, symlink escapes, and <code>max_workers != 1</code>. <code>auth</code> accepts only none in this release.

- [ ] **Step 2: Implement validated loopback policy**

Normalize roots and origins at startup. If a reverse-proxy or forwarded header would make a non-loopback origin reachable, reject it; trusted-proxy support is a future authenticated-deployment design. Default allowed hosts/origins are derived only from loopback host/port.

- [ ] **Step 3: Enforce protocol checks before routing**

Apply trusted Host, allowed Origin, JSON command content type, request ID, audit context, routers, and redacted exception handling in that order. Health may omit Origin but still enforces Host. Command endpoints reject form/text/multipart with 415. Artifact endpoints accept only opaque IDs and never paths.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/bootstrap/test_security_settings.py backend/tests/contract/http/test_protocol_security.py -v
uv run --project backend mypy backend/src/codelens/bootstrap backend/src/codelens/interface/http
uv run --project backend ruff check backend
git add backend
git commit -m "feat: enforce loopback protocol policy"
~~~

Expected: every remote/wildcard/multi-Worker configuration fails before binding.

---

### Task 2: Add SecretStore Ports, Keyring/Environment Adapters, And Redaction

**Files:**
- Create: <code>backend/src/codelens/secrets/domain/ports.py</code>
- Create: <code>backend/src/codelens/secrets/infrastructure/keyring_store.py</code>
- Create: <code>backend/src/codelens/secrets/infrastructure/environment_store.py</code>
- Create: <code>backend/src/codelens/secrets/application/redaction.py</code>
- Modify: <code>backend/pyproject.toml</code>
- Test: <code>backend/tests/contract/secrets/test_secret_store.py</code>
- Test: <code>backend/tests/unit/secrets/test_redaction.py</code>

**Interfaces:**
- Produces: async <code>SecretStore</code>, opaque <code>SecretReference</code>, and streaming-safe Redactor.

- [ ] **Step 1: Add and lock the keyring dependency**

~~~bash
uv add --project backend keyring
uv lock --project backend
~~~

Expected: pyproject and uv.lock change together. The system keyring backend is runtime-dependent; no fallback writes plaintext to SQLite or files.

- [ ] **Step 2: Define the port**

~~~python
@dataclass(frozen=True)
class SecretReference:
    namespace: str
    name: str

    @property
    def display_name(self) -> str:
        return f"{self.namespace}/{self.name}"


class SecretStore(Protocol):
    async def available(self) -> bool:
        raise NotImplementedError

    async def get(self, reference: SecretReference) -> str:
        raise NotImplementedError

    async def set(self, reference: SecretReference, value: str) -> None:
        raise NotImplementedError

    async def delete(self, reference: SecretReference) -> None:
        raise NotImplementedError
~~~

EnvironmentSecretStore is read-only and maps administrator-configured references to environment variable names. KeyringSecretStore calls blocking keyring APIs through <code>asyncio.to_thread</code>. A CompositeSecretStore reads keyring then environment; writes only to available keyring.

- [ ] **Step 3: Write contract tests**

Both adapters must:

- distinguish missing from empty;
- never expose values in repr, exception, or logs;
- accept only normalized namespace/name;
- make delete idempotent;
- keep an injected fake backend for deterministic tests.

- [ ] **Step 4: Implement redaction**

Redactor maintains every non-empty configured secret value plus safe fingerprints/prefix rules. It redacts strings, recursively structured values, exception messages, command output chunks across chunk boundaries, and trace attributes. Short values may over-redact unrelated output; preventing a credential leak takes precedence. References remain redactable by key name.

~~~python
class Redactor:
    _sensitive_keys = ("authorization", "password", "secret", "token", "api_key")

    def __init__(self, values: Iterable[str]) -> None:
        self._values = tuple(
            sorted({value for value in values if value}, key=len, reverse=True)
        )

    def redact_text(self, value: str) -> str:
        result = value
        for secret in self._values:
            result = result.replace(secret, "[REDACTED]")
        return result

    def _redact_value(self, value: object) -> object:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return self.redact_mapping(value)
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact_value(item) for item in value)
        return value

    def redact_mapping(self, value: Mapping[str, object]) -> dict[str, object]:
        redacted: dict[str, object] = {}
        for key, item in value.items():
            normalized = key.lower()
            if any(marker in normalized for marker in self._sensitive_keys):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = self._redact_value(item)
        return redacted

    def stream(self) -> "StreamingRedactor":
        return StreamingRedactor(self)


class StreamingRedactor:
    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor
        self._tail = ""
        self._keep = max((len(value) for value in redactor._values), default=1) - 1

    def feed(self, chunk: str) -> str:
        combined = self._tail + chunk
        split_at = max(0, len(combined) - self._keep)
        ready, self._tail = combined[:split_at], combined[split_at:]
        return self._redactor.redact_text(ready)

    def flush(self) -> str:
        ready, self._tail = self._tail, ""
        return self._redactor.redact_text(ready)
~~~

Tests inject sentinel secrets through provider errors, MCP headers, subprocess output, Pydantic errors, logs, events, and Artifact metadata, then scan persisted outputs for the sentinel.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/contract/secrets backend/tests/unit/secrets -v
uv run --project backend ruff check backend/src/codelens/secrets
uv run --project backend mypy backend/src/codelens/secrets
git add backend
git commit -m "feat: add server-side secrets and redaction"
~~~

---

### Task 3: Implement Opaque Artifact Storage, Retention, And Deletion

**Files:**
- Create: <code>backend/src/codelens/artifacts/domain/models.py</code>
- Create: <code>backend/src/codelens/artifacts/domain/ports.py</code>
- Create: <code>backend/src/codelens/artifacts/infrastructure/filesystem.py</code>
- Create: <code>backend/src/codelens/artifacts/application/retention.py</code>
- Create: <code>backend/src/codelens/interface/http/artifacts.py</code>
- Create: <code>backend/src/codelens/interface/http/data_management.py</code>
- Modify: <code>backend/src/codelens/review/infrastructure/tables.py</code>
- Create: <code>backend/migrations/versions/0005_security_artifacts_audit.py</code>
- Test: <code>backend/tests/integration/security/test_artifact_store.py</code>
- Test: <code>backend/tests/contract/http/test_artifacts_api.py</code>

**Interfaces:**
- Produces: content-verified Artifact write/read/delete, opaque-ID HTTP download, and retention sweep.

- [ ] **Step 1: Write containment and atomicity tests**

Assert:

- Artifact ID is random/opaque and independent of file path;
- internal path is derived only by adapter and remains under artifact root;
- interrupted write leaves no published row/file;
- read rechecks size and SHA-256;
- traversal IDs and arbitrary path parameters are rejected;
- metadata contains media type, size, hash, category, owner aggregate, created/expiry timestamps, but no source path or secret;
- delete is idempotent;
- a task delete removes rows and files transactionally enough to retry after partial filesystem failure.

- [ ] **Step 2: Define contracts**

~~~python
class ArtifactCategory(str, Enum):
    SNAPSHOT = "snapshot"
    AGENT_OUTPUT = "agent_output"
    COMMAND_OUTPUT = "command_output"
    PATCH = "patch"
    REPORT = "report"
    EVAL = "eval"


@dataclass(frozen=True)
class ArtifactMetadata:
    artifact_id: str
    owner_id: str
    category: ArtifactCategory
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    expires_at: datetime | None


class ArtifactStore(Protocol):
    async def write(
        self,
        owner_id: str,
        category: ArtifactCategory,
        media_type: str,
        chunks: AsyncIterator[bytes],
        expires_at: datetime | None,
    ) -> ArtifactMetadata:
        raise NotImplementedError

    def open(self, artifact_id: str) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def delete(self, artifact_id: str) -> None:
        raise NotImplementedError
~~~

- [ ] **Step 3: Implement retention defaults**

Implement the existing Phase 3 RunArtifactPort on top of ArtifactStore. Migration 0005 imports/reconciles existing
Snapshot, unvalidated Agent output, redacted provider diagnostics, verification, ContextPlan, command, and PatchSet
references without changing aggregate-visible opaque IDs; missing legacy files are reported and never treated as valid.

- Snapshot and unvalidated Agent output expire after 30 days.
- Reports, findings, immutable prompt/rule versions, and eval results do not expire automatically.
- Review/Fix worktrees are not Artifacts and become cleanup-eligible only after required Snapshot/output/Patch
  Artifacts are durable; cleanup remains ownership-scoped and may quarantine on mismatch.
- Retention sweep claims a database row, deletes the file, then marks/deletes metadata; missing files are reconciled.
- UI commands support delete one task and clear all local data with explicit confirmation and audit.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend alembic upgrade head
uv run --project backend pytest backend/tests/integration/security/test_artifact_store.py backend/tests/contract/http/test_artifacts_api.py -v
git add backend
git commit -m "feat: store and retain artifacts behind opaque IDs"
~~~

---

### Task 4: Define Sandbox Requests, Limits, Mounts, And Provider Selection

**Files:**
- Create: <code>backend/src/codelens/sandbox/domain/models.py</code>
- Create: <code>backend/src/codelens/sandbox/domain/ports.py</code>
- Create: <code>backend/src/codelens/sandbox/application/selector.py</code>
- Test: <code>backend/tests/unit/sandbox/test_policy.py</code>

**Interfaces:**
- Consumes: trusted command profile, Review/Fix mode, verified task-owned Review/Fix worktree, and settings.
- Produces: validated SandboxRequest and selected provider health.

- [ ] **Step 1: Write policy tests**

Review task-worktree mount is read-only; Fix task-worktree content mount is read-write but excludes usable Git
metadata; task temp is the only other writable mount. Reject user workspace/common-dir/other-task paths, Docker
socket, devices, privileged mode, host network, secret env, unknown image, root user, unlimited values, and
repository-requested network changes.

Mask the worktree administrative <code>.git</code> file/directory with a policy-owned empty read-only mount or a
verified content-view adapter. Tests assert the container cannot read its common-dir pointer.

- [ ] **Step 2: Define models**

~~~python
class MountAccess(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class SandboxMount(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: Path
    target: PurePosixPath
    access: MountAccess


class SandboxLimits(BaseModel):
    model_config = ConfigDict(frozen=True)
    cpu_count: float = Field(gt=0, le=8)
    memory_bytes: int = Field(ge=128 * 1024 * 1024, le=16 * 1024**3)
    pids: int = Field(ge=16, le=1024)
    tmp_bytes: int = Field(ge=16 * 1024 * 1024, le=4 * 1024**3)
    timeout_seconds: int = Field(ge=1, le=3600)
    stdout_bytes: int = Field(ge=1024, le=50 * 1024 * 1024)
    stderr_bytes: int = Field(ge=1024, le=50 * 1024 * 1024)


class SandboxRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    image_reference: str
    argv: tuple[str, ...]
    working_directory: PurePosixPath
    mounts: tuple[SandboxMount, ...]
    environment: tuple[tuple[str, str], ...]
    limits: SandboxLimits
    network_enabled: Literal[False] = False
~~~

- [ ] **Step 3: Define provider selection**

At startup, probe configured engine then Docker then Podman using version/info argv and short timeout. A healthy container provider is default. If none is healthy:

- static model review continues;
- command/Fix gates are SKIPPED/FAILED according to required flag;
- health and UI report degraded execution.

There is no host fallback. Repository execution trust is necessary but not sufficient without a healthy sandbox.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/sandbox -v
git add backend
git commit -m "feat: define sandbox policy and safe provider selection"
~~~

---

### Task 5: Implement Hardened Docker And Podman CLI Execution

**Files:**
- Create: <code>backend/src/codelens/sandbox/infrastructure/container_cli.py</code>
- Test: <code>backend/tests/unit/sandbox/test_container_argv.py</code>
- Test: <code>backend/tests/integration/sandbox/test_container_runtime.py</code>

**Interfaces:**
- Consumes: SandboxRequest.
- Produces: bounded SandboxResult and Artifact references.

- [ ] **Step 1: Write exact argv construction tests**

For both engines assert generated argv includes:

~~~text
run --rm
--network none
--read-only
--cap-drop ALL
--security-opt no-new-privileges
--pids-limit <n>
--memory <bytes>
--cpus <n>
--tmpfs /tmp:rw,nosuid,nodev,noexec,size=<bytes>
--user 65532:65532
--workdir /workspace
~~~

It includes only policy-produced task-worktree mounts, never <code>/var/run/docker.sock</code>, the user workspace,
Git common-dir, another task worktree, home, .ssh, .gitconfig, or environment secret values. Image references are
allowlisted and digest-pinned for release profiles.

- [ ] **Step 2: Implement engine-specific path handling**

Normalize macOS/Linux/Windows host mount paths in one adapter function and pass each mount as one argv element. Never interpolate a shell command. Create Fix content mirrors with ownership compatible with the non-root container user. Exclude .git before constructing the mount source.

- [ ] **Step 3: Implement cancellation and output handling**

Run container CLI as a new process group. Assign a random container name so timeout/cancel can issue bounded <code>engine stop</code> then <code>engine rm -f</code>. Stream stdout/stderr through Redactor into capped Artifact writers. Treat only configured exit codes as pass.

- [ ] **Step 4: Run real opt-in isolation tests**

Mark real Docker/Podman tests <code>container</code>. The test image attempts network access, writes root filesystem, reads host home/socket/secret env, forks beyond PID limit, exceeds memory, and writes allowed temp/workspace paths. Assert only intended operations succeed. CI release gate runs:

~~~bash
uv run --project backend pytest -m container backend/tests/integration/sandbox/test_container_runtime.py -v
~~~

Default developer tests use a fake engine executable and need no image/network.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/sandbox backend/tests/integration/sandbox -m "not container" -v
uv run --project backend ruff check backend/src/codelens/sandbox
uv run --project backend mypy backend/src/codelens/sandbox
git add backend
git commit -m "feat: execute repository commands in hardened containers"
~~~

---

### Task 6: Integrate Sandbox, Fail-Closed Degradation, Tracing, Logging, And Audit

**Files:**
- Modify: <code>backend/src/codelens/commands/application/runner.py</code>
- Modify: <code>backend/src/codelens/changes/application/fix_pipeline.py</code>
- Create: <code>backend/src/codelens/observability/trace_policy.py</code>
- Create: <code>backend/src/codelens/observability/logging.py</code>
- Create: <code>backend/src/codelens/governance/domain/audit.py</code>
- Create: <code>backend/src/codelens/governance/application/audit.py</code>
- Create: <code>backend/src/codelens/governance/infrastructure/sql_audit.py</code>
- Test: <code>backend/tests/integration/security/test_no_secret_persistence.py</code>
- Test: <code>backend/tests/integration/security/test_audit.py</code>

**Interfaces:**
- Produces: consistent execution selection, local-safe trace policy, structured redacted logs, and audit events.

- [ ] **Step 1: Route command/Fix execution**

CommandRunner, stdio MCP, Skill scripts, and CodeGraph request the container provider. Event/report state names the
provider. A required capability with no sandbox fails closed; an optional one records an explicit coverage gap.
Static model review and in-process parsing remain available. No server setting can enable host execution.

- [ ] **Step 2: Configure tracing**

~~~python
@dataclass(frozen=True)
class TracePolicy:
    enabled: bool
    send_to_openai: bool
    include_sensitive_data: Literal[False] = False
    local_processor_enabled: bool = True
~~~

Bootstrap sets Agents SDK sensitive-data tracing off. When ZDR or tracing-disabled is configured, no OpenAI trace exporter is installed. Local custom spans contain IDs, hashes, timing, counts, tool names, and stable error codes only. RunContext contains identifiers and policy references, never secret values.

- [ ] **Step 3: Define audit schema**

AuditEvent fields: ID, timestamp, request ID, source IP, User-Agent hash or bounded value, action, aggregate kind/ID, repository path hash, outcome, stable code, and structured non-secret metadata. Audit review creation, cancellation, trust change, capability version change, MCP sample, Fix creation/apply, Artifact access/delete, and data clear. Do not store prompts, code, full model output, patch body, or credentials.

- [ ] **Step 4: Run sentinel leak tests**

Inject unique sentinel values as OpenAI key, MCP token, repository file content marked forbidden-to-export, command output, provider exception, and browser input. After all error paths, scan SQLite, event payloads, logs, trace fake exporter, Artifact metadata, HTTP bodies, and serialized RunContext. Assert no secret sentinel appears.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/integration/security/test_no_secret_persistence.py backend/tests/integration/security/test_audit.py -v
git add backend
git commit -m "feat: integrate sandbox tracing redaction and audit"
~~~

---

### Task 7: Add Independent API/Worker Commands And A Supervising Start Command

**Files:**
- Modify: <code>backend/src/codelens/bootstrap/cli.py</code>
- Create: <code>backend/src/codelens/bootstrap/supervisor.py</code>
- Modify: <code>backend/src/codelens/bootstrap/components.py</code>
- Test: <code>backend/tests/unit/bootstrap/test_cli.py</code>
- Test: <code>backend/tests/integration/security/test_supervisor.py</code>

**Interfaces:**
- Produces:
  - <code>codelens-review api</code>
  - <code>codelens-review worker</code>
  - <code>codelens-review start [repository-root]</code>
  - <code>codelens-review doctor</code>

- [ ] **Step 1: Write CLI and lifecycle tests**

Assert API starts without Worker, Worker starts without API, both communicate only through SQLite/Artifacts, and
start launches them as separate child processes using <code>sys.executable</code> argv. A second Worker for the same
data directory exits with <code>worker_already_running</code>. SIGINT/SIGTERM stops accepting API work, requests
Worker shutdown, waits bounded time, then terminates remaining child process groups. A failed child causes non-zero
supervisor exit and stops its sibling.

- [ ] **Step 2: Implement subcommands**

Use argparse or the existing CLI library without adding a framework. Config precedence is CLI explicit values,
environment, config file, defaults. Never print secret values. <code>doctor</code> reports database/artifact writable
state, singleton lock state, frontend assets, container engine, keyring, model/MCP secret references, repository
root policy, and loopback-only policy.

- [ ] **Step 3: Avoid implicit startup ordering**

Database migrations run under a process-safe migration lock before either service becomes ready. API health reports ready after schema; Worker independently retries database open. Supervisor does not share component instances or event loop between them.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/bootstrap/test_cli.py backend/tests/integration/security/test_supervisor.py -v
git add backend
git commit -m "feat: run API and Worker independently under supervisor"
~~~

---

### Task 8: Package The Built Frontend In The Python Distribution

**Files:**
- Modify: <code>frontend/vite.config.ts</code>
- Modify: <code>backend/pyproject.toml</code>
- Create: <code>backend/src/codelens/interface/static/.gitkeep</code>
- Modify: <code>backend/src/codelens/interface/http/app.py</code>
- Create: <code>scripts/build-release.sh</code>
- Test: <code>backend/tests/integration/security/test_packaged_frontend.py</code>

**Interfaces:**
- Consumes: frontend build.
- Produces: wheel/sdist with static assets and SPA fallback that never shadows /api.

- [ ] **Step 1: Configure deterministic frontend assets**

Vite base path is root-compatible. Build output uses hashed assets. The release script runs pnpm frozen install, frontend tests/build, clears only the package-owned static staging directory, copies <code>frontend/dist</code>, then builds wheel/sdist through uv.

The script uses normal commands and fails on any missing lockfile or asset; it never downloads during the final wheel build stage.

- [ ] **Step 2: Include assets**

Configure Hatch wheel include for <code>src/codelens/interface/static/**</code>. Use <code>importlib.resources.files</code> to locate assets. Serve immutable cache headers for hashed assets and no-cache for index.html. SPA fallback applies only to non-/api GET paths and returns 404 for missing static files with extensions.

- [ ] **Step 3: Test installed wheel**

Build wheel, install it into a clean temporary uv environment without Node/pnpm, run <code>codelens-review api</code>, and assert index, hashed JS/CSS, API health, and one client-side route work. Inspect wheel contents for source maps, secrets, local config, .superpowers, test data, and source repository paths; none may be present.

- [ ] **Step 4: Verify and commit**

~~~bash
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend test
pnpm --dir frontend build
uv build --project backend
uv run --project backend pytest backend/tests/integration/security/test_packaged_frontend.py -v
git add backend frontend scripts
git commit -m "build: package the CodeLens frontend with the server"
~~~

---

### Task 9: Build Security, Data Management, And Local-Only UI

**Files:**
- Create: <code>frontend/src/features/settings/SecuritySettingsPage.tsx</code>
- Create: <code>frontend/src/features/settings/DataManagementPage.tsx</code>
- Create: <code>frontend/src/shared/LocalOnlyBanner.tsx</code>
- Modify: <code>frontend/src/app/App.tsx</code>
- Modify: <code>frontend/src/features/reviews/NewReviewPage.tsx</code>
- Test: <code>frontend/src/features/settings/SecuritySettingsPage.test.tsx</code>
- Test: <code>frontend/src/features/settings/DataManagementPage.test.tsx</code>
- Test: <code>frontend/src/shared/LocalOnlyBanner.test.tsx</code>
- Test: <code>frontend/src/features/reviews/NewReviewPage.security.test.tsx</code>
- Create: <code>frontend/e2e/security-disclosure.spec.ts</code>

**Interfaces:**
- Consumes: security health/settings, retention/delete, provider health.
- Produces: persistent risk disclosure and operational controls.

- [ ] **Step 1: Write failing UI tests**

Assert:

- auth none shows a local-only notice and settings offer no remote-bind control;
- New Review explains which code categories go to OpenAI/remote MCP;
- unavailable container state explains that executable capabilities are disabled with no host fallback;
- keyring unavailable state offers environment-reference instructions without accepting plaintext persistence;
- Artifact retention shows category/default/expiry and supports task delete/clear all confirmation;
- clear all requires typed confirmation and reports partial cleanup retry;
- container unavailable does not imply static review unavailable;
- mobile layout keeps risk and destructive confirmations visible.

- [ ] **Step 2: Implement safe display**

Show repository roots as administrator-configured paths only on the settings screen; ordinary run API uses repository display paths already permitted by policy. Never display secret values. Health errors use stable codes with operator guidance.

- [ ] **Step 3: Add Playwright protocol/risk flow**

At 1440x900 and 390x844 verify local-only notice, data disclosure, provider health, retention, delete confirmation,
and degraded execution. Direct fetch tests cover remote Host/Origin/content type rejection.

- [ ] **Step 4: Verify and commit**

~~~bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test frontend/e2e/security-disclosure.spec.ts
git add frontend
git commit -m "feat: disclose deployment risk and manage local data"
~~~

---

### Task 10: Add Cross-Platform Release And Security Gates

**Files:**
- Create: <code>.github/workflows/release.yml</code>
- Create: <code>backend/tests/acceptance/test_phase_6.py</code>
- Modify: <code>README.md</code>

**Interfaces:**
- Consumes: full application.
- Produces: reproducible package, security acceptance, and release matrix.

- [ ] **Step 1: Define CI matrix**

Run core backend/frontend/package tests on Linux, macOS, and Windows with Python 3.12. Run real Docker sandbox tests on a pinned Linux runner/image digest; run Podman contract tests where available. Do not require live OpenAI, remote MCP, or general network in default tests.

- [ ] **Step 2: Add acceptance scenarios**

Test local 127.0.0.1, rejection of 0.0.0.0/non-loopback and second Worker, out-of-root repository rejection,
Host/Origin/DNS-rebinding defense, JSON-only commands, opaque Artifacts, retention/deletion, secret sentinel absence,
container isolation, no-host-fallback behavior, API/singleton-Worker restart, packaged frontend, and clean wheel install.

- [ ] **Step 3: Run complete release gate**

~~~bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
uv build --project backend
uv run --project backend pytest -m container backend/tests/integration/sandbox/test_container_runtime.py -v
~~~

The final container command is required in release CI; a developer without an engine may omit only that marked suite and must not claim the container gate passed.

- [ ] **Step 4: Commit**

~~~bash
git add .github README.md backend frontend scripts
git commit -m "test: add phase six release security gate"
~~~

## Phase 6 Acceptance Checklist

- [ ] 127.0.0.1 is default; unauthenticated non-loopback binds and multiple Workers fail at startup.
- [ ] No wildcard Host/CORS policy or arbitrary repository path is accepted.
- [ ] Command endpoints require JSON and Artifact access uses opaque IDs.
- [ ] SecretStore uses keyring or read-only environment references; no plaintext persistence fallback exists.
- [ ] Sentinel secrets are absent from SQLite, events, logs, traces, RunContext, Artifact metadata, and HTTP.
- [ ] Snapshot/unvalidated-output retention and explicit deletion are idempotent and auditable.
- [ ] Container execution has no network, host socket, credentials, source path, root user, or broad capabilities.
- [ ] CPU, memory, PID, tmp/disk, timeout, output, cancellation, and cleanup limits are tested.
- [ ] Missing container support visibly disables executable capabilities; no LocalExecutor host fallback exists.
- [ ] API and Worker run independently; start supervises separate processes.
- [ ] Wheel contains built frontend and runs without Node/pnpm.
- [ ] Linux/macOS/Windows core package tests and Linux container release tests pass.
- [ ] Desktop/mobile security, disclosure, deletion, and degraded states pass.

## Deferred To Later Plans

- Phase 7 owns golden eval datasets, prompt/model comparison, quality dashboards, release thresholds, and rollback decisions.
- Authentication, RBAC, trusted-network/public deployment, multi-tenant isolation, and multi-Worker operation remain explicit non-goals requiring a new design.
