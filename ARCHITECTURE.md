# CodeLens 架构约束

## 1. 文档定位

本文档是 CodeLens 项目的架构约束唯一权威来源，适用于后端、Worker、前端、数据库迁移、API 契约、测试夹具和部署入口。所有新增功能、缺陷修复与重构都必须先确定所属业务边界和架构层，再开始实现。

架构调整必须同时更新本文档；涉及稳定契约、依赖方向、数据所有权、安全边界或部署拓扑的决策，还必须新增或更新 ADR。实现、测试和其他说明文档与本文档冲突时，以本文档为准。

## 2. 技术栈

### 2.1 后端

- Python `>=3.12,<3.13`，依赖和命令统一通过 `uv` 管理。
- FastAPI 提供 HTTP API 和 SSE 事件流；Pydantic v2 负责边界数据校验。
- SQLAlchemy 2 负责持久化适配，Alembic 管理数据库迁移。
- SQLite 使用 WAL 模式；大对象写入 Artifact Store，数据库仅保存元数据、内容哈希和不透明引用。
- OpenAI Agents SDK、Git、文件系统、Skill、MCP、沙箱、代码检索和 Secret Store 均作为外部能力，通过 Port/Adapter 接入。
- 异步 I/O 使用 `asyncio`；Git 和外部进程使用参数数组调用，禁止 `shell=True`。
- pytest、Ruff 和 mypy 是后端的基础质量门禁。

### 2.2 前端

- React、TypeScript 严格模式和 Vite。
- TanStack Query 管理服务端状态，React Router 管理路由。
- Vitest 负责单元与组件测试，Playwright 负责端到端测试。
- 依赖和命令统一通过 `pnpm` 管理。
- Monaco Diff Editor 或同等成熟组件负责差异展示，不自行实现通用 diff 算法和编辑器内核。

### 2.3 通信与运行

- 前后端只通过稳定的 HTTP/JSON 和 SSE 契约通信；前端不得直接访问仓库、数据库、Artifact Store 或模型运行时。
- HTTP 用于命令和查询，SSE 用于可恢复的单向事件推送，并支持 `Last-Event-ID`。
- API、Worker 和前端必须能够独立启动，不得依赖进程内共享状态或隐式启动顺序。
- 当前阶段不实现可替代 Web 的 Review/Fix 业务 CLI；启动、进程管理和诊断命令不属于业务交互入口。架构仅保留未来 CLI 入站适配器的扩展能力。
- 本机无鉴权模式仅允许绑定 `127.0.0.1`；非回环地址必须配置明确的信任和访问边界。

## 3. 前后端分离

### 3.1 后端职责

后端负责领域规则、用例编排、权限与安全校验、Git 和文件系统访问、模型与 Agent 调度、持久化、任务恢复以及 API/SSE 契约。任何影响业务正确性或安全边界的判断都不得只存在于前端。

### 3.2 前端职责

前端负责用户流程编排、状态呈现、输入收集和交互反馈。页面组件只组织流程；业务状态、API 调用和缓存逻辑下沉到对应的 `features/*` 模块；`shared` 目录不得包含特定领域规则。

### 3.3 稳定契约

- API 请求、响应和模型输出必须在后端边界使用 Pydantic DTO 校验，不得直接序列化领域实体或 ORM 模型。
- JSON 字段、错误码、事件名称和状态值属于稳定契约。变更时必须考虑向后兼容、幂等、迁移和失败恢复。
- SSE 事件必须来自持久化 outbox；部分成功、超时和失败必须显式表达，不能伪装为完整成功。
- 前端类型应从经过验证的契约生成或集中维护，不得通过 `any`、非空断言或未校验的类型转换绕过边界。

### 3.4 CLI 可扩展约束

当前产品交互入口是 Web，当前交付范围不包含用于创建 Review、查看进度与报告或执行 Fix 的业务 CLI。未来新增 CLI 时，它必须作为 Interface 层的薄入站适配器，不得形成独立的业务实现：

