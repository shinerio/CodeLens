# CodeLens Prompt Cache 与上下文 Checkpoint 设计

## 1. 文档定位

本文档定义 CodeLens 长周期 Agent 的 Prompt Cache 与上下文生命周期设计，解释为何不能通过持续改写早期工具结果来同时获得无限上下文和稳定前缀缓存，并规定后续实现 checkpoint、证据外置、国际化压缩 Prompt 和缓存诊断时必须遵循的方案。

本文是 [`docs/ARCHITECTURE.md`](./ARCHITECTURE.md) 中运行期成本与上下文边界的展开说明。若两者冲突，以 `docs/ARCHITECTURE.md` 为准。本文不改变 Review 的确定性范围划分策略；是否对 Review 范围分片不属于本方案，本阶段也不以确定性分片作为缓存优化手段。

实现状态：Epoch checkpoint 主路径已实现，包括完整 round 切分、单一 checkpoint 替换、独立普通文本摘要调用、宿主 envelope 构造与校验、宿主 evidence 索引、国际化 Prompt、失败熔断及分阶段用量统计。Checkpoint 基线不要求模型网关支持 Structured Outputs、JSON Schema、模型手写 JSON 或工具调用。旧的逐项 Tool Result 替换、占位 replay allowance 和 `context-compaction.md` 已退出运行时。仍待完成的长期增强是供应商原生压缩能力探测、确定性旧工具正文预剪枝、基于模型 token 的异步软水位、可持久恢复的完整宿主 envelope、批量工具总预算，以及缓存差异点评测；当前兼容设置仍以证据字节数触发同步 checkpoint。

## 2. 核心判断

Prompt Cache 是前缀缓存。只要历史中某个位置的字节发生变化，从该位置开始的后续缓存就不能继续复用。为旧 `read_file` 或 `get_diff` 结果就地写入摘要、占位符或删除标记，即使压缩内容本身很小，也会使其后的模型输出、工具调用和工具结果失去前缀一致性。

因此，下列目标不能由“每轮增量改写旧历史”同时满足：

- 活跃上下文无限增长；
- 旧工具结果随时可被替换；
- 全部消息历史持续命中同一个前缀缓存；
- 模型始终看到完整原始证据。

CodeLens 采用以下取舍：

1. 在证据进入模型历史前控制体积，优先避免产生无效上下文。
2. 一个上下文 Epoch 内保持 append-only，最大化连续模型调用的缓存命中。
3. 必须回收上下文时执行一次显式 checkpoint，并接受该 Epoch 边界之后的一次缓存重建。
4. 固定工具定义、平台系统 Prompt、仓库规则和原始 Agent 输入保持字节稳定，使 Epoch 切换后仍能复用最长公共前缀。
5. 完整审计 Transcript、原始证据和模型活跃上下文分离；缩短模型上下文不能删除审计事实。

## 3. 三层上下文模型

模型可见上下文分为三个逻辑层：

```text
┌──────────────────────────────────────────────────────────┐
│ Immutable Prefix                                         │
│ 有序工具定义、平台规则、仓库规则、工作流、Agent Prompt、 │
│ 原始 review_files 与冻结 role_context                    │
│ 一个 Agent Run 内字节级不变                              │
├──────────────────────────────────────────────────────────┤
│ Epoch Checkpoint                                         │
│ 宿主状态快照 + 经校验的语义摘要 + evidence_id 引用       │
│ 只在 Epoch 切换时替换                                    │
├──────────────────────────────────────────────────────────┤
│ Active Tail                                              │
│ 最近的完整 assistant/tool rounds，Epoch 内只追加         │
└──────────────────────────────────────────────────────────┘

完整 Transcript / 原始证据 -> Audit Artifact，永不因 checkpoint 删除
```

### 3.1 Immutable Prefix

一个 Agent Run 内，以下内容的文本、顺序和序列化必须稳定：

