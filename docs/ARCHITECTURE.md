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
- OpenAI Agents SDK、Git、文件系统和 Secret Store 作为外部能力，通过 Port/Adapter 接入。`capabilities` 上下文以供应商无关的版本化 Tool Contract、Capability Profile、Skill Policy 和冻结执行规格约束模型可见能力；当前代码检索仍只由 CodeLens 内置、只读的 Snapshot 工具提供，不依赖本机预装的第三方 CodeGraph、LSP 或 MCP 工具。MCP Binding 与文本 Skill 已有声明式冻结契约，但当前没有 live MCP Adapter、MCP 进程或网络连接，也不支持可执行 Skill。
- Git 是内置 Snapshot 工具唯一需要的外部可执行文件；macOS、Linux 和 Windows 启动入口都必须在服务就绪前验证 Git 可执行文件及版本响应。`find_files`、`grep` 和文件读取不得依赖操作系统提供的 `find`、`grep`、`glob` 或 Shell。
- 所有静态的平台系统提示词、仓库规则优先级、通用 Review 工作流、输出约束与工具说明必须存放在 `prompts/sys/<locale>/`；每个语言包固定包含合并平台边界与仓库规则策略的 `review-policy.md`、合并通用工作流与输出契约的 `review-workflow.md`、运行时纠偏文本 `review-feedback.md`、工具循环告警模板 `tool-loop-warning.md` 和结构化工具说明 `tools.json`，避免跨文件重复约束。组合根在启动时通过 `I18nPromptLoader` 完整校验并加载为不可变语言包。Context Builder 产生包含完整 `review_files` 与冻结 `repository_instructions` 的确定性内部信封；Review Runtime 在供应商边界拆分该信封，并按“平台边界与仓库规则策略、可信 `repository_instructions`、通用工作流与输出契约、Agent 专属策略”的固定顺序组成系统指令，首次用户输入只保留 `review_files`。宿主拒绝未落在实际 diff 行的评论后，必须在对应 `comment` 工具结果中返回稳定的 `reason_code` 和来自 `review-feedback.md` 的本地化纠偏文本，不得把纠偏文本混入工具定义或在工具实现中硬编码。设置页面只能覆盖 `prompts/<agent_id>/<locale>.md` 对应的 Agent 专属策略，不能覆盖通用系统层或仓库规则。Review 运行时只按任务 `prompt_locale` 读取已加载语言包，未知语言回退至配置的默认语言；新增语言不得要求在模型 Runtime 中拼接或硬编码自然语言提示词。
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
- Web 界面仅面向 PC 桌面浏览器，最小支持视口宽度为 1280px；移动端、窄屏和触摸端不属于产品支持范围，前端不得为其引入响应式断点、专用布局、导航或交互分支。

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
- JSON 字段、错误码、事件名称和状态值属于稳定契约。v2 发布后变更必须考虑兼容、幂等、迁移和失败恢复；本次 v2 硬切不兼容任何 v1 数据或契约。
- `/api/settings/model-gateways` 是本地模型网关集合契约，支持创建、列出、更新和删除；`PUT /api/settings/active-model-gateway` 原子切换当前网关。读取只返回网关 ID、名称、模型 ID、Base URL、激活状态和非 Secret 的模型与执行策略，API Key 永不通过读取契约返回。每个网关独立持有 Agent 总超时、最大模型回合、最大工具调用、相同参数与结果熔断阈值和单次工具超时；默认值依次为 1800 秒、100、300、3 和 30 秒，允许范围依次为 60–7200、1–500、1–5000、2–20 和 1–300。新 Agent Run 在实际调用时读取当前激活网关的完整策略，无需重启。
- `GET/PUT /api/settings/repositories` 读取或更新最近 Review 仓库目录容量，字段为 `recent_repository_limit`，允许 1–20，默认 10。更新必须持久化并立即按当前 LRU 顺序裁剪溢出目录。
- `GET/PUT /api/settings/instruction-files` 读取或更新仓库规则文件的行数上限，字段为 `root_max_lines` 和 `nested_max_lines`，均允许 1–10000，默认分别为 500 和 200，且根目录上限不得低于嵌套目录上限。更新必须原子持久化，并在后续规则解析时无需重启即可生效。
- `GET/PUT /api/settings/file-exclusions` 读取或更新统一文件排除策略，字段为默认开启的 `exclude_binary`、后缀列表 `ignored_suffixes` 和仓库相对 POSIX 路径正则列表 `ignored_path_regexes`。PUT 支持部分更新并必须原子持久化；后缀和正则在保存时规范化、去重和校验。创建 Review 时把当前策略的 canonical JSON 与哈希冻结到任务，已创建任务不得因后续设置变化而改变文件范围。
- `GET/PUT /api/settings/review-completion` 读取或更新未完整 Review 的最大打回次数，字段为 `max_incomplete_review_retries`，允许 0–20，默认 3。更新必须原子持久化；每个 Agent Run 开始时读取当前值，无需重启即可生效，已开始的 Run 保持其启动时策略不变。
- `GET/PUT /api/settings/tool-limits` 读取或更新工具级资源限制，字段包括 `max_results`（find_files/grep/get_diff 单页结果上限，默认 200）、`max_read_bytes`（read_file/get_diff 输出上限，默认 65536）、`max_scan_bytes`（grep 扫描上限，默认 1048576）、`max_source_bytes`（源文件大小上限，默认 1048576）、`max_lines`（read_file 行数上限，默认 500）、`max_path_chars`（路径长度上限，默认 1024）、`max_pattern_chars`（模式长度上限，默认 512）、`regex_timeout_seconds`（正则超时，默认 30.0）、`comment_batch_size`（评论批次上限，默认 20）、`short_text_max`（短文本上限，默认 240）、`long_text_max`（长文本上限，默认 8000）、`task_summary_max`（任务摘要上限，默认 8000）。PUT 支持部分更新，省略字段保留当前值。更新必须原子持久化；每个 Agent Run 开始时读取当前值，无需重启即可生效。
- `POST /api/settings/reset-all` 将所有用户可配置设置重置为产品默认值，包括工具限制、执行限制、规则文件限制、Review 完成重试次数、最近仓库数量和日志级别。不影响网关身份/凭证、Reviewer prompt 自定义和触发器插件配置。返回重置后的所有设置快照。
- `/api/repositories/browse` 只返回系统根目录、目录项和 Git 仓库标记；`/api/repositories/catalog` 返回全部可选分支，并按请求中的目标分支返回该分支 tip 之前的分页 Commit 元数据。目标分支必须来自后端枚举的分支，Commit 候选不得混入目标分支不可达的提交或目标 tip 本身。两者都不能返回文件正文。
- `GET /api/repositories/recent` 返回独立持久化的最近 Review 仓库目录、名称和最近使用时间，用于本机仓库快捷选择；`DELETE /api/repositories/recent` 按请求中的 `repository_path` 幂等删除单个快捷记录，不得删除或修改关联 Review 工作空间。目录按 LRU 维护，Review 创建成功时提升对应目录，并按持久化设置保留 1–20 个，默认 10 个；Review tombstone 不得删除或降级目录项。该列表不读取文件系统；选中路径后仍必须通过既有 inspect/catalog 契约重新执行允许根目录、Git 仓库和身份校验。
- `GET /api/reviews/{task_id}/findings/{finding_id}/source` 同时返回该 Finding 所在文件在 Review 固定 base/head revision 的可用完整正文、评论所属 old/new 侧及高亮行范围；新增或删除文件允许一侧为空。它不得读取可变原始工作区，也不得用模型输出决定文件路径或 revision。Review 页面只提供一种等宽并排对比方式：base/old 位于左侧，target/new 位于右侧；纯删除使用红色，纯新增使用绿色，替换修改使用高对比蓝色。old 评论完整内嵌在左侧对应变更行后，new 评论完整内嵌在右侧对应变更行后，不显示重复的侧别提示。意见导航位于代码区上方；桌面全局导航默认折叠并在 hover 或键盘聚焦时展开，不能挤占代码横向空间。
- `GET /api/reviews/{task_id}/process-report` 仅在 Review 到达终态后返回由完整脱敏转录确定性聚合的执行指标，包括 LLM 调用与 token、Agent、工具 call/result、时长和 Finding 数；旧转录或失败执行缺少供应商 usage 时必须通过 `usage_is_complete=false` 显式表达，不得估算为精确用量。
- `GET /api/reviews` 返回未删除的持久化 Review 工作空间；`DELETE /api/reviews/{task_id}` 使用软删除语义，活动任务必须同时持久化取消意图。
- `POST /api/reviews` 只接受 v2 `reviewer_selection`，使用 `fixed`（带 1–32 个不可重复 v2 Reviewer 引用）或 `adaptive` 判别联合，并同时冻结可选的 profile ID/revision；`selected_agents` 和任何 v1 引用必须作为未知或非法输入拒绝。Review 读取响应稳定返回原始 `selection_request`、profile 来源、可空 `review_plan`、按 Planned/Completed/Failed/Omitted 重建的 Reviewer coverage 和 Verdict 计数（`verdict_summary` 含 accept/deny/merge 三态）；这些字段只能从持久化 Plan、checkpoint 和决策重建，SSE 不是真相来源。
- `GET /api/reviewer-catalog` 只返回公开且不可变的 v2 Reviewer 版本，包含维度、成本类别、Planner eligibility 和 Capability readiness；Planner 与 Verifier 不得出现在公开 Catalog，Catalog 中不存在 legacy Reviewer。
- `POST /api/reviews/{task_id}/retry` 仅接受失败且未删除的 Review，并从原任务已经冻结的请求输入创建具有新任务 ID、独立队列、检查点和事件流的 Review；原失败任务及其诊断保持不变，重试不得重新读取可变工作区来构造输入。
- `POST /api/reviews/{task_id}/export` 仅接受终态 Review，body 为 `{plugin_id}`，触发该插件的导出。返回 `ExportResult`（包含 `plugin_id`、`task_id`、`success`、`output_path`、`error`、`exported_at`）；非终态 Review 必须拒绝；插件未启用或不存在必须返回明确错误。导出是用户主动发起的后置操作，不修改 Review 工作空间、Finding 或事件。
- `/api/plugins` 是统一插件集合契约：`GET` 列出所有已安装插件，`GET /{plugin_id}` 返回显式的 API 兼容状态、配置修订和复制策略，`POST /install` 接收 `{git_url, ref?}` 并把外部 Git 插件安装到 `data/plugins/{plugin_id}/`，`DELETE /{plugin_id}` 卸载外部插件；`PUT /{plugin_id}/trigger/enable|disable|config` 和 `PUT /{plugin_id}/report/enable|disable|config|auto-export` 分别管理 Trigger 与 Report 能力。Manifest 必须显式声明主版本为 2 的 `plugin_api_version` 和兼容的 `min_codelens_version`，缺失版本或其他主版本必须拒绝；候选代码必须在激活或加载前再次通过兼容检查。配置写入前必须同时匹配 Manifest Schema 和 Core v2 Reviewer Policy 不变量，未声明的能力必须拒绝；本地 Git Hook 通过 `/trigger/install-hooks`、`/trigger/uninstall-hooks` 和 `/trigger/hook-status` 显式同步与查询。统一记录持久化到 `data/plugins.json`，写入必须原子化并串行保护读改写；插件更新时，候选代码与配置作为一个用户可见事务激活，任一失败恢复旧目录和旧记录。内置插件 ID `local` 为保留 ID 且不可卸载。外部入口代码通过受控 loader 加载，实现 v2 `TriggerSinkPort` 或 `ReportSinkPort`，卸载和重新安装必须使旧实例缓存失效。安装请求和普通运行日志不得记录可能携带凭证的完整 Git URL。
- SSE 事件必须来自持久化 outbox；部分成功、超时和失败必须显式表达，不能伪装为完整成功。多 Agent 稳定事件包括 `review.plan_created`、`agent_run.started`、`agent_run.completed`、`agent_run.failed`、`review.verdict_completed`、`review.completed`、`review.partial`、`review.failed`、`review.canceled` 和 `review.superseded`；载荷仅含有界身份、状态和计数，不含 Prompt、源码、工具参数/结果正文、Skill 文本、Secret 或供应商原始输出。
- 前端类型应从经过验证的契约生成或集中维护，不得通过 `any`、非空断言或未校验的类型转换绕过边界。