- 独立进程 CLI 优先复用稳定的 HTTP/SSE 契约；与后端同进程的 CLI 只能通过组合根调用 Application 层用例。
- CLI 不得直接访问 ORM、数据库、Git、文件系统、Artifact Store、供应商 SDK 或领域对象的内部可变状态。
- CLI 与 Web 必须遵循相同的输入校验、权限、安全、幂等、任务状态、错误语义和失败恢复规则。
- 长时间任务必须复用既有任务模型，返回任务标识并通过轮询或事件流观察进度，不得另建同步执行管线。
- CLI 的加入不得要求修改 Domain 层；只有新增真实业务能力时，才能先扩展领域和应用契约。
- 本节只约束未来扩展，不构成当前阶段的 CLI 实现要求或验收条件。

## 4. DDD 领域分层

### 4.1 依赖方向

后端依赖方向固定为：

```text
interface / infrastructure -> application -> domain
bootstrap -----------------> interface / infrastructure / application
```

所有依赖只能指向内层：

- `domain` 不依赖 `application`、`interface`、`infrastructure` 或 `bootstrap`。
- `application` 可以依赖 `domain`，但不得依赖具体基础设施实现。
- `interface` 和 `infrastructure` 可以依赖 `application` 与 `domain`，负责协议转换和 Port 实现。
- `bootstrap` 仅负责配置读取、依赖组装和进程入口，不承载业务规则。
- `worker` 是应用用例的驱动入口；任务状态转换和恢复规则仍归属于相应领域与应用层。

禁止通过循环依赖、运行时全局容器、服务定位器或跨层重新导出来规避依赖方向。

### 4.2 Domain 层

Domain 层包含聚合、实体、值对象、领域服务、领域事件、领域错误和必要的领域 Port。它必须保持纯净、确定且可在无外部服务的情况下测试。

Domain 层不得导入或直接调用：

- FastAPI、Pydantic API DTO 或 HTTP 类型；
- SQLAlchemy、Alembic、SQLite 驱动；
- OpenAI SDK、MCP SDK 或供应商模型类型；
- Git 库、子进程、文件系统、网络或环境变量；
- React、浏览器或界面状态。

领域模型优先使用标准库 `dataclass`、`Enum`、不可变值对象和显式领域错误。聚合必须维护自身不变量，不得依赖调用方按特定顺序修改公开字段。

### 4.3 Application 层

Application 层实现命令、查询、用例编排、事务边界、权限决策、幂等控制和跨领域协作。它通过 Port 请求持久化、时钟、模型、Git、文件系统、消息、代码检索和沙箱等能力。

Application 层不得创建 SQLAlchemy Session、发起 HTTP 响应、读取 React 状态或实例化供应商 SDK 客户端。Commands 与 Queries 在概念和模块上分离，但没有真实复杂度前不引入独立 CQRS 框架。

### 4.4 Infrastructure 层

Infrastructure 层实现 Application 或 Domain 定义的 Port，包括 SQLAlchemy Repository、Git CLI、Artifact Store、OpenAI Agent Runtime、Skill/MCP、沙箱、Secret Store 和代码检索适配器。

适配器负责把供应商异常、数据结构和生命周期转换为项目内部契约。供应商类型不得穿透 Port；外部输入必须先校验，再交给应用层或领域层。

### 4.5 Interface 层

Interface 层当前包含 FastAPI 路由、请求/响应 DTO、SSE 端点和 Worker 驱动入口，并为未来 CLI 等入站适配器保留扩展位置。它只负责协议解析、身份与边界校验、调用应用用例以及把结果映射为稳定契约，不实现领域决策。

### 4.6 组合根

`bootstrap` 是唯一允许集中读取配置并组装具体实现的位置。业务模块不得自行读取环境变量、创建数据库引擎、选择模型供应商或访问全局可变单例。

## 5. 业务边界

后端按限界上下文组织，而不是按技术类型建立全局 `models.py`、`services.py` 或 `utils.py`：