- Tool Contract 生成的工具定义和顺序；
- 平台 `review-policy`、仓库规则、`review-workflow` 和 Agent 专属 Prompt；
- 冻结的 Skill 指令；
- 初始 `review_files` 与模型可见 `role_context`；
- 模型、推理模式以及会影响供应商 Prompt 渲染的工具选择和并行工具配置。

运行进度、当前时间、缓存统计、剩余预算和 checkpoint 序号不得插入这一层。工具不得根据覆盖进度动态增删；例如不能为了隐藏或显示 `task_done` 而改变工具数组。需要变化的状态应进入有界 Tool Result 或 Epoch Checkpoint。

供应商支持显式缓存断点时，应把断点放在 Immutable Prefix 末尾。供应商只支持自动前缀缓存时，也必须保持相同的规范序列化。`prompt_cache_key` 只能作为稳定路由提示，不能替代字节级前缀一致性。

### 3.2 Epoch Checkpoint

Checkpoint 是前一 Epoch 的最小可继续执行状态，不是完整聊天摘要，也不是审计记录。它由两部分组成：

- **宿主确定性状态**：由 CodeLens 从领域状态和 Transcript 生成，模型不得修改。
- **LLM 语义摘要**：由专门的 checkpoint 调用生成普通文本，经非空、长度和 `evidence_id` 白名单校验后采用。

宿主确定性状态至少包括：

- checkpoint 格式版本、宿主持有的 Agent Run ID 和单调递增 Epoch 序号；
- 已压缩到的最后一个完整 round/call 边界；
- Review 覆盖状态与尚未覆盖的路径/范围；
- active/retracted Candidate ID 及其宿主状态；
- 已消费的工具预算和其他继续执行所需的有界计数；
- 可重新获取原始证据的 `evidence_id`、工具名和 canonical arguments；
- checkpoint 输入所覆盖 Transcript 片段的完整性哈希。

宿主持有的 Agent Run ID、Transcript 哈希、Snapshot identity 和其他 `_host_` 元数据只进入持久化 checkpoint envelope，不进入模型可见投影。下一 Epoch 发送给模型的确定性状态只包含继续执行所必需且原本已允许模型看到的覆盖、Candidate、预算和 evidence 引用。

LLM 语义摘要只负责表达：

- 已确认的调查结论及其 `evidence_id`；
- 已排除的假设及排除理由；
- 尚未解决的问题；
- 当前调查焦点；
- 建议的下一步证据读取。

LLM 不得决定文件是否已经完整 Review，不得创建或撤销 Candidate，不得修改调用预算，也不得把没有 `evidence_id` 支持的新事实写入 checkpoint。宿主状态与 LLM 摘要冲突时，以宿主状态为准；无法通过校验的摘要不能触发 Epoch 切换。

### 3.3 Active Tail

Active Tail 保存最近若干完整交互 round。一个 round 至少包含对应的 assistant tool call 以及其全部 Tool Result；不得保留没有结果的调用，也不得只保留多个并行调用中的一部分。

Epoch 内 Active Tail 只能追加。禁止逐条替换旧 Tool Result、嵌套历史摘要、删除部分并行结果、重新排序结果，或把 checkpoint 追加到原始用户输入中。

## 4. Checkpoint 为什么需要一次 LLM 调用

需要，但只让 LLM 处理不能由宿主可靠推导的语义部分。

工具调用和代码阅读产生的是证据正文。宿主可以确定路径、范围、状态、哈希、覆盖率和调用关系，却不能在不复制代码审查推理的前提下可靠判断“这个读取排除了哪个假设”“多个文件共同证明了什么问题”“下一步最值得验证什么”。这些信息需要一次语义压缩调用。

Checkpoint 调用遵循以下边界：

