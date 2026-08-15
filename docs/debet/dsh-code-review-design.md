# DSH Code Review 设计文档（CodeLens 能力迁移方案）

> 状态：可行性已验证（P0 未开始）
> 目标读者：后续负责实现的 Agent
> 关联文档：本仓库 `../ARCHITECTURE.md`（思想来源）、`../plugin.md`（插件体系来源）、`../build-in-tool.md`（工具契约来源）、`../runtime-dag.md`（编排来源）

本文档是「把 CodeLens 的代码 Review 能力迁移到 DeepSeek Harness（DSH）上」的权威设计，包含目标架构、迁移前后能力对照、标准契约、分阶段路径与模块级任务分解。实现 Agent 以本文档为唯一依据；与本文档冲突的实现需求必须先修改本文档。

---

## 1. 目标与约束

### 1.1 目标

用 DSH 的 Agent 运行时**直接复用**（llm / tools / subagents / workflow / skills / commands / systemPrompt / compaction / session 持久化 / subprocess / sandbox），把 CodeLens 的**思想**（冻结快照、只读证据工具、零伪完成、确定性位置校验、结构化 Findings、可插拔的入站触发与出站报告）迁移为一套**无 Web UI、语言无关、任意本地 git 仓库**的代码 Review 能力。

### 1.2 已确认的产品决策

| 决策点 | 结论 |
|---|---|
| 载体形态 | npm 包 + `dsh plugin add`（形态不重要，功能重要） |
| Web UI | 不保留；headless 触发 + 结构化 JSON 输出 |
| Review 对象 | 任意编程语言、任意本地 git 仓库 |
| 结果上传 | 优先走 `gh` CLI（GitHub REST 作为可选扩展） |
| 插件分发 | 接受 npm 包 + Cordis 组合形态，不做运行时 git_url 安装 |

### 1.3 硬约束（迁移时必须保持的 CodeLens 安全边界）

1. 对源仓库**严格只读**：证据工具只读快照，绝不写源仓库、不改 Git 引用。
2. 模型只读**冻结后的快照**，不得访问可变工作树；每次读取前验内容哈希。
3. 模型输出**不得承载可应用代码变更**（Finding 只含位置、证据、影响、建议）。
4. Secret（GitHub token、webhook secret）走 `credentials`，不得进日志/事件/Findings/Transcript。
5. 无鉴权 HTTP 只绑回环；对外 webhook 必须验签 + 反向代理/隧道。
6. 评论必须落在真实 changed hunk 上，宿主拒绝无效位置并保留同批其余有效评论。

---

## 2. 结论摘要

| 模块 | 可行性 | 迁移方式 |
|---|---|---|
| Review 引擎（快照/证据工具/编排/位置校验/Findings） | ✅ 可行 | DSH 复用 + CodeLens 思想照搬（TS 重写） |
| 入站 webhook 适配（复杂请求体 → 标准 ReviewRequest） | ✅ 可行 | 标准接口 + `review.trigger` 注册表 + `webServer` 路由 |
| 出站结果上传（FindingExportEnvelope → 平台格式 → gh 上传） | ✅ 可行 | 标准信封 + `review.report` 注册表 + `subprocess(gh)` |
| 插件系统本体（安装/加载/启停/配置） | 🟡 可行但形态变 | 从「运行时 git 安装 Python 插件」改为「npm 包 + Cordis 组合」 |

三条核心判断：

1. **CodeLens 的价值不在载体（Python/git_url），而在「标准接口 + 可插拔适配器」的解耦**——这个解耦在 DSH 上 1:1 保留。
2. **引擎是语言无关的**，一切位置校验/零伪完成都作用在 unified diff 文本上。
3. **DSH 的 `dsh-headless`（`dsh --profile headless "<task>"`）是触发面的完美接口**：一次性运行、stdout 输出最后一条 assistant 文本、exit 0/1，无 HTTP 服务器。

---

## 3. 两个系统的本质定位