稳定契约统一使用以下命名：HTTP 路径使用小写、复数资源名和 `kebab-case`，普通 CRUD 不使用动词路径；JSON 字段使用 `snake_case`，枚举和状态值使用小写 `snake_case`；事件名称使用已发生的领域事实并显式版本化，载荷遵循 JSON 命名规则。未来 CLI 的命令和选项使用小写 `kebab-case` 并复用领域词汇，机器可读输出使用稳定、版本化的 JSON schema。

### 3.4 CLI 可扩展约束

当前产品交互入口是 Web，当前交付范围不包含用于创建 Review 的 CLI。系统设计必须保持前后端分离；未来 CLI 应复用稳定 API 或 Application 用例完整替代 Web 入口，不得复制业务流程或绕过安全边界。

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
- `review`：ReviewTask 生命周期、完成策略、Agent 运行和应用层编排；运行入口只接收已经解析并冻结的 `FrozenAgentExecutionSpec`，不自行选择工具或加载 Skill。
- `reviewer_catalog`：Reviewer、Prompt、模型策略、运行期多网关目录、激活网关和能力绑定的版本化目录。不可变 Reviewer 版本只保存 Capability Profile 与 Skill Policy 的字符串引用，不拥有或实例化 Capability 实现。
- `instruction_policy`：规则文件的发现、解析、优先级和冻结。
- `findings`：Finding、Evidence、校验、去重、抑制和报告。
- `capabilities`：拥有版本化 Tool Contract、Capability Profile、声明式 MCP Binding、文本 Skill Manifest/Policy、基于冻结宿主事实的 Skill 激活，以及 `FrozenAgentExecutionSpec` 的确定性解析与执行指纹。该上下文不依赖 OpenAI Agents SDK、FastAPI、SQLAlchemy 或 MCP SDK；具体模型工具适配仍由 `review.infrastructure` 完成。
- `plugin`：统一插件限界上下文，拥有 `PluginManifest`、`PluginRecord`、`PluginStorePort`、安装与加载 Port、Trigger/Report 能力状态、配置校验、事件分发和导出编排。一个安装单元可声明 Trigger、Report 或两种能力；内置保留插件 `local` 同时提供本地 Git Hook 触发和本地文件导出。外部插件安装到 `data/plugins/{plugin_id}/`，状态统一持久化到 `data/plugins.json`。插件只支持 API v2，公开 `TriggerReviewPolicy`、互斥的 Fixed/Adaptive 和 supersede 策略；缺少版本或主版本不是 2 的 Manifest 必须拒绝，不提供配置迁移或 v1 防腐层。`ReviewCreatorPort` 只把该值对象桥接到 review 上下文，插件不得导入 Review 持久化或运行时实现。用户从 Review Profile 复制到插件的只有 Reviewer Selection；插件拥有独立配置快照，Core 在配置外拥有可选 `PluginProfileSource`，来源信息不参与执行、指纹或插件 Schema。自动 Trigger 只负责解析已保存策略并提交持久任务；幂等键、`latest_snapshot` supersede、取消请求和 outbox 与创建事务共同由 review 上下文拥有，Planner 只能由后续 Worker 异步执行。Hook 安装必须保留用户已有钩子，向 CodeLens 与用户钩子分别回放 `pre-push` 输入，并在跨仓库同步或状态持久化失败时恢复同步前状态；HTTP 回调保持 fire-and-forget，不得阻塞 Git 操作。v2 Report 仅接收 `FindingExportEnvelopeV2` 2.0，其中包含原始 Selection、冻结 Plan 摘要和终态 Coverage；输入只来自 Published Findings，不能包含 Candidate、Cluster、被拒绝/未解析结果、Prompt、Transcript 或 Secret。多个自动导出插件按 Review 来源平台路由，单个插件失败必须返回结构化 `ExportResult` 并与其他插件及 Review 终态隔离。
- `governance`：审计、反馈、评测和规则建议，不直接改变正在运行的规则。