1. 输入是待压缩的完整 rounds、已有有效 checkpoint、宿主状态投影和证据索引。
2. 使用独立的 checkpoint Prompt，不复用 Reviewer 的调查 Prompt。
3. 默认使用当前 Agent 的同一模型族和兼容推理设置；单独使用低成本模型必须先经过代表性评测。
4. 请求和响应都使用普通文本，不声明供应商 Structured Outputs、JSON Schema 或压缩专用端点，也不要求模型手写 JSON；宿主把合格文本封装进严格、版本化的内部 Schema。
5. 宿主校验所有 `evidence_id`、Candidate 引用、数量和长度，拒绝未知字段与未知引用。
6. Checkpoint 调用的 input/output/cache usage 独立计入过程报告，并以 `checkpoint_compaction` 类型写入完整脱敏 Transcript。
7. 调用失败、超时、输出为空或校验失败时保持原活跃上下文不变；接近硬上限且无法安全 checkpoint 时显式失败，不能静默丢弃历史。

Checkpoint 调用通常是一次性输入，不能期待待压缩历史获得长期缓存收益。它的价值在于让后续多个主 Agent 调用重新获得短小、稳定、append-only 的上下文。

### 4.1 跨供应商能力阶梯

上下文回收按以下能力阶梯演进，不能把高阶供应商能力作为最低运行要求：

1. **原生压缩层**：仅当 Adapter 明确确认支持时，使用 OpenAI Responses compact、Anthropic context management 等厂商原生能力。
2. **普通文本摘要层**：默认兼容路径。复用当前模型传输，只发送 system/user 文本并接收 Markdown 或纯文本；不注册工具，不声明 `output_type`、`response_format`、strict JSON Schema 或供应商压缩参数，也不要求模型手写 JSON。
3. **确定性裁剪层**：在进入 LLM 摘要前，对已被宿主索引、且不在保留尾部的旧 Tool Result 正文执行安全预剪枝；完整正文仍保留在 Transcript/Artifact，通过 `evidence_id` 重取。该层尚未实现，实施前必须保证完整 round、并行 call/result 配对和审计边界不被破坏。

Adapter 必须把能力支持与普通请求成功区分开。OpenAI-compatible Base URL 只说明协议形状相似，不能推导其支持 Structured Outputs、Responses compact 或同名模型能力。原生路径收到明确的 capability rejection 后必须降级到普通文本路径；普通文本路径收到 400、404 或 422 时，当前 Agent Run 立即打开 checkpoint 熔断。其他压缩错误连续三次后打开熔断。熔断后在软水位以下继续使用原上下文，不再因每个新增 Tool Result 重复调用；到达硬水位仍无法切换时显式失败。

当前实现直接以第二层作为跨供应商基线；第一层和第三层是后续增强。这样即使 DeepSeek、OpenAI-compatible proxy 或其他 Chat Completions 网关不实现 JSON Schema，或者 JSON 遵循能力不稳定，也能执行 checkpoint。

## 5. 国际化专用 Prompt

Checkpoint 必须使用专门的本地化 Prompt 文件：

```text
prompts/sys/en/checkpoint-compaction.md
prompts/sys/zh-CN/checkpoint-compaction.md
```

采用 `checkpoint-compaction.md` 而不是含义模糊的 `compact.md`，以明确它生成的是 Agent Epoch Checkpoint，不是通用文本摘要，也不是现有的单条证据占位通知。

旧的 `context-compaction.md` 曾用于逐项证据占位通知，不能兼任 checkpoint 生成 Prompt，现已与旧运行时机制一并移除。

`I18nPromptLoader` 必须在启动时完整加载和校验每个 locale 的 `checkpoint-compaction.md`。缺少任一必需语言文件、文件为空或模板变量不合法时，进程启动失败。运行期不得从磁盘临时读取，也不得在 Python 代码中拼接自然语言压缩指令。

Checkpoint Prompt 的自然语言随 locale 变化，但版本 marker、`evidence_id`、Candidate ID、call ID、Tool Contract 名称、canonical arguments 和错误码不得本地化。

