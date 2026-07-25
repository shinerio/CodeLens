# CodeLens 架构约束

## 1. 文档定位

本文档是 CodeLens 项目架构事实与约束的唯一权威来源，适用于后端、Worker、前端、数据库、外部能力、稳定契约和部署入口。本文只回答系统由什么组成、边界在哪里、依赖如何流动，以及哪些契约和安全属性必须长期成立。

修改稳定契约、依赖方向、数据所有权、安全边界、限界上下文或部署拓扑时，必须同步更新本文档。实现和其他说明文档与本文档冲突时，以本文档为准。

## 2. 技术栈

### 2.1 后端

- Python `>=3.12,<3.13`，依赖和命令统一通过 `uv` 管理。
- FastAPI 提供 HTTP API 和 SSE 事件流；Pydantic v2 负责边界数据校验。
- SQLAlchemy 2 负责持久化适配，Alembic 管理数据库迁移。
- SQLite 使用 WAL 模式；大对象写入 Artifact Store，数据库仅保存元数据、内容哈希和不透明引用。
- OpenAI Agents SDK、Git、文件系统、Skill、MCP、沙箱、代码检索和 Secret Store 均作为外部能力，通过 Port/Adapter 接入。MVP 的代码检索仅由 CodeLens 内置、只读的 Snapshot 工具提供，不依赖本机预装的第三方CodeGraph、LSP 或 MCP 工具。
- 所有模型可见的平台系统提示词、仓库规则优先级、通用 Review 工作流、输出约束与工具说明必须存放在 `prompts/sys/<locale>/`；每个语言包固定包含合并平台边界与仓库规则策略的 `review-policy.md`、合并通用工作流与输出契约的 `review-workflow.md` 和结构化工具说明 `tools.json`，避免跨文件重复约束。组合根在启动时通过 `I18nPromptLoader` 完整校验并加载为不可变语言包。Review Runtime 按“平台边界、仓库规则策略、通用工作流、输出契约、Agent 专属策略”的固定顺序组成系统指令；适用的冻结仓库规则由 Context Builder 完整封装在首次用户输入中，不进入系统指令。设置页面只能覆盖 `prompts/<agent_id>/<locale>.md` 对应的 Agent 专属策略，不能覆盖通用系统层。Review 运行时只按任务 `prompt_locale` 读取已加载语言包，未知语言回退至配置的默认语言；新增语言不得要求在模型 Runtime 中拼接或硬编码自然语言提示词。
- 模型 Provider 配置由本机 Web Settings API 在运行期写入 Secret Store；API Key 是只写字段，不进入普通配置、数据库、日志、事件或 API 响应。
- 后端运行日志统一写入项目根目录 `logs/` 并由 `.gitignore` 排除。统一后端进程按职责拆分：HTTP、Uvicorn 和 API 应用日志写入 `logs/api.log`，Worker 调度和模型 Runtime 诊断写入 `logs/worker.log`；Supervisor 使用独立的 `logs/supervisor.log`。拆分由 logger 命名空间和独立 Handler 完成，不得依赖消息正文分类；各日志独立限量轮转，互不传播和重复写入。
- 运行日志和日志级别变更使用结构化字段。日志级别只使用 `debug`、`info`、`warning`、`error`；默认级别和当前级别必须明确。未处理异常记录异常堆栈和最小必要的任务或请求标识，不得记录密钥等敏感信息。当前级别通过稳定的 Settings HTTP/JSON 契约读取和更新，持久化到项目 `data/` 目录；API、Worker 和 Supervisor 必须无需重启即可采用新级别。前端不得直接读写日志文件或数据目录。
- 完整的模型可见输入、provider raw response 和工具交互仅允许在 Review 终态转录持久化时写入项目根目录 `logs/model.log`，不得在流式事件路径逐 delta 写日志。写入前必须执行与 Transcript 相同的凭证脱敏；当前文件和 gzip 备份都必须使用 owner-only `0600` 权限。`model.log` 单文件上限为 10 MiB，最多保留当前文件和一个压缩备份。普通 API、Worker 和 Supervisor 日志仍不得包含完整 Prompt、模型原始输出或源码正文。
- 后端异步运行模型基于 `asyncio`。