| 维度 | CodeLens | DeepSeek Harness |
|---|---|---|
| 是什么 | 本地多 Agent 代码 Review 工作台（领域应用） | 本地 Agent 宿主框架（harness/composition） |
| 运行时 | FastAPI + Worker 同进程，React/Vite 前端独立进程 | Cordis 组合单进程 + 内嵌 Web（可只挂 headless） |
| 模型编排 | 自研确定性 DAG + OpenAI Agents SDK | `agentLoop` + `subagents` + `workflow` + `goals`/`ralph` |
| 工具模型 | 版本化 Tool Contract + Capability Profile + FrozenExecutionSpec | `tools` 注册表（register/restrict/guard）+ Typert 严格 Schema |
| 持久化 | SQLAlchemy + Alembic（关系型，迁移/事务/幂等） | 追加式 JSONL 会话日志 + JSON 存储域 + 可选 SQLite（仅搜索） |
| 前端 | 完整 App（Findings + Monaco Diff + 设置） | Chat 壳 + Slots + React Client 插件 |

关键推论：**迁移不保留 CodeLens 的关系型数据层与 Monaco 前端**，这两块是「形态」，不是「思想」；用 DSH 的会话日志 + JSON 存储域 + 结构化 stdout 替代。

---

## 4. 迁移前后能力对照

图例：✅ 直接复用 · 🟡 改造复用 · 🔴 重写 · ➖ 舍弃

### 4.1 引擎 / 运行时

| CodeLens 能力 | 迁移前载体 | 迁移后载体（DSH） | 方式 | 说明 |
|---|---|---|---|---|
| LLM 调用 / 多供应商 / 密钥 | OpenAI Agents SDK + Secret Store | `llm` 适配器注册表 + `credentials` | ✅ | DSH 更通用，覆盖多网关 |
| 模型可见工具注册 | 自研 Tool Contract | `tools.register` + Typert Schema | ✅ | 严格 JSON Schema 双方一致 |
| 工具按角色白名单 | Capability Profile | `tools.restrict` + `guard` | ✅ | 一一对应 |
| 文本 Skill 策略 | Skill Policy（只读文本） | `skills` 注册表 | ✅ | 语义一致 |
| 系统提示分层 | i18n 语言包 + Context Builder | `systemPrompt.section` + `context` | ✅ | 逐段组装、优先级分层 |
| 上下文压缩 | Epoch Checkpoint | `compaction` + `tool-result-pruner` + `token-meter` | ✅ | 同构，最省力 |
| 成本/诊断计量 | Process Report + model.log | `token-meter` + `session-stats` + telemetry | ✅ | 直接复用 |
| 子任务扇出 | 自研 DAG Scheduler | `subagents` + `workflow` | 🟡 | 原语在，DAG 状态机需自建 |
| Agent 循环/重试/熔断 | OpenAI Agents SDK + 自研 loop | `agentLoop` + `llm/stream` 瀑布 | ✅ | 直接复用 |

### 4.2 工具 / 契约

| CodeLens 能力 | 迁移前载体 | 迁移后载体（DSH） | 方式 | 说明 |
|---|---|---|---|---|
| 只读证据工具（find/grep/read/get_diff） | 自研 Snapshot 工具 | 新写（快照作用域，见 §5.6） | 🔴 | 快照语义无 DSH 对应，需重建 |
| 有状态输出工具（comment/retract/task_done） | 自研 Collector | 新写（`tools.register` 有状态工具） | 🟡 | 状态机需自建，注册机制复用 |
| Planner/Verifier 工具（finalize_plan/verdict/merge） | 自研 | 新写 | 🔴 | P3 引入 |
| Tool Result v2 信封 | 自研 canonical serializer | 新写（复用其 Schema 语义） | 🔴 | 照搬 CodeLens 的 status/diagnostics 语义 |

### 4.3 编排 / 多 Agent