- `workspace`：仓库识别、Review 范围、Git ignore、任务 worktree 和不可变快照。
- `review`：ReviewTask 生命周期、预算、完成策略、Agent 运行和应用层编排。
- `reviewer_catalog`：Reviewer、Prompt、模型策略和能力绑定的版本化目录。
- `instruction_policy`：规则文件的发现、解析、优先级和冻结。
- `findings`：Finding、Evidence、校验、去重、抑制和报告。
- `change_proposal`：隔离修复、补丁验证、审批和安全应用。
- `capabilities`：Skill、MCP、静态工具、沙箱和仓库信任策略。
- `governance`：审计、反馈、评测和规则建议，不直接改变正在运行的规则。

跨上下文协作必须使用明确的应用服务、领域事件或 Port。一个上下文不得导入另一个上下文的 `infrastructure` 实现、ORM 模型或内部可变状态。共享模块只允许放置稳定、无领域归属且被多个上下文实际复用的最小基础类型。

推荐目录结构：

```text
backend/src/codelens/
  bootstrap/
  shared/domain/
  <bounded_context>/
    domain/
    application/
    infrastructure/
  interface/http/
  worker/

frontend/src/
  app/
  features/<feature>/
  shared/
```

新增抽象必须解决已经存在的复杂度、重复或替换需求，禁止为了预期复用提前创建无边界的公共层。

## 6. 数据、安全与执行边界

- REVIEW 模式对源仓库严格只读。每个任务在应用数据目录创建自己拥有的 detached worktree，并在其中冻结 `ReviewSnapshot`。
- FIX 模式只能修改隔离工作区；补丁通过结构校验、测试或命令门禁、审批和目标仓库冲突检查后才能应用。
- Agent、模型和沙箱不得访问用户原始工作区，也不得修改源分支、index、tag 或非本任务 worktree。
- 仓库内容、规则文件、Skill、MCP 输出和模型输出全部视为不可信数据，不能扩大 Agent、进程或工具权限。
- Secret 不得进入数据库、日志、事件、Artifact、Prompt、RunContext 或错误响应。日志使用结构化字段，并对路径、源码和供应商诊断执行最小披露。
- 数据库结构只能通过 Alembic migration 演进；持久化任务和事件必须支持幂等、重启恢复及部分失败。

## 7. 命名规范

所有代码标识符使用英文，名称必须表达业务含义和所属边界。禁止使用含义不明的缩写、单字母业务变量、`data`、`info`、`manager`、`helper` 等无法说明职责的泛化名称。

### 7.1 Python

- 包、模块、函数、方法和变量使用 `snake_case`。
- 类、协议、枚举、领域事件和异常使用 `PascalCase`。
- 常量使用 `UPPER_SNAKE_CASE`；私有成员以单下划线开头。
- Port 使用职责名加 `Port`，例如 `ReviewWorktreePort`；具体实现使用能力或供应商名加 `Adapter`，例如 `GitCliWorktreeAdapter`。
- Repository 接口使用聚合名加 `Repository`；实现类必须体现持久化技术，例如 `SqlAlchemyReviewTaskRepository`。
- Command 使用祈使动作命名，Query 使用查询意图命名，Handler 使用对应消息名加 `Handler`。
- 领域事件使用已经发生的事实命名，例如 `ReviewTaskCreated`；异常以 `Error` 结尾；布尔值使用 `is_`、`has_`、`can_` 或 `should_` 前缀。
- 测试文件使用 `test_<subject>.py`，测试名称描述条件和预期行为。

### 7.2 TypeScript 与 React