### 2.2 前端

- React、TypeScript 严格模式和 Vite。
- TanStack Query 管理服务端状态，React Router 管理路由。
- Vitest 负责单元与组件测试，Playwright 负责端到端测试。
- 依赖和命令统一通过 `pnpm` 管理。
- Monaco Diff Editor 或同等成熟组件负责差异展示，不自行实现通用 diff 算法和编辑器内核。

### 2.3 通信与运行

- 前后端只通过稳定的 HTTP/JSON 和 SSE 契约通信；前端不得直接访问仓库、数据库、Artifact Store 或模型运行时。
- HTTP 用于命令和查询，SSE 用于可恢复的单向事件推送，并支持 `Last-Event-ID`。
- API 和 Worker 运行于同一后端进程，共享内存事件总线和转录存储；前端进程独立启动。后端进程必须能够独立启动，不得依赖进程内共享状态或隐式启动顺序。
- 运行中的执行转录由后端进程保留在该任务的进程内内存中；SSE 端点通过内存事件总线实时推送事件，无需数据库轮询。Review 到达终态后，后端进程才一次性把完整转录写入任务 Artifact 并清理内存副本。
- 当前阶段不实现可替代 Web 的 Review 业务 CLI；启动、进程管理和诊断命令不属于业务交互入口。架构仅保留未来 CLI 入站适配器的扩展能力。
- 本机无鉴权模式仅允许绑定 `127.0.0.1`；非回环地址必须配置明确的信任和访问边界。

## 3. 前后端分离

### 3.1 后端职责

后端负责领域规则、用例编排、权限与安全校验、Git 和文件系统访问、模型与 Agent 调度、持久化、任务恢复以及 API/SSE 契约。任何影响业务正确性或安全边界的判断都不得只存在于前端。

### 3.2 前端职责

前端负责用户流程编排、状态呈现、输入收集和交互反馈。页面组件只组织流程；业务状态、API 调用和缓存逻辑下沉到对应的 `features/*` 模块；`shared` 目录不得包含特定领域规则。

### 3.3 稳定契约

- API 请求、响应和模型输出必须在后端边界使用 Pydantic DTO 校验，不得直接序列化领域实体或 ORM 模型。
- JSON 字段、错误码、事件名称和状态值属于稳定契约。变更时必须考虑向后兼容、幂等、迁移和失败恢复。
- `/api/settings/model-gateways` 是本地模型网关集合契约，支持创建、列出、更新和删除；`PUT /api/settings/active-model-gateway` 原子切换当前网关。读取只返回网关 ID、名称、模型 ID、Base URL 和激活状态，API Key 永不通过读取契约返回。
- `GET/PUT /api/settings/repositories` 读取或更新最近 Review 仓库目录容量，字段为 `recent_repository_limit`，允许 1–20，默认 10。更新必须持久化并立即按当前 LRU 顺序裁剪溢出目录。
- `/api/repositories/browse` 只返回系统根目录、目录项和 Git 仓库标记；`/api/repositories/catalog` 返回全部可选分支以及分页 Commit 元数据。两者都不能返回文件正文。
- `GET /api/repositories/recent` 返回独立持久化的最近 Review 仓库目录、名称和最近使用时间，用于本机仓库快捷选择。目录按 LRU 维护，Review 创建成功时提升对应目录，并按持久化设置保留 1–20 个，默认 10 个；Review tombstone 不得删除或降级目录项。该列表不读取文件系统；选中路径后仍必须通过既有 inspect/catalog 契约重新执行允许根目录、Git 仓库和身份校验。
- `GET /api/reviews/{task_id}/findings/{finding_id}/source` 只返回该 Finding 所在文件在 Review 固定 base/head revision 的完整正文及高亮行范围；不得读取可变原始工作区，也不得用模型输出决定文件路径或 revision。
- `GET /api/reviews/{task_id}/process-report` 仅在 Review 到达终态后返回由完整脱敏转录确定性聚合的执行指标，包括 LLM 调用与 token、Agent、工具 call/result、时长和 Finding 数；旧转录或失败执行缺少供应商 usage 时必须通过 `usage_is_complete=false` 显式表达，不得估算为精确用量。
- `GET /api/reviews` 返回未删除的持久化 Review 工作空间；`DELETE /api/reviews/{task_id}` 使用软删除语义，活动任务必须同时持久化取消意图。
- SSE 事件必须来自持久化 outbox；部分成功、超时和失败必须显式表达，不能伪装为完整成功。
- 前端类型应从经过验证的契约生成或集中维护，不得通过 `any`、非空断言或未校验的类型转换绕过边界。