跨上下文协作必须使用明确的应用服务、领域事件或 Port。一个上下文不得导入另一个上下文的 `infrastructure` 实现、ORM 模型或内部可变状态。共享模块只允许放置稳定、无领域归属且被多个上下文实际复用的最小基础类型。

仓库审查规则按 Candidate 文件独立解析。从仓库根目录到 Candidate 文件所在目录的每一级，都以大小写不敏感方式同时发现 `AGENTS.md` 与 `REVIEW.md`，最后发现大小写不敏感的 `<target-file>.review.md`；同一目录出现仅大小写不同的同名规则文件属于歧义并必须拒绝。每份规则文件同时受固定字节上限和可配置行数上限约束；仓库根目录中的规则使用较宽松的根目录上限，其他目录和文件专属规则使用嵌套上限，超限必须在 Snapshot 冻结前明确拒绝。每个目标的规则链按通用到具体排列：更深目录高于上级目录，同一目录 `REVIEW.md` 高于 `AGENTS.md`，文件专属规则最高。结构化 exclude 为累积并集，不允许下级规则重新包含已排除路径。

文件排除由 `workspace` 上下文中的 `ReviewFileExclusionPolicy`、`ReviewFileScope` 和纯领域 `ReviewFileScopeResolver` 统一拥有。Resolver 同时处理 Candidate Review 路径和 Candidate Context 路径，输入为 Git Adapter 提供的 `.gitignore` facts、Repository Instruction exclude facts、用户后缀/路径正则和冻结 old/current 内容的二进制 facts；输出为唯一的 `review_paths`、`context_paths`、每路径全部排除原因及稳定 scope hash。基础设施不得在 Resolver 外再次实现独立过滤。二进制排除默认开启；modified/renamed 任一侧为二进制即排除，deleted 检查 base，added/untracked 检查 current。Instruction 控制文件必须独立捕获和校验，即使其自身不进入模型可见范围也不能因此停止加载规则。

