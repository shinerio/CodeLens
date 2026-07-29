# CodeLens Agent 工作规范

## 1. 文档职责

本文件规定 Agent 在 CodeLens 仓库中的工作方式，是实现流程、代码规范和质量门禁的唯一权威来源。系统本身的架构事实与约束由 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) 定义。

内容按以下标准归属。同一规则的细节只在归属文档中维护，另一份文档只能通过链接和执行动作引用，不复制规则正文：

| 文档 | 应包含 | 不应包含 |
| --- | --- | --- |
| `docs/ARCHITECTURE.md` | 技术选型、前后端与进程边界、DDD 分层和依赖方向、限界上下文、稳定契约、数据所有权、安全与信任边界、部署拓扑、架构变更条件 | Agent 操作步骤、代码风格细则、测试命令、日常完成清单 |
| `AGENTS.md` | 任务开始与实施流程、代码和命名规范、注释要求、测试策略、运行与验证命令、完成标准 | 具体业务契约、领域边界定义、数据流、运行时拓扑的重复说明 |

判断一项规则是否应进入 `docs/ARCHITECTURE.md`：描述系统结构、跨边界行为或需要长期保持的系统属性时，它是架构规则；只约束如何实现、检查和交付代码时，应进入本文件。具体架构变更范围以 `docs/ARCHITECTURE.md` 的“文档定位”为准。

## 2. 开始任务前

- 开始设计、编码、审查或重构前，必须完整阅读并遵循 `docs/ARCHITECTURE.md`，再确认任务所属限界上下文、分层和稳定契约。
- 阅读相关实现和测试，检查工作区已有改动；不得覆盖或回退用户改动。
- 若任务要求与 `docs/ARCHITECTURE.md` 冲突，必须指出冲突并获得架构调整确认，不得静默偏离。
- 任务触及 `docs/ARCHITECTURE.md` 定义的架构变更范围时，必须同步更新该文档。

## 3. 实施规范

- 行为变更遵循测试驱动开发：先编写能复现需求或缺陷的失败测试，再实现最小修改，最后执行回归验证。
- 修改范围必须聚焦，不顺带重构无关模块。
- Python 新增代码必须提供完整类型标注，并通过 Ruff 和 mypy 严格检查。I/O 密集操作使用异步接口；阻塞调用必须隔离，禁止在事件循环中直接执行长时间同步任务。
- 调用 Git 或外部进程时使用参数数组，禁止 `shell=True`，并设置超时、输出上限和明确的允许退出码。
- TypeScript 启用严格类型检查，禁止使用无理由的 `any`、非空断言和未校验的类型转换。
- 保持文件和函数职责单一，优先使用清晰命名和小型模块。
- 配置通过环境变量或配置对象注入，不得在源码中硬编码密钥、模型凭证、仓库路径或环境专用地址。
- 新增依赖前确认现有工具链无法合理解决问题，并锁定依赖版本、补充适配层和契约测试。
- 涉及日志、Prompt、Transcript 或模型输出的实现必须按 `docs/ARCHITECTURE.md` 的数据与安全边界检查脱敏、文件权限、轮转和传播行为，不得把敏感正文写入普通运行日志。

## 4. 命名规范

所有代码标识符使用英文，名称必须表达业务含义和所属边界。禁止使用含义不明的缩写、单字母业务变量，以及 `data`、`info`、`manager`、`helper` 等无法说明职责的泛化名称。

### 4.1 Python

- 包、模块、函数、方法和变量使用 `snake_case`。
- 类、协议、枚举、领域事件和异常使用 `PascalCase`。
- 常量使用 `UPPER_SNAKE_CASE`；私有成员以单下划线开头。
- Port 使用职责名加 `Port`，例如 `ReviewWorktreePort`；具体实现使用能力或供应商名加 `Adapter`，例如 `GitCliWorktreeAdapter`。
- Repository 接口使用聚合名加 `Repository`；实现类必须体现持久化技术，例如 `SqlAlchemyReviewTaskRepository`。
- Command 使用祈使动作命名，Query 使用查询意图命名，Handler 使用对应消息名加 `Handler`。
- 领域事件使用已经发生的事实命名；异常以 `Error` 结尾；布尔值使用 `is_`、`has_`、`can_` 或 `should_` 前缀。
- 测试文件使用 `test_<subject>.py`，测试名称描述条件和预期行为。