| CodeLens 能力 | 迁移前载体 | 迁移后载体（DSH） | 方式 | 说明 |
|---|---|---|---|---|
| 确定性图流控（Planner→Reviewer→Verifier） | runtime-dag | `subagents` + 自建 DAG 状态机 | 🔴 | 原语在，状态机重写 |
| ReviewPlan 持久化 / checkpoint / 重启恢复 | SQLAlchemy + checkpoint | session 日志 + JSON 存储域 | 🔴 | 持久化载体换成 JSON 域 |
| sticky partial / 事务性 verdict | 自研 | 自建状态机 | 🔴 | 业务资产，语言无关重写 |
| 零伪完成（task_done 覆盖门禁） | 自研 | 自建（复用证据工具调用日志） | 🔴 | 思想照搬，实现重写 |

### 4.4 插件体系

| CodeLens 能力 | 迁移前载体 | 迁移后载体（DSH） | 方式 | 说明 |
|---|---|---|---|---|
| TriggerSinkPort（入站适配） | Python 插件 + 受控 loader | `review.trigger` 注册表 + `webServer.register` | 🟡 | Port 解耦保留，载体变 TS |
| ReportSinkPort（出站适配） | Python 插件 | `review.report` 注册表 + `subprocess(gh)` | 🟡 | 同上 |
| 插件安装（git_url 运行时安装） | `POST /install` | `dsh plugin add`（pnpm） | 🟡 | 形态变化，功能等价 |
| Manifest 版本校验 | `plugin_api_version` 校验 | 自定义 manifest Schema + 组合层校验 | 🟡 | 自建校验 |
| enable/disable/config | API + 配置修订 | cordis.yml `disabled` + `settings` 命名空间 | ✅ | 原生支持 |
| 事件分发 / 导出编排 | 自研 | 引擎在 trigger/report 边界分发 | 🟡 | 引擎自建 |

### 4.5 前端 / UI（整体舍弃）

| CodeLens 能力 | 迁移后 | 方式 | 说明 |
|---|---|---|---|
| Findings 列表 + Monaco Diff | ➖ 舍弃 | ➖ | 用结构化 JSON 输出替代 |
| Review 列表 / 范围选择 / 设置页 | ➖ 舍弃 | ➖ | 用 `/review` 命令 + profile 配置替代 |
| SSE 实时事件流 | ➖ 舍弃（或映射为 session 事件日志） | ➖ | headless 无需实时 UI |

### 4.6 持久化 / 数据

| CodeLens 能力 | 迁移前载体 | 迁移后载体（DSH） | 方式 | 说明 |
|---|---|---|---|---|
| 任务/checkpoint/事件/Finding 关系模型 | SQLAlchemy + Alembic | session JSONL + `storage` JSON 域 | 🟡 | 关系型迁移为 JSON 域，丢弃迁移体系 |
| 幂等 / 部分失败恢复 | 事务 + 幂等键 | JSON 域 + 幂等键 | 🟡 | 重写 |
| outbox / SSE 断线续传 | 自研 outbox | session 事件日志 | 🟡 | 语义可迁，传输层重写 |
| Secret Store | 本地 JSON（0700/0600） | `credentials` 服务 | ✅ | 原生支持 |

### 4.7 安全 / 信任边界

| CodeLens 能力 | 迁移后载体（DSH） | 方式 | 说明 |
|---|---|---|---|
| 仓库只读 | 证据工具契约只读（不依赖沙箱） | ✅ | 工具层保证，与 CodeLens 同思路 |
| detached worktree 隔离 | 可延后（P4 加固） | 🟡 | MVP 用 git 只读读取 |
| 每读验内容哈希 | 快照层自建 | 🔴 | 照搬 |
| Secret 不进日志 | `credentials` | ✅ | 原生 |
| 回环绑定 | `dsh-host-webserver` 只认 127.0.0.1/0.0.0.0 | ✅ | 需自建验签 + 反代 |

---

## 5. 目标架构

### 5.1 运行形态与 Profile

