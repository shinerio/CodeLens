# CodeLens 多 Agent Review 设计

> **SUPERSEDED（2026-08-09）：** 本文中的 v1 兼容和迁移设计已废弃。当前实现合同与执行顺序以 [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) 和 [`2026-08-09-multi-agent-review-v2-hard-cut.md`](../plans/2026-08-09-multi-agent-review-v2-hard-cut.md) 为准，本文仅保留为历史记录。

## 1. 文档状态

- 状态：已批准（后端编排、插件、Capability 与前端交互均已确认）
- 日期：2026-07-31
- 适用仓库：CodeLens
- 目标：把现有单 Reviewer Review 扩展为可配置、可恢复、可审计的多 Agent 并行 Review，并为后续 MCP 与 Skills 提供稳定能力边界。

本文描述目标设计，不代表当前代码已经实现。实现进入主干时，必须同步更新 `docs/ARCHITECTURE.md` 中受影响的系统事实与长期约束。

## 2. 背景与问题

CodeLens 当前虽然允许任务保存多个 Agent 引用，`ReviewOrchestrator` 也会并发调用 `PreparedReview.agents`，但运行期目录只提供 `correctness:v1`，且 `synthesizing` 阶段没有真正的跨 Agent 语义汇总。现有实现还存在以下限制：

- Reviewer 目录没有形成职责明确的专业角色集合。
- 所有 Agent 使用同一类输出，缺少 Candidate、Cluster、Resolution 和 Published Finding 的分层。
- 任意并行 Agent 异常可能使整批 Review 失败。
- 节点键仍按单 Agent 形态构造，不能稳定表达多 Pass DAG。
- Finding 去重主要发生在单个 Agent 输出内部，缺少跨 Agent 根因合并。
- 插件自动触发只接受提前配置的 `selected_agents`，没有统一的 Fixed/Adaptive 协议。
- OpenAI Runtime 直接硬编码七个模型可见工具，尚未形成按 Agent 角色冻结的 Capability Profile。
- 当前明确不支持 MCP 与 Skills，未来扩展所需的权限、版本和信任边界尚未落为执行契约。

本设计的核心问题不是简单增加并发调用，而是建立一条可控的 fan-out/fan-in 流水线：多个独立 Reviewer 从不同风险维度调查同一冻结 Snapshot，宿主确定性校验候选，再由受约束的 Resolver 合并、裁决和发布。

## 3. 目标与非目标

### 3.1 目标

- 支持多个专业 Reviewer 从不同风险维度并行 Review。
- 提供 Fixed 与 Adaptive 两种互斥的 Reviewer 选择模式。
- 支持低成本、广而浅的 General Reviewer。
- 使用统一 `ReviewPlan` 和持久化 DAG 执行两种模式。
- 在内部保留高召回 Candidate，在外部只发布高精度 Finding。
- 支持局部失败、重试、取消、恢复、幂等和插件高频自动触发。
- 为每种 Agent 角色定义最小 built-in tools 集合。
- 通过版本化 Capability Profile 为未来 MCP、代码图、静态分析和 Skills 保留稳定扩展点。
- 保持 Snapshot 只读隔离、Secret、Prompt Injection 和 Transcript 边界。

### 3.2 非目标

- 本阶段不设计或实施模型评测体系、质量 Benchmark、Shadow 流量、灰度发布或上线指标。
- 本阶段不实现第三方 MCP Server、远程代码检索、LSP、CodeGraph Adapter 或通用沙箱。
- 本阶段不实现包含可执行脚本的 Skill。
- 不允许 Agent 修改代码、Git 引用、工作区或外部系统。
- 不通过 Agent 间自由对话、辩论或 peer-to-peer handoff 汇总结论。
- 不自动迁移历史 `correctness:v1` 配置到新 Reviewer。

## 4. 业界方案与选型

业界常见编排可以归纳为三类：

1. **中心化宿主编排**：宿主确定 DAG，专业 Agent 并行，统一 Judge/Resolver 汇总。
2. **Manager Agent 自主委派**：一个 LLM 决定何时调用哪些子 Agent，并拥有最终回答。
3. **去中心化协作**：Agent 互相通信、辩论、投票或 handoff。

OpenAI Agents SDK 同时支持 LLM 编排和代码编排，并指出代码编排在速度、成本和性能上更确定，且可以直接并行运行独立 Agent；Anthropic 的 Research 系统采用 orchestrator-worker 模式并行调查不同方面；Google Research 对多种 Agent 架构的研究显示，中心化编排更适合可并行任务并具有更好的错误隔离，而工具数量和协调开销会成为多 Agent 系统的重要负担；Qodo 公开描述了专业 Reviewer 并行、Judge 合并冲突和过滤低信号 Finding 的代码审查架构。

参考资料：