- 变量、函数和普通模块导出使用 `camelCase`；类型、接口、枚举和 React 组件使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`。
- React 组件文件使用 `PascalCase.tsx`；Hook 以 `use` 开头；其他文件和目录使用 `kebab-case`。
- 事件处理函数使用 `handle<Action>`，回调属性使用 `on<Action>`；布尔值使用 `is`、`has`、`can` 或 `should` 前缀。
- Feature 名称必须对应用户可识别的业务能力；禁止用页面位置或临时实现细节命名共享业务模块。
- 测试文件使用 `<subject>.test.ts` 或 `<subject>.test.tsx`；端到端测试使用 `<flow>.spec.ts`。

### 7.3 HTTP、CLI、事件与数据库

- HTTP 路径使用小写、复数资源名和 `kebab-case`，不在路径中使用动词表达普通 CRUD。
- JSON 字段使用 `snake_case`；枚举和状态的序列化值使用小写 `snake_case`。
- 未来 CLI 的命令和选项使用小写 `kebab-case`，并复用领域词汇；机器可读输出使用稳定、版本化的 JSON schema，标准输出、诊断输出和退出码必须有明确契约。
- 事件名称使用已经发生的领域事实，并进行显式版本控制；事件载荷字段遵循 JSON 命名规则。
- 数据库表使用复数 `snake_case`，列使用单数 `snake_case`；外键使用 `<entity>_id`，时间字段使用 `<event>_at`。
- Alembic revision 名称必须描述实际结构变化，不使用 `update`、`changes` 等空泛名称。

### 7.4 文件与通用名称

- 文件名应与其主要职责或公开类型一致；一个文件只承担一个可清晰描述的职责。
- 同一业务概念在领域模型、API、事件、数据库和前端中使用一致词汇。需要转换命名时，必须在边界适配器中显式完成。
- 禁止创建无业务边界的 `utils.py`、`services.py`、`models.py`、`common.ts` 或全局状态容器。

## 8. 注释完整性

注释完整性是指关键意图和契约可被维护者理解，不是为每行代码添加复述性注释。代码应先通过清晰命名和小型结构自解释，注释用于补充代码无法表达的信息。

以下内容必须具有完整且与实现同步的 docstring、TSDoc 或邻近注释：

- 对外公开的 Port、Adapter、应用用例、领域服务、API/SSE 契约和可复用前端组件。
- 聚合不变量、状态机转换、幂等策略、事务边界和失败恢复规则。
- 并发控制、锁顺序、超时、重试、取消和资源清理语义。
- REVIEW/FIX 隔离、权限、信任、Secret 处理和 Prompt Injection 防护等安全边界。
- 不直观的算法、性能权衡、兼容性处理以及供应商限制或临时绕行。

完整注释至少应按适用情况说明：用途、输入与输出约束、关键不变量、副作用、可能失败、并发或安全注意事项。Python 公共 API 使用 docstring；TypeScript 公共契约在名称和类型不能完整表达语义时使用 TSDoc。

禁止以下注释：

- 逐字复述代码、保留已经失效的历史说明或注释掉的代码。
- 用注释掩盖过大的函数、错误的命名或不清晰的领域边界。
- 无追踪信息的 `TODO`、`FIXME` 或永久性临时方案。确需保留时，格式为 `TODO(<issue-or-owner>): <原因与移除条件>`。
- 包含 API Key、访问凭证、完整 Prompt、完整模型原始输出或不必要源码正文的示例和日志说明。

修改行为时必须同步更新关联注释、docstring、契约示例和架构文档；过期注释视为缺陷。

## 9. 架构变更检查清单

提交实现前至少确认：

- 变更位于正确的限界上下文和分层，没有出现反向依赖或跨上下文基础设施访问。
- 外部能力通过 Port/Adapter 接入，供应商类型没有进入领域或应用契约。
- 前后端只通过经过校验的 HTTP/SSE 契约通信，业务规则没有只存在于 UI。
- 新增 CLI 或其他入站适配器时，只复用稳定契约或 Application 用例，没有复制业务流程或绕过安全边界。
- API、事件、数据库和持久化任务变更已覆盖兼容、迁移、幂等及恢复。
- 命名遵循统一领域词汇，没有新增泛化公共模块或无实际需求的抽象。
- 关键契约、不变量、并发和安全边界具有完整且最新的注释。
- 行为变更具有相应层级的测试；架构调整已同步更新本文档和必要的 ADR。