```
~/.dsh/profiles/code-review/
├── package.json        # dsh.profile.bundles: ["@deepseek-ai/dsh-headless", "dsh-code-review"]
├── cordis.patch.yml    # 用户 patch 层（enable/disable/config 覆盖）
└── node_modules/       # pnpm 安装 dsh-code-review 及依赖
```

- **P1-P2 用 headless 形态**：`dsh --profile code-review "<task>"`，无 HTTP 服务器、无浏览器。
- **P3 webhook 形态**：同一 `dsh-code-review` 包 + `dsh-base` + `dsh-host-webserver`（不挂 `dsh-web-app`），常驻监听。

### 5.2 插件划分（`dsh-code-review` 包内的 Cordis 插件行）

| 插件行 | 职责 | 提供 / 注册 |
|---|---|---|
| `review-snapshot` | 冻结仓库：解析 refs、算 diff、生成文件清单 + hunk + 内容哈希 | `review.snapshot` 服务 |
| `review-context` | 生成 `review_files`（规范化路径 + 变更类型 + 可评论 old/new 范围） | `review.context` 服务 |
| `review-evidence` | 4 个只读证据工具，圈定快照、每读验哈希 | `tools.register` + `tools.restrict` |
| `review-comment` | 有状态输出工具 + 覆盖门禁 | `tools.register` |
| `review-locate` | 确定性行号定位（line_resolver） | `review.locate` 服务 |
| `review-orchestrate` | 任务状态机 + 编排 + 终态聚合 | `review.orchestrator` 服务 |
| `review-findings` | Findings 模型 + 信封序列化 | `review.findings` 服务 |
| `review-trigger` | 入站 Port 注册表（webhook 适配器接入点） | `review.trigger` 服务 |
| `review-report` | 出站 Port 注册表（报告/上传适配器接入点） | `review.report` 服务 |
| `review-prompt` | 各角色系统提示段 | `systemPrompt.section` |
| `review-command` | `/review` 斜杠命令 + headless 任务解析 | `commands.register` |
| `review-local-report` | 内置 `local` 报告插件（本地 JSON 导出） | 实现 `review.report` Port |

### 5.3 服务与 Port 接口

以下为 TS 接口签名（DSH 插件为 TS，标识符遵循本仓库英文命名规范）：

```ts
// review.snapshot
interface ReviewSnapshotService {
  freeze(req: ReviewRequest): Promise<FrozenSnapshot>
}
interface FrozenSnapshot {
  snapshotId: string                 // 内容哈希派生
  repoRoot: string                   // 宿主路径，不暴露给模型
  manifest: SnapshotManifest         // 文件清单 + 内容哈希
  readFile(path: string, version: 'base'|'head'|'current', opts?): Promise<FileRead>
  diff(path: string, cursor?): Promise<DiffPage>
  // 每次 readFile/diff 内部重新校验 manifest 哈希，失败抛 SnapshotIntegrityError
}

// review.context
interface ReviewContextService {
  build(snapshot: FrozenSnapshot): Promise<ReviewFiles>
}
interface ReviewFiles {
  files: ReviewFile[]                // 稳定排序
  totalCount: number
}
interface ReviewFile {
  path: string                       // 规范化仓库相对 POSIX 路径
  change: 'added'|'modified'|'deleted'|'renamed'
  renameFrom?: string
  oldRanges: LineRange[]             // 允许产生评论的 old 侧行范围
  newRanges: LineRange[]             // 允许产生评论的 new 侧行范围
}

// review.locate
interface ReviewLocateService {
  resolve(candidate: RawComment, snapshot: FrozenSnapshot): LocatedComment | DiscardReason
}

// review.orchestrator
interface ReviewOrchestrator {
  start(req: ReviewRequest): Promise<ReviewOutcome>
  cancel(taskId: string): Promise<void>
}

// review.trigger（入站 Port 注册表）
interface TriggerPort {
  readonly platform: string
  readonly event: string
  register(route: WebRoute, verify: (body: unknown) => ReviewRequest): () => void
}
interface ReviewTriggerService {
  register(adapter: TriggerAdapter): () => void
  submit(req: ReviewRequest): Promise<SubmitResult>   // 幂等 + supersede
}

// review.report（出站 Port 注册表）
interface ReportPort {
  readonly pluginId: string
  readonly platforms: string[]        // 匹配 review_request.source.platform
  export(env: FindingExportEnvelope): Promise<ExportResult>
}
interface ReviewReportService {
  register(port: ReportPort): () => void
  dispatch(env: FindingExportEnvelope): Promise<ExportResult[]>
}
```