稳定契约统一使用以下命名：HTTP 路径使用小写、复数资源名和 `kebab-case`，普通 CRUD 不使用动词路径；JSON 字段使用 `snake_case`，枚举和状态值使用小写 `snake_case`；事件名称使用已发生的领域事实并显式版本化，载荷遵循 JSON 命名规则。未来 CLI 的命令和选项使用小写 `kebab-case` 并复用领域词汇，机器可读输出使用稳定、版本化的 JSON schema。

### 3.4 CLI 可扩展约束

当前产品交互入口是 Web，当前交付范围不包含用于创建 Review的 CLI，模型设计必须保证前后端分离，支持通过API调用或者CLI调用的方式完整替代Web入口

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
- `reviewer_catalog`：Reviewer、Prompt、模型策略、运行期多网关目录、激活网关和能力绑定的版本化目录。
- `instruction_policy`：规则文件的发现、解析、优先级和冻结。
- `findings`：Finding、Evidence、校验、去重、抑制和报告。
- `capabilities`：Skill、MCP、静态工具、沙箱和仓库信任策略。
- `governance`：审计、反馈、评测和规则建议，不直接改变正在运行的规则。

跨上下文协作必须使用明确的应用服务、领域事件或 Port。一个上下文不得导入另一个上下文的 `infrastructure` 实现、ORM 模型或内部可变状态。共享模块只允许放置稳定、无领域归属且被多个上下文实际复用的最小基础类型。

仓库审查规则按目标文件独立解析。从仓库根目录到目标文件所在目录的每一级，都以大小写不敏感方式同时发现 `AGENTS.md` 与 `REVIEW.md`，最后发现大小写不敏感的 `<target-file>.review.md`；同一目录出现仅大小写不同的同名规则文件属于歧义并必须拒绝。每个目标的规则链按通用到具体排列：更深目录高于上级目录，同一目录 `REVIEW.md` 高于 `AGENTS.md`，文件专属规则最高。结构化 exclude 为累积并集，不允许下级规则重新包含已排除路径。

多目标 Review 必须在后端分别保留每个目标的规则链，用于结构化 exclude、Snapshot 冻结、完整性与作用域校验，不得在这些确定性控制中丢失适用范围。Snapshot 只冻结最终 Review 目标实际引用的规则文件。Context Builder 在首次模型调用前校验规则路径、正文哈希、作用域和顺序，然后把所有适用规则确定性封装为 `repository_instructions`：每份规则正文只出现一次，`applies_to` 列出其精确适用的 `review_files` 路径，条目按从通用到具体稳定排序。无规则的目标不产生占位条目，无关目标的规则不得进入模型输入。模型不得看到内部规则链对象、优先级数字或内容哈希，也不提供加载规则的模型工具。仓库规则不能覆盖平台、安全、工具、Snapshot 范围或输出契约。

### 5.1 MVP 内置 Review 工具