- [OpenAI Agents SDK：Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
- [Anthropic：How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Google Research：Towards a science of scaling agent systems](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
- [Qodo：Introducing Qodo 2.0 and agentic code review](https://www.qodo.ai/blog/Introducing-qodo-2.0-agentic-code-review/)

CodeLens 采用方案 1：**中心化、宿主控制的 fan-out/fan-in DAG**。LLM 只在 Adaptive 模式中负责 Reviewer 选择，以及在 Resolver/Verifier 节点中处理需要语义判断的局部任务；流程、权限、预算、状态转换和发布门槛由宿主控制。

不选择 Manager Agent 或去中心化协作的原因是：它们会把调用顺序、成本、重试、权限和完成语义交给模型，难以满足插件自动触发、进程恢复和高精度发布要求。

## 5. 总体架构

```text
Review Request / Plugin Trigger
              |
              v
      Freeze ReviewSnapshot
              |
              v
      Compile ReviewPlan
       |               |
   Fixed mode      Adaptive mode
   Host compile    Planner Agent
       |               |
       +-------+-------+
               |
               v
       Freeze plan_hash and
       capability fingerprint
               |
               v
       Parallel Reviewer Runs
               |
               v
    Deterministic Candidate Validation
               |
               v
         Root-cause Clustering
               |
               v
             Resolver
               |
        optional Verifier
               |
               v
        Published Findings
```

Fixed 和 Adaptive 只在 `ReviewPlan` 产生方式上不同。Plan 冻结后，后续调度、校验、汇总、失败和发布语义完全一致。

## 6. Reviewer 选择模式

### 6.1 Fixed

用户或插件提前指定 Reviewer，运行时不让 LLM 识别、推荐、增选或取消 Reviewer：

```json
{
  "reviewer_selection": {
    "mode": "fixed",
    "reviewer_versions": [
      "correctness:v2",
      "security:v1"
    ]
  },
  "budget_profile": "standard"
}
```

宿主验证引用、互斥和预算后直接编译 `ReviewPlan`。Fixed 允许：

- 仅 `general:v1`；
- 一个专业 Reviewer；
- 两个或更多专业 Reviewer。

General 不能与专业 Reviewer 混选。

### 6.2 Adaptive

用户和插件不选择 Reviewer，由内部 `review-planner:v1` 独立决定：

```json
{
  "reviewer_selection": {
    "mode": "adaptive"
  },
  "budget_profile": "standard"
}
```

Planner 必须在两种策略中二选一：

- `generalist`：只选择 `general:v1`；
- `specialist_team`：选择 2 到预算允许上限个专业 Reviewer。

Planner 失败、重复输出非法计划或引用不可用 Reviewer 时，任务失败；不得静默回退到 General 或 Correctness。

### 6.3 无混合交互

不存在“LLM 先推荐，用户再增删”的中间模式。该模式会增加人工步骤，也使插件自动触发无法获得稳定语义。Reviewer 选择权要么完全属于用户配置，要么完全属于 Planner。

## 7. Reviewer Catalog

### 7.1 初始目录

| Reviewer | 主要职责 | 明确边界 |
| --- | --- | --- |
| `general:v1` | 从正确性、安全、契约、可靠性、性能、测试和架构多个角度做广而浅的检查 | 必须单独运行，只报告高置信度、可操作问题 |
| `correctness:v2` | 业务逻辑、状态转换、边界条件、错误处理和控制流 | 不主审并发、性能或宏观架构 |
| `security:v1` | 鉴权、权限、注入、Secret、敏感数据、信任边界和不可信输入 | 不报告没有安全影响的普通健壮性问题 |
| `reliability-concurrency:v1` | 并发、锁、事务、幂等、重试、超时、取消、资源清理和恢复 | 不主审普通业务逻辑或性能优化 |
| `contract-data:v1` | API、SSE、事件、配置、序列化、数据库迁移、兼容性和数据完整性 | 不判断宏观架构合理性 |
| `architecture:v1` | 分层、依赖方向、限界上下文、所有权、隔离边界和跨模块影响 | 不报告局部风格或普通重构建议 |
| `performance:v1` | 算法复杂度、重复 I/O、N+1、内存、资源消耗和关键路径扩展性 | 只报告具有现实影响或明确增长条件的问题 |
| `test-regression:v1` | 缺失的关键测试、无效断言、行为与测试不一致和回归保护缺口 | 不把覆盖率不足作为单独 Finding |

`maintainability` 和 `documentation` 首版不作为独立 Reviewer，以避免主观建议和角色重叠；`release-risk` 是 Planner 与最终报告的属性，不是 Reviewer。

### 7.2 独立上下文

每个 Reviewer：

- 使用独立模型上下文并行执行；
- 读取同一个冻结 Snapshot 和完整 `review_files`；
- 看不到其他 Reviewer 输出；
- 按风险维度分工，不按文件分片；
- 可以接收 Planner 的 `focus_paths`，但它只是关注提示，不限制其他 Snapshot 文件访问；
- 只有通过输出工具提交的 Candidate 才进入后续流水线，自然语言最终回答不生成 Finding。

首版不做文件 Sharding。安全、契约、并发和调用链问题经常跨文件，硬分片会形成覆盖空洞。

### 7.3 Correctness 版本升级

`correctness:v1` 已经发布，不能原地改变输出和工具契约。因此：

- `correctness:v1` 保留 Comment v1、数值 `confidence` 和原有单 Agent 直接发布行为；
- `correctness:v1` 不出现在新建 Profile 的 Reviewer 选择列表中，也不对 Adaptive Planner 开放；
- `correctness:v1` 不能与 Comment v2 Reviewer 组成多 Agent Team；
- 新 Fixed Profile 和 Adaptive 模式使用 `correctness:v2`；
- `correctness:v2` 使用 Comment v2 和 CandidateFinding v2；组成专业 Team 时进入 Resolver/Verifier 链路，Fixed Single Specialist 时经过严格自检和确定性发布门槛后直接发布；
- 历史配置继续引用 `correctness:v1`，只有用户明确保存升级操作时才切换到 v2。

最终外部 `Finding` 契约保持稳定，前端和导出插件不需要理解 Comment 版本。

## 8. ReviewPlan 与 Planner 协议

### 8.1 ReviewPlan

`ReviewPlan` 至少保存：

- `task_id`；
- `selection_mode`；
- `selection_policy_fingerprint`；
- Adaptive 模式的 Planner 版本和输出 Artifact；
- `strategy`：`generalist`、`single_specialist` 或 `specialist_team`；
- `selected_reviewer_versions`；
- 每个 Reviewer 的选择原因和可选 `focus_paths`；
- 冻结 Capability/Skill 执行指纹；
- `plan_hash`；
- `created_at`。

`single_specialist` 只允许 Fixed 模式使用。`plan_hash` 持久化后，重试、进程重启和配置变更都不能改变 Plan。

### 8.2 Planner 输入

Planner 只能看到：

- 完整且冻结的 `review_files`、变更类型、允许范围和基础统计；
- 经现有规则链解析并冻结的 `repository_instructions`；
- 带版本、职责、排除项、成本等级和能力就绪状态的 Reviewer Catalog；
- 当前 Budget Profile；
- 当前 Snapshot 的受限只读证据工具。

Planner 不能执行 Shell、访问网络、修改工作区、查看 Candidate、生成 Finding 或改变 Capability Profile。

### 8.3 Planner 输出

Planner 对目录中的每个可选 Reviewer 返回选择记录：

```json
{
  "schema_version": "1",
  "strategy": "specialist_team",
  "risk_signals": [
    {
      "code": "AUTHORIZATION_BOUNDARY_CHANGED",
      "evidence_paths": ["backend/src/example.py"]
    }
  ],
  "reviewer_decisions": [
    {
      "reviewer_version": "security:v1",
      "selected": true,
      "reason_codes": ["AUTHORIZATION_BOUNDARY_CHANGED"],
      "focus_paths": ["backend/src/example.py"]
    },
    {
      "reviewer_version": "performance:v1",
      "selected": false,
      "reason_codes": ["NO_PERFORMANCE_SENSITIVE_CHANGE"],
      "focus_paths": []
    }
  ]
}
```

宿主只校验计划合法性，不自行增删 Reviewer。`focus_paths` 必须属于当前 Snapshot，但不会改变 Reviewer 的可见范围。

## 9. 持久化 DAG 与运行时状态

### 9.1 Pass 定义

```text
Pass 0  Planner      Adaptive only
Pass 1  General OR one/many specialist Reviewers
Pass 2  Resolver     multi-specialist team with candidates
Pass 3  Verifier     conditional, bounded batch
```

General 和 Fixed Single Specialist 在确定性校验后直接进入发布门槛，不调用独立 Resolver。专业 Team 只要存在 Candidate 就运行 Resolver；即使只有一个 Reviewer 报告问题，也不能因为缺少多数票跳过裁决。

### 9.2 节点身份

节点键使用：

```text
{agent_version}:{pass_index}:{shard_id}
```

稳定身份包含 `task_id`、Agent 版本、Pass、Shard 和 `logical_attempt_group`，但不包含物理执行次数。重试创建新的 Attempt，不能创建新的逻辑节点。

现有 `AgentRun` 继续作为 DAG 节点实体，并补充 `node_role` 等必要属性；不再创建一套职责重叠的通用 Node 模型。

### 9.3 状态

任务状态：

```text
queued -> planning -> reviewing -> resolving -> verifying
       -> completed | partial | failed | canceled | superseded
```

AgentRun 状态继续表达 Pending、Running、Output Saved、Validating 和各类终态。DAG 调度只根据持久化节点状态推进，不依赖进程内 Future 是否存在。

### 9.4 有界并发

调度器执行三层限制：

- Worker 全局模型调用上限；
- 单 Review 并行 Agent 上限；
- 按模型或供应商配置的并发上限。

多个任务之间公平调度，不能让一个 Deep Review 占满 Worker。Reviewer 节点全部进入终态后才能调度 Resolver。

## 10. Finding 流水线

### 10.1 分层模型

```text
Model submission
    -> deterministic location/snapshot/evidence validation
    -> CandidateFinding
    -> FindingCluster
    -> ResolutionDecision
    -> Published Finding
```

必须持久化 Candidate、Cluster、Decision、Published Finding 以及完整 Provenance。普通 API 和结果页只展示 Published Finding；被抑制和未确认候选仅通过受权限控制的审计能力读取。

### 10.2 Comment v2

新 Reviewer 使用 Comment v2，移除未校准的数值 `confidence`：

```json
{
  "path": "backend/src/example.py",
  "side": "new",
  "existing_code": "...",
  "title": "...",
  "content": "...",
  "recommendation": "...",
  "category": "incorrect-state-transition",
  "severity": "high",
  "primary_dimension": "correctness",
  "evidence_strength": "direct"
}
```

枚举：

- `evidence_strength`：`direct`、`inferred`、`weak`。

Reviewer 可以报告跨维度问题，但必须指定主维度。角色边界减少重复，不作为丢弃真实问题的硬过滤器。

### 10.3 聚类

确定性逻辑先按照 Snapshot、路径、位置、根因、影响和证据建立粗粒度 Cluster。多个 Reviewer 对同一问题的发现作为多重 Provenance 保留，不产生多条对外 Finding。

## 11. Resolver 与 Verifier

### 11.1 Resolver

Resolver 只处理已有 Candidate Cluster，不能重新开放式 Review。它可以：

- 合并 Candidate ID；
- 从候选位置中选择规范位置；
- 在候选严重度范围内降低或选择有证据支持的严重度；
- 基于已有事实和证据生成统一表述；
- 返回 `publish`、`suppress` 或 `verify`。

Resolver 不能：

- 创建新 Finding、位置或证据；
- 添加候选没有支持的影响；
- 把严重度提升到所有候选之上；
- 依靠多数票代替证据。

Resolver 不看到模型供应商、执行顺序或位置偏置信息。候选展示顺序根据 `plan_hash` 稳定打乱。

### 11.2 裁决规则

- 直接代码证据、明确触发路径且影响成立：`publish`。
- 多个 Reviewer 指向同一根因且证据一致：合并后 `publish`。
- 单个专业 Reviewer 证据直接充分：允许 `publish`。
- 高影响结论依赖跨文件、运行时或隐含前提：`verify`。
- Reviewer 对关键事实冲突：`verify`。
- 推测、风格偏好或无法描述失败场景：`suppress`。
- 严重度冲突：采用证据能支持的最低明确严重度。

### 11.3 Verifier

每个任务最多启动一个批量 `verifier:v1`，一次处理多个待验证 Cluster。Verifier 只能返回：

- `confirmed`：发布 Resolver 已生成的规范 Finding；
- `rejected`：抑制并保留审计记录；
- `unresolved`：按照外部高精度原则抑制。

不再调用第二次 Resolver，避免产生无上限循环。Verifier 超时或失败时，相关候选不发布，其他已确认 Finding 正常发布，任务进入 `partial`。

### 11.4 General 特例

`general:v1` 为低成本路径，只进行一次模型调用：多角度调查、自我反驳、证据检查、Comment v2 提交和任务完成。宿主继续执行全部位置、Snapshot、证据和可操作性校验。不满足直接发布门槛的候选保留内部记录但不对外发布。

## 12. Capability 架构

### 12.1 决策

每个不可变 Reviewer 版本静态绑定一个 `CapabilityProfile` 和一个 `SkillPolicy`。Planner 只选择 Reviewer，不能选择、增删或提升工具权限。Agent 运行时不能发现、安装或加载额外 Capability。

```json
{
  "reviewer_version": "security:v1",
  "capability_profile_ref": "security-review:v1",
  "skill_policy_ref": "security-skills:v1",
  "output_contract_version": "finding-candidate:v2"
}
```

### 12.2 Agent built-in tools

| Agent 角色 | 证据工具 | 输出与控制工具 |
| --- | --- | --- |
| `review-planner:v1` | `find_files`、`grep`、`read_file`、`get_diff` | `submit_review_plan` |
| `general:v1` | `find_files`、`grep`、`read_file`、`get_diff` | `comment:v2`、`review_file_done`、`task_done` |
| 专业 Reviewer | `find_files`、`grep`、`read_file`、`get_diff` | `comment:v2`、`review_file_done`、`task_done` |
| `resolver:v1` | `read_file`、`get_diff` | `submit_resolution` |
| `verifier:v1` | `find_files`、`grep`、`read_file`、`get_diff` | `submit_verification` |

现有 `FilesystemReviewTools` 继续实现四个 Snapshot 证据工具，现有 `ToolExecutionLimiter` 继续提供 Run 级共享调用预算、单次超时和无进展循环熔断。输出工具只维护当前 AgentRun 的有界内存状态，不执行持久化、网络或文件写入；AgentRun 结束后由应用层保存规范输出 Artifact。

所有 Agent 禁止 Shell、任意进程、文件写入、任意 Git ref、Snapshot 外访问、原始工作区访问、默认网络访问和动态工具加载。

### 12.3 Capability Profile

```json
{
  "profile_id": "security-review",
  "version": 1,
  "tool_contracts": [
    "snapshot.find-files:v1",
    "snapshot.grep:v1",
    "snapshot.read-file:v1",
    "snapshot.get-diff:v1",
    "review.comment:v2",
    "review.file-done:v1",
    "review.task-done:v1"
  ],
  "limits": {
    "shared_call_budget": 40,
    "tool_timeout_seconds": 30,
    "maximum_result_bytes": 65536
  }
}
```

模型只看到稳定的 CodeLens Tool Contract：

```text
Model-visible Tool Contract
            |
            v
Policy-enforcing Capability Gateway
            |
            v
Built-in Adapter | MCP Adapter | Future Index Adapter
```

Capability Gateway 统一执行：

- Profile allowlist；
- 参数 Schema 和未知字段校验；
- Snapshot、路径和数据作用域校验；
- 调用、时间、并发和结果大小限制；
- 无进展熔断；
- Secret 与输出脱敏；
- Transcript 和工具 Provenance；
- 供应商结果规范化。

`OpenAIAgentRuntime` 不再直接硬编码 Snapshot Tool 与 Comment Collector 列表，而是接收冻结后的 Provider-neutral `FrozenAgentExecutionSpec`，再把其中工具契约适配为 Agents SDK `FunctionTool`。

### 12.4 未来语义工具

未来可以增加稳定 CodeLens 工具契约：

- `search_symbols:v1`；
- `find_references:v1`；
- `trace_call_path:v1`；
- `get_type_hierarchy:v1`；
- `get_static_analysis:v1`。

同一个逻辑工具可以由内置索引、MCP 或其他 Adapter 实现。Reviewer Prompt 不感知实现来源，切换 Adapter 不改变模型可见参数和返回协议。

## 13. MCP 设计边界

### 13.1 禁止原始透传

不能把 MCP Server 的动态工具列表、原始 Schema、Prompt、Resource URI 或权限请求直接交给 Agent。每个 MCP 工具必须映射到 CodeLens 预先定义的稳定 Tool Contract：

```json
{
  "binding_id": "codegraph-trace:v1",
  "tool_contract": "trace_call_path:v1",
  "server_ref": "codegraph-local:v3",
  "server_tool": "trace_path",
  "expected_schema_hash": "sha256:...",
  "data_scope": "review_snapshot",
  "side_effect": "none",
  "egress": "local"
}
```

### 13.2 安全要求

MCP Binding 在执行前验证：

- Server、工具和版本被显式启用；
- 远端 Schema 与冻结 Hash 一致；
- 工具被归类为只读且无外部副作用；
- 输入只能引用当前 Snapshot；
- 输出、超时、调用数和并发有界；
- MCP 输出只作为不可信数据，不能进入高优先级系统指令；
- Secret 只由 Secret Store 注入传输层，不进入 Prompt、参数、结果、事件或日志。

本地 MCP 子进程只能获得任务 Snapshot 的只读视图和清理后的环境变量。无法证明 Snapshot 隔离的 MCP 不允许用于插件自动触发。远程 MCP 会产生源码外发，必须作为显式 Data Egress Capability 启用，不得进入默认 Profile。

### 13.3 可用性语义

- Required Capability 在 Plan 冻结前不可用：Reviewer 不具备 Ready 状态，Fixed 创建失败，Adaptive Planner 不得选择。
- Optional Capability 在 Plan 冻结前不可用：从本次执行包移除并记录降级。
- 已冻结 Capability 在执行期失效：不得切换 Server 或 Adapter；对应 Agent 按失败处理。
- MCP Schema Hash 或版本发生变化：当前任务拒绝继续，未来任务重新解析新版本。

## 14. Skill 设计边界

### 14.1 Skill 不是权限

Tool 是可执行能力；Skill 是版本化的 Review 方法、检查清单和工具使用指导。Skill 本身不能：

- 注册或发现工具；
- 扩大 Capability Profile；
- 启动进程、访问网络或读取 Secret；
- 修改 Snapshot 或执行代码；
- 覆盖平台安全、工具、Snapshot 或输出契约。

### 14.2 Skill Manifest

```json
{
  "skill_id": "python-async-safety",
  "version": 1,
  "instruction_artifact_hash": "sha256:...",
  "compatible_reviewer_roles": ["reliability-concurrency"],
  "required_capabilities": [
    "snapshot.read-file:v1",
    "snapshot.grep:v1"
  ],
  "activation": {
    "changed_file_patterns": ["**/*.py"]
  }
}
```

Skill 激活流程：

1. Reviewer 版本绑定允许使用的 Skill Policy。
2. 宿主使用冻结路径、文件类型和 Manifest 做确定性匹配。
3. 宿主校验 Required Capabilities 已存在于 Reviewer Profile。
4. 在模型调用前冻结 Skill ID、版本、内容 Hash 和激活原因。
5. Runtime 把 Skill 内容作为受约束、不能提升权限的指令段注入。
6. Transcript 记录 `skill_loaded` 事件。

Agent 不提供 `load_skill` 工具。首版 Skill 只允许声明式文本，不支持脚本、依赖安装或任意代码执行。

Skill 指令的优先级低于平台安全策略、稳定工具契约、冻结仓库规则、通用 Review 工作流和 Reviewer Policy。Runtime 必须使用明确的结构化边界封装 Skill 正文；Skill 中声称的新权限、工具、输出协议或指令优先级均无效。

示例组合：

- Reliability Reviewer + `python-async-safety:v1`；
- Contract Reviewer + `alembic-migration-safety:v1`；
- Security Reviewer + `web-input-trust-boundary:v1`。

## 15. 冻结执行定义

`ReviewPlan` 必须保存或引用不可变的执行指纹：

```text
Reviewer versions
+ Capability Profile versions
+ Tool contract versions
+ Skill IDs, versions and content hashes
+ MCP server, tool and schema hashes
+ output contract versions
+ budget profile
```

重试和进程恢复只能复用该执行定义。配置、Skill 内容、MCP Server 或 Catalog 后续变化不能影响已经创建的任务。

## 16. 预算与限制

`budget_profile` 只描述资源边界，不选择 Reviewer：

- Reviewer 数量上限；
- 单节点与总 Token 上限；
- Agent Turn 上限；
- 全局与任务内并发上限；
- 共享工具调用预算；
- 单 Agent 执行时间和单工具超时；
- Verifier 可处理的最大 Cluster 数。

Fixed 选择超过预算时拒绝创建，不能静默移除 Reviewer。Adaptive Planner 必须在预算内选择，非法计划按 Planner 失败语义处理。在启动 Reviewer 前，为必要 Resolver 和最大允许 Verifier 预留预算。

所有 built-in 和 MCP Tool 共享 AgentRun 的总调用预算；昂贵 MCP 可以额外设置更低的单工具上限，但不能绕过总预算。

## 17. ReviewProfile、API 与插件

### 17.1 ReviewProfile

`ReviewProfile` 是可复用的 Review 策略模板，只包含 Reviewer 选择和预算，不包含插件 Debounce、Supersede 等触发策略：

```json
{
  "profile_id": "profile-balanced",
  "revision": 3,
  "name": "Default adaptive review",
  "is_default": true,
  "reviewer_selection": {
    "mode": "adaptive"
  },
  "budget_profile": "standard"
}
```

本地 CodeLens 实例必须始终有且仅有一个手工 Review 默认 Profile。Profile 可以编辑、复制和删除；默认 Profile 必须先被另一个 Profile 替代才能删除。Profile 更新使用 `revision` 做乐观并发检查，防止两个页面互相覆盖。

Profile 是可变模板，执行定义不是。手工任务创建时必须把 Profile 解析为不可变 `ReviewProfileSnapshot`，保存 Profile ID 与 Revision 只用于来源追踪，不能在重试或恢复时重新读取 Profile。用户在创建页临时调整策略时生成内联快照，不会修改原 Profile；只有显式选择“另存为新 Profile”才创建新模板。

插件配置页可以选择 Profile 作为填写模板，但保存时必须把 `reviewer_selection` 和 `budget_profile` 复制到插件独立配置快照。Core 可以在插件配置之外保存来源 Profile、Revision 和复制时间用于 UI 提示；该元数据不传给插件、不参与幂等指纹，也不形成实时引用。Profile 后续变化不会影响插件，只有用户显式“从 Profile 重新载入”并保存配置时才更新。

### 17.2 创建与查询

Profile 应用服务和 API 支持列表、创建、按 Revision 更新、复制、删除和切换默认 Profile。创建 Review 的请求仍使用 Fixed/Adaptive 判别联合，Profile 只负责在界面和应用层产生该请求，不进入领域选择协议。

Adaptive 任务创建后 `review_plan` 可以暂时为空，Planner 完成后再填充。查询结果至少包含：

- `selection_request`；
- 可空的来源 Profile ID 与 Revision；
- 可空 `review_plan`；
- Planned、Completed、Failed、Omitted Coverage；
- Published Findings；
- `partial` 或降级说明；
- Resolver 合并数与 Verifier 结果摘要。

Reviewer Catalog 提供独立只读 API，返回版本、职责、类型、成本等级、Capability Ready 状态和是否允许 Planner 选择。

### 17.3 前端

前端采用 **Profile 优先 + 原地展开编辑**。手工和插件配置复用同一个 `ReviewStrategyEditor`，但两者的保存语义不同：手工创建直接冻结任务快照，插件配置先冻结自动化配置快照，之后每次触发再从该配置创建任务快照。

#### 17.3.1 Profile 管理

在 Settings 增加 Review Profiles 页面，使用左侧 Profile 列表和右侧编辑器的桌面双栏布局：

- 列表展示名称、Fixed/Adaptive、Budget 和默认标记；
- 编辑器负责名称、是否默认、选择模式和 Budget；
- Adaptive 只说明 Planner 的选择范围，不显示 Reviewer 多选；
- Fixed 显示版本化 Reviewer 多选，选择 General 后立即清除并禁用专项 Reviewer；选择专项 Reviewer 时 General 不可选；
- Budget 使用面向用户的 `Economy`、`Standard`、`Deep` 标签，协议值分别为 `lean`、`standard`、`deep`；
- Duplicate 创建独立 Profile；删除默认 Profile 前必须先设置另一个默认值；
- 页面明确提示 Profile 可变、任务与插件配置快照不可变。

新安装至少创建一个 Adaptive + Standard 的默认 Profile。已有 `correctness:v1` 任务和插件配置不自动改变；新建或复制 Profile 的 Reviewer 选择器不展示 `correctness:v1`。

#### 17.3.2 创建 Review

现有创建页继续使用 Repository/Scope 主列和 Recent Repositories 侧栏。Review 策略默认只显示紧凑 Profile 摘要卡，包含 Profile 名称、Fixed/Adaptive、Budget 和关键行为说明：

- 默认选中实例默认 Profile，常规用户确认 Scope 后可以直接启动；
- “更换或调整”在当前位置展开 Profile 选择和 `ReviewStrategyEditor`，不进入 Wizard、Drawer 或新页面；
- 切换 Profile 会替换当前草稿；修改草稿只影响当前 Review；
- 用户可以显式勾选“另存为新 Profile”，否则不修改 Profile；
- Adaptive 提示实际 Reviewer 会在 Review Plan 生成后展示，运行前不要求用户确认；
- Fixed 和 Adaptive 都在提交前做 Catalog 版本、General 互斥和 Budget 校验；失败时保留表单草稿。

#### 17.3.3 插件配置

插件 Trigger 配置页把标准字段 `reviewer_selection` 和 `budget_profile` 作为一个公共 Review Strategy 区域，用 Core 拥有的 `ReviewStrategyEditor` 渲染；插件自有字段继续由 JSON Schema 表单渲染。禁止 Local Hook、Webhook 等插件各自复制 Reviewer UI。

默认态展示策略摘要和来源 Profile 信息，展开后原地编辑。用户可以选择 Profile 初始化配置、显式重新载入或直接调整独立草稿。保存操作把 Review Strategy、`supersede_policy`、Locale、Debounce 和插件自有字段一起校验并原子持久化。自动触发只读取已保存配置，全程不弹窗、不等待用户确认，也不在 Planner 之后允许增选或取消 Reviewer。

#### 17.3.4 执行与结果

结果页采用“最终结果优先、编排过程可追溯”的层级：

1. 页头展示终态、模式、Budget、Scope 和耗时；`partial` 必须紧邻终态显示。
2. 紧凑 Review Plan 展示 Planner、并行 Reviewer、Resolver 和 Verifier；默认折叠节点日志，允许展开查看单节点状态、重试和失败原因。
3. 默认 Tab 只展示 Published Findings，按严重级别和位置组织；Candidate、被抑制结果和原始 Transcript 不进入普通 Finding 列表。
4. Coverage Tab 展示 Planned、Completed、Failed、Omitted 视角。任何 Reviewer、Resolver 或 Verifier 失败都必须说明结果缺口，不能只改变颜色。
5. Execution 与 Logs Tab 承载诊断信息，避免把主结果页变成 Agent 控制台。

Finding 卡片显示严重级别、类别、位置、建议、发布或验证状态，以及 CodeLens 内部可见的规范主 Reviewer 和合并来源。Comment v2 的 `evidence_strength` 使用类别标签表达；新任务不显示或按数字置信度排序。Legacy `correctness:v1` 的数字置信度只保留在历史数据与兼容接口中，不提升为新版结果页的主信息。

General 或 Fixed Single Specialist 没有 Resolver/Verifier 时，Review Plan 自动压缩未创建的 Pass，而不是显示伪造的 `skipped` 节点。Planning、Running、Partial、Failed、Canceled 和 Superseded 都使用同一结果页骨架，避免任务完成时页面结构跳变。

#### 17.3.5 共享组件与状态

前端至少形成以下边界：

- `ReviewProfilePicker`：选择 Profile，只负责模板来源；
- `ReviewStrategySummary`：在创建页和插件页显示紧凑摘要；
- `ReviewStrategyEditor`：唯一的 Fixed/Adaptive、General 互斥和 Budget 编辑器；
- `ReviewPlanSummary`：把持久化 DAG 投影为紧凑进度；
- `CoverageSummary`：展示视角完成度和缺口；
- `FindingList`：只消费 Published Finding 视图，不读取 Candidate 或 Resolver 内部记录。

公共组件消费经过 API Client 校验的类型，不直接解释插件 Manifest、领域实体或 SSE 原始 Payload。SSE 事件只更新查询缓存中的稳定 Review 投影；页面刷新后必须能仅靠查询 API 恢复相同状态。

### 17.4 插件自动触发

手动和插件触发进入同一个 `CreateReview` 用例，只保留 `trigger_source` 和自动任务合并策略差异。幂等键包含：

```text
repository + base/head Snapshot
+ reviewer selection policy fingerprint
+ planner/catalog version
+ budget profile
+ capability/skill policy fingerprint
```

插件默认使用 `latest_snapshot`：同一仓库和配置下，旧排队任务进入 `superseded`，运行中旧任务发起协作取消，新 Snapshot 创建新任务。需要逐 Commit 审计时可以显式配置 `preserve_all`。

插件全过程不产生用户确认步骤。

插件 API v1 到 v2 的 Manifest、Trigger 配置、`ReviewCreatorPort`、自动触发、Report Envelope 和迁移要求见 [`docs/plugin-upgradev2.md`](../../plugin-upgradev2.md)。

## 18. SSE 与 Transcript

SSE 只发送稳定领域事件：

```text
review.plan_created
agent_run.started
agent_run.completed
agent_run.failed
review.resolution_completed
review.verification_completed
review.completed
review.partial
review.canceled
review.superseded
```

事件具有递增 ID，支持 `Last-Event-ID` 恢复。物理 Attempt 重试不能重复发送同一个逻辑完成事件。

普通 SSE、数据库事件和运行日志不得包含 Prompt、代码正文、MCP 原始输出或 Secret。完整已脱敏模型交换、工具调用、结果和 Skill 生命周期继续遵循现有任务 Artifact 与专用模型日志边界。

## 19. 失败、重试、取消与恢复

### 19.1 失败语义

| 情况 | 结果 |
| --- | --- |
| Adaptive Planner 失败 | `failed`，不回退 |
| General 失败 | `failed` |
| Fixed Single Specialist 失败 | `failed` |
| 部分 Team Reviewer 失败 | 继续汇总，最终 `partial` 并报告缺失视角 |
| Team Reviewer 全部失败 | `failed` |
| Resolver 失败 | `failed`；保留 Candidate，不发布 |
| Verifier 失败 | 相关候选抑制，其他结果发布，最终 `partial` |
| Required Capability 执行期失效 | 对应 Agent 失败 |

Planner 主动未选择某一视角、General 策略或 Optional Capability 的计划内降级不属于 `partial`，但必须在 Coverage 中解释。

### 19.2 重试

- 只重试失败节点，不重跑整个 Review。
- 限流、连接、供应商临时故障和 Lease 过期可以指数退避重试。
- 结构化输出非法允许一次协议修复重试。
- Snapshot、版本、Profile、Schema Hash 和配置错误不重试。
- 已成功节点永久复用，迟到旧 Attempt 不能覆盖新 Attempt。

### 19.3 取消

取消设置任务级意图，停止领取新节点并尝试终止运行中模型调用。无法立即中断的迟到结果不得继续发布。Plan、Transcript、Candidate 和错误 Artifact 保留，最终状态为 `canceled`。

### 19.4 完成状态

- `completed`：冻结 Plan 中的节点均按策略得到明确结果，Finding 可以为零。
- `partial`：部分计划能力因非预期故障缺失，但仍有可信结果可发布。
- `failed`：无法形成可信最终结果。
- `canceled`：用户或系统协作取消。
- `superseded`：插件产生更新 Snapshot，旧任务不再作为当前结果展示。

## 20. 数据与限界上下文

模型归属：

- `ReviewTask`：生命周期、Snapshot、触发来源、配置快照和最终状态；
- `ReviewPlan`：不可变选人和执行定义；
- `AgentRun`：持久化 DAG 节点；
- `CandidateFinding`：通过基础校验的原始候选；
- `FindingCluster`：共享根因候选集合；
- `ResolutionDecision`：Resolver/Verifier 裁决；
- `Finding`：最终对外发布结果。

上下文职责：

- `reviewer_catalog` 拥有 Reviewer 版本和 Capability/Skill 引用；
- `capabilities` 拥有 Capability Profile、Skill Manifest、MCP Binding、权限策略和冻结解析；
- `review` 接收 `FrozenAgentExecutionSpec` 并编排 DAG；
- `findings` 负责 Candidate 校验、聚类、裁决记录、抑制和发布；
- `plugin` 通过 `ReviewCreatorPort` 提交同构选择协议；
- `infrastructure` 实现 Snapshot、MCP、索引和供应商 Runtime Adapter；
- `bootstrap` 是唯一选择具体 Adapter 和读取外部配置的位置。

任何上下文不得导入其他上下文的 Infrastructure 实现。Domain 与 Application 契约不得出现 OpenAI、MCP SDK、供应商模型或绝对文件系统路径类型。

## 21. 兼容迁移

- 旧 API `selected_agents` 只在 Interface 适配层转换为 Fixed 选择。
- 已有 `correctness:v1` 配置保持原版本和成本语义。
- 新旧字段同时出现时返回明确校验错误。
- 新建 Reviewer Profile 不展示 `correctness:v1`，改用 `correctness:v2`。
- `correctness:v1` 只允许单 Reviewer Legacy 路径，禁止与 v2 Reviewer 混组。
- 当前 Finding 查询继续只返回最终发布结果。
- 领域层只接受新协议，兼容分支不得进入核心模型。

## 22. 实现验证要求

本节只定义功能正确性验证，不包含模型效果评测或上线指标。

- 单元测试覆盖选择联合、General 互斥、Reviewer 版本兼容、Plan 冻结、DAG 状态、预算和发布规则。
- Comment v1/v2、Planner、Resolver、Verifier、Capability Profile 和 SSE 使用契约测试。
- 并发集成测试覆盖局部失败、重试、Lease 过期、进程重启、取消、迟到 Attempt 和恰好一次发布效果。
- Git 与 Snapshot 测试使用真实临时仓库，覆盖符号链接、Overlay、删除、重命名和固定 OID。
- Capability 测试验证 Profile allowlist、Schema、路径范围、共享预算、超时、结果大小和循环熔断。
- MCP Adapter 未来实现时必须使用假 Server 做契约测试，并覆盖 Schema 漂移、Secret 脱敏、输出注入和不可用语义。
- Skill 测试验证确定性激活、内容 Hash 冻结、Required Capability 校验和禁止扩权。
- 插件集成测试覆盖相同事件幂等、`latest_snapshot` Supersede 和 `preserve_all`。
- 前端组件测试覆盖 Profile 草稿隔离、General 互斥、Adaptive 隐藏 Reviewer、插件显式重新载入和 Profile Revision 冲突。
- 前端在 `1280x800` 覆盖 Profile 列表与编辑、创建页折叠与展开、Fixed、Adaptive、General、Planning、Partial、Failed、Superseded 和长文本状态。

## 23. 最终不变量

1. Fixed 模式中 LLM 永远不改变 Reviewer 列表。
2. Adaptive 模式中用户永远不增删 Planner 选择结果。
3. General 永远单独运行。
4. Reviewer 彼此隔离，只共享冻结 Snapshot，不共享推理或 Candidate。
5. Resolver 和 Verifier 永远不能创建新问题。
6. 未确认 Candidate 默认不对外发布。
7. Reviewer 版本永远静态绑定 Capability Profile 和 Skill Policy。
8. Planner 和 Reviewer 永远不能动态加载 MCP、Tool 或 Skill。
9. Skill 永远不能授予权限，MCP 永远不能绕过稳定 Tool Contract。
10. 所有模型可见代码数据永远受 Snapshot、路径、Hash 和大小边界约束。
11. 重试、恢复和配置变化永远不能改变冻结 Plan 的执行定义。
12. 插件自动触发和手动 Review 永远使用同一领域协议与失败语义。
13. 可变 Profile 永远不能改变已创建任务或已保存插件自动化配置的快照。