创建 Review 时把当前 File Exclusion Settings 的 canonical JSON 与哈希冻结到任务。Worker 在任务自有冻结 worktree 上首次解析 Scope 后按任务幂等持久化；重启必须读取并验证已有 Scope，不得重新读取当前 Web 设置。Snapshot Manifest 直接持有 `ReviewFileScope`，ChangeIndex、Planner、Context Builder、Reviewer、完成门禁、Candidate 校验、Verifier、Finding 和 API 统计全部只消费该 Scope。Candidate 捕获和规则发现只是 Scope 的输入，不属于模型可见 Review 范围。全部 Candidate 被排除时任务跳过模型调用，以 0 Findings 和明确事件完成。

多目标 Review 必须在后端分别保留每个 Candidate 的规则链，用于产生 Instruction exclusion facts、Snapshot 冻结、完整性与作用域校验。Snapshot 只冻结最终 Review 路径实际引用的规则文件。Context Builder 在首次模型调用前校验规则路径、正文哈希、作用域和顺序，然后把有效 Review 路径适用的规则确定性封装为可信 `repository_instructions`：每份规则正文只出现一次，`applies_to` 只列出该规则的单一规范化作用域路径；`.` 表示仓库根目录，目录级 `AGENTS.md` 或 `REVIEW.md` 使用其生效目录，文件专属规则使用对应 Review 文件。作用域只在本次 `review_files` 集合内解释，不得把目录规则展开为逐文件列表。条目按从通用到具体稳定排序。无规则的目标不产生占位条目，无关目标的规则不得进入模型输入。模型不得看到内部规则链对象、优先级数字或内容哈希，也不提供加载规则的模型工具。`repository_instructions` 是受信任的 Review 配置并进入系统指令，但仍受更高优先级的平台、安全、工具、Snapshot 范围和输出契约约束。

### 5.1 Capability 管控的内置 Review 工具

每个不可变 Reviewer/内部 Agent 版本静态绑定一个 Capability Profile 和 Skill Policy 引用。任务创建边界解析版本化 Prompt、Capability Profile、声明式 Skill 激活和执行限制，生成包含 Agent 身份、Prompt 内容哈希、完整工具契约、MCP Schema 哈希、Skill 内容哈希及所有执行限制的 `FrozenAgentExecutionSpec`；Prompt 与 Skill 正文只进入哈希映射的受限 Artifact，数据库仅保存安全元数据和 Artifact 身份。Worker 重启后必须从这些 Artifact 重建规格并校验内容哈希和执行指纹，不得从当前 Catalog、Prompt 设置或 Capability 配置重新解析。该规格的指纹是确定性的；Runtime 在供应商调用前重新验证 Prompt、Skill 内容哈希和执行指纹，不能根据模型输出、Planner、插件或运行期发现改变工具集合。

Fixed 选择由宿主直接编译为不可变 Review Plan，绝不调用 Planner；Adaptive 选择只允许 `review-planner:v2` 通过一次 `submit_review_plan:v2` 提交完整的 Catalog 决策。Planner 只能选择冻结 Catalog 中 Ready 的公开 v2 Reviewer 并提供受限原因代码和 Snapshot 内关注路径，不能提交工具、能力、Finding、Prompt 或自由 DAG；Reviewer 和 Verifier 的节点及依赖全部由宿主编译。General 与单 Specialist 不包含 Verifier，多 Specialist 必须预建批量 Verifier。计划在任何 Reviewer 扇出前持久化；Plan 哈希覆盖选择、节点、Planner 指引与能力降级，任务 planning context 另行哈希覆盖解析后限制。可选 MCP/Skill 不可用只形成降级元数据，不改变任务完成状态。

多 Agent 执行的就绪判断、失败归约和重启恢复只能读取持久化 Plan 与 AgentRun checkpoint，不能以进程内 `gather` 返回值作为阶段事实。Reviewer 节点相互隔离：至少一个成功时允许 Verifier 在全部 Reviewer 终态后继续，任一 Reviewer 失败或超时设置不可被后续成功阶段清除的 sticky partial 标记；全部 Reviewer 失败时跳过未运行的下游节点并使任务失败。General 与 Fixed 单 Reviewer 失败直接使任务失败。取消必须传播到全部非终态节点，已经持久化的模型输出在普通重启后保留并从验证边界继续。Worker 以全局 Agent、模型和工具信号量、每任务信号量及为并发任务保留容量的公平上限共同约束模型调用，单个 Deep Review 不得占满可并行 Worker 的全部 Agent 槽位。

