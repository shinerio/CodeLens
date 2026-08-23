# 参与 CodeLens 开发

我们欢迎各种形式的贡献！CodeLens 是一个处于早期可用阶段的开源项目，仍有大量功能待实现。

## 开发环境搭建

```bash
# 克隆仓库
git clone <repo-url> && cd CodeLens

# 安装后端依赖
uv sync --project backend

# 安装前端依赖
pnpm --dir frontend install

# 启动后端（API + Worker 统一进程）
uv run --project backend codelens-review start

# 启动前端（另开终端）
pnpm --dir frontend dev
```

也可以使用一键启动脚本自动完成以上步骤：

```bash
# macOS / Linux
./code-lens

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File .\code-lens.ps1
```

## 质量门禁

提交前请确保通过对应的质量检查：

```bash
# 后端
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src

# 前端
pnpm --dir frontend test
pnpm --dir frontend build
```

## 贡献方向

- **Reviewer 开发** — 实现和调优 Security、Reliability & Concurrency、Contract & Data、Architecture、Performance、Test Regression 等专项 Reviewer 的 Prompt 和策略
- **插件开发** — 为 GitHub / GitLab / CodeHub 等平台开发 Trigger（Webhook / Git Hook 触发）和 Report（PR 评论 / 本地文件导出）插件
- **Prompt 优化** — 改进审查策略、减少误报、提升覆盖深度
- **前端体验** — 完善 Settings、Artifacts、Review 详情等页面
- **质量评测** — 构建可重复的评测基准，衡量有效发现率和误报比例
- **文档改进** — 补充使用指南、架构说明和示例

## 开发规范

- 后端遵循 DDD 分层，依赖方向只向内（`interface/infrastructure → application → domain`）；前端遵循 feature-based 结构
- Python 新增代码必须提供完整类型标注，通过 Ruff + mypy 严格检查
- TypeScript 启用严格模式，禁止无理由的 `any`
- 行为变更遵循测试驱动：先写失败测试，再实现最小修改
- 命名、注释、测试策略等完整规范参见 [AGENTS.md](./AGENTS.md)
- 架构约束和稳定契约参见 [docs/ARCHITECTURE.md](docs/architecture.md)

## 项目结构速览

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
│   ├── sys/<locale>/       # 多语言系统 Prompt（review-policy / review-workflow / tools.json ...）
│   └── <reviewer>/         # 各 Reviewer 专属 Prompt（en.md / zh-CN.md）
├── conf/                   # 基础配置默认值（file-exclusions / web-settings-defaults）
├── docs/                   # 架构约束、白皮书和实现文档
└── TODO.md                 # 延期功能与路线图
```

## 深入了解

- [产品白皮书](./docs/CodeLens-白皮书.md) — 产品愿景、功能体系和设计原则
- [架构约束](docs/architecture.md) — 技术栈、分层、稳定契约和安全边界
- [运行时 DAG](./docs/runtime-dag.md) — Planner → Reviewer → Verifier 的执行链路
- [Prompt 设计](./docs/prompt-design.md) — 多层 Prompt 构造机制
- [插件生态](./docs/plugin.md) — 统一插件模型和接入指南
- [与 Open Code Review 对比](./docs/open-code-review-comparison.md) — 实现与能力差异
- [TODO](./TODO.md) — 延期功能与路线图
