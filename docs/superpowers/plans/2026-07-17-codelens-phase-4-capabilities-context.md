# CodeLens Phase 4 Capabilities And Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add versioned Skill/MCP catalogs, per-Agent bindings, repository trust, sandbox-gated command profiles, and auditable bounded context without allowing repository content or tool output to widen permissions or mutate a Review worktree.

**Architecture:** Capability metadata and authorization live in domain/application services; OpenAI Agents SDK, MCP transports, subprocesses, files, and code-index providers remain adapters. Each AgentVersion receives an immutable CapabilityGrant and ContextPlan frozen for the ReviewTask. A central CapabilityEnforcer intersects the binding, repository trust, run mode, server trust, tool policy, and command profile before any adapter is invoked.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite WAL, OpenAI Agents SDK MCP support, stdio, Streamable HTTP, asyncio subprocesses, React, TypeScript, TanStack Query, Vitest, Playwright.

## Global Constraints

- Phase 0-3 acceptance gates pass before this plan starts.
- Repository files, Skill text/scripts, MCP metadata/output, code-index output, and model output are untrusted data.
- Text cannot grant a capability; only persisted application policy can.
- Repository Skills default to untrusted and are unavailable until an explicit RepositoryTrust decision.
- Review mode permits read-only tools by default and blocks write/delete/publish tools.
- Secret values never enter SQLite, events, prompts, RunContext, logs, traces, or browser responses.
- SQLite stores secret references only.
- MCP is limited to stdio and Streamable HTTP; legacy SSE is not configured by new entries.
- Each AgentVersion sees only explicitly bound capabilities and allowed tools.
- Required capability failure fails or skips only the dependent AgentRun; optional failure degrades its ContextPlan.
- Local/stdio processes require both explicit execution trust and an available SandboxProvider; without Phase 6 sandbox support they remain configurable but runtime-disabled.
- A repository Skill script cannot run merely because its SKILL.md requests it.
- TDD and focused commits are mandatory.

## Official OpenAI References

- SDK MCP and observability: https://developers.openai.com/api/docs/guides/agents/integrations-observability
- MCP trust, approvals, and allowed tools: https://developers.openai.com/api/docs/guides/tools-connectors-mcp
- SDK guardrails and human review: https://developers.openai.com/api/docs/guides/agents/guardrails-approvals

The app may use SDK-managed MCP connections behind its adapter. Streamable HTTP read-only tools can run after
policy checks. stdio MCP and repository commands remain runtime-disabled until a SandboxProvider is available;
configuration support is not authorization to spawn a host process.

## 2026-07-17 Correctness Amendment

- Every capability receives only the verified task worktree/Snapshot. REVIEW mounts are read-only, and pre/post
  Manifest checks detect mutation.
- Context planning selects fragments before reading bodies and enforces hard per-node/task budgets. It records every
  omitted shard and never claims full coverage when budget is exhausted.
- Shard/pass dimensions use the Phase 3 concrete AgentRun identity. Shards may run concurrently under global and
  per-task semaphores; they do not create or share mutable worktrees.
- Secrets are resolved only inside the adapter immediately before invocation and never enter Agent input, SQLite,
  events, RunContext, trace payloads, or browser responses.
- stdio MCP, Skill scripts, and local repository commands are fail-closed until Phase 6 installs a sandbox provider.
  Phase 4 tests their policy and disabled-state UX but does not provide a trusted-host execution escape hatch.

---

## File And Module Map

~~~text
backend/src/codelens/
  capabilities/
    domain/models.py                 # capability references, trust, grants
    domain/ports.py                  # Skill, MCP, command and context ports
    application/enforcer.py          # single authorization decision point
    infrastructure/repositories.py   # catalogs, versions, bindings, trust
  skills/
    domain/models.py                 # SkillVersion and script manifest
    application/discovery.py         # built-in/user/repository discovery
    application/materialize.py       # lazy immutable task copy
    infrastructure/filesystem.py     # contained SKILL.md reader
  mcp/
    domain/models.py                 # MCP definition and tool metadata
    application/registry.py          # health, list tools, sample call
    infrastructure/openai_mcp.py     # SDK stdio/Streamable HTTP adapter
  commands/
    domain/models.py                 # argv-only command profiles
    application/runner.py            # policy and output handling
    infrastructure/local_executor.py # explicit-trust fallback
  context/
    domain/models.py                 # ContextPlan and ContextFragment
    domain/ports.py                  # CodeContextProvider
    application/planner.py           # provider priority and token budget
    application/sharding.py          # full-repository shards
    infrastructure/codegraph.py      # .codegraph-aware CLI adapter
    infrastructure/mcp_graph.py      # graph MCP adapter
    infrastructure/fallback.py       # language-aware bounded fallback
  review/
    application/cache.py             # immutable result-cache policy
