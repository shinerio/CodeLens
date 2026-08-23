<div align="center">

# CodeLens

### 本地优先的多 Agent 智能代码审查工作台

**让多个专业 Reviewer 并行审查你的代码变更，每一条结论都有据可查、有迹可循。**

[Apache License 2.0](./LICENSE) · [产品白皮书](./docs/CodeLens-白皮书.md) · [架构约束](docs/architecture.md)

</div>

---

## 为什么选择 CodeLens

代码审查依赖个人经验、容易遗漏且耗时。CodeLens 将审查拆解为多个专业维度，由专属 Agent 并行执行，在受控的确定性图谱中按需推理——兼顾灵活性与确定性。

### 核心优势

- 🧭 **确定性图流控 (Deterministic Graph Orchestration)**
  基于 Graph Engineering 构建审查工作流：Planner 规划 → Reviewer 调查 → Verifier 核验与本次去重 → Deduplicator 历史去重 + Remediator 修复检测 → Finding 发布。LLM 在受控的节点图谱中按需推理，每个阶段的输入、输出和工具集都被严格约束，兼顾灵活性与确定性。

- 🤖 **多 Agent 专项并发审查 (Multi-Agent Specialist Swarm)**
  按正确性、安全性、可靠性与并发、契约与数据、架构、性能、测试回归拆分专属 Reviewer，另有 General 全维度扫描；同一任务内多 Reviewer 并发执行。上下文更聚焦，召回率与准确率双高。

- 🛡️ **零伪完成机制 (Zero "Fake-Completion" Assurance)**
  每个 DAG 角色用不同工具守卫自己的完成条件，杜绝 AI 的"敷衍应付"：
  - **Reviewer** — `task_done` 在仍有未读取 diff 的文件时拒绝结束，强制覆盖全部改动文件
  - **Verifier** — `finalize_verdicts` 检查每个 Cluster 恰好被覆盖一次，缺漏或重复都不允许结束
  - **Deduplicator** — 逐一比对存活 Finding 与历史已有 Finding，未处理的候选不允许跳过，避免多次运行产生重复意见
  - **Remediator** — 逐条检查待处理已有 Finding 是否已被当前代码修复，不确定时标记 `unclear` 而非猜测 `resolved`

- 🔒 **全程只读、输入冻结 (Read-Only & Frozen Input)**
  每个任务创建独立的 detached worktree 并冻结只读 Snapshot，Reviewer 不直接访问用户原始工作区。任务创建后审查对象保持固定，多个并行任务互不干扰。

- 📍 **有证据的 Findings (Evidence-Based Findings)**
  每条 Finding 包含准确位置、严重级别、置信度、影响、解释、证据来源和修改建议。后端校验模型输出的位置、证据和结构——位置无效或依据无法对应的输出会被直接拒绝。

- 🎯 **CR-Native 深度定制 (CR-Native Architecture)**
  Prompt、Tools 和 Skills 全面聚焦代码审查场景。分层系统指令（平台策略 → 仓库规则 → 工作流契约 → Agent 专属策略）无冗余指令干扰，极大收敛模型注意力。

- 🧠 **精细化上下文工程 (Precision Context Engineering)**
  从提示词到 token 预算，全链路优化模型可见输入：
  - **Review-Only 纯净指令** — 系统提示词仅 2–12 行，剔除通用编程 agent 的写代码、调试、重构等指令。这些指令在 review 场景下会与"发现问题"的任务导向冲突（模型倾向于"修复"而非"报告"），同时浪费数千 token 样板
  - **分层无冗余** — 平台策略、仓库规则、工作流契约、Agent 专属策略四层各司其职，同一约束只表达一次；角色间上下文隔离，Deduplicator 甚至不注入平台策略和仓库规则
  - **工具最小化** — 每个角色只暴露 2–7 个工具（通用编程 agent 通常 20–30+），工具描述仅一行，不包含示例或冗余说明
  - **信息密度最大化** — 首次用户消息只包含文件路径和行范围，不含源码全文；仓库规则拆入系统指令赋予更高优先级，模型注意力集中于审查对象
  - **运行时可定制** — 每个 Agent 的专属策略可在 Web 上按语言版本编辑，即时生效无需重启；默认值不可变，覆盖按版本和语言独立持久化
  - **Epoch 检查点压缩** — 基于 tiktoken 精确计量 token，上下文接近窗口上限时自动触发语义压缩，用 evidence_id 引用替换原始代码片段，同时保护 prompt cache 前缀不失效

