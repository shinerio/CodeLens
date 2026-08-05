# CodeLens 多 Agent Review DAG 编排

## 1. 文档目的

本文描述 CodeLens 多 Agent Review 的 DAG（有向无环图）设计、阶段编排、角色职责和工具集。适用于理解 Review 任务从创建到完成的完整执行链路。

系统边界和长期约束以 [`ARCHITECTURE.md`](./ARCHITECTURE.md) 为准，单 Reviewer 的模型 Runtime 机制见 [`runtime-mechanism.md`](./runtime-mechanism.md)，内置工具参数说明见 [`build-in-tool.md`](./build-in-tool.md)。

---

## 2. DAG 总览

CodeLens 支持两种执行路径，由是否包含 `ReviewPlan` 决定：

### 2.1 简单路径（无 DAG）

单 Reviewer 场景，所有执行规格并行运行，不经过 DAG 调度：

```
Preparing → Reviewing → Validating → Synthesizing → Completed
```

### 2.2 DAG 路径（多 Agent）

多 Reviewer 场景，通过 `PersistedDagScheduler` 按依赖关系调度节点执行：

```
                  [Planner]（仅 adaptive 模式）
                      |
         +------------+------------+
         |            |            |
    [Reviewer A] [Reviewer B] [Reviewer C]    ← 并行执行
         |            |            |
         +------------+------------+
                      |
                  [Verifier]（Final Verifier，verdict 阶段）
                      |
                  Completed
```

---

## 3. 任务级状态机

`ReviewTask` 的状态由 `ReviewStatus` 枚举定义（`review/domain/models.py`）：

```
CREATED
  → PROVISIONING_WORKTREE     创建 Git worktree
    → SNAPSHOTTING            冻结代码快照
      → PREPARING             构建执行规格
        → PLANNING            编译 Review Plan（仅 adaptive）
          → REVIEWING         执行 Reviewer 节点
            → VERIFYING       执行 Verifier 节点（仅多 Reviewer，verdict 阶段）
            → VALIDATING      验证输出（仅单 Reviewer）
              → SYNTHESIZING  汇总结果（仅单 Reviewer）
```

**终态**：`COMPLETED`（全部成功）、`PARTIAL`（部分成功）、`FAILED`（全部失败）、`CANCELED`（用户取消）、`SUPERSEDED`（被新 Review 取代）

### 3.1 允许的状态转换

| 当前状态 | 可转换到 |
|---------|---------|
| `CREATED` | `PROVISIONING_WORKTREE` |
| `PROVISIONING_WORKTREE` | `SNAPSHOTTING` |
| `SNAPSHOTTING` | `PREPARING` |
| `PREPARING` | `PLANNING`（adaptive）、`REVIEWING`（fixed） |
| `PLANNING` | `REVIEWING` |
| `REVIEWING` | `VALIDATING`、`VERIFYING`、`COMPLETED`、`PARTIAL` |
| `VERIFYING` | `COMPLETED`、`PARTIAL` |
| `VALIDATING` | `SYNTHESIZING` |
| `SYNTHESIZING` | `COMPLETED`、`PARTIAL` |

---

## 4. 节点级状态机

每个 DAG 节点（Agent 执行实例）有独立的生命周期，由 `AgentRun` 管理（`review/domain/agent_run.py`）：

```
PENDING
  → RUNNING              开始执行
    → OUTPUT_SAVED       输出已持久化（SHA-256 校验）
      → VALIDATING       验证输出格式
        → SUCCEEDED      验证通过

    RUNNING/VALIDATING → FAILED（执行或验证失败）
    RUNNING → TIMED_OUT（超时）
    PENDING/RUNNING → CANCELED（取消）
    FAILED/TIMED_OUT → PENDING（重试，未超过最大次数时）
    PENDING → SKIPPED（前置节点全部失败时跳过）
```

**关键不变量**：输出必须先持久化到 `OUTPUT_SAVED` 并校验哈希，才能进入 `VALIDATING`。这保证进程崩溃后可以从持久化输出恢复验证。

---

## 5. 阶段编排

### 5.1 基础设施阶段

| 阶段 | 职责 | 输出 |
|------|------|------|
| **Provisioning** | 创建隔离的 Git worktree | worktree 路径 |
| **Snapshotting** | 冻结代码快照，生成 manifest | Snapshot 引用 |
| **Preparing** | 加载 Agent 目录，构建执行规格 | `FrozenAgentExecutionSpec` 列表 |

### 5.2 Planning 阶段（仅 adaptive 模式）

- **触发条件**：`PREPARING` 完成且选择模式为 `AdaptiveReviewerSelection`
- **职责**：Planner Agent 分析变更风险，从 eligible reviewer 中选择本次 Review 的团队
- **输出**：`PlannerSelection`（reviewer 引用列表）
- **编译**：`ReviewPlanCompiler` 将选择编译为 DAG 节点和依赖关系
- **跳过条件**：Fixed 模式直接进入 Reviewing