### 5.4 两个标准接口（Schema）

这两个 Schema 是插件体系的地基，用 DSH Typert 严格 Schema 承载，`required` 全量、拒绝未知字段。

**入站 `ReviewRequest`：**

```json
{
  "schema_version": "2",
  "source": { "platform": "cli", "event": "manual", "delivery_id": "" },
  "repo": { "path": "/abs/local/repo", "provider": "local" },
  "scope": { "kind": "branch", "base_ref": "origin/main", "target_ref": "HEAD" },
  "idempotency_key": "cli:manual:<hash>",
  "existing_findings": [],
  "labels": {}
}
```

- `scope.kind` ∈ `branch | commit | pr | full | uncommitted`
- `existing_findings`：结构化已有问题，用于跨 revision 去重（对应 CodeLens 的 `ExistingFindingV2`）
- `idempotency_key`：宿主幂等 + latest_snapshot supersede 的键

**出站 `FindingExportEnvelope`：**

```json
{
  "schema_version": "2.0",
  "review_request": { "...ReviewRequest..." },
  "verdict_summary": {
    "total": 12, "accepted": 10, "discarded": 2,
    "coverage": { "total_files": 30, "reviewed": 30, "missing": 0 }
  },
  "findings": [
    {
      "id": "f-<hash>", "path": "src/foo.py", "side": "new",
      "line_start": 42, "line_end": 45,
      "title": "...", "severity": "high", "category": "correctness",
      "evidence": { "excerpt": "...", "strength": "strong" },
      "recommendation": "..."
    }
  ]
}
```

### 5.5 快照机制

`review-snapshot` 用 `ctx.subprocess` 跑 git（`git rev-parse` / `git diff --no-ext-diff` / `git show`），**只读，MVP 不建 detached worktree**（P4 再加）：

1. 解析 `scope` → 确定 base/target（`branch`：`git diff base..target`；`uncommitted`：`git diff HEAD` + 未忽略 untracked；`full`：target 树全量）。
2. 生成快照清单：每个改动文件 → 变更类型 + old/new 行范围 + base/target 内容哈希。
3. 正文读取：`git show <ref>:<path>`（base/head）或工作树（current），读前验哈希。
4. `snapshotId` = 清单内容哈希；任何内容不匹配 → `SnapshotIntegrityError`，本轮失败。

> 前提：目标仓库必须是 DSH 进程 cwd（在其 workspace-write 沙箱内）。审工作区外仓库需把仓库设为 DSH 工作区或提升沙箱权限——部署时显式边界。

### 5.6 工具契约

#### 5.6.1 证据工具（只读、快照作用域、严格 Schema）

| 工具 | 参数（全部 `required`） | 返回 |
|---|---|---|
| `find_files` | `pattern`（Glob：无 `/` = 递归 basename，含 `/` = POSIX path pattern） | 匹配路径 + `total_matches` + `truncated` |
| `grep` | `pattern` + `mode(literal\|regex)` + 可选 `path` / `file_pattern` | 命中 `file` + `line` + `content` |
| `read_file` | `path` + `version(base\|head\|current)` + `line_range?` | 带稳定行前缀的行 + 续读范围 |
| `get_diff` | `path` + `cursor?`（opaque，绑 snapshotId + path + hunk） | unified diff hunks 分页 |