## 支持的功能

### 审查范围

| 范围 | 说明 |
| --- | --- |
| **Branch diff** | 两个分支引用之间的差异 |
| **Commit diff** | 指定 base commit 到 target ref 的差异 |
| **Uncommitted** | 当前 HEAD 之上的 staged、unstaged 和 untracked 改动 |
| **Full repository** | 目标引用中所有符合规则的文件 |

### 审查维度（Reviewer）

**专项 Reviewer** — 每个维度独立调查同一变更，上下文更聚焦：

| Reviewer | 关注点 |
| --- | --- |
| **Correctness** | 追踪输入在业务逻辑、控制流、边界条件和异常处理中的传播，定位不正确结果 |
| **Security** | 追踪攻击者可控来源经过认证、注入、密钥和信任边界的路径，定位具体资产影响 |
| **Reliability & Concurrency** | 追踪交错执行、故障和锁、事务、幂等、重试、超时、取消之间的传播，定位不变量违反 |
| **Contract & Data** | 追踪生产/消费端或新旧版本持久化不匹配，定位 API、事件、序列化和迁移中的数据不兼容或丢失 |
| **Architecture** | 只报告违反仓库显式规则或已确立边界的依赖、所有权和后果影响 |
| **Performance** | 建立"规模变量 → 重复工作 → 关键路径影响"链路（复杂度、I/O、N+1、内存、资源使用） |
| **Test Regression** | 追踪行为变更到缺失或无效的断言，定位可能逃逸的回归 |

**通用 Reviewer** — 全维度浅扫，只报告高置信度、高影响缺陷：

| Reviewer | 关注点 |
| --- | --- |
| **General** | 跨所有维度的广覆盖快速扫描；单独使用或用于超大变更（50+ 文件），不与专项 Reviewer 混用 |

Planner 根据变更风险自动选择专项组合或 General；Correctness 在专项模式下始终必选。

### 审查流水线（DAG 角色）

除了可见的 Reviewer，CodeLens 还内置四个 DAG 角色保障审查质量：

| 角色 | 职责 | 完成工具 |
| --- | --- | --- |
| **Planner** | 根据变更风险和 token 成本选择 Reviewer 组合，Correctness 在专项模式下强制注入 | `finalize_plan` |
| **Reviewer** | 调查目标文件，提交候选 Finding 并声明文件完成 | `comment` + `task_done` |
| **Verifier** | 对候选 Finding 聚类做接受、拒绝或合并决策，最大化精度的同时保留有依据的意见 | `verdict` + `merge` + `finalize_verdicts` |
| **Deduplicator** | 将本次存活 Finding 与历史已有 Finding（本地或外部平台）逐一比对，拒绝重复项，保留新发现；避免多次运行同一仓库时产生重复意见 | `dedup_verdict` |
| **Remediator** | 在 Deduplicator 之后运行，逐条检查待处理的已有 Finding 是否已被当前代码修复；产出 `resolved`（已修复）或 `unclear`（不确定），不确定时保守标记而非猜测 | `remediation_verdict` |

Deduplicator 和 Remediator 并行执行，互不依赖；两者保守策略一致——不确定时保留而非删除。

### 其他能力

- **分层项目规则** — 支持 `AGENTS.md`、`REVIEW.md` 和文件级 `<path>.review.md`，按目录层级优先级叠加
- **插件生态** — 统一插件模型支持 Trigger（Webhook / Git Hook 触发）和 Report（PR 评论 / 本地文件导出）
- **多模型网关** — Web 界面持久化多个 OpenAI-compatible 网关，运行时热切换，无需重启
- **实时事件流** — SSE 推送任务状态和 Agent 事件，支持断线续传 (`Last-Event-ID`)
- **中英文双语** — 界面和 Prompt 均提供中英文版本，根据浏览器语言自动切换
- **任务恢复** — SQLite + checkpoint 持久化，Worker 重启后从最后稳定边界继续
- **执行指标** — 过程报告展示 token 用量、工具调用统计、拒绝原因和耗时
- **Review 指令冻结** — 任务创建时快照冻结仓库规则，保证一次任务使用一致输入
- **凭证安全** — API Key 只写入 `data/secrets/` (权限 `0600`)，API 绝不回传密钥正文
- **模型输出脱敏日志** — `logs/model.log` 自动脱敏、按 10 MiB 轮转，普通日志不含 Prompt 或源码