### 5.3 Reviewing 阶段

- **触发条件**：Planning 完成（adaptive）或 Preparing 完成（fixed）
- **职责**：每个 Reviewer Agent 独立审查代码，提交 Finding
- **并行性**：所有 Reviewer 节点并行执行
- **输出**：`FindingBatch`（Reviewer v2）或 `CandidateFindingBatch`（legacy v1）

### 5.4 Verdict 阶段（仅多 Reviewer）

- **触发条件**：所有 Reviewer 节点到达终态，且至少一个成功
- **职责**：Final Verifier Agent 接收所有 Reviewer 的 Candidate，宿主已确定性聚类为 `FindingCluster`，Verifier 通过 `verdict`（accept/deny）、`merge`（合成字段）和 `finalize_verdicts`（校验覆盖）三工具对每个 Cluster 做出终审决策
- **输出**：`ValidatedVerdictBatch`（accept/deny/merge 决策）

### 5.5 Validating + Synthesizing（仅单 Reviewer）

- **触发条件**：单 Reviewer 完成且无 Verifier
- **职责**：验证 Finding 格式，汇总为最终结果
- **输出**：发布到前端

---

## 6. 角色定义

### 6.1 Planner（`review-planner:v1`）

| 属性 | 值 |
|------|---|
| **角色** | `AgentRole.PLANNER` |
| **Pass** | 0（最先执行） |
| **职责** | 分析代码变更的风险特征，从 eligible reviewer 目录中选择本次 Review 需要的 specialist reviewer |
| **选择模式** | 仅 adaptive 模式使用；fixed 模式由用户手动选择 |
| **候选资格** | `general:v1` 不参与 adaptive 选择（`planner_eligible=False`），仅 specialist reviewer 可被 Planner 选择 |
| **输出** | `PlannerSelection`：选中的 reviewer 引用列表 |
| **完成信号** | 调用 `finalize_plan` |

### 6.2 Reviewer（多个 specialist + general）

| 属性 | 值 |
|------|---|
| **角色** | `AgentRole.REVIEWER` |
| **Pass** | 1 |
| **职责** | 使用证据工具阅读代码变更，按自身维度提交 Finding |
| **并行性** | 多个 Reviewer 并行执行 |
| **Specialist** | `security:v1`、`correctness:v2`、`reliability-concurrency:v1`、`contract-data:v1`、`architecture:v1`、`performance:v1`、`test-regression:v1` — 各自聚焦单一维度 |
| **General** | `general:v1` — 覆盖所有维度，必须单独运行（不与其他 Reviewer 组合） |
| **输出** | `FindingBatch`（v2）或 `CandidateFindingBatch`（v1 legacy） |
| **完成信号** | 调用 `task_done` |

### 6.3 Verifier / Final Verifier（`review-verifier:v1`）

| 属性 | 值 |
|------|---|
| **角色** | `AgentRole.VERIFIER` |
| **Pass** | 2 |
| **职责** | 接收所有 Reviewer 的 Candidate 和宿主已聚类的 `FindingCluster`，通过 `verdict`（accept/deny）、`merge`（合成字段）和 `finalize_verdicts`（校验覆盖）三工具对每个 Cluster 做出终审决策 |
| **出现条件** | 仅多 Reviewer 计划 |
| **依赖** | 所有 Reviewer 节点 |
| **输出** | `ValidatedVerdictBatch` |
| **完成信号** | 调用 `finalize_verdicts` |
| **Prompt Key** | `review-verdict` |

---

## 7. 工具矩阵

### 7.1 按角色分配

| 工具 | 版本 | Planner | Reviewer (v2) | Reviewer (v1) | Verifier |
|------|------|---------|--------------|--------------|----------|
| `find_files` | 1 | ✓ | ✓ | ✓ | — |
| `grep` | 1 | ✓ | ✓ | ✓ | — |
| `read_file` | 1 | ✓ | ✓ | ✓ | ✓ |
| `get_diff` | 1 | ✓ | ✓ | ✓ | ✓ |
| `comment` | 2 | — | ✓ | — | — |
| `comment` | 1 | — | — | ✓ | — |
| `review_file_done` | 1 | — | ✓ | ✓ | — |
| `task_done` | 1 | — | ✓ | ✓ | — |
| `submit_review_plan` | 1 | ✓ | — | — | — |
| `finalize_plan` | 1 | ✓ | — | — | — |
| `verdict` | 1 | — | — | — | ✓ |
| `merge` | 1 | — | — | — | ✓ |
| `finalize_verdicts` | 1 | — | — | — | ✓ |

### 7.2 证据工具