路径规范化（照搬 CodeLens）：空串/`.`/`..` = 快照根；移除单个前导 `..` 与尾 `/`；拒绝绝对路径、Windows drive path、反斜杠、NUL、任何 `..` segment。返回只含规范化相对路径，不暴露宿主路径。

**Tool Result 信封（照搬 CodeLens Tool Result v2 语义）：**

```json
{
  "schema_version": "2",
  "tool": "read_file",
  "status": "success|partial|needs_action|rejected|failed",
  "data": {},
  "diagnostics": [
    { "code": "missing_review_files", "message": "...", "retryable": true, "field": "path" }
  ]
}
```

宿主只按 status 把 `success`/`partial` 归为 accepted，其余归为 rejected；非 JSON/缺字段/未知状态归为 unclassified。

#### 5.6.2 输出工具（有状态，MVP 单 reviewer）

| 工具 | 作用 |
|---|---|
| `comment(comments[])` | 批量提交候选，每条 `{path, side(old\|new), excerpt, title, content, severity, category}`；返回 `candidate_id` + 逐条诊断 |
| `retract_comment(ids[])` | 幂等撤销当前 Run 的候选 |
| `task_done()` | 声明完成；宿主以 `review_files` 为基准校验覆盖，未读完返回 `needs_action/missing_review_files` + 缺失清单 |

状态按 AgentRun（= 该 reviewer 的 session）隔离，`task_done` 成功后 comment 状态不可再变。

#### 5.6.3 多 specialist 工具（P3 引入）

- Planner：`finalize_plan(selection)`（仅 adaptive 选择调用一次）
- Verifier：`verdict(cluster_ids, action=accept|deny)`、`merge(cluster_ids, ...)`、`finalize_verdicts()`

### 5.7 编排状态机

```
created → snapshot_frozen → context_built → running → completed | partial | failed | canceled
                                                        └────→ findings_exported
```

MVP（单 reviewer）流程：

```
snapshot → review_files → reviewer agent（证据工具 + comment/task_done）
   ├─ 宿主记录每次 read 覆盖的文件
   ├─ task_done: 未全覆盖 → needs_action + 缺失文件；超重试阈值 → 强制完成并标 partial
   └─ 覆盖完整 → 逐条 comment 走 review.locate → 丢弃无效 → 聚合 FindingExportEnvelope
```

P3 多 specialist：`subagents` 并发（correctness/security/performance/maintainability）→ Verifier 用 `verdict/merge` 事务性合并（Cluster→Finding，sticky partial）。所有阶段事实只读持久化 checkpoint，不能以进程内 `gather` 返回值为准。

### 5.8 位置解析（line_resolver，语言无关）

模型提交 `(path, side, excerpt)`，宿主：

1. 取该文件快照 diff hunks。
2. 在对应 side（old/new）正文定位 `excerpt`。
3. 确认其完整落在**唯一 changed hunk** 内 → 派生 side、hunk id、行号。
4. 找不到 / 越界 / 侧别不符 → 丢弃该条（保留同批其余有效评论）。

照搬 CodeLens `line_resolver.py` 逻辑改写为 TS，作用在 unified diff 文本上。

### 5.9 触发与报告流程

#### 触发（入口统一产出 `ReviewRequest`）

| 入口 | 机制 |
|---|---|
| headless CLI | `dsh --profile code-review "/review branch origin/main"` |
| 交互斜杠命令 | `/review [scope] [args]`（`commands.register`） |
| Git Hook（P3） | pre-push 调 headless |
| CI（P3） | 流水线调 headless |
| Webhook（P3） | `webServer.register('/webhook/<platform>/<event>')` + 适配器解析 → `ReviewRequest` |

> P1 spike：确认 headless 下 `/review` 命令文本的派发路径（`commands.execute` 还是首条 user message 直读）；两种都支持，二选一固化。

#### 报告（入口统一消费 `FindingExportEnvelope`）