MVP 为每个 Agent Run 提供 CodeLens 自身实现的模型可见只读证据工具：`explore`、`glob`、`grep`、`read_file`、`get_diff` 与 `read_revision`。这些工具的唯一数据源是该任务冻结后的 `ReviewSnapshot`。规则发现与装载是宿主 Context Builder 的职责，不作为工具暴露给模型。Context Builder 在首次模型调用前从 Snapshot 的不可变文件级变更元数据确定性构造完整 `review_files`；每项只包含规范化仓库相对路径、`added`、`modified`、`deleted` 或 `renamed` 类型、可选重命名前路径，以及允许产生 Finding 的新侧范围。文件和范围稳定排序，超过产品上限必须在模型调用前明确失败，不得静默截断。无法可靠表达变更类型的范围也必须在 Snapshot 构建边界失败，不能根据 hunk 正文猜测。全仓 Review 使用明确的空概念基线：最终存在的目标按 `added` 处理，其完整文本范围为新侧范围，overlay 删除的目标按 `deleted` 处理；二进制文件可以没有新侧文本范围。

工具驱动 Agent 的首次用户输入只序列化完整 `review_files` 和去重后的 `repository_instructions`。每条仓库规则只包含规范化相对路径、完整正文和精确 `applies_to` 目标列表；正文只注入一次，不得在系统指令或同一首包其他位置重复。任务持久化的 `prompt_locale` 由 Runtime 显式接收并用于选择系统语言包，不进入模型输入。Snapshot ID、hunk ID、内容哈希、摘录哈希、内部规则链标识和优先级数字仅由后端保留，用于隔离、完整性校验、Finding 定位、转录与 Artifact，不得序列化给模型。完整 diff 和上下文 excerpt 不预加载；Agent 已在首轮获得全部适用规则，再根据调查需要从可用的只读证据工具中自行选择。

除证据工具外，Review Runtime 还提供任务内有状态的 `comment` 与 `task_done` 工具。`comment` 可批量收集候选评论；`task_done` 只记录调查完成声明及已检查变更文件数。它们不读写持久化数据、不执行文件写入、Shell 或网络操作，也不访问原始工作区。模型仅可提交路径、行范围与评论内容；适配器必须以冻结 Snapshot 重新解析范围，确认其完整位于唯一的新侧变更 hunk，并派生 hunk ID 与 excerpt hash。无法解析、越界或未变更位置的候选评论必须丢弃，不得进入最终报告。运行结束后，最终 FindingBatch 只能由已解析评论确定性生成，模型的最终文本和模型提供的 hunk ID、哈希均不得作为输出依据。

内置工具的模型可见自然语言描述、平台审查规则、输出约束和运行结束要求统一由启动时加载的 `prompts/sys/<locale>` 提供。工具名、JSON 字段名、路径、代码标识符与 Snapshot 返回结构属于稳定技术契约，不随本地化改变。

工具实现必须位于 `review.infrastructure` 或 `workspace.infrastructure`，并通过 Review 的 Runtime Port 接入。每次证据读取必须校验 Snapshot ID、规范化相对路径、Manifest 可见性和内容哈希；必须限制读取字节数、行数、搜索结果数与 Git 输出。工具调用不设置独立次数上限，而由可配置的模型回合数、单次工具输出上限和用户取消共同约束；Review 不设置总执行时限。必须记录总调用数用于诊断和成本治理。宿主构造 `review_files`、`repository_instructions` 和 `get_change_map` 类上下文不产生工具转录或工具计数，过程报告中的调用次数为零。所有工具不得写入文件、执行任意 Shell、访问网络、访问原始工作区或读取 Snapshot 之外的路径。

MVP 不实现 Serena、CodeGraph、codebase-memory、第三方 MCP、Skills、LSP 或通用沙箱工具。未来接入这些能力时，必须经 `capabilities` 上下文的版本化 Capability Profile 与受控 Adapter 暴露稳定工具契约，不能将供应商工具、路径或权限直接泄漏给 Agent。

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

