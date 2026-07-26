# CodeLens 与 Open Code Review 实现及能力对比

## 1. 对比口径

本文比较以下固定版本在代码中已经实现的能力，不把 README 中的规划项当作现成功能：

| 项目 | 对比版本 | 对比日期 |
| --- | --- | --- |
| CodeLens | [`5ae033abef1f5baabbced5c6dd31e4e391594b36`](https://github.com/shinerio/CodeLens/commit/5ae033abef1f5baabbced5c6dd31e4e391594b36) | 2026-07-26 |
| Alibaba Open Code Review（下称 OCR） | [`c9b145635c6b6343b108941c2a627ac636836c6b`](https://github.com/alibaba/open-code-review/commit/c9b145635c6b6343b108941c2a627ac636836c6b) | 2026-07-26 |

主要证据来自双方的运行时、Prompt、工具、diff 定位和测试代码。OCR 的产品范围和公开基准以其该版本的 [`README.zh-CN.md`](https://github.com/alibaba/open-code-review/blob/c9b145635c6b6343b108941c2a627ac636836c6b/README.zh-CN.md) 为补充。双方技术栈、产品入口和目标不同，因此本文不把功能数量直接等同于审查质量，也不把 OCR 公布的基准成绩外推为 CodeLens 的相对成绩。

## 2. 开源来源与改编范围

OCR 使用 Apache License 2.0。CodeLens 同样使用 Apache License 2.0，第三方归属见根目录 [`NOTICE`](../NOTICE)。

CodeLens 对 OCR 的参考分为两类：

1. **实现思路参考**：在 Agent 调查过程中使用有状态评论工具收集候选意见，最终文本不作为结构化 Review 结果。对应 CodeLens 的 `ReviewCommentCollector` 和 OCR 的 [`CodeCommentProvider`](https://github.com/alibaba/open-code-review/blob/c9b145635c6b6343b108941c2a627ac636836c6b/internal/tool/code_comment.go)。两者的数据模型、校验边界和最终产物并不相同。
2. **算法改编**：CodeLens 的 [`line_resolver.py`](../backend/src/codelens/review/infrastructure/line_resolver.py) 将 OCR 的 [`internal/diff/resolver.go`](https://github.com/alibaba/open-code-review/blob/c9b145635c6b6343b108941c2a627ac636836c6b/internal/diff/resolver.go) 从 Go 改写为 Python。保留的核心算法是：规范化模型引用的 `existing_code`，优先在 diff hunk 中滑窗匹配，再回退到完整新文件内容。

CodeLens 没有复制 OCR 的 CLI、Prompt 模板、规则库、MCP、并发调度、二次定位或评论过滤实现。改编后的定位器被接入 CodeLens 自己的冻结 Snapshot 和 Finding 验证链路，并增加以下约束：

- 工具只能读取 Snapshot Manifest 可见路径，每次读取重新验证内容哈希；
- 首次输入已包含目标文件适用的完整冻结仓库规则；
- 定位结果必须完整落在且只落在一个新侧变更 hunk；
- hunk ID 和 excerpt hash 由后端从 Snapshot 派生，模型不能提交；
- 无法定位或不满足约束的评论直接拒绝，不进入最终 FindingBatch。

## 3. 端到端执行链路

### 3.1 CodeLens

```text
进程启动
  -> 校验并加载 prompts/sys/<locale> 与 Reviewer Prompt
  -> 用户选择仓库和 Review 范围
  -> 创建隔离 detached worktree，冻结 Snapshot、Manifest、diff 范围和规则链
  -> ContextBuilder 生成包含 review_files 与去重 repository_instructions 的首次用户输入
  -> 单个 Reviewer Run 调查全部目标
       -> 按需 get_diff/read_file/grep/... 获取证据
       -> comment 提交候选 Finding
       -> task_done 显式结束
  -> 后端定位 existing_code 并校验唯一新侧 hunk
  -> 派生 hunk ID/excerpt hash，再做领域校验与去重
  -> 持久化任务、事件、Finding 和终态转录，通过 Web/SSE 展示
```

关键实现见 [`context_builder.py`](../backend/src/codelens/review/application/context_builder.py)、[`openai_runtime.py`](../backend/src/codelens/review/infrastructure/openai_runtime.py)、[`snapshot_tools.py`](../backend/src/codelens/review/infrastructure/snapshot_tools.py) 和 [`comment_collector.py`](../backend/src/codelens/review/infrastructure/comment_collector.py)。

### 3.2 Open Code Review

```text
CLI 启动
  -> 加载内嵌任务模板、工具定义和规则库，并合并用户/项目/全局规则
  -> 读取 workspace、range 或 commit diff，过滤文件和超大 diff
  -> 注入全部变更文件的只读 diff map
  -> 按文件并发执行 subtask
       -> 大变更可先运行 PLAN_TASK，输出风险与工具调用建议
       -> MAIN_TASK 首次提示直接包含当前文件完整 diff、规则、其他变更文件和计划
       -> 主循环按需调用 file_read/code_search/file_find/file_read_diff/MCP
       -> code_comment 收集意见，task_done 结束当前文件
  -> existing_code 确定性定位；失败时可再调用 LLM 做 re-location
  -> REVIEW_FILTER_TASK 用 diff 反证并删除明显错误评论
  -> 输出 CLI/JSON，会话可恢复；CI 集成可发布行内评论和汇总
```

关键实现见 OCR 的 [`agent.go`](https://github.com/alibaba/open-code-review/blob/c9b145635c6b6343b108941c2a627ac636836c6b/internal/agent/agent.go)、[`loop.go`](https://github.com/alibaba/open-code-review/blob/c9b145635c6b6343b108941c2a627ac636836c6b/internal/llmloop/loop.go) 和 [`task_template.json`](https://github.com/alibaba/open-code-review/blob/c9b145635c6b6343b108941c2a627ac636836c6b/internal/config/template/task_template.json)。

## 4. 核心机制逐项对比

| 维度 | CodeLens | Open Code Review | 判断 |
| --- | --- | --- | --- |
| 产品入口 | 本地 Web + HTTP/JSON + SSE；当前业务 CLI 未实现 | 成熟 CLI，支持本地输出、JSON、CI/CD、会话查看器和 Agent 委托 | OCR 集成面更广；CodeLens 的交互过程和任务工作区更完整 |
| 当前 Reviewer | 只启用 `correctness:v1`；多 Reviewer 尚在规划 | 单套规则驱动的通用 Review Agent，按文件产生多个 subtask | 两者都不是“多个专业 Reviewer 已并行汇总”的完整形态 |
| Review 范围 | 分支差异、Commit、未提交改动、全仓；可冻结 overlay | workspace、range、commit、全文件 `scan`，另有 delegate | 范围接近；OCR 的 scan、delegate 与 CI 使用更成熟 |
| 执行粒度 | 一个 Agent Run 查看完整 `review_files` | 每个文件独立 plan/main subtask，并发执行 | OCR 对大变更吞吐和覆盖更有优势；CodeLens 更利于跨文件统一推理，但容易受单一上下文和回合预算影响 |
| 输入一致性 | 任务专属 detached worktree + immutable Snapshot + Manifest/hash | 按运行模式读取 Git 对象或当前工作区；有路径约束，但没有等价的内容哈希 Snapshot | CodeLens 的复现、隔离和证据完整性更强；OCR workspace 模式更轻但运行中工作区变化的影响更大 |
| 首次提示 | 用户消息包含完整、排序后的 `review_files`，以及正文去重并带精确目标映射的 `repository_instructions`；不含完整 diff 和内部 ID | 每文件的 plan/main 用户消息直接含完整 diff、当前路径、其他变更文件、规则、背景和可选计划 | CodeLens 规则首轮可用且避免逐文件工具往返；OCR 首轮还包含当前完整 diff，可直接分析但大 diff 成本更高 |
| Prompt 加载 | 启动时完整校验本地化 `review-policy`、`review-workflow` 和工具说明，再组合 Reviewer 策略；仓库规则由 Context Builder 放入首次用户输入 | 编译期嵌入 task manifest 和 Markdown Prompt，启动时解析；语言指令追加到 system 消息 | 两者都避免运行中散乱拼接；CodeLens 的本地化完整性校验更严格，OCR 的多阶段模板更丰富 |
| 仓库指令 | 冻结每个目标的 `AGENTS.md`、`REVIEW.md`、文件级规则链；宿主按规则正文去重并附带精确 `applies_to` 目标映射 | 内嵌语言规则库，叠加命令行、项目和全局 `.opencodereview/rule.json`，按路径匹配且可合并系统规则 | CodeLens 更适合仓库原生、层级化规则并可审计适用范围；OCR 内建规则覆盖和集中配置成熟度更高 |
| 规则强制 | Context Builder 在模型调用前确定性校验并完整注入所有适用规则，模型不参与规则加载 | 规则正文直接进入当前文件 Prompt，不需要加载工具 | 两者都避免规则加载工具往返；CodeLens 额外保留 Snapshot 哈希与多目标作用域校验 |
| 内置调查工具 | `find_files`、`grep`、`read_file`、`get_diff` | `file_read`、`code_search`、`file_read_diff`、`file_find` | CodeLens 通过 `read_file` 支持 current/base/head 版本读取；OCR 的代码搜索参数、Git pathspec 与跨文件 diff 批量读取更强 |
| 输出工具 | `comment`、`task_done` | `code_comment`、`task_done` | 核心模式一致；CodeLens 的提交 schema 和后端拒绝规则更严格 |
| 外部工具 | 当前明确不启用 MCP、Skills、LSP 或代码图 | 支持 stdio MCP，动态工具同时加入 plan/main 阶段，可配置 allowlist | OCR 扩展性显著领先，但 MCP 子进程、环境变量和工具结果扩大了信任面；CodeLens 当前能力较窄但边界清晰 |
| 工具选择 | 六个工具固定暴露给整个 Run，由模型按需选择；平台未做阶段裁剪 | plan 阶段只描述搜索/查找/diff/MCP 工具并生成建议，不实际执行；main 阶段暴露六个内置工具和 MCP | OCR 的阶段化工具集降低无关选择并让大变更先规划；CodeLens 链路更短，但所有工具常驻会增加选择噪声 |
| 工具限制 | Snapshot allowlist/hash、路径规范化、单次输出上限、模型回合上限和取消；当前工具次数不另设上限 | 路径 containment、Git 参数约束、输出上限、每文件最多 30 个工具回合，可设并发任务超时 | CodeLens 对“读到什么版本的什么内容”保证更强；OCR 对每文件资源上限更明确 |
| 上下文管理 | 按需读取，超出完整 scope 上限则在模型调用前失败，不静默截断；目前无模型上下文压缩 | 超大 diff 预过滤，循环中支持 memory compression | CodeLens 不会把跳过误作完整覆盖，但大任务可用性偏弱；OCR 更能跑完超长会话，但过滤文件会损失覆盖 |
| 并发与恢复 | Worker 有持久任务、租约、重启恢复和 SSE 续传；当前单 Reviewer Run 内不按文件并发 | 文件 subtask 并发、单文件失败隔离、session resume 和异步评论后处理 | OCR 的 Review 计算吞吐更成熟；CodeLens 的 Web 任务状态、事件和数据生命周期更系统化 |
| 评论质量后处理 | 确定性 schema/位置/证据校验、置信度门槛、去重；不再用第二次模型输出决定结果 | 确定性定位 + LLM re-location + diff-only REVIEW_FILTER；scan 另有批次 dedup 和项目总结 | OCR 以额外模型调用提高召回和过滤误报；CodeLens 的最终信任链更短、更可解释，但缺少反思层 |
| Finding 数据 | 标题、类别、严重性、置信度、影响、解释、建议、证据、hunk/excerpt 身份 | 内容、建议代码、existing code、类别、严重性、行范围 | CodeLens 结构更适合只读治理和审计；OCR 更贴近直接发布代码评论 |
| 可观测性 | 完整脱敏 transcript、模型交换日志、过程报告、HTTP/SSE 事件 | JSONL session、token/tool 统计、OpenTelemetry、viewer | 两者都较完整；CodeLens 更偏产品工作区与稳定 API，OCR 更偏 CLI/工程遥测 |
| Provider | 当前通过 OpenAI Agents SDK 接 OpenAI-compatible 网关，可保存并切换多个网关 | 原生支持 Anthropic、OpenAI Chat Completions、OpenAI Responses 和自定义 Provider | OCR 协议覆盖更广；CodeLens 的本地 Secret Store 和只写 Key API 边界更完整 |
| 公开评测 | 只有小型 correctness fixture，尚不能证明跨语言整体质量 | 公布 50 个仓库、200 个 PR、10 种语言的基准与精度/召回/token 取舍 | OCR 的效果证据成熟度明显领先；双方必须在同一数据集和模型上实测后才能给出直接质量排名 |

## 5. 首次提示词加载与成本取舍

### 5.1 CodeLens

CodeLens 在进程启动时由 `I18nPromptLoader` 校验每个 locale 的固定语言包：合并平台边界与仓库规则策略的 `review-policy.md`、合并通用工作流与输出契约的 `review-workflow.md` 和六个工具说明。运行时按固定顺序组合 `review-policy`、`review-workflow` 和 Reviewer 专属策略。Context Builder 在模型调用前验证冻结规则并构造首次用户消息：`review_files` 提供路径、变化类型、可选旧路径和允许 Finding 的 old/new 侧范围；`repository_instructions` 提供正文去重的完整规则和精确 `applies_to` 目标映射。

优点：

- 不把完整 diff 对所有后续轮次预加载，首包与变更正文解耦；
- 所有适用规则在首包中按正文去重，模型无需逐文件加载；
- Snapshot ID、hash 和内部规则链不暴露给模型，缩小 Prompt Injection 和伪造内部标识的空间；
- 宿主先验证完整 scope，超过上限直接失败，结果不会悄悄漏审文件。

缺点：

- 首包会携带全部适用规则；规则很多时首轮 token 成本高于按需加载；
- 一个 Run 承担全部文件，文件很多时可能在达到模型回合上限前无法完成；
- 当前没有基于风险的宿主分片、并发和上下文压缩；
- `review_files` 只给范围而不含变更摘要，首轮工具选择完全依赖模型遵循工作流。

### 5.2 Open Code Review

OCR 把 Prompt 作为内嵌模板加载。每个文件先决定是否运行 plan；变化行数达到阈值时，PLAN_TASK 看到当前完整 diff、其他变更文件、规则和业务背景，并输出风险及建议工具。MAIN_TASK 再次看到完整 diff，并附带 plan 结果。小变更跳过 plan，直接进入 main。

优点：

- 当前文件 diff 和适用规则首轮即用，模型可立即分析；
- 大变更先形成显式风险清单，main 阶段更容易有目的地选工具；
- 按文件隔离并发，覆盖行为可由宿主调度而不是依赖一个 Agent 自觉遍历；
- 单文件上下文便于失败隔离、恢复和 Prompt cache。

缺点：

- plan 与 main 重复携带完整 diff，增加 token 和首轮延迟；
- 跨文件缺陷依赖文件列表与工具主动召回，不同 subtask 不共享推理状态；
- 超大 diff 会被过滤而非拆分，可能形成明确 warning 但仍损失该文件覆盖；
- plan 只是生成工具建议，真正调用仍由 main 模型决定，并非确定性的工具路由器。

## 6. 工具选择与证据获取

CodeLens 的工具选择原则是“固定、最小权限、同一套契约”：所有工具只面对冻结 Snapshot。`find_files` 和 `grep` 简洁且易控，`read_file` 能读取 current 并精确比较固定 base/head；代价是没有语义导航、MCP 和更强的搜索参数。

OCR 的原则是“阶段裁剪、面向代码审查优化、允许扩展”：plan 只考虑查找类工具，main 才加入评论、完成和文件读取；`code_search` 支持大小写、正则、Git pathspec，`file_read_diff` 可批量查其他变更，MCP 可补充代码图等能力。代价是工具定义和外部输出更复杂，MCP 还会启动用户配置的子进程，其权限与可复现性取决于外部服务配置。

因此，OCR 当前工具能力上限更高，CodeLens 当前证据可信度下限更高。CodeLens 后续不宜直接把任意 MCP 工具透传给模型，应保持现有架构要求的 Capability Profile、allowlist、版本化 Adapter、输出限额和 transcript 边界。

## 7. Review 意见锚点定位

### 7.1 两边共有的算法

双方都要求模型提交 `existing_code`，而不是信任模型计算的行号：

1. 去掉每行首尾空白和可选的 `+`/`-` diff 标记，忽略空行；
2. 解析 unified diff hunk；
3. 根据模型显式提交的 `side`，只在对应侧的 context + added/deleted 行中做连续滑窗精确匹配；
4. diff hunk 无法定位时，只在所选侧的完整文件非空行序列中匹配；
5. 返回第一处匹配的绝对行号范围。

这比让模型返回行号稳定，且对缩进差异、diff 标记和空行有一定容错，但它不是语义定位算法。

### 7.2 OCR 的定位链路

- 宿主把 `code_comment` 的路径强制覆盖成当前 subtask 文件，避免模型伪造或漂移到其他文件；
- 首次确定性匹配失败时，可通过 `RE_LOCATION_TASK` 再调用一次 LLM，让模型从 diff 中逐字抽取更合适的代码片段，然后重试；
- 评论仍会进入收集器；最终输出前再统一执行一次 `ResolveLineNumbers`；
- 仍无行号的意见可降级出现在 summary，而不是作为行内评论丢失；
- `REVIEW_FILTER_TASK` 还能删除被当前 diff 直接反证的意见。

这套方案偏向召回率和最终可交付性：定位失败不必丢评论，二次模型能纠正部分格式漂移。代价是二次定位与过滤都由模型参与，增加 token、延迟和非确定性；未定位评论的质量边界也弱于行内评论。

### 7.3 CodeLens 的定位链路

- 模型可在首包已声明适用规则的 Review 目标中提交规范化相对路径；路径必须存在于 Snapshot Manifest；
- 后端先按相同两级文本算法定位，再要求结果完整位于模型显式选择的唯一 old/new 侧 changed hunk；
- 后端从冻结文件字节派生 excerpt hash，并从 ChangeIndex 取得 hunk ID；
- 任何无法定位、越界、跨 hunk、正文被修改、二进制或 excerpt 截断都会拒绝；
- 最终 FindingValidator 再检查 schema、范围、证据、置信度和去重，模型最终文本完全不参与结果。

这套方案偏向 precision、可审计性和安全性：一个 Finding 一旦进入报告，其路径、revision、hunk 和 excerpt 都能回到同一冻结证据。代价是格式略有偏差但语义正确的意见会直接丢失，当前没有“保留为未锚定建议”的产品状态。

### 7.4 双方共同风险与 CodeLens 当前缺口

- **第一处匹配歧义**：重复代码出现多次时，两边都取第一处，没有要求文本匹配唯一。CodeLens 的“唯一 hunk 包含范围”只验证行号属于一个 hunk，不证明引用片段在文件中唯一。
- **过度规范化**：去掉所有首尾空白、忽略空行会把 Python 缩进或空行分隔等有意义差异抹平；去掉首字符 `+`/`-` 也可能改变真实源码。
- **侧别选择错误**：CodeLens 同时允许 old/new 侧 Finding；若模型把删除代码标成 `new` 或把新增代码标成 `old`，确定性门禁会拒绝，但仍会消耗一次模型工具调用。
- **完整文件回退**：CodeLens 会读取整个文件用于内部定位，虽不暴露给模型且有 hash 校验，但当前没有独立字节上限；超大文本文件会增加内存和匹配成本。

CodeLens 应优先修正其余定位缺口：返回全部候选并要求唯一；保留原始行内容做二次精确确认；为内部完整文件回退设置明确上限。之后再考虑 OCR 式 LLM re-location，而且二次结果仍必须通过同一确定性门禁。

## 8. 综合优缺点

### 8.1 CodeLens

优势：

- Snapshot、Manifest、内容哈希、固定 revision 和隔离 worktree 形成完整证据链；
- 首次输入最小化，规则与源码按需读取，内部身份不交给模型；
- 仓库指令按目标和目录层级冻结，流程完成度可由后端强制；
- Finding 结构丰富，适合只读治理、审计和反馈评测；
- Web 工作区、持久状态、SSE、终态 transcript 和 Secret Store 构成较完整的本地产品底座。

不足：

- 当前只有一个 correctness Reviewer，和“多 Agent 工作台”的最终定位仍有差距；
- 单 Run 遍历全部文件，缺少文件分片、并发和上下文压缩，大变更效率与覆盖风险较高；
- 工具能力仍是基础文本/Git 查询，没有 MCP、语义索引或代码图；
- 没有独立 plan、re-location、reflection/filter 层，严格拒绝会牺牲召回；
- 评测规模太小，无法量化 precision、recall、F1、成本和不同语言表现；
- 尚无成熟 CLI/CI 发布、增量评论和全仓项目总结。

### 8.2 Open Code Review

优势：

- CLI、CI/CD、scan、delegate、session resume、viewer、Provider 与 MCP 生态完整；
- 按文件并发、失败隔离和阶段化 Prompt 更适合大规模变更；
- 内建跨语言规则库和分层配置能快速获得较高的开箱覆盖；
- 搜索工具更强，MCP 提供更高的上下文召回上限；
- 确定性定位、LLM re-location、review filter、scan dedup 形成多层质量处理；
- 有公开的大规模标注基准，优化方向和 precision/recall 取舍更可验证。

不足：

- 每文件 Prompt 直接注入完整 diff，plan/main 还可能重复，token 成本随大文件上升；
- 没有与 CodeLens 等价的冻结 Snapshot/hash 证据链，workspace 模式的复现和运行时一致性较弱；
- 无法定位的评论仍可保留为 summary，结果可信度存在分级；
- re-location 和 review filter 引入额外模型非确定性，且其失败采取尽量保留策略；
- MCP 扩展提高能力的同时扩大子进程、环境变量、外部工具和不可信输出的安全边界；
- 按文件上下文隔离有利于吞吐，但复杂跨文件不变量更依赖工具召回质量。

## 9. 双方可学习的点

### 9.1 CodeLens 可从 OCR 学习

按优先级建议：

1. **P0：修正定位器歧义**。先解决新/旧侧混用、第一匹配和超大文件回退问题，再扩展定位召回。
2. **P0：建立同模型同数据集评测**。至少覆盖 precision、recall、F1、位置准确率、工具调用数、token、耗时和未完成文件率；否则无法判断新增 plan/filter 是否真正改善结果。
3. **P1：宿主确定性分片并发**。按文件或关联文件组创建隔离 Agent Run，同时保留同一 Snapshot、逐目标规则链和最终 FindingValidator；不要把“遍历所有文件”只交给单个模型完成。
4. **P1：按风险启用轻量 plan**。只对大文件或高风险变化启用，计划只引用 `review_files` 和按需工具结果，避免 plan/main 重复塞完整 diff。
5. **P1：增加受控 re-location 与误报反证**。可以借鉴二次定位和 diff-only veto，但结果只能生成新候选，仍需通过确定性 Snapshot 门禁；失败应进入诊断指标，不得静默升级为 Finding。
6. **P1：扩充版本化 Reviewer/规则库**。把 OCR 的多语言规则覆盖理念转化为 CodeLens 的 Reviewer Catalog 和评测夹具，而非直接复制大段 Prompt。
7. **P2：上下文压缩和细粒度恢复**。为每个分片保留检查点、摘要所有权和 token 统计，降低长会话失败成本。
8. **P2：CLI/CI 与增量发布**。复用现有 Application 用例和稳定 API，补机器可读报告、GitHub/GitLab 行内评论、历史重叠去重和部分失败统计。
9. **P2：受控 MCP/代码图**。通过架构规定的 Capability Profile 和 Adapter 接入，只允许显式工具、固定权限和可审计输出。

### 9.2 OCR 可从 CodeLens 学习

- 为每次 Review 冻结不可变输入，并给工具读取增加内容身份校验；
- 把“定位成功”和“可作为可信 Finding”分开，未定位评论显式降级而非与行内意见同等看待；
- 从宿主证据派生位置身份，不接受模型提供内部 hash/hunk 标识；
- 对仓库指令建立逐目标、可审计的层级链，并防止仓库内容覆盖平台安全边界；
- 对 MCP 引入版本化能力配置、结果大小限制、Secret 脱敏与执行 transcript；
- 在失败、跳过超大 diff、定位降级和 Provider usage 缺失时，用稳定契约表达不完整性。

## 10. 完整结论

在当前固定版本上，**OCR 的实际能力广度、规模化吞吐、工具扩展、规则积累、交付集成和公开评测成熟度明显领先**。它更适合直接进入 CLI/CI 流水线，对大量文件并发审查，并通过 plan、re-location、filter 和 dedup 在成本、召回与误报之间做工程化平衡。

**CodeLens 的优势不在功能数量，而在 Review 输入和 Finding 证据的可信链路**：冻结 Snapshot、内容哈希、逐目标规则、最小首次输入、显式 old/new 侧的唯一 hunk 门禁、后端派生身份和稳定 Web/API 工作区，使结果更可复现、更适合审计，并保持清晰的只读边界。

如果以“今天谁能覆盖更多真实使用场景”为标准，OCR 更强；如果以“每条进入系统的 Finding 是否能确定回到同一份冻结证据，且模型不能扩大权限或伪造内部身份”为标准，CodeLens 的设计更严格。CodeLens 下一阶段最有价值的方向不是照搬 OCR 全部链路，而是**保留现有证据与安全边界，吸收 OCR 的确定性分片并发、阶段化工具选择、定位恢复、误报反证和大规模评测方法**。这样才能补足覆盖率、吞吐和产品集成，而不牺牲当前最有差异化价值的可验证性。
