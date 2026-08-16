# CodeLens

CodeLens 是一个本地优先的多 Agent 代码 Review 工作台。它通过 Web 界面固定 Git 审查范围，在任务专属的隔离 worktree 中运行 Reviewer，并将模型输出转换为可定位、可验证的结构化 Findings。

## 优势

- 🎯 确定性图流控 (Deterministic Graph Orchestration)。基于 Graph Engineering 构建审查工作流，让 LLM 在受控的节点图谱中按需推理，兼顾灵活性与确定性。
- 🔒 零伪完成机制 (Zero "Fake-Completion" Assurance)。
   - 视角真实性：记录每次工具调用轨迹，无 read 操作禁止触发 review_file_done。 
   - 覆盖全面性：未通过所有改动文件的单文件审核，严禁触发 task_done，彻底杜绝 AI 的“敷衍应付”。
- ⚡ 多 Agent 专项并发审查 (Multi-Agent Specialist Swarm)。解耦单一 Agent 的庞杂任务，按正确性、安全性、性能、可维护性维度拆分为专属 Agent 并发执行。上下文更聚焦，召回率与准确率双高。
- 🛠️ 全链路 CR 深度定制 (CR-Native Architecture)。Prompt、Tools 和 Skills 全面聚焦代码审查场景。无冗余指令干扰，极大地收敛模型注意力，显著提升审查深度与结果质量。

> [!IMPORTANT]
> 项目目前处于早期可用阶段（Phase 0–2）。当前可用的是 `correctness:v1` Reviewer；多 Reviewer、完整报告和 Artifact 浏览仍在开发中。

## 当前功能

- 界面提供中英文双版本，并根据操作系统/浏览器语言自动选择中文或英文。
- 通过文件夹资源管理器选择任意本机可访问的合法 Git 仓库；macOS/Linux 可从 `/` 浏览，Windows 可从所有现有盘符浏览。
- 支持分支差异、指定 Commit、未提交改动和全仓扫描四种 Review 范围；分支和 Commit 均从 Git 下拉列表选择。
- Commit 默认显示最近 10 条，包含缩略 Commit ID、提交者和 Message 概要，并可继续加载。
- 左侧 Review 列表持久化展示所有 Review 工作空间，支持创建、重新打开和删除。
- 每个任务创建独立的 detached worktree 并冻结只读 Snapshot，Reviewer 不直接访问用户原始工作区。
- 冻结提交、工作区改动、`.gitignore` 结果和仓库 Review 指令，保证一次任务使用一致输入。
- 使用 OpenAI Agents SDK 运行内置的正确性 Reviewer。
- 通过 SQLite 持久化任务、检查点和事件；Worker 重启后可恢复未完成工作。
- 通过 SSE 实时展示任务状态和 Agent 事件，并支持断线续传。
- 校验模型输出的位置、证据和结构，展示严重级别、置信度、影响、解释、复现信息与修改建议。
- 后端以统一进程运行 API 和 Worker；默认监听 `0.0.0.0`，仅面向本机或明确受信任局域网。
- 服务启动后可在 Web Settings 页面持久化多个模型网关，随时切换当前激活网关，无需重启。

## 环境要求