Comment v2 Reviewer 输出首先形成与 task、AgentRun、Snapshot、Reviewer 和证据哈希绑定的 Candidate 审计记录，不能直接成为公开 Finding。宿主必须重新校验规范路径、侧别、变更 hunk、摘录哈希、维度和证据身份，再按位置、类别、标题、影响和证据的规范化键确定性聚类，生成携带 canonical 字段的 `FindingCluster`。多 Specialist 的 Verifier 只处理当前任务已有 Cluster；它必须通过 `verdict:v2`（accept/deny）或 `merge:v2` 对每个 Cluster 恰好提交一次决策，并通过 `finalize_verdicts:v2` 校验全部 Cluster 被覆盖且无重复。批量 accept/deny 分别处理每个 Cluster，只有 merge 能把一个或多个 Cluster 物化为单个 Finding，单 Cluster merge 也合法。accept 使用 Cluster canonical 字段；merge 使用模型提交的完整 Comment 字段和位置，允许覆盖参与 Candidate 的内容、类别、维度、证据强度和严重级别，且不设置 Candidate 严重级别上限、证据强度阈值、影响范围门禁或内容相似度门禁。宿主只校验 Cluster 引用、Review Scope、冻结位置解析、枚举、资源限制和幂等性，不判断意见的证据强弱或影响范围，并派生 Finding ID、指纹、行号、excerpt hash 及来源关系。Cluster、Candidate 与 Verdict 决策必须作为审计状态持久化，Verdict 决策、Cluster 关系、Finding 和节点成功在同一事务提交。General 与 Fixed 单 Reviewer 不运行 Verifier；其所有通过宿主结构和位置校验的 Cluster 均生成 accept 决策，不按 evidence_strength 或影响范围过滤，由用户自行判断意见价值。

Verifier 在多 Specialist 场景下运行，并且每个任务至多运行一个 `review-verifier:v2`（prompt key 为 `review-verdict`）batch 节点；输入覆盖全部 Cluster 以及完成 merge 所需的 Candidate 评论和位置投影。`verdict:v2` 只包含 `cluster_ids` 与 `action=accept|deny`；`merge:v2` 包含 `cluster_ids`、`path`、`side`、`existing_code`、`title`、`content`、`recommendation`、`category`、`severity`、`primary_dimension` 和 `evidence_strength`，全部必填且拒绝额外字段。Verifier 应呈现所有有效且非重复的 Reviewer 意见，不得仅因 evidence_strength 较弱或影响范围不确定而 deny；deny 仅用于结构无效或已由其他决策完整表达的 Cluster。只有 accept 和 merge 可生成最终 Finding；deny 保持抑制。v2 Finding 不包含数值 confidence，并保留分类轴、证据强度、可复现性和来源 Reviewer 引用。最终 Finding 插入、Verdict 状态与发布事件必须在一个幂等事务中提交，并继续依赖 `(task_id, fingerprint)` 唯一效应保证重放不会重复发布。Verifier 失败时未决 Cluster 保留审计状态，任务必须保留 partial 状态。

模型可见工具由冻结 Profile 按角色精确选择：

| Agent 角色 | 证据工具 | 输出与控制工具 |
| --- | --- | --- |
| `review-planner:v2` | `find_files:v2`、`grep:v2`、`read_file:v2`、`get_diff:v2` | `submit_review_plan:v2`、`finalize_plan:v2` |
| v2 Reviewer | `find_files:v2`、`grep:v2`、`read_file:v2`、`get_diff:v2` | `comment:v2`、`task_done:v2` |
| `review-verifier:v2` | `read_file:v2`、`get_diff:v2` | `verdict:v2`、`merge:v2`、`finalize_verdicts:v2` |

Profile 只允许只读工具契约，明确禁止 Shell、文件写入、任意 Git、网络、`load_skill` 和动态工具发现。当前内置 Profile 的 MCP 工具集合为空，内置 Skill Policy 也不激活任何 Skill；现有 MCP 与 Skill 模块只提供未来 Adapter 可消费的不可变声明、Schema/内容哈希和安全校验。Skill 仅是低优先级、不可信的文本指令，不能注册工具、提升 Profile 权限、启动进程或执行脚本。