| 出口 | 机制 |
|---|---|
| 内置 `local`（P1） | 写 JSON 到仓库根 `../../CodeLensReview`（UTC 时间戳，不覆盖历史） |
| GitHub PR 上传（P3） | `gh` CLI：`gh pr review` / `gh api`（`ctx.subprocess.spawn`，参数数组） |
| 其它平台（P4） | 各自 report 插件实现 `review.report` Port |

---

## 6. 迁移路径规划

### P0 —— 动态插件原型验证（当前会话内，不持久化）

- **目标**：用 DSH 动态 Cordis 插件（`cordis_define/run`）跑通核心链路，验证可行性，不产出产品代码。
- **范围**：冻结快照（git diff + 哈希）→ 证据工具（find/grep/read/get_diff）→ comment/task_done + 覆盖门禁 → line_resolver → 单一 reviewer → FindingExportEnvelope。
- **交付物**：一个可运行的原型插件 + 一次真实仓库跑通记录。
- **验收**：对 CodeLens 仓库跑一次 review，产出合法 `FindingExportEnvelope`，无效位置被正确丢弃。
- **明确不做**：多 specialist、webhook、gh 上传、持久化插件包。

### P1 —— 引擎闭环（持久插件包）

- **目标**：把 P0 验证过的逻辑固化为 `dsh-code-review` npm 包 + `code-review` profile。
- **范围**：§5.2 除 `review-trigger`/`review-report`/`review-local-report` 外的插件行；`/review` 命令；`local` 报告写 JSON；headless 触发。
- **交付物**：`dsh-code-review` 包（可 `dsh plugin add` 安装）、`code-review` profile、`review-command` 的 headless 派发 spike 结论。
- **验收**：`dsh --profile code-review "/review"` 对任意 git 仓库产出 findings JSON + 正确 exit code；覆盖门禁与位置校验生效。

### P2 —— 插件骨架（Port 注册表 + 标准 Schema 冻结）

- **目标**：把 `ReviewRequest` / `FindingExportEnvelope` 冻结为 Typert Schema，建立 `review.trigger` / `review.report` 注册表 + manifest/启停/配置。
- **范围**：`review-trigger`、`review-report`、内置 `local` 报告插件、manifest Schema 校验。
- **验收**：外部插件可 `dsh plugin add` 后注册进 trigger/report 注册表；未声明能力/版本不匹配被拒；启停配置生效。

### P3 —— 真实适配器（webhook 入站 + GitHub 出站）

- **目标**：跑通「GitHub push webhook → review → PR 评论上传」完整闭环。
- **范围**：
  - 入站：GitHub push 适配器（`webServer.register('/webhook/github/push')` + 验签 + payload → ReviewRequest）；幂等 + supersede。
  - 出站：GitHub PR review 上传插件（`gh` CLI）；按来源平台路由。
  - 多 specialist + Verifier DAG（`subagents` + `finalize_plan`/`verdict`/`merge`）。
  - Git Hook 触发、CI 触发。
- **验收**：push 触发 review，gh 把 findings 作为 PR review comments 上传；幂等（重复 delivery 不重复建任务）。

### P4 —— 加固

- **范围**：GitLab 适配器、SARIF 输出、detached worktree 隔离、Prompt 本地化、webhook secret 轮换、多仓库并发隔离、GH REST 上传（可选，替代/补充 gh）。

---

## 7. 模块级任务分解（供实现 Agent）

每个模块给出：职责 / 输入 / 输出 / 依赖 / 验收标准。

### 7.1 review-snapshot

- **职责**：把 `ReviewRequest` 冻结为不可变 `FrozenSnapshot`（清单 + 内容哈希）。
- **输入**：`ReviewRequest`。
- **输出**：`FrozenSnapshot`（含 `readFile` / `diff` 只读接口）。
- **依赖**：`ctx.subprocess`（git）、`ctx.fs`（读工作树）。
- **验收**：对同一仓库同一 ref 重复 freeze 得到相同 `snapshotId`；改一个字节 → `SnapshotIntegrityError`；四种 scope 全部可解析。

### 7.2 review-context