所有证据工具由 `FilesystemReviewTools` 提供，操作范围限定在冻结的 Snapshot 内。

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `find_files` | Glob 模式文件发现 | `path`, `pattern`（支持 `**`） |
| `grep` | 正则文本搜索 | `pattern`（Python regex）, `path`, `file_pattern` |
| `read_file` | 有界文件读取 | `path`, `version`（current/base/head）, `start_line`, `end_line` |
| `get_diff` | 获取变更 diff | `path` |

### 7.3 角色输出工具

| 工具 | 所属角色 | 用途 | 参数 |
|------|---------|------|------|
| `submit_review_plan` | Planner | 添加一批 reviewer 引用到 Plan（可多次调用，自动去重） | `reviewer_references: list[str]` |
| `finalize_plan` | Planner | 验证累积的 reviewer 集合是否完全匹配 eligible 集，完成 Plan | 无参数 |
| `comment` (v2) | Reviewer | 提交 Finding（引用代码而非行号） | `reviewer_id`, `path`, `existing_code`, `title`, `content`, `severity`, `category`, `evidence_strength` 等 |
| `review_file_done` | Reviewer | 声明已审查的文件列表 | `reviewed_files: list[str]` |
| `task_done` | Reviewer | 声明当前 Reviewer 工作结束 | `summary: str` |
| `verdict` | Verifier | 对 cluster 做出 accept（原样接收）或 deny（拒绝误报）决策 | `cluster_ids: list[str]`, `action: "accept"\|"deny"` |
| `merge` | Verifier | 将多个 cluster 合并为单个 Finding，所有字段必填 | `cluster_ids: list[str]`, `path`, `side`, `title`, `content`, `recommendation`, `category`, `severity` 等 |
| `finalize_verdicts` | Verifier | 校验所有 cluster 被 accept/deny/merge 之一覆盖且无重复，完成 verdict 阶段 | 无参数 |

### 7.4 禁止工具

以下工具名被 `FORBIDDEN_REVIEW_TOOL_NAMES` 禁止出现在任何 Review 能力配置中：

`shell`、`write_file`、`apply_patch`、`git`、`network`、`load_skill`、`discover_tools`

---

## 8. DAG 依赖规则

### 8.1 节点依赖

| 节点类型 | 依赖 | 可执行条件 |
|---------|------|-----------|
| **Planner** | 无 | 立即可执行 |
| **Reviewer**（adaptive） | Planner | Planner 成功后 |
| **Reviewer**（fixed） | 无 | 立即可执行 |
| **Verifier** | 所有 Reviewer | 所有 Reviewer 到达终态，且至少一个成功 |

### 8.2 关键约束

- **Verifier 容错**：即使部分 Reviewer 失败，只要至少一个成功，Verifier 仍可执行
- **无依赖死锁检测**：如果调度器找不到可执行节点且存在 pending 节点，抛出 `RuntimeError`
- **节点唯一性**：最多一个 Planner、一个 Verifier

### 8.3 DAG 不变量（`ReviewPlan.create()` 强制执行）

1. Reviewer 引用非空且唯一
2. `general:v1` 和 `correctness:v1` 必须单独运行
3. Adaptive 模式必须有且仅有一个 Planner 节点
4. Fixed 模式不能有 Planner 节点
5. 多 specialist 计划必须有 Verifier
6. Verifier 必须依赖所有 Reviewer 节点
7. 所有依赖必须引用已知节点

---

## 9. 选择模式

### 10.1 Fixed（固定团队）

用户手动选择 Reviewer 列表，不经过 Planner：

```
Preparing → Reviewing → [Verifying] → Completed
```

- 支持所有 public 和 legacy reviewer
- `general:v1` 可被手动选择（但必须单独运行）
- 无 Planner 节点

### 10.2 Adaptive（自适应）

Planner Agent 根据代码变更风险自动选择 specialist reviewer：

```
Preparing → Planning → Reviewing → Verifying → Completed
```

- 仅 `planner_eligible=True` 且 `is_public=True` 且 `is_legacy=False` 的 reviewer 可被选择
- `general:v1` 不参与 adaptive 选择
- 至少选择 2 个 specialist reviewer
- 必须包含 Planner 节点

---

## 11. 故障处理

| 场景 | 行为 |
|------|------|
| 所有 Reviewer 失败 | 任务标记为 `FAILED`（`all_reviewers_failed`） |
| 部分 Reviewer 失败 | Verifier 仍执行，任务可能标记为 `PARTIAL` |
| Verifier 失败 | 任务标记为 `PARTIAL`，已满足直接发布条件的 Finding 仍发布 |
| 节点超时 | 标记为 `TIMED_OUT`，按依赖规则处理 |
| 用户取消 | 所有非终态节点标记为 `CANCELED`，任务标记为 `CANCELED` |
| 进程崩溃重启 | 从持久化 checkpoint 恢复，跳过已完成节点 |