每个 Agent Run 可由其 v2 Profile 选择 CodeLens 自身实现的模型可见只读证据工具：`find_files`、`grep`、`read_file` 与 `get_diff`。工具参数默认由严格 schema 要求模型显式提供；`read_file` 的 `start_line` 与 `end_line` 是唯一例外，两者可以同时省略以执行有界全文读取，也只能同时提供。所有模型可见路径都是 Git 风格的仓库相对路径，在包括 Windows 的所有操作系统上统一使用 `/`，不包含盘符或宿主 worktree 绝对路径；`find_files` 的 `path` 使用空字符串表示仓库根目录，`pattern` 使用路径段感知的 glob，其中 `*` 匹配单个路径段、`**` 匹配任意层级。`find_files` 不分页且最多返回 200 个稳定排序的路径，存在更多结果时设置 `truncated=true`，模型必须缩小 `path` 或细化 `pattern`。`grep` 必须显式接收搜索文件或目录 `path` 和相对于该范围的路径段感知 `file_pattern`；它不分页，在过滤后的路径范围内最多扫描 1 MiB、返回 200 个匹配，任一上限导致 `truncated=true` 时，模型必须缩小内容 `pattern`、`path` 或细化 `file_pattern`。单个 Snapshot 源文件的工具读取上限为 1 MiB，超过上限必须在载入正文前明确拒绝。`read_file` 的 `version` 必须是 `current`、`base` 或 `head`，分别读取冻结 Snapshot 当前内容或固定 Git revision；无行范围时从第一行读取至文件结尾或 500 行、64 KiB 上限，超限必须设置 `truncated=true`。`get_diff:v2` 比较固定 base 与经过 Manifest 哈希验证的冻结 current 内容，因此会包含已捕获的 workspace overlay；`path` 精确命中 Review 文件时读取该文件，否则作为目录前缀递归批量读取，空路径表示 Review Scope 根目录。批量结果按 path 稳定排序，受 `max_results` 与 `max_read_bytes` 限制并用 opaque cursor 续读；只有本页完整返回的文件才计入已读覆盖，截断的 diff 结果不计入。所有证据工具的唯一数据源是该任务冻结后的 `ReviewSnapshot`，排除文件不能通过精确路径或目录前缀重新出现。规则发现与装载是宿主 Context Builder 的职责，不作为工具暴露给模型。Context Builder 在首次模型调用前从 Snapshot 的不可变文件级变更元数据确定性构造完整 `review_files`；每项只包含规范化仓库相对路径、`added`、`modified`、`deleted` 或 `renamed` 类型、可选重命名前路径，以及允许产生 Finding 的 `old_ranges` 与 `new_ranges`。文件和范围稳定排序，超过产品上限必须在模型调用前明确失败，不得静默截断。无法可靠表达变更类型的范围也必须在 Snapshot 构建边界失败，不能根据 hunk 正文猜测。全仓 Review 使用明确的空概念基线：最终存在的目标按 `added` 处理，其完整文本范围为新侧范围，overlay 删除的目标按 `deleted` 处理；二进制文件在统一 Scope 冻结前排除，不进入 `review_files`。

Context Builder 的内部 Runtime 信封只序列化完整 `review_files`、去重后的 `repository_instructions` 和宿主按角色生成的有界 `role_context`。Review Runtime 必须在供应商调用前确定性拆分信封：首次用户输入包含 `review_files` 与允许模型读取的角色上下文；`repository_instructions` 以规范 JSON 作为独立系统指令段，位于平台策略之后、通用工作流和 Agent 专属策略之前。以下划线 `_host_` 开头的运行身份元数据只供宿主构造工具结果与校验约束，必须在模型输入和完整模型请求转录形成前移除。每条仓库规则只包含规范化相对路径、完整正文和紧凑的 `applies_to` 作用域列表；正文只注入一次，不得在用户输入或其他系统指令段重复。任务持久化的 `prompt_locale` 由 Runtime 显式接收并用于选择系统语言包，不进入模型输入。Snapshot ID、AgentRun ID、hunk ID、内容哈希、摘录哈希、内部规则链标识和优先级数字仅由后端保留，用于隔离、完整性校验、Finding 定位、转录与 Artifact，不得序列化给模型。完整 diff 和上下文 excerpt 不预加载；Agent 已通过系统指令获得全部适用规则，再根据调查需要从可用的只读证据工具中自行选择。

除证据工具外，Review Runtime 还提供任务内有状态的 `comment:v2` 与 `task_done:v2`。`comment` 可批量收集候选评论；每项语义校验相互独立，响应以从零开始的 `rejected_comments[].index` 和稳定原因标识拒绝项，并保留同批次已接受项。`task_done` 尝试结束整个 Agent Run。它们不读写持久化数据、不执行文件写入、Shell 或网络操作，也不访问原始工作区。Runtime 自动记录本次 Run 中模型成功调用 `read_file` 的 Review 文件，以及由模型可见 `get_diff` 完整返回的 Review 文件；宿主为评论定位执行的内部 diff 或全文读取不构成已读覆盖。

`task_done` 不接受模型自报的文件计数，并以 `ReviewFileScope.review_paths` 为完成基准。尚未读过全部文件时必须拒绝，并在 `missing_review_files` 中稳定排序返回全部未读文件，不分片、不截断；模型可据此用 `read_file` 或文件/目录 `get_diff` 补充调查并再次调用 `task_done`。覆盖完整后的成功结果不重复返回已读文件列表。未调用一次被接受的 `task_done` 就结束的 Run 必须失败。Runtime 必须通过自定义 `tool_use_behavior` 只在宿主接受 `task_done` 后立即结束 SDK Agent Loop，被拒绝的调用仍把结果送回模型继续调查。拒绝次数超过当前 Run 启动时读取的 `max_incomplete_review_retries` 后，宿主接受完成并保留当前已验证 Finding，同时在 Agent checkpoint 持久化不完整覆盖状态；最终聚合中全部 Agent 覆盖完整的任务进入 `completed`，任一 Agent 强制完成的任务进入 `partial`，未获得一次有效完成声明或执行异常的任务进入 `failed`。`review_coverage_incomplete` 告警必须精确列出未完成文件，多 Agent 的告警文件必须合并展示，不得只保留最后一个 Agent 的列表。

模型必须为每条评论显式提交路径、`old` 或 `new` 侧、原样代码摘录与评论内容；`old` 侧从固定 base 正文及删除行解析，`new` 侧从哈希验证后的 current 正文及新增行解析。适配器必须以冻结 Snapshot 重新解析范围，确认其完整位于所选侧唯一的 changed hunk，并派生 side、hunk ID、excerpt hash 和整文件删除标记。无法解析、越界、侧别不符、重复或位于未变更位置的候选评论必须丢弃，不得进入最终报告；宿主必须尽力保留同批次的其余有效 Finding，并通过脱敏转录在 Web 提示丢弃计数，不能因单条候选校验失败而使整个 Review 失败。只有无法解析整体输出信封、无法确定候选边界或 Snapshot 完整性失效时才能使校验阶段失败。运行结束后，最终 FindingBatch 只能由已解析评论确定性生成，模型的最终文本和模型提供的 hunk ID、哈希均不得作为输出依据。