### 4.2 TypeScript 与 React

- 变量、函数和普通模块导出使用 `camelCase`；类型、接口、枚举和 React 组件使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`。
- React 组件文件使用 `PascalCase.tsx`；Hook 以 `use` 开头；其他文件和目录使用 `kebab-case`。
- 事件处理函数使用 `handle<Action>`，回调属性使用 `on<Action>`；布尔值使用 `is`、`has`、`can` 或 `should` 前缀。
- Feature 名称必须对应用户可识别的业务能力，禁止用页面位置或临时实现细节命名共享业务模块。
- 测试文件使用 `<subject>.test.ts` 或 `<subject>.test.tsx`；端到端测试使用 `<flow>.spec.ts`。

### 4.3 数据库与文件

- 数据库表使用复数 `snake_case`，列使用单数 `snake_case`；外键使用 `<entity>_id`，时间字段使用 `<event>_at`。
- Alembic revision 名称必须描述实际结构变化，不使用 `update`、`changes` 等空泛名称。
- 文件名应与其主要职责或公开类型一致。同一业务概念在领域模型、API、事件、数据库和前端中使用一致词汇，命名转换必须在边界适配器中显式完成。

## 5. 注释规范

代码应先通过清晰命名和小型结构自解释。注释用于记录代码本身无法表达的意图、约束和取舍，不要求逐行复述。

以下内容必须具有完整且与实现同步的 docstring、TSDoc 或邻近注释：

- 对外公开的 Port、Adapter、应用用例、领域服务、API/SSE 契约和可复用前端组件。
- 聚合不变量、状态机转换、幂等策略、事务边界和失败恢复规则。
- 并发控制、锁顺序、超时、重试、取消和资源清理语义。
- Review 只读隔离、权限、信任、Secret 处理和 Prompt Injection 防护等安全边界。
- 不直观的算法、性能权衡、兼容性处理以及供应商限制或临时绕行。

注释按适用情况说明用途、输入输出约束、关键不变量、副作用、失败方式、并发或安全注意事项。禁止逐字复述代码、保留失效历史说明、注释掉代码，或用注释掩盖过大的函数和错误的边界。确需保留的待办使用 `TODO(<issue-or-owner>): <原因与移除条件>`。修改行为时同步更新关联注释、契约示例和文档。

## 6. 运行方式

后端依赖和命令统一通过 `uv` 管理：

```bash
uv sync --project backend
uv run --project backend codelens-review start
```

前端依赖和命令统一通过 `pnpm` 管理：

```bash
pnpm --dir frontend install
pnpm --dir frontend dev
```

启动开发服务时，网络绑定和允许访问范围必须遵循 `docs/ARCHITECTURE.md` 定义的信任边界，不得通过命令参数绕过。

## 7. 测试与质量门禁

- 测试分为单元、集成、契约、端到端和评测。领域逻辑使用单元测试；Git、SQLite 和文件系统使用真实临时夹具；外部模型调用默认使用可注入的假实现。
- 先运行与修改范围直接相关的测试，再运行适用的完整门禁。

后端标准门禁：

```bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
```

前端标准门禁：

```bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

- 涉及 Git 范围、`.gitignore`、快照或符号链接的测试必须使用临时真实 Git 仓库，不能只模拟命令输出。
- 涉及并发、任务租约、取消或恢复的测试必须验证重复执行、超时、进程重启和部分失败场景。
- 前端仅支持 PC 端，桌面视口最小支持宽度为 1280px；不得要求、实现或测试移动端、窄屏或触摸端适配。涉及 UI 的变更必须至少在 `1280x800` 桌面视口检查加载、空数据、失败、部分成功和长文本状态，避免溢出和遮挡。
- 真实 OpenAI、远程 MCP 或网络测试必须显式启用，不得成为默认测试套件的前置条件。
- 未实际运行对应验证命令时，不得声称测试、构建或功能已经通过。

## 8. 完成标准

提交结果前确认：变更位于正确边界且范围聚焦；新增或修改行为有相应测试；类型、命名和注释符合本文件；适用门禁已运行并如实报告；架构变更已同步更新 `docs/ARCHITECTURE.md`；用户已有改动未被覆盖。