- Git
- Python `3.12`
- [uv](https://docs.astral.sh/uv/)
- Node.js 与 [pnpm](https://pnpm.io/)
- 执行 Review 时可用的 OpenAI API Key 和模型 ID（启动服务时不需要）

## 快速启动

### 一键启动（推荐）

脚本会自动安装或同步前后端依赖，同时启动统一后端进程和前端，并在终端打印所有访问地址。启动时读取 `conf/runtime-settings.toml`；默认 `repository.roots = []` 表示允许选择当前操作系统可访问的任意合法 Git 仓库（Windows 为现有磁盘），相对根目录相对项目根解析。

macOS / Linux：

```bash
./code-lens
```

`start` 是默认参数；启动完成后命令会退出，服务继续在后台运行。使用 `./code-lens stop` 停止服务，或使用 `./code-lens restart` 先停止再重新启动。

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\code-lens.ps1
```

启动完成后访问：

- Web 页面：`http://127.0.0.1:5173`
- 后端 API：`http://127.0.0.1:8800`
- OpenAPI 文档：`http://127.0.0.1:8800/docs`

Windows 脚本会保持前台运行，按 `Ctrl+C` 会同时停止前后端；macOS/Linux 脚本在服务就绪后退出，应使用 `./code-lens stop` 停止后台服务。首次启动后，先打开 Web 页面中的 **Settings / 设置** 添加模型网关；服务启动和浏览仓库都不依赖模型配置。

### 手动启动

#### 1. 安装依赖

在项目根目录执行：

```bash
uv sync --project backend
pnpm --dir frontend install
```

#### 2. 启动统一后端

`start` 会在同一个后端进程内运行 API 和 Worker。省略位置参数时不限制合法仓库的所在目录：

```bash
uv run --project backend codelens-review start
```

后端默认监听 `http://127.0.0.1:8800`。健康检查和 OpenAPI 文档分别位于：

- `http://127.0.0.1:8800/api/health`
- `http://127.0.0.1:8800/docs`

#### 3. 启动前端

另开一个终端：

```bash
pnpm --dir frontend dev
```

浏览器访问 `http://127.0.0.1:5173`。Vite 会将 `/api` 请求代理到本地后端。

#### 4. 在 Web 页面配置模型

打开侧边栏的 **Settings / 设置**，点击 **Add gateway / 添加网关**，填写并保存：

- **API Key**：模型网关的访问凭证。
- **Base URL**：OpenAI-compatible API 地址，例如 `https://api.openai.com/v1`。
- **Model**：网关提供的模型 ID。

可以重复添加多个网关。第一个网关会自动激活；之后可以在网关卡片上点击 **Activate / 激活** 随时切换，新的 Review 会使用当时激活的网关。编辑网关时 API Key 留空会保留原凭证。

统一后端在未配置模型时也能正常启动。配置保存在项目数据目录 `data/secrets/model-gateways.json`，API 不会回传 Key；Worker 在 Review 实际执行时读取当前激活网关。项目 `.gitignore` 会排除 `data/`；删除该目录会删除本地设置、数据库、任务数据、Artifact 和网关凭证。

请勿将 API Key 写入仓库、日志或截图。使用远程 HTTP 地址会以明文传输凭证和 Review 内容，仅应在明确受信任的网络中使用。

## 使用介绍

### 1. 检查仓库

在 **New Review / 新建 Review** 页面点击 **Browse folders / 浏览文件夹**，通过资源管理器选择 Git 仓库：

- macOS/Linux 从 `/` 开始浏览；Windows 会显示所有现有磁盘盘符。
- 只展示启动 CodeLens 的当前用户具备读取和进入权限的目录；无权限目录会自动跳过。
- 只有 Git 仓库目录会出现选择按钮，普通目录可以继续展开。
- 选择后会自动检查仓库，并展示分支、HEAD、Dirty 状态和仓库标识，不需要手工输入路径。

### 2. 选择 Review 范围

| 范围 | 用途 | 工作区改动 |
| --- | --- | --- |
| Branch diff | 审查 base ref 与 target ref 的分支差异 | 可选捕获当前工作区改动 |
| Commit diff | 审查指定 base commit 到 target ref 的差异 | 可选捕获当前工作区改动 |
| Uncommitted | 审查当前 HEAD 之上的 staged、unstaged 和未忽略的 untracked 改动 | 自动包含 |
| Full repository | 审查 target ref 中所有符合规则的文件 | 可选捕获当前工作区改动 |

Branch 和 Full repository 的引用通过分支下拉框选择。Commit diff 的 Commit 下拉框默认加载最近 10 条记录，每条展示缩略 ID、提交者和 Message 概要；点击 **Load more commits / 加载更多 Commit** 可继续向前加载。

对于 Branch、Commit 和 Full repository，只有目标指向当前 checkout 的 `HEAD` 时，工作区改动才会作为不可变 overlay 被捕获。所有范围都会排除当前 `.gitignore` 命中的路径，包括已经被 Git 跟踪的文件。

### 3. 选择 Reviewer

当前可用的 Reviewer 是 `correctness:v1`。页面中的其他 Reviewer 用于展示后续产品方向，目前不可选择。

### 4. 启动并查看结果

点击 **Start review** 后，页面会进入任务运行视图：

- **Overview**：任务状态、连接状态、事件数和 Finding 数量。
- **Findings**：经过校验的问题列表及详细证据、影响和建议。
- **Agent Runs**：可恢复的 SSE 事件流。
- **Artifacts**：当前仅为占位页，尚不能浏览运行产物。

任务可能以 `completed`、`partial`、`failed` 或 `canceled` 结束。`partial` 表示只产生了部分可信结果，不应当作完整审查处理。

所有创建过的 Review 都会显示在左侧 **Reviews / Review 列表** 二级菜单中。点击条目可重新打开对应工作空间；点击删除按钮会将其从列表中软删除，正在运行的任务会先请求取消。

## Review 指令

CodeLens 会随任务快照冻结适用于目标文件的规则，按从仓库根目录到目标文件的顺序解析：

- 从仓库根目录到目标文件所在目录的每一级，以大小写不敏感方式发现 `AGENTS.md` 和 `REVIEW.md`
- 以大小写不敏感方式发现文件级 `<relative/path/to/file>.review.md`

同一目录中的规则按 `AGENTS.md`、`REVIEW.md` 顺序应用，更深目录优先级更高，文件级规则最高；同一目录出现仅大小写不同的同名规则文件会被拒绝。`REVIEW.md` 可以通过 YAML frontmatter 的 `exclude` 字段或 Markdown 的 `## Skip` 列表排除路径。仓库内容和规则文件都会被视为不可信输入，不能扩大 Reviewer 权限。

## 安全与数据隔离

- CodeLens v2 无认证，定位为个人电脑或明确受信任局域网内的单用户工具；默认监听 `0.0.0.0`，不要暴露到互联网或不受信任网络。
- 默认不设置仓库根目录白名单，因此操作系统当前用户可读的目录都属于本地信任边界。Web 资源管理器只展示该用户具备读取和进入权限的目录，无权限目录会被跳过；需要收窄时在 `conf/runtime-settings.toml` 配置仓库根目录。
- REVIEW 模式对源仓库只读。任务只在应用数据目录下创建由 CodeLens 管理的 detached worktree。
- 模型不会获得用户原始工作区路径；持久化记录和事件使用哈希或不透明引用表示敏感路径与 Artifact。
- 完整脱敏模型交换日志默认写入项目 `logs/model.log`，并按 10 MiB 一个压缩备份轮转；可在 Settings 页关闭 Model output logging。普通运行日志不包含 Prompt、模型原始输出或源码正文。
- Web 保存的模型网关配置位于 data directory 的 `secrets/model-gateways.json`，目录权限为 `0700`、文件权限为 `0600`。读取 API 返回网关 ID、名称、模型 ID、Base URL、供应商、API 类型、激活状态和非 Secret 的模型与执行策略，绝不返回 API Key。

## 配置

常用环境变量：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CODELENS_DATA_DIR` | 项目 `data/` | SQLite、worktree、检查点和 Artifact 的应用数据目录；删除该目录会删除本地任务数据和网关凭证 |
| `CODELENS_HOST` | `0.0.0.0` | HTTP 监听地址；仅用于本机或明确受信任局域网，不要暴露到互联网 |
| `CODELENS_PORT` | `8800` | HTTP 端口 |
| `CODELENS_DATABASE_URL` | 本地 SQLite | 可选数据库连接 URL；数据库结构仍由 Alembic 管理 |

启动命令也支持 `--host`、`--port` 和 `--data-dir`。执行以下命令查看完整参数：

```bash
uv run --project backend codelens-review --help
uv run --project backend codelens-review start --help
```

## 后端进程模式

后端只支持统一进程入口：

```bash
uv run --project backend codelens-review start
```

不要拆成独立 API 和 Worker 进程运行 Web Review 流程；后端进程内共享内存事件总线和运行中 transcript，拆分后前端可能看不到实时进度并停在等待事件流状态。

> `codelens-review` 目前只提供启动和进程管理命令；产品交互入口是 Web/API。

## 项目结构

```text
CodeLens/
├── backend/
│   ├── src/codelens/       # DDD 分层的后端、HTTP 接口与 Worker
│   ├── migrations/         # Alembic 数据库迁移
│   ├── scripts/            # Fake Server 与真实模型 smoke test
│   └── tests/              # 单元、集成、契约和评测测试
├── frontend/
│   ├── src/                # React 应用与领域 feature
│   └── e2e/                # Playwright 端到端流程
├── docs/
│   ├── ARCHITECTURE.md     # 强制架构约束
│   ├── CodeLens-白皮书.md  # 产品白皮书
│   └── ...                 # 机制与实现文档
└── TODO.md                 # 延期功能与路线图
```

后端依赖方向固定为：

```text
interface / infrastructure -> application -> domain
bootstrap -----------------> interface / infrastructure / application
```

前端只通过 HTTP/JSON 和 SSE 与后端通信，不直接访问仓库、数据库、Artifact Store 或模型运行时。完整约束见 [ARCHITECTURE.md](./docs/ARCHITECTURE.md)。

## 测试与质量门禁

后端：

```bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
```

前端：

```bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

默认测试使用可注入的假模型，不需要网络或真实 OpenAI 凭证。真实模型 smoke test 仍可通过 `OPENAI_API_KEY`、`CODELENS_OPENAI_MODEL` 和可选的 `OPENAI_BASE_URL` 显式注入临时配置后单独运行；常规 Web 启动不依赖这些环境变量。

## 当前限制与后续规划

以下能力尚未完成，不应视为当前可用功能：

- Security、Performance、Maintainability、Testing、Docs & Style 和 Cross-file Reviewer。
- 多 Reviewer 并发汇总和完整 Review Report。
- Artifact 浏览和 Reviewer 管理页面。
- 互联网远程部署、多用户身份认证和权限；当前无认证边界只面向本机或明确受信任局域网。

更多背景和后续计划：

- [产品白皮书](./docs/CodeLens-白皮书.md)
- [架构约束](./docs/ARCHITECTURE.md)
- [与 Open Code Review 的实现与能力对比](./docs/open-code-review-comparison.md)
- [TODO 与延期事项](./TODO.md)

## 开源许可与致谢

CodeLens 采用 [Apache License 2.0](./LICENSE) 开源。第三方来源、版权归属和修改说明见 [NOTICE](./NOTICE)。

本项目的任务内评论收集思路参考了 [Alibaba Open Code Review](https://github.com/alibaba/open-code-review) 的 `code_comment` 模式；`backend/src/codelens/review/infrastructure/line_resolver.py` 对其 `internal/diff/resolver.go` 中的确定性行号定位算法进行了 Python 改编。CodeLens 在此基础上增加了冻结 Snapshot、Manifest 内容哈希校验、模型显式选择的 old/new 侧唯一变更 hunk 约束，以及由后端派生 hunk ID 和 excerpt hash 的验证链路。参考版本、逐项差异和可借鉴结论见[完整对比报告](./docs/open-code-review-comparison.md)。