## 环境要求

- Git
- Python `>= 3.12, < 3.13`
- [uv](https://docs.astral.sh/uv/)
- Node.js 与 [pnpm](https://pnpm.io/)
- 执行 Review 时需要一个 OpenAI-compatible API Key 和模型 ID（启动服务时不需要）

## 快速启动

### 一键启动（推荐）

脚本自动安装依赖、启动后端和前端，并在终端打印访问地址：

macOS / Linux：

```bash
./code-lens
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\code-lens.ps1
```

启动完成后访问：

| 服务 | 地址 |
| --- | --- |
| Web 页面 | `http://127.0.0.1:5173` |
| 后端 API | `http://127.0.0.1:8800` |
| OpenAPI 文档 | `http://127.0.0.1:8800/docs` |

- macOS/Linux：`./code-lens stop` 停止服务，`./code-lens restart` 重启
- Windows：脚本保持前台运行，`Ctrl+C` 同时停止前后端
- 首次启动后，先在 **Settings** 页面添加模型网关

### 手动启动

```bash
# 1. 安装依赖
uv sync --project backend
pnpm --dir frontend install

# 2. 启动统一后端（API + Worker 同进程）
uv run --project backend codelens-review start

# 3. 另开终端启动前端
pnpm --dir frontend dev
```

### 配置模型网关

打开 Web 页面 **Settings**，点击 **Add gateway**，填写 API Key、Base URL 和 Model。可添加多个网关并随时切换激活项，新 Review 使用当时激活的网关。

## 使用流程

1. **选择仓库** — 通过文件夹资源管理器浏览并选择本机 Git 仓库
2. **选择范围** — 选择 Branch diff / Commit diff / Uncommitted / Full repository
3. **选择 Reviewer** — 选择本次需要的审查维度
4. **启动审查** — 点击 Start review，实时查看事件流和 Finding 结果
5. **查看结果** — 在 Findings 面板查看严重级别、证据、影响和修改建议；在 Source 面板查看代码定位

所有创建的 Review 持久化在左侧列表，支持重新打开和删除。

## 项目结构

```text
CodeLens/
├── backend/
│   ├── src/codelens/       # DDD 分层后端：domain → application → interface/infrastructure
│   ├── migrations/         # Alembic 数据库迁移
│   └── tests/              # 单元、集成、契约和评测测试
├── frontend/
│   ├── src/                # React + TypeScript 应用与领域 feature
│   └── e2e/                # Playwright 端到端测试
├── prompts/
│   └── sys/<locale>/       # 多语言系统 Prompt（review-policy / review-workflow / tools.json ...）
├── conf/                   # 基础配置默认值（file-exclusions / web-settings-defaults）
├── docs/                   # 架构约束、白皮书和实现文档
└── TODO.md                 # 延期功能与路线图
```

后端依赖方向严格单向：

```text
interface / infrastructure → application → domain
bootstrap ----------------→ interface / infrastructure / application
```

前端只通过 HTTP/JSON 和 SSE 与后端通信，不直接访问仓库、数据库或模型运行时。

## 参与开发

我们欢迎各种形式的贡献！详见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 安全说明

- CodeLens v2 **无认证**，定位为个人电脑或明确受信任局域网内的单用户工具
- 默认监听 `0.0.0.0`，**不要**暴露到互联网或不受信任网络
- 审查模式对源仓库**只读**，任务在隔离的 detached worktree 中执行
- 模型不会获得用户原始工作区路径；敏感路径使用哈希或不透明引用
- API Key 保存在 `data/secrets/` (目录 `0700` / 文件 `0600`)，API 永不返回密钥
- 完整脱敏模型交换日志写入 `logs/model.log`，按 10 MiB 轮转

## 开源许可

CodeLens 采用 [Apache License 2.0](./LICENSE) 开源。第三方来源、版权归属和修改说明见 [NOTICE](./NOTICE)。