- CodeLens 对源仓库严格只读。每个任务在应用数据目录创建自己拥有的 detached worktree，并在其中冻结 `ReviewSnapshot`；任务 worktree 只用于构建和读取不可变审查输入。
- Agent、模型和沙箱不得访问用户原始工作区，不得写入任务 worktree，也不得修改源分支、index、tag 或任何其他 Git 引用。
- Finding 只包含问题位置、证据、影响、解释、复现信息和建议；模型输出、HTTP 契约与前端均不得承载可应用的代码变更。
- Agent 的内置代码工具只能读取 Snapshot Manifest 中的 target/context 文件，并在每次读取前重新验证内容哈希；Git 旧版本读取只能使用 Snapshot 固定的 base/head OID，不能接受模型提供的任意 ref。
- 默认本地部署不设置仓库根目录白名单，目录浏览从 POSIX `/` 或 Windows 现有盘符开始；因此操作系统用户可读的全部目录构成本地信任边界。该模式只能绑定回环地址。显式传入允许根目录时，后端仍必须在每次仓库访问时执行真实路径边界校验。
- 目录浏览只能列出当前启动用户具备读取和进入权限的目录及必要的 Git 仓库标记，无权限或无法解析的目录项必须逐项跳过且不得阻断同级列表，并设置数量上限；分支和 Commit 列表由后端通过受限 Git 参数数组读取，前端不得接收任意 Git 参数或自由文本 ref。
- 仓库内容、规则文件、Skill、MCP 输出和模型输出全部视为不可信数据，不能扩大 Agent、进程或工具权限。
- Secret（包括 API Key、Authorization、Cookie 和会话凭证）不得进入数据库、日志、事件、Artifact、Prompt、RunContext 或错误响应。为本机操作者提供可审计执行过程时，系统可以将已脱敏的 Prompt、模型可见输出、工具调用和 Skill 生命周期写入任务专属 Artifact，并仅通过稳定的 HTTP/JSON 与可恢复 SSE 契约读取；Transcript 对内容不做截断，折叠仅是前端呈现能力。经本地操作者明确启用的 `logs/model.log` 是唯一允许记录完整已脱敏模型交换的日志，不得包含凭证，也不得把正文复制到其他运行日志。任务级存储配额和删除策略负责 Artifact 保留边界；模型日志按固定大小和数量轮转，不得通过静默截断单条记录控制容量。
- 本地 Web 写入的多网关 Secret Catalog 保存在 data directory 的 `secrets/model-gateways.json`；目录和文件分别使用 owner-only `0700`/`0600` 权限并原子替换。API 与 Worker 只通过 Secret Store Port 共享，Worker 在实际模型调用时读取当前激活网关，进程启动不得依赖网关已配置。Secret Store 默认位于源码仓库之外。
- Review 工作空间删除使用数据库 tombstone，不级联删除 Finding、事件、快照或审计数据；读取单个已删除 Review 与列表查询都不得重新暴露 tombstone 记录。
- 最近 Review 仓库目录拥有独立于 Review 工作空间 tombstone 的持久化生命周期；删除 Review 不得改变该目录，目录只在新仓库使用或容量设置更新导致 LRU 超出当前配置时淘汰。
- 非 HTTPS 的远程模型 Base URL 会明文传输凭证和 Review 内容，界面必须显式警告；是否使用该受信任网络边界由本机操作者决定。
- 数据库结构只能通过 Alembic migration 演进；持久化任务和事件必须支持幂等、重启恢复及部分失败。

## 7. 架构治理

架构设计或调整完成前至少确认：

- 变更位于正确的限界上下文和分层，没有出现反向依赖或跨上下文基础设施访问。
- 外部能力通过 Port/Adapter 接入，供应商类型没有进入领域或应用契约。
- 前后端只通过经过校验的 HTTP/SSE 契约通信，业务规则没有只存在于 UI。
- 新增 CLI 或其他入站适配器时，只复用稳定契约或 Application 用例，没有复制业务流程或绕过安全边界。
- API、事件、数据库和持久化任务变更已覆盖兼容、迁移、幂等及恢复。
- 数据所有权、Secret、仓库访问和执行隔离没有突破既有安全与信任边界。
- 进程职责、启动关系、事件传递和持久化策略与既定运行拓扑一致。
- 架构调整已同步更新本文档；实施与验证要求交由 `AGENTS.md` 维护。