backend/migrations/versions/
  0003_capabilities_context.py
backend/src/codelens/interface/http/
  skills.py
  mcp_servers.py
  capabilities.py
  settings.py
frontend/src/features/
  skills/
  mcp/
  agents/CapabilityBindingsEditor.tsx
  settings/RepositoryTrustPanel.tsx
  reviews/ContextPlanPanel.tsx
backend/tests/
  unit/capabilities/
  unit/skills/
  unit/mcp/
  unit/commands/
  unit/context/
  integration/capabilities/
  contract/http/test_capabilities_api.py
frontend/e2e/capabilities.spec.ts
~~~

### Task 1: Define Capability, Binding, And Repository Trust Contracts

**Files:**
- Create: <code>backend/src/codelens/capabilities/domain/models.py</code>
- Create: <code>backend/src/codelens/capabilities/domain/ports.py</code>
- Test: <code>backend/tests/unit/capabilities/test_models.py</code>

**Interfaces:**
- Consumes: AgentVersion reference, ReviewMode, repository realpath hash.
- Produces: <code>CapabilityReference</code>, <code>AgentCapabilityBinding</code>, <code>RepositoryTrust</code>, and <code>CapabilityGrant</code>.

- [ ] **Step 1: Write failing least-privilege tests**

~~~python
from codelens.capabilities.domain.models import (
    AgentCapabilityBinding,
    CapabilityKind,
    CapabilityReference,
    RepositoryTrust,
)
from codelens.workspace.domain.models import ReviewMode


def test_repository_content_cannot_grant_execution() -> None:
    trust = RepositoryTrust.untrusted("repo_hash")
    binding = AgentCapabilityBinding(
        agent_reference="security:v1",
        capability=CapabilityReference(CapabilityKind.SKILL, "repo-sast:v1"),
        required=False,
        allowed_tools=(),
    )

    grant = trust.resolve(binding, ReviewMode.REVIEW)

    assert not grant.available
    assert grant.reason == "repository_skill_not_trusted"
~~~

- [ ] **Step 2: Run and observe missing contracts**

~~~bash
uv run --project backend pytest backend/tests/unit/capabilities/test_models.py -v
~~~

Expected: FAIL on import.

- [ ] **Step 3: Implement immutable policy values**

~~~python
from dataclasses import dataclass
from enum import Enum

from codelens.workspace.domain.models import ReviewMode


class CapabilityKind(str, Enum):
    SKILL = "skill"
    MCP = "mcp"
    STATIC_TOOL = "static_tool"
    CODE_CONTEXT = "code_context"


class TrustLevel(str, Enum):
    UNTRUSTED = "untrusted"
    READ_ONLY = "read_only"
    EXECUTION_ALLOWED = "execution_allowed"


@dataclass(frozen=True)
class CapabilityReference:
    kind: CapabilityKind
    reference: str


@dataclass(frozen=True)
class AgentCapabilityBinding:
    agent_reference: str
    capability: CapabilityReference
    required: bool
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityGrant:
    capability: CapabilityReference
    available: bool
    required: bool
    allowed_tools: tuple[str, ...]
    reason: str | None


@dataclass(frozen=True)
class RepositoryTrust:
    repository_path_hash: str
    level: TrustLevel
    allowed_capability_refs: tuple[str, ...]
    allowed_command_profile_ids: tuple[str, ...]

    @classmethod
    def untrusted(cls, repository_path_hash: str) -> "RepositoryTrust":
        return cls(repository_path_hash, TrustLevel.UNTRUSTED, (), ())

    @classmethod
    def read_only(
        cls,
        repository_path_hash: str,
        capability_refs: tuple[str, ...],
    ) -> "RepositoryTrust":
        return cls(repository_path_hash, TrustLevel.READ_ONLY, capability_refs, ())

    def resolve(
        self,
        binding: AgentCapabilityBinding,
        mode: ReviewMode,
    ) -> CapabilityGrant:
        available = binding.capability.reference in self.allowed_capability_refs
        reason = (
            None
            if available
            else f"{binding.capability.kind.value}_not_trusted"
        )
        return CapabilityGrant(
            binding.capability,
            available,
            binding.required,
            binding.allowed_tools if available else (),
            reason,
        )
~~~

Do not implement tool side-effect classification in this value object; Task 2 centralizes it in CapabilityEnforcer so all adapters share the same decision.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/capabilities/test_models.py -v
uv run --project backend mypy backend/src/codelens/capabilities/domain
git add backend
git commit -m "feat: define capability and repository trust contracts"
~~~