Prompt 不规定 Markdown、JSON、XML、固定标题或其他输出形式，而是规定信息保真边界：必须保留任务目标与约束、调查覆盖进度、已确认结论、已排除假设及原因、未决问题和具体下一步；上一 checkpoint 中仍有效但未在新 transcript 重复的状态也必须继续携带。依赖证据的陈述必须引用准确的宿主 `evidence_id`，并区分已确认事实、推断和尚未验证的假设。只有可重新读取的大段正文、重复叙事和无关过程才应省略。Function Tool、固定格式和固定标题解析都不得成为 checkpoint 成功的供应商能力门槛。

宿主对模型文本只执行以下校验：去除可选的单层 Markdown fence、拒绝空文本、限制总长度，并拒绝正文中出现的未知 `evidence_id`。通过后，宿主把整段文本放入内部 `investigation_summary`，其余确定性字段和 evidence 索引由宿主生成，再组合成版本化 checkpoint envelope。模型原始输出本身不能直接成为下一 Epoch 的完整输入。

## 6. Epoch 生命周期

### 6.1 正常阶段

主 Agent 调用保持 append-only：

```text
Immutable Prefix + Checkpoint N + Active Tail
Immutable Prefix + Checkpoint N + Active Tail + Round A
Immutable Prefix + Checkpoint N + Active Tail + Round A + Round B
```

相邻调用共享完整前缀，供应商可以复用前一次请求的大部分缓存。

### 6.2 触发阶段

目标设计优先使用供应商报告的 input token 和模型上下文上限；缺少可靠 usage 时才使用与模型适配的本地 token 估算。字节数可以作为工具单次输出边界，但不能作为长期上下文容量的唯一判断依据。当前实现仍使用 `context_compaction_trigger_bytes` 与 `context_compaction_target_bytes` 作为兼容水位，因此这两个设置当前仍然生效；切换到可靠 token 水位前不得从 UI 或稳定设置契约中删除。

采用软、硬两个水位：

- 软水位：异步准备 checkpoint，不阻塞当前安全 round。
- 硬水位：在发起下一次可能越界的主 Agent 调用前同步等待或生成 checkpoint。

具体比例必须配置化并通过评测确定。触发判断需要计入 Immutable Prefix、已有 checkpoint、Active Tail、预留输出 token 和工具 Schema，而不只是证据 Tool Result 正文。`context_compaction_keep_recent_evidence_results` 当前只是完整 round 选择的额外下限，不应成为长期主策略；目标实现应由 Active Tail token budget 决定保留范围，并在完成迁移后废弃该按结果数量配置。

### 6.3 切换阶段

Checkpoint 成功后，从相同 Immutable Prefix 开启下一 Epoch：

```text
Epoch N:   Immutable Prefix + Checkpoint N   + Active Tail N
Epoch N+1: Immutable Prefix + Checkpoint N+1 + Preserved Recent Rounds
```

不得把 `Checkpoint N+1` 追加到原始 user message，也不得保留 `Checkpoint N` 后再追加新摘要。Epoch 切换允许从 checkpoint 位置开始发生一次缓存失效，但 Immutable Prefix 仍可复用；新 Epoch 内再次恢复 append-only。

### 6.4 原始证据重取

Checkpoint 只保留 `evidence_id` 和结论，不保留大段源码。模型需要复核时，使用原 Tool Contract 和 canonical arguments 重新读取 Snapshot。重取继续受同一 Snapshot、路径、哈希、timeout、调用预算和输出边界约束，并在 Transcript 中与首次读取区分。

## 7. 从源头减少上下文

Checkpoint 是兜底机制，不是允许工具无限输出的理由。优化优先级固定为：

1. 批量执行彼此独立的只读工具调用，减少 LLM 往返。
2. 为批量结果设置总字节/token 上限，而不只是每个子调用上限。
3. 宿主侧完成确定性的过滤、去重、排序和范围合并。
4. `read_file`、`get_diff` 返回稳定续读范围，不重复已返回内容。
5. 原始大结果写入 Artifact/Transcript，通过 `evidence_id` 引用。
6. 只将当前判断所需的代码片段送入模型。
7. 只有活跃上下文仍接近上限时才生成 checkpoint。