内置工具的模型可见自然语言描述、参数语义与边界、平台审查规则、输出约束和运行结束要求统一由启动时加载的 `prompts/sys/<locale>` 提供；工具参数必须在 JSON schema 中表达可机器校验的类型、枚举、长度和数量边界，描述与 schema 不得声明互相矛盾的默认值或可选性。除 `read_file` 为允许真正省略两个可选行字段而使用非严格 provider schema 外，其余工具保持严格 JSON schema；该例外仍必须经过生成的 Pydantic 参数适配器和本地未知字段拒绝，不得放宽路径、版本、行号配对或额外参数校验。工具名、JSON 字段名、路径、代码标识符与 Snapshot 返回结构属于稳定技术契约，不随本地化改变。

工具实现必须位于 `review.infrastructure` 或 `workspace.infrastructure`，并通过 Review 的 Runtime Port 接入。`CapabilityToolAssembler` 只从冻结 Profile 的有序 Tool Contract allowlist 选择宿主已注册的实现；未知名称或版本必须在模型调用前失败。每次证据读取必须校验 Snapshot ID、规范化相对路径、Manifest 可见性和内容哈希；返回内容必须直接来自本次已验证的字节，不能在校验后再次从可变工作树读取。必须限制读取字节数、行数、搜索结果数与 Git 输出；模型提供的正则表达式必须在可终止的隔离执行单元中运行并设置时限。一个 Agent Run 的全部已选择工具共享同一个调用次数、单次超时和相同参数/结果无进展熔断限制，任何内置或未来 MCP Adapter 都不得绕过该共享 Limiter。Review 不设置跨 Agent 的任务总执行时限。必须记录总调用数用于诊断和成本治理。宿主构造 `review_files`、`repository_instructions` 和 `get_change_map` 类上下文不产生工具转录或工具计数，过程报告中的调用次数为零。所有当前已注册工具不得写入文件、执行任意 Shell、访问网络、访问原始工作区或读取 Snapshot 之外的路径。

当前不提供 Serena、CodeGraph、codebase-memory、第三方 MCP、LSP 或通用沙箱的 live Adapter，也不执行 Skill 脚本。未来接入这些能力时，必须把外部实现映射到 `capabilities` 上下文预先定义的稳定 Tool Contract，并经过版本、Schema Hash、Snapshot 数据范围、只读副作用、数据外发和共享资源限制校验；不得把供应商工具列表、路径、权限或动态 Schema 直接泄漏给 Agent。

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
- 例外：`plugin` 上下文的导出能力在用户明确触发（手动或自动导出）时，可向被 Review 源仓库根目录下的 `CodeLensReview/` 目录写入带 UTC 时间戳且不覆盖历史结果的导出产物。该写入是用户主动发起的后置操作，不属于 Review 只读流程；写入路径必须是源仓库内、与代码目录隔离的固定子目录，不得修改任何代码文件、Git 引用或任务 worktree。外部插件实现 `ReportSinkPort` 时必须遵守此边界，不得越过配置的导出目录写入其他位置。内置 `local` 插件使用原子写入（`tempfile` + `os.replace`），并确保仓库根 `.gitignore` 覆盖配置的导出目录；这是该插件唯一可在导出目录外执行的仓库文件写入，且不得跟随 `.gitignore` 符号链接。
- Finding 只包含问题位置、证据、影响、解释、复现信息和建议；模型输出、HTTP 契约与前端均不得承载可应用的代码变更。
- Agent 的内置代码工具只能读取 Snapshot Manifest 中的 target/context 文件，并在每次读取前重新验证内容哈希；Git 旧版本读取只能使用 Snapshot 固定的 base/head OID，不能接受模型提供的任意 ref。
- Agent 只能调用 `FrozenAgentExecutionSpec` 中列出的版本化工具契约；模型输出、Planner、插件、Skill 文本和未来 MCP 返回值都不能添加工具或改变 Capability Profile。当前所有内置工具以及未来受控 MCP Adapter 必须共享一个 Agent Run 级 Limiter，并继续服从同一 Snapshot、路径、哈希、超时和结果大小边界。
- 默认本地部署不设置仓库根目录白名单，目录浏览从 POSIX `/` 或 Windows 现有盘符开始；因此操作系统用户可读的全部目录构成本地信任边界。该模式只能绑定回环地址。显式传入允许根目录时，后端仍必须在每次仓库访问时执行真实路径边界校验。
- 目录浏览只能列出当前启动用户具备读取和进入权限的目录及必要的 Git 仓库标记，无权限或无法解析的目录项必须逐项跳过且不得阻断同级列表，并设置数量上限；分支和 Commit 列表由后端通过受限 Git 参数数组读取，前端不得接收任意 Git 参数或自由文本 ref。
- 仓库源码、未经验证的规则文件、Skill 文本、MCP 输出和模型输出全部视为不可信数据，不能扩大 Agent、进程或工具权限。Skill 激活只能由宿主基于冻结语言和变更路径事实确定，并冻结 Skill ID、版本、内容哈希和激活原因；Agent 不具备 `load_skill` 工具。MCP Binding 目前仅为声明式数据，未启动 MCP Server 或 Client；未来 Adapter 也不得把 Secret、原始动态 Schema 或 Snapshot 外数据交给模型。只有经过规则发现、Snapshot 冻结、路径/哈希/作用域/顺序校验并由 Context Builder 规范化的 `repository_instructions` 才是可信 Review 配置；其可信性仅用于进入系统指令，不能覆盖平台安全边界或扩大 Agent、进程和工具权限。
- Secret（包括 API Key、Authorization、Cookie 和会话凭证）不得进入数据库、日志、事件、Artifact、Prompt、RunContext 或错误响应。为本机操作者提供可审计执行过程时，系统可以将已脱敏的 Prompt、模型可见输出、工具调用和 Skill 生命周期写入任务专属 Artifact，并仅通过稳定的 HTTP/JSON 与可恢复 SSE 契约读取；Transcript 对内容不做截断，折叠仅是前端呈现能力。经本地操作者明确启用的 `logs/model.log` 是唯一允许记录完整已脱敏模型交换的日志，不得包含凭证，也不得把正文复制到其他运行日志。任务级存储配额和删除策略负责 Artifact 保留边界；模型日志按固定大小和数量轮转，不得通过静默截断单条记录控制容量。
- 本地 Web 写入的多网关 Secret Catalog 保存在 data directory 的 `secrets/model-gateways.json`；目录和文件分别使用 owner-only `0700`/`0600` 权限并原子替换。API 与 Worker 只通过 Secret Store Port 共享，Worker 在实际模型调用时读取当前激活网关，进程启动不得依赖网关已配置。Secret Store 默认位于源码仓库之外。
- Review 工作空间删除使用数据库 tombstone，不级联删除 Finding、事件、快照或审计数据；读取单个已删除 Review 与列表查询都不得重新暴露 tombstone 记录。
- 最近 Review 仓库目录拥有独立于 Review 工作空间 tombstone 的持久化生命周期；删除 Review 不得改变该目录，目录只在新仓库使用或容量设置更新导致 LRU 超出当前配置时淘汰。
- 非 HTTPS 的远程模型 Base URL 会明文传输凭证和 Review 内容，界面必须显式警告；是否使用该受信任网络边界由本机操作者决定。
- 数据库结构只能通过 Alembic migration 演进；v2 初始版本只保留 `0001_codelens_v2` 一个 `down_revision = None` 的基线 revision，从空数据库直接创建最终 v2 Schema。系统不识别或升级任何 v1 revision，也不包含 backfill、旧列改名或旧表搬运脚本。持久化任务和事件必须支持幂等、重启恢复及部分失败。