---

### Task 2: Build The Central Capability Enforcer

**Files:**
- Create: <code>backend/src/codelens/capabilities/application/enforcer.py</code>
- Test: <code>backend/tests/unit/capabilities/test_enforcer.py</code>

**Interfaces:**
- Consumes: binding, trust, run mode, capability metadata, tool metadata.
- Produces: <code>CapabilityEnforcer.freeze_grants</code> and <code>CapabilityEnforcer.authorize_tool</code>.

- [ ] **Step 1: Write a full policy matrix test**

Cover:

| Source | Trust | Mode | Tool effect | Result |
|---|---|---|---|---|
| built-in Skill | built-in | REVIEW | text only | allow |
| repository Skill | untrusted | REVIEW | text only | deny |
| repository Skill | read-only | REVIEW | text only | allow |
| repository Skill script | read-only | REVIEW | process | deny |
| user MCP | trusted | REVIEW | read | allow if bound |
| user MCP | trusted | REVIEW | write/delete/publish | deny |
| any MCP | trusted | FIX | write | manual approval required, not auto-approved |
| command profile | execution allowed | REVIEW | exact argv template | allow |

Assert an unbound tool is denied even if the repository is trusted.

- [ ] **Step 2: Implement one authorization result type**

~~~python
class ToolEffect(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    PUBLISH = "publish"
    PROCESS = "process"


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    approval_required: bool
    code: str


class CapabilityDeniedError(PermissionError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
~~~

<code>authorize_tool</code> checks, in order: frozen grant exists, capability available, tool is allowlisted, tool is not blocklisted, repository trust permits the source, and run-mode effect policy permits it. Denials return stable codes and never include secrets or raw tool arguments.

- [ ] **Step 3: Freeze grants with the ReviewTask**

Add <code>capability_grants_json</code> to ReviewTask persistence. The stored value includes references, content hashes, required flags, allowed tool names, and denial reason; it never includes Skill body, MCP URL authorization header, or secret value. A resumed task reuses this frozen grant even if catalog settings later change.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/capabilities -v
git add backend
git commit -m "feat: enforce immutable least-privilege capability grants"
~~~

---

### Task 3: Discover, Version, And Lazily Materialize Skills

**Files:**
- Create: <code>backend/src/codelens/skills/domain/models.py</code>
- Create: <code>backend/src/codelens/skills/application/discovery.py</code>
- Create: <code>backend/src/codelens/skills/application/materialize.py</code>
- Create: <code>backend/src/codelens/skills/infrastructure/filesystem.py</code>
- Test: <code>backend/tests/unit/skills/test_discovery.py</code>
- Test: <code>backend/tests/integration/capabilities/test_skill_materialization.py</code>

**Interfaces:**
- Consumes: built-in, app-data, and repository Skill roots.
- Produces: immutable <code>SkillVersion</code> and contained task materialization.

- [ ] **Step 1: Write failing discovery safety tests**

Create real temporary directories with:

- valid SKILL.md plus references;
- script manifest;
- symlink escaping the Skill directory;
- duplicate name from built-in/user/repository sources;
- malformed frontmatter;
- repository Skill without trust.

Assert precedence does not silently replace versions: every source has a separate content-hashed reference. Escaping symlinks and malformed metadata are rejected. Untrusted repository versions are cataloged as unavailable, not loaded into AgentInput.

- [ ] **Step 2: Define SkillVersion**

~~~python
@dataclass(frozen=True)
class SkillScript:
    relative_path: str
    command_profile_id: str


@dataclass(frozen=True)
class SkillVersion:
    skill_id: str
    version: int
    name: str
    description: str
    source: Literal["builtin", "user", "repository"]
    source_root_hash: str
    content_hash: str
    mode_support: tuple[ReviewMode, ...]
    required_tools: tuple[str, ...]
    entry_file: str
    references: tuple[str, ...]
    scripts: tuple[SkillScript, ...]

    @property
    def reference(self) -> str:
        return f"{self.skill_id}:v{self.version}:{self.content_hash[:12]}"
~~~

Parse frontmatter through <code>python-frontmatter</code>, validate with Pydantic at the infrastructure boundary, normalize every relative path, and hash sorted path plus bytes for all files in the immutable version.

- [ ] **Step 3: Implement lazy materialization**

<code>SkillMaterializer.materialize(task_root, versions, grants)</code> copies only bound and available versions into <code>task_root/capabilities/skills/&lt;reference&gt;</code>. It verifies every file hash during copy, creates no executable permission, and returns a <code>MaterializedSkill</code> with entry text and reference URIs. Script content is not added to prompts automatically.

~~~python
@dataclass(frozen=True)
class MaterializedSkill:
    reference: str
    root: Path
    instructions: str
    reference_paths: tuple[Path, ...]
    scripts: tuple[SkillScript, ...]
~~~

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/skills backend/tests/integration/capabilities/test_skill_materialization.py -v
uv run --project backend ruff check backend/src/codelens/skills
uv run --project backend mypy backend/src/codelens/skills
git add backend
git commit -m "feat: version and lazily materialize trusted skills"
~~~

---

### Task 4: Add MCP Definitions, Health Checks, And Filtered Runtime Sessions

**Files:**
- Create: <code>backend/src/codelens/mcp/domain/models.py</code>
- Create: <code>backend/src/codelens/mcp/application/registry.py</code>
- Create: <code>backend/src/codelens/mcp/infrastructure/openai_mcp.py</code>
- Test: <code>backend/tests/unit/mcp/test_policy.py</code>
- Test: <code>backend/tests/contract/mcp/test_mcp_adapter.py</code>

**Interfaces:**
- Consumes: secret references, grants, AgentVersion binding, run mode.
- Produces: list-tools health result and per-Agent filtered MCP sessions.

- [ ] **Step 1: Write failing transport and tool-filter tests**

Use injected fake MCP sessions. Assert:

- stdio command is argv, never a shell string;
- Streamable HTTP URL must be https unless an explicit loopback development flag is set;
- SSE is rejected for new definitions;
- allowed_tools and blocked_tools are applied before exposure to the model;
- secret reference resolves only inside connection setup and is never returned;
- unavailable optional MCP degrades one AgentRun; required MCP fails that AgentRun;
- write/delete/publish tools are invisible in REVIEW mode.

- [ ] **Step 2: Define validated MCP models**

~~~python
class McpTransport(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema_hash: str
    effect: ToolEffect


@dataclass(frozen=True)
class McpServerDefinition:
    server_id: str
    version: int
    name: str
    transport: McpTransport
    command: str | None
    args: tuple[str, ...]
    url: str | None
    environment_secret_refs: tuple[tuple[str, str], ...]
    connect_timeout_seconds: int
    read_timeout_seconds: int
    max_attempts: int
    allowed_tools: tuple[str, ...]
    blocked_tools: tuple[str, ...]
    trusted: bool
    content_hash: str
~~~

Pydantic command DTO validation requires exactly one of command or URL according to transport, timeouts from 1 to 300, attempts from 1 to 5, no NUL characters, and no secret-looking literal environment values.

- [ ] **Step 3: Define adapter ownership**

<code>McpSessionPort</code> exposes <code>connect</code>, <code>list_tools</code>, <code>call_tool</code>, and <code>close</code>. <code>OpenAIMcpSessionFactory</code> maps Streamable HTTP to the SDK class available in the locked version. It recognizes stdio configuration but returns <code>capability_requires_sandbox</code> until a SandboxProvider supplies process isolation in Phase 6; it must not instantiate <code>MCPServerStdio</code> on the host. All SDK objects remain in infrastructure.

Connection flow:

1. Resolve secret references into a short-lived local mapping.
2. Construct SDK session.
3. Connect under connect timeout.
4. List tools.
5. Classify each configured tool effect from administrator-owned metadata.
6. Intersect server policy and CapabilityGrant.
7. Expose only the filtered session to the Agent runtime.
8. Close in <code>finally</code> on success, failure, timeout, or cancellation.

- [ ] **Step 4: Add health and sample-call application service**

Health returns server reference, reachable boolean, latency, filtered tool metadata, and stable error code. Sample calls require a read-only tool and validated JSON args; their output is truncated, redacted, and marked untrusted. It is never included in a later review unless a ContextPlan explicitly records it.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/mcp backend/tests/contract/mcp -v
uv run --project backend ruff check backend/src/codelens/mcp
uv run --project backend mypy backend/src/codelens/mcp
git add backend
git commit -m "feat: add filtered stdio and http MCP sessions"
~~~

---

### Task 5: Define Command Profiles And Enforce Sandbox-Required Execution

**Files:**
- Create: <code>backend/src/codelens/commands/domain/models.py</code>
- Create: <code>backend/src/codelens/commands/application/runner.py</code>
- Test: <code>backend/tests/unit/commands/test_policy.py</code>
- Test: <code>backend/tests/integration/commands/test_phase4_disabled_execution.py</code>

**Interfaces:**
- Consumes: RepositoryTrust, verified task worktree, immutable command profile, and optional SandboxProvider.
- Produces: validated argv-only profiles and a fail-closed command runner.

- [ ] **Step 1: Write failing policy and disabled-execution tests**

Reject shell strings, option-template injection, unbounded timeout/output, cwd escape, environment inheritance, write-capable profiles in REVIEW, and unknown executables. With execution trust but no SandboxProvider, assert <code>capability_requires_sandbox</code> and prove no subprocess starts. Assert the UI/API can distinguish configured, trusted, and runnable states.

- [ ] **Step 2: Implement immutable command profiles**

A profile contains executable identity, fixed argv plus typed placeholders, allowed exit codes, timeout, output-byte cap, read-only/writable mode support, network requirement, environment allowlist, and version hash. Validate placeholders before adapter invocation. Repository content cannot create/alter profiles or trust.

- [ ] **Step 3: Implement sandbox-only delegation**

The Phase 4 runner normalizes the request against the verified task worktree, asks CapabilityEnforcer, and then requires <code>SandboxProvider.execute(request)</code>. When no provider is installed, return the stable disabled result. Do not implement <code>LocalExecutor</code>, <code>create_subprocess_exec</code>, or host fallback in this phase.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/commands backend/tests/integration/commands/test_phase4_disabled_execution.py -v
uv run --project backend mypy backend/src/codelens/commands
uv run --project backend ruff check backend
git add backend
git commit -m "feat: gate command profiles on sandbox availability"
~~~

Expected: policy tests pass and zero repository commands execute on the host.

---

### Task 6: Define Auditable Context Plans And Budget Enforcement

**Files:**
- Create: <code>backend/src/codelens/context/domain/models.py</code>
- Create: <code>backend/src/codelens/context/domain/ports.py</code>
- Create: <code>backend/src/codelens/context/application/planner.py</code>
- Modify: <code>backend/src/codelens/review/domain/ports.py</code>
- Create: <code>backend/src/codelens/review/application/cache.py</code>
- Test: <code>backend/tests/unit/context/test_planner.py</code>
- Test: <code>backend/tests/unit/review/test_result_cache.py</code>

**Interfaces:**
- Consumes: snapshot, instructions, change index, AgentVersion, grants, Skills, MCP/static outputs.
- Produces: one immutable <code>ContextPlan</code> and AgentInput per reviewer.

- [ ] **Step 1: Write ordering, budget, and disclosure tests**

Assert fragment order is: task goal, instructions, diff/hunks, changed definitions, callers/callees/interfaces/tests/config, bound Skill text, MCP context, static evidence. Every fragment records source, hash/version, relevance reason, token estimate, and sensitive flag. When over budget, lower-priority fragments are omitted and listed in truncation records; changed hunks and applicable instructions are never silently removed.

Use a recording file reader and assert the planner reads bodies only after metadata ranking and only for fragments
that fit the remaining budget. A candidate that will be omitted for budget must not have its body read first.

- [ ] **Step 2: Define models**

~~~python
class ContextSource(str, Enum):
    TASK = "task"
    INSTRUCTION = "instruction"
    DIFF = "diff"
    DEFINITION = "definition"
    CALLER = "caller"
    CALLEE = "callee"
    INTERFACE = "interface"
    TEST = "test"
    CONFIG = "config"
    SKILL = "skill"
    MCP = "mcp"
    STATIC_TOOL = "static_tool"


class ContextFragment(BaseModel):
    model_config = ConfigDict(frozen=True)
    fragment_id: str
    source: ContextSource
    uri: str
    content_hash: str
    relevance: str
    token_estimate: int
    contains_sensitive_data: bool
    content: str


class TruncationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: ContextSource
    uri: str
    token_estimate: int
    reason: Literal["budget", "policy", "unavailable", "oversized"]


class ContextPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id: str
    agent_reference: str
    token_budget: int
    fragments: tuple[ContextFragment, ...]
    truncations: tuple[TruncationRecord, ...]
    capability_references: tuple[str, ...]
~~~

- [ ] **Step 3: Replace the Phase 0-2 flat AgentInput**

AgentInput stores <code>context_plan_hash</code> and a bounded serialization generated from ContextPlan. The full ContextPlan is persisted through the Phase 3 RunArtifactPort. AgentRuntimePort accepts the serialized input but cannot request a capability outside the frozen plan.

- [ ] **Step 4: Verify and commit**

Before verification, add a cache policy whose key contains every behavior-bearing immutable input:

~~~python
@dataclass(frozen=True)
class AgentResultCacheKey:
    snapshot_manifest_hash: str
    agent_reference: str
    instruction_set_hash: str
    model_profile_reference: str
    context_plan_hash: str
    skill_references: tuple[str, ...]
    mcp_response_versions: tuple[str, ...]
    static_tool_versions: tuple[str, ...]
    output_schema_version: int

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode()).hexdigest()
~~~

<code>AgentResultCachePolicy</code> permits reuse only when the full key matches and every dynamic MCP response has a stable version identifier. Any unversioned dynamic MCP call, changed capability grant, changed ContextPlan, changed tool version, or changed schema makes the run non-cacheable. Cache hits still pass current deterministic FindingValidator before entering the pipeline. Shared code-index data may be reused by snapshot/content hash, but reviewer prompts and conclusions remain isolated per AgentVersion.

~~~bash
uv run --project backend pytest backend/tests/unit/context backend/tests/unit/review/test_result_cache.py -v
uv run --project backend mypy backend/src/codelens/context backend/src/codelens/review/domain
git add backend
git commit -m "feat: build auditable bounded context plans"
~~~

---

### Task 7: Implement CodeContextProvider Priority And Safe Adapters

**Files:**
- Create: <code>backend/src/codelens/context/infrastructure/codegraph.py</code>
- Create: <code>backend/src/codelens/context/infrastructure/mcp_graph.py</code>
- Create: <code>backend/src/codelens/context/infrastructure/fallback.py</code>
- Modify: <code>backend/src/codelens/context/application/planner.py</code>
- Test: <code>backend/tests/contract/context/test_context_provider.py</code>
- Test: <code>backend/tests/integration/capabilities/test_codegraph_adapter.py</code>

**Interfaces:**
- Consumes: contained snapshot and changed symbol queries.
- Produces: normalized <code>ContextCandidate</code> values.

- [ ] **Step 1: Define the provider contract**

~~~python
@dataclass(frozen=True)
class ContextQuery:
    snapshot_root: Path
    target_paths: tuple[str, ...]
    symbols: tuple[str, ...]
    kinds: tuple[ContextSource, ...]
    max_results: int


@dataclass(frozen=True)
class ContextCandidate:
    source: ContextSource
    relative_path: str
    start_line: int
    end_line: int
    relevance: str
    content: str


class CodeContextProvider(Protocol):
    async def available(self, snapshot_root: Path) -> bool:
        raise NotImplementedError

    async def search(self, query: ContextQuery) -> tuple[ContextCandidate, ...]:
        raise NotImplementedError
~~~

- [ ] **Step 2: Write one shared adapter contract suite**

Every provider must return only snapshot-contained relative paths, valid line ranges, capped content, stable ordering, and no more than max_results. Malformed or escaping provider output is discarded with a warning, not trusted.

- [ ] **Step 3: Implement priority**

<code>ContextProviderRouter</code> selects:

1. CodeGraph CLI only when <code>.codegraph/</code> exists and a SandboxProvider is available.
2. Bound read-only graph MCP only when its CapabilityGrant is available.
3. Bounded language-aware fallback.

It does not silently create an index. CodeGraph requests use argv <code>("codegraph", "explore", query)</code>
through SandboxProvider with a read-only task-worktree mount, timeout, output cap, and no shell. Without a sandbox,
record CodeGraph as unavailable and continue to a granted graph MCP or the in-process Python AST/text fallback;
do not spawn CodeGraph/ripgrep directly on the host. MCP graph results pass the same normalizer. The fallback reports
unsupported language gaps rather than claiming full call-graph coverage.

- [ ] **Step 4: Verify with a fake CodeGraph executable**

Use a fake SandboxProvider that records argv and emits one valid and one escaping result. Assert the query is literal,
only the valid task-worktree path survives, absent <code>.codegraph</code> bypasses it, and missing sandbox starts no process.

- [ ] **Step 5: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/contract/context backend/tests/integration/capabilities/test_codegraph_adapter.py -v
uv run --project backend ruff check backend/src/codelens/context
uv run --project backend mypy backend/src/codelens/context
git add backend
git commit -m "feat: add prioritized safe code context providers"
~~~

---

### Task 8: Add Full-Repository Sharding And Cross-File Second Pass

**Files:**
- Create: <code>backend/src/codelens/context/application/sharding.py</code>
- Modify: <code>backend/src/codelens/review/application/pipeline.py</code>
- Test: <code>backend/tests/unit/context/test_sharding.py</code>
- Test: <code>backend/tests/integration/review/test_full_repository_pipeline.py</code>

**Interfaces:**
- Consumes: FullRepositoryScope manifest and ContextProvider.
- Produces: deterministic <code>RepositoryShard</code>, coverage gaps, bounded second pass.

- [ ] **Step 1: Write deterministic shard tests**

Group by top-level module, language, and configured maximum files/tokens. The same manifest always yields the same shard IDs. No file appears in two primary shards. Oversized files become their own bounded shard. Ignored/policy-excluded files never appear.

~~~python
shards = RepositorySharder(max_files=50, max_estimated_tokens=80_000).build(manifest)
assert len({path for shard in shards for path in shard.paths}) == sum(
    len(shard.paths) for shard in shards
)
assert [item.shard_id for item in shards] == sorted(item.shard_id for item in shards)
~~~

- [ ] **Step 2: Implement shard-local review and global fan-in**

Run selected reviewers per shard under both global and per-task reviewer semaphores. Each run identity contains
ReviewerVersion, pass index, shard ID, and logical attempt group. Validate and exact-dedupe inside each shard.
Global fan-in receives compact findings plus a cross-shard symbol index, never all source text. The cross_file
reviewer gets a second bounded pass only for high-risk boundaries. Persist uncovered shards when task/model/tool
budget stops scheduling. All shards read the same task worktree and cannot mutate it.

- [ ] **Step 3: Verify budget exhaustion truthfulness**

An integration test sets a two-shard budget for a four-shard repository. Report coverage must identify the two uncovered shard IDs/paths and must not say full coverage.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend pytest backend/tests/unit/context/test_sharding.py backend/tests/integration/review/test_full_repository_pipeline.py -v
git add backend
git commit -m "feat: shard full-repository reviews with coverage gaps"
~~~

---

### Task 9: Persist Catalogs And Expose Capability Configuration APIs

**Files:**
- Modify: <code>backend/src/codelens/review/infrastructure/tables.py</code>
- Create: <code>backend/src/codelens/capabilities/infrastructure/repositories.py</code>
- Create: <code>backend/migrations/versions/0003_capabilities_context.py</code>
- Create: <code>backend/src/codelens/interface/http/skills.py</code>
- Create: <code>backend/src/codelens/interface/http/mcp_servers.py</code>
- Create: <code>backend/src/codelens/interface/http/capabilities.py</code>
- Modify: <code>backend/src/codelens/interface/http/settings.py</code>
- Test: <code>backend/tests/contract/http/test_capabilities_api.py</code>

**Interfaces:**
- Produces Skill, MCP, binding, trust, command-profile, health, and ContextPlan APIs from the overall design.

- [ ] **Step 1: Add schema**

Create tables:

- skill_versions, unique by content hash and source;
- mcp_server_versions, immutable configuration with secret references;
- capability_bindings, unique by agent reference plus capability reference;
- repository_trust, one row per repository path hash;
- command_profiles and versions;
- context_plans, metadata plus RunArtifact reference.
- agent_result_cache, cache-key digest, FindingBatch RunArtifact reference, creation/expiry metadata, and no raw prompt.
- instruction_documents, snapshot ID, relative path, content hash, parsed metadata, and RunArtifact reference;
- settings, versioned non-secret application settings and active pointer.

Instruction document rows freeze what a task resolved and never reread the mutable repository. Settings reject API keys, tokens, authorization headers, and other secret values at the DTO and repository boundaries.

Never persist secret values or materialized Skill bodies in these rows.

- [ ] **Step 2: Write API contract tests**

Cover:

~~~text
GET/POST/PUT /api/skills
GET/POST/PUT /api/mcp-servers
POST         /api/mcp-servers/{id}/check
POST         /api/mcp-servers/{id}/sample
GET/PUT      /api/agents/{id}/capabilities
GET/PUT      /api/repositories/trust
GET/POST/PUT /api/command-profiles
GET          /api/reviews/{id}/context-plans
~~~

PUT creates a new immutable version and moves an active pointer; it never rewrites a version referenced by a ReviewTask. MCP sample calls reject side-effect tools. Repository trust changes require a contained repository path and return a warning describing local command risk.

- [ ] **Step 3: Implement DTO boundary and stable errors**

Use discriminated Pydantic DTOs for MCP transport. Responses redact secret reference identifiers to a display alias where necessary. Stable error codes include <code>skill_invalid</code>, <code>capability_untrusted</code>, <code>mcp_unreachable</code>, <code>mcp_tool_denied</code>, and <code>command_profile_denied</code>.

- [ ] **Step 4: Verify and commit**

~~~bash
uv run --project backend alembic upgrade head
uv run --project backend pytest backend/tests/contract/http/test_capabilities_api.py -v
uv run --project backend alembic downgrade 0002
uv run --project backend alembic upgrade head
git add backend
git commit -m "feat: expose versioned capability configuration"
~~~

---

### Task 10: Build Skills, MCP, Trust, Binding, And Context UI

**Files:**
- Create: <code>frontend/src/features/skills/SkillsPage.tsx</code>
- Create: <code>frontend/src/features/skills/api.ts</code>
- Create: <code>frontend/src/features/mcp/McpServersPage.tsx</code>
- Create: <code>frontend/src/features/mcp/api.ts</code>
- Create: <code>frontend/src/features/agents/CapabilityBindingsEditor.tsx</code>
- Create: <code>frontend/src/features/settings/RepositoryTrustPanel.tsx</code>
- Create: <code>frontend/src/features/reviews/ContextPlanPanel.tsx</code>
- Modify: <code>frontend/src/app/router.tsx</code>
- Test: <code>frontend/src/features/skills/SkillsPage.test.tsx</code>
- Test: <code>frontend/src/features/mcp/McpServersPage.test.tsx</code>
- Test: <code>frontend/src/features/agents/CapabilityBindingsEditor.test.tsx</code>
- Test: <code>frontend/src/features/settings/RepositoryTrustPanel.test.tsx</code>
- Test: <code>frontend/src/features/reviews/ContextPlanPanel.test.tsx</code>
- Create: <code>frontend/e2e/capabilities.spec.ts</code>

**Interfaces:**
- Consumes: Task 9 APIs.
- Produces: visible trust, tool filtering, health, bindings, disclosure, truncation, and failure states.

- [ ] **Step 1: Write failing interaction tests**

Assert:

- repository Skill shows Untrusted until explicit confirmation;
- execution trust uses a second, risk-specific confirmation;
- MCP form switches between argv and HTTP fields without retaining hidden secrets;
- tool list distinguishes read and side-effect tools;
- Agent binding editor cannot select a blocked tool;
- required/optional toggle is visible;
- ContextPlan panel shows source, relevance, hash/version, token estimate, sensitive flag, and truncation reason;
- optional MCP failure is visible as degraded rather than silently omitted.

- [ ] **Step 2: Implement strict DTO parsing and accessible UI**

No raw HTML from Skill/MCP output is rendered. Sample output is plain preformatted text after server redaction. Long command args, URLs, paths, and tool schemas wrap. Desktop and mobile layouts keep the primary action and risk warning visible.

- [ ] **Step 3: Add Playwright malicious-input coverage**

Return Skill descriptions and MCP output containing script tags, prompt-injection text, path traversal, and long unbroken strings. Assert no script executes, no permission changes, no overflow hides controls, and review creation sends only frozen references.

- [ ] **Step 4: Run the full phase gate**

~~~bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test frontend/e2e/capabilities.spec.ts
~~~

- [ ] **Step 5: Commit the phase gate**

~~~bash
git add backend frontend README.md
git commit -m "test: add phase four capability acceptance gate"
~~~

## Phase 4 Acceptance Checklist

- [ ] Built-in, user, and repository Skills have immutable versions and hashes.
- [ ] Repository Skills remain unavailable until explicit repository trust.
- [ ] Skill text/scripts and MCP output cannot widen permissions.
- [ ] Each Agent sees only bound and allowlisted capabilities.
- [ ] Streamable HTTP MCP sessions close on success, failure, timeout, and cancel; stdio is visibly runtime-disabled until sandboxed.
- [ ] Secret values never enter persistence, events, prompts, traces, or browser responses.
- [ ] Review mode cannot invoke write/delete/publish MCP tools.
- [ ] Command profiles require execution trust, exact argv, and SandboxProvider; no LocalExecutor host fallback exists.
- [ ] Missing sandbox starts no stdio/command/CodeGraph process; Phase 6 owns real sandbox process tests.
- [ ] ContextPlan records provenance, relevance, version/hash, token cost, sensitivity, and truncation.
- [ ] Context planning enforces budget before reading file bodies.
- [ ] CodeGraph is used only when an existing .codegraph directory is present; indexing is never implicit.
- [ ] Provider output is normalized against the immutable snapshot.
- [ ] Full-repository budget gaps are explicit.
- [ ] Every shard/pass has a unique AgentRun identity and runs under global/per-task concurrency limits.
- [ ] Skills, MCP, trust, bindings, ContextPlan, desktop, and mobile UI states pass.

## Deferred To Later Plans

- Phase 5 reuses owned worktrees for Fix and owns Snapshot-based PatchSet validation/apply approvals.
- Phase 6 enables stdio/commands/CodeGraph through hardened Docker/Podman execution and upgrades secrets/artifacts/packaging.
- Phase 7 owns quantitative eval and release policy.