模型一次返回多个独立 Tool Call 时，宿主可以有界并行执行，但必须将该轮所有 Tool Result 一次性、按原 Tool Call 顺序返回模型。状态工具和完成工具不得与只读证据调用在同一并行批次中执行。并行度与批次总输出必须由 Agent Run scoped Limiter 约束。

## 8. Transcript、恢复与安全

完整脱敏 Transcript 是审计真相；Checkpoint 只是模型继续执行所需的派生状态：

- Transcript append-only，保存 checkpoint 前被回收的完整 rounds；
- checkpoint envelope 保存到任务 Artifact，并带来源 Transcript 哈希；
- Worker 恢复时验证版本、哈希、Snapshot identity 和 `FrozenAgentExecutionSpec`；
- 验证失败时不得把 checkpoint 交给模型，应从可信持久化状态重建或明确失败；
- checkpoint 摘要与工具结果一样是不可信模型输出，不能提升权限或覆盖平台规则；
- Secret、未脱敏凭证和 Snapshot 外正文不得进入 checkpoint Prompt 或输出。

## 9. 指标与验收

过程报告至少分别统计主 Agent 与 checkpoint 调用：

- LLM 调用数、输入/输出 token、cache read/write token；
- Epoch 数与 checkpoint 成功、失败、超时、校验失败次数；
- 每次切换前后的有效 input token；
- Immutable Prefix token 数及缓存命中数；
- 从最早差异点估算的缓存失效 token；
- 被 checkpoint 覆盖的 round、工具结果和原始字节数；
- evidence 重取次数与重取 token；
- 批量工具调用数、子调用数、并行度峰值和批次总结果大小。

长期方案的验收标准：

1. 同一 Epoch 相邻请求的既有上下文字节一致。
2. Epoch 内不出现旧 Tool Result 改写。
3. 每次切换只有一个新 checkpoint，不积累历史摘要。
4. Checkpoint 不能改变宿主覆盖状态、Candidate 状态和预算。
5. 回收模型活跃历史不影响完整 Transcript 和 Review 恢复。
6. 代表性 Review 的 LLM 调用数、总输入 token、缓存读 token、延迟和 Finding 质量均纳入评测。
7. 降低 token 或调用次数不能以降低覆盖正确性、证据完整性或 Finding 准确率为代价。

## 10. 迁移顺序

1. 为证据 Tool Result 建立稳定 `evidence_id` 和可重读参数索引；后续补充独立原文 Artifact 索引。
2. 引入批量只读工具及 Agent Run 级并行度、批次总输出限制。
3. 已新增 `checkpoint-compaction.md` 多语言文件、加载契约、Markdown/纯文本输出约定和宿主严格 envelope 校验。
4. 已实现 evidence 宿主状态、完整 round 分区和未知引用校验；后续补全可持久恢复的宿主 envelope。
5. 当前已接入同步软/硬字节保护、能力拒绝即时熔断和连续三次失败熔断；后续切换为模型 token 水位并增加异步软水位准备。
6. 已将主 Agent 切换为 Immutable Prefix + 单一 Checkpoint + Active Tail。
7. 已增加 checkpoint 调用、token、失败和覆盖结果指标；后续增加缓存差异点、重取和评测指标。
8. 已移除逐项 Tool Result 替换、占位 replay allowance 和 `context-compaction.md`。
9. 后续按 Adapter 能力增加厂商原生压缩，并确保任何不支持响应都回退到已经验证的普通文本路径；再评估确定性旧工具正文预剪枝。

迁移不得一次性删除现有审计或恢复能力。每一步都必须用 Transcript 回放和代表性 Review 评测确认状态、覆盖和 Finding 不发生静默退化。