- **职责**：从 snapshot 生成规范 `review_files`（变更类型 + 可评论 old/new 范围），稳定排序，超上限明确失败不静默截断。
- **输入**：`FrozenSnapshot`。
- **输出**：`ReviewFiles`。
- **验收**：added/deleted/renamed 的 range 语义正确；二进制文件排除；超限报错。

### 7.3 review-evidence

- **职责**：注册 4 个证据工具，圈定快照、每读验哈希、路径规范化、分页/续读、正则超时。
- **依赖**：`ctx.tools`（register/restrict）、`review.snapshot`。
- **验收**：工具只返回快照内数据；非法路径（绝对/`..`/反斜杠）被拒；get_diff 分页 cursor 绑定正确。

### 7.4 review-comment

- **职责**：注册 `comment` / `retract_comment` / `task_done`，维护 AgentRun 级状态与覆盖计数。
- **验收**：comment 返回 candidate_id + 逐条诊断；task_done 未全覆盖返回 `missing_review_files`；重复完成 `rejected`。

### 7.5 review-locate

- **职责**：把 `(path, side, excerpt)` 解析到唯一 changed hunk，派生 side/hunk id/行号。
- **验收**：对照 CodeLens `line_resolver.py` 的测试用例重写为 TS 测试，行为一致。

### 7.6 review-orchestrate

- **职责**：任务状态机 + reviewer 调度 + 覆盖/完成门禁 + 终态聚合。
- **验收**：completed/partial/failed/canceled 四态正确；partial 只出现在覆盖缺失/部分失败；取消可传播。

### 7.7 review-findings

- **职责**：Candidate → 校验 → Finding → `FindingExportEnvelope` 序列化。
- **验收**：信封 Schema 严格校验通过；无效位置被丢弃但保留同批有效评论。

### 7.8 review-trigger / review-report

- **职责**：入站/出站 Port 注册表 + manifest 校验 + 幂等/supersede（trigger）+ 按平台路由（report）。
- **验收**：见 P2 验收。

### 7.9 review-command / review-prompt

- **职责**：`/review` 命令解析 + headless 派发；各角色系统提示段（planner/reviewer/verifier 分离）。
- **验收**：`/review` 全 scope 可解析；角色提示不含敏感字段（快照 ID/宿主路径/哈希不进模型输入）。

---

## 8. 风险与开放问题

1. **headless 下 `/review` 命令派发路径未确认**（P1 spike，见 §5.9）。
2. **`tools.restrict` 与多 reviewer 的工具隔离粒度**：需确认按 agent/session 隔离工具是否原生支持，否则自建 scope 键。
3. **多 specialist 的 checkpoint 持久化**：JSON 域能否承载事务性 verdict 提交的幂等，P3 验证。
4. **`web.fetch` 默认关闭**：GitHub REST 路径需注册 fetch provider；当前以 `gh` CLI 为主，规避此风险。
5. **webhook 公网可达性**：需反向代理/隧道 + 验签；DSH webserver 无 TLS/鉴权。
6. **大仓库快照内存占用**：P4 需评估 snapshot 落盘（`ctx.spill` / `ctx.storage`）而非纯内存。

---

## 9. 术语表

| 术语 | 含义 |
|---|---|
| DSH | DeepSeek Harness，本地 Agent 宿主框架 |
| Profile | DSH 的组合形态，由 bundles + cordis.patch.yml 构成 |
| 插件行 / 插件 | Cordis 组合中的一个 plugin 条目，提供/消费 service |
| 快照（Snapshot） | 冻结的只读审查输入（文件清单 + diff + 内容哈希） |
| review_files | 规范化改动文件清单（变更类型 + 可评论行范围） |
| line_resolver | 确定性行号定位：摘录 → 唯一 changed hunk |
| FindingExportEnvelope | 出站标准信封，report 插件的唯一输入 |
| ReviewRequest | 入站标准请求，所有触发源的统一产出 |
| Port | 依赖倒置的抽象接口（入站 trigger / 出站 report） |
