# CodeLens README 内容设计

**状态：** 已确认  
**日期：** 2026-07-18  
**目标文件：** `README.md`

## 1. 目标与受众

README 使用中文撰写，同时服务两类读者：希望尽快运行和体验 CodeLens 的使用者，以及需要理解开发、验证和架构入口的贡献者。内容采用“快速上手优先”的顺序，让首次访问者无需阅读设计文档即可完成依赖安装、配置、启动和第一次代码审查。

README 只把仓库当前实现并可从代码或测试验证的能力描述为可用功能。尚未完成的能力集中放入“当前限制与后续规划”，不得根据白皮书或阶段计划将其表述为已经交付。

## 2. 内容结构

README 按以下顺序组织：

1. 项目简介与当前阶段说明。
2. 当前已实现功能。
3. 环境要求。
4. 快速启动。
5. Web 使用流程。
6. 四种 Review 范围说明。
7. 安全与隔离边界。
8. 常用配置及 API、Worker 独立启动方式。
9. 测试与质量门禁。
10. 项目结构和进一步文档入口。
11. 当前限制与后续规划。

## 3. 当前能力表述边界

“当前已实现”只包括以下能力：

- 检查服务器本地 Git 仓库，并显示仓库、分支、HEAD 和工作区状态。
- 支持分支差异、指定 Commit、未提交改动和全仓四种 Review 范围。
- 为任务创建隔离的 detached worktree，冻结快照、变更索引、忽略规则和适用的仓库指令。
- 使用 OpenAI Agents SDK 运行内置 `correctness:v1` Reviewer。
- 通过持久化任务、检查点和事件支持 Worker 重启恢复，并通过 SSE 展示实时状态。
- 对模型输出执行结构、位置和证据校验，展示严重级别、置信度、影响、解释、证据和建议。
- API 和 Worker 可组合启动或独立启动；本地无鉴权模式只允许回环地址。

以下内容必须明确标为尚未完成：

- Security、Performance、Maintainability、Testing、Docs & Style 和 Cross-file Reviewer。
- FIX 模式及补丁验证、审批和应用流程。
- 完整汇总报告和 Artifact 浏览。
- Runs 历史列表、Reviewer 管理和设置页面。
- 多用户认证、远程部署和多 Worker 调度。

## 4. 启动与使用设计

环境要求列出 Python 3.12、`uv`、Node.js、`pnpm`、Git，以及可用的 OpenAI API Key。安装命令严格采用仓库规定的工具链：

```bash
uv sync --project backend
pnpm --dir frontend install
```

运行真实 Reviewer 前必须设置 `OPENAI_API_KEY` 和 `CODELENS_OPENAI_MODEL`。后端示例显式传入允许访问的仓库根目录，避免默认放开任意本地路径：

```bash
export OPENAI_API_KEY="your-api-key"
export CODELENS_OPENAI_MODEL="your-model"
uv run --project backend codelens-review start /absolute/path/to/allowed/repositories
```

前端在另一终端启动：

```bash
pnpm --dir frontend dev
```

README 指引用户访问 `http://127.0.0.1:5173`，输入一个位于允许根目录内的 Git 仓库根路径，执行 Inspect，选择 Review 范围并启动任务，最后在运行页查看 Overview、Findings 和 Agent Runs。

## 5. 开发与架构信息

README 简要说明 FastAPI、SQLite、独立 Worker、React/Vite 和 HTTP/SSE 的关系，不复制 `ARCHITECTURE.md`。项目结构只展示后端、前端、迁移、测试和文档的主要入口，并链接到 `ARCHITECTURE.md`、`CodeLens-白皮书.md`、`TODO.md` 及详细设计文档。

验证命令直接复用项目质量门禁：

```bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

## 6. 验收标准

- 新用户可以仅凭 README 安装依赖、配置模型、启动前后端并找到 Web 入口。
- 四种 Review 范围、仓库根目录限制和工作区改动选项的语义清楚且与当前 UI/API 一致。
- 已实现功能与规划能力有明确分区，不暗示 FIX、完整报告或多 Reviewer 已可用。
- 所有命令、端口、环境变量、内部链接和当前限制都能从仓库配置或实现中验证。
- README 不包含密钥、环境专用绝对路径或未经验证的产品承诺。