### 6.1 运行期成本与诊断契约

- 每个系统语言包必须包含 `context-compaction.md`，用于提供旧证据被确定性压缩后的本地化重读说明；该说明属于平台系统提示词，不能由 Agent 自定义 Prompt 覆盖。
- `/api/settings/tool-limits` 持久化 `context_compaction_enabled`、`context_compaction_trigger_bytes`、`context_compaction_target_bytes` 和 `context_compaction_keep_recent_evidence_results`；默认分别为开启、128 KiB、32 KiB 和 6，target 必须严格小于 trigger。HTTP 契约和持久化继续使用字节，设置界面的容量字段统一换算为 KB 展示和输入。Runtime 在每个 Agent Run 启动时读取一次并冻结该组配置，运行中的设置变更只影响后续 Run。
- 每次供应商调用前，Runtime 通过 Agents SDK `call_model_input_filter` 确定性压缩已经进入较旧历史的只读证据工具结果。证据正文总字节数达到 trigger 后，按最早优先批量压缩直到不高于 target 或没有可压缩结果；最近配置的 N 条证据结果始终保留。只有 `find_files`、`grep`、`read_file` 和 `get_diff` 的结果正文可以替换为包含稳定版本标记、工具名、参数、原 call ID、原始字节数和本地化重读说明的有界占位对象；已压缩占位不得嵌套或反复改写。模型历史输出、可见推理摘要、系统提示、仓库规则、初始 `review_files`、`comment`、`task_done`、Planner 与 Verifier 输出/控制工具结果必须完整保留。压缩不得改变宿主已记录的已读覆盖、候选状态或完整脱敏 Transcript；占位对象本身不构成证据，模型需要精确正文时必须重新调用对应工具。
- 过程报告必须返回压缩次数、被压缩结果数、压缩前后字节数，以及供应商报告的缓存命中/写入 input token。一次逻辑 Agent Run 内的 provider 重试不增加 Agent Run 数；只有每次 provider 尝试都具有完整 usage 时 `usage_is_complete` 才能为 true，任一重试尝试缺少 usage 时必须标记为 false，不能把最终成功尝试的 usage 冒充整个逻辑 Run 的完整用量。
- 模型产生的工具名不在当前冻结 allowlist 时，Runtime 必须记录独立的非法工具调用事件；过程报告返回非法工具名及次数，且不得把它计入正常工具调用与结果统计。

## 7. 架构治理

架构设计或调整完成前至少确认：

- 变更位于正确的限界上下文和分层，没有出现反向依赖或跨上下文基础设施访问。
- 外部能力通过 Port/Adapter 接入，供应商类型没有进入领域或应用契约。
- 前后端只通过经过校验的 HTTP/SSE 契约通信，业务规则没有只存在于 UI。
- 新增 CLI 或其他入站适配器时，只复用稳定契约或 Application 用例，没有复制业务流程或绕过安全边界。
- API、事件、数据库和持久化任务变更已覆盖 v2 内的幂等及恢复；本次硬切不得遗留 v1 兼容或迁移路径。
- 数据所有权、Secret、仓库访问和执行隔离没有突破既有安全与信任边界。
- 进程职责、启动关系、事件传递和持久化策略与既定运行拓扑一致。
- 架构调整已同步更新本文档；实施与验证要求交由根目录 [`AGENTS.md`](../AGENTS.md) 维护。
