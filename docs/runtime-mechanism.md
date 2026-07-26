# CodeLens 大模型 Runtime 机制与源码导读

## 1. 文档目的

本文用于辅助阅读和理解 CodeLens 当前的大模型运行时代码，重点回答以下问题：

1. 一次 Review 如何从 Worker 进入模型 Runtime；
2. 平台提示词、Reviewer 提示词和仓库规则分别如何加载；
3. 模型可见工具如何定义、注册和执行；
4. 模型与工具之间的多轮循环由谁驱动、如何停止；
5. 模型输出如何转换为可信 Finding，并最终展示到前端。

本文描述当前代码实现，不把规划中的 MCP、Skills、多 Reviewer 汇总或通用沙箱当作已实现能力。工具参数和返回值的详细说明见 [`build-in-tool.md`](./build-in-tool.md)，系统边界和长期约束以根目录 [`ARCHITECTURE.md`](../ARCHITECTURE.md) 为准。

## 2. 先建立整体认识

CodeLens 不是把完整 diff 拼进一条 Prompt 后调用一次普通 Chat API。它采用工具驱动的 Agent 运行方式：模型先拿到完整 Review 文件范围和仓库规则，再按需调用只读工具获取 diff、源码和搜索结果，最后通过结构化工具提交 Finding。

端到端链路如下：

```text
用户创建 Review
    |
    v
Worker 重建任务输入并冻结 ReviewSnapshot
    |
    +-- 解析目标文件适用的仓库规则
    +-- 构造 review_files
    +-- 构造 repository_instructions
    +-- 加载 Reviewer 专属 Prompt
    |
    v
ReviewOrchestrator 调用 AgentRuntimePort
    |
    v
OpenAIAgentRuntime
    +-- 读取当前激活的模型网关
    +-- 选择 Provider Adapter
    +-- 取得已加载的本地化系统 Prompt
    +-- 创建 4 个证据工具和 2 个状态工具
    +-- 组合 Agent instructions
    |
    v
OpenAI Agents SDK Runner
    |
    +-- 调用模型
    +-- 执行模型返回的工具调用
    +-- 把工具结果送回模型
    +-- 重复以上过程
    |
    v
ReviewCommentCollector 生成 FindingBatch
    |
    v
Codec 校验 -> Artifact/checkpoint -> FindingValidator -> 持久化 Finding
    |
    v
HTTP 查询 Findings/Transcript，SSE 通知 Review 生命周期变化
```

这条链路最重要的边界是：

- 模型只能读取任务冻结后的 `ReviewSnapshot`，不能读取用户原始工作区；
- 模型最终自然语言文本不生成 Finding；
- 只有通过 `comment` 工具提交、并被宿主重新定位和校验的内容才能进入报告；
- SDK 类型被限制在 Infrastructure Adapter 内，不穿透 `AgentRuntimePort`。

## 3. 推荐源码阅读顺序

建议按调用顺序阅读，而不是从 `openai_runtime.py` 的细节开始：

| 顺序 | 文件 | 阅读重点 |
| --- | --- | --- |
| 1 | [`worker/execution.py`](../backend/src/codelens/worker/execution.py) | Worker 如何重建 Snapshot、规则、Agent 和首次输入 |
| 2 | [`review/application/context_builder.py`](../backend/src/codelens/review/application/context_builder.py) | 首次用户输入的精确结构和完整性校验 |
| 3 | [`review/application/orchestrator.py`](../backend/src/codelens/review/application/orchestrator.py) | Runtime 调用、转录、Artifact、checkpoint 和结果校验 |
| 4 | [`review/infrastructure/openai_runtime.py`](../backend/src/codelens/review/infrastructure/openai_runtime.py) | 模型客户端、Agent、工具和 Runner 的组装 |
| 5 | [`review/infrastructure/i18n_prompt_loader.py`](../backend/src/codelens/review/infrastructure/i18n_prompt_loader.py) | 系统语言包的启动加载与校验 |
| 6 | [`review/infrastructure/snapshot_tools.py`](../backend/src/codelens/review/infrastructure/snapshot_tools.py) | 4 个只读证据工具 |
| 7 | [`review/infrastructure/comment_collector.py`](../backend/src/codelens/review/infrastructure/comment_collector.py) | `comment`、`review_file_done`、`task_done` 和任务内状态 |
| 8 | [`review/infrastructure/tool_contract.py`](../backend/src/codelens/review/infrastructure/tool_contract.py) | 工具次数、超时和重复结果限制 |
| 9 | [`review/infrastructure/transcripts.py`](../backend/src/codelens/review/infrastructure/transcripts.py) | 活动转录和终态持久化 |
| 10 | [`interface/http/routers/reviews.py`](../backend/src/codelens/interface/http/routers/reviews.py) | Findings、Transcript 和 SSE 对外接口 |

如果只想快速理解核心循环，可先看 `WorkerReviewExecutor.prepare()`、`ReviewOrchestrator._checkpoint_output()`、`OpenAIAgentRuntime._invoke()` 和 `_run_observable()`。

## 4. 依赖组装与任务入口

### 4.1 组合根

统一组合根位于 [`bootstrap/unified.py`](../backend/src/codelens/bootstrap/unified.py)。启动时它会创建：

- `SnapshotService`：解析规则并冻结 Review 输入；
- `AgentOutputCodec`：校验 Runtime 产出的版本化 Finding 信封；
- `I18nPromptLoader`：一次性加载全部系统语言包；
- `OpenAIAgentRuntime`：`AgentRuntimePort` 的 OpenAI Agents SDK 适配器；
- Worker 并发信号量、Artifact Store、Checkpoint Store 和 Transcript Store。

`bootstrap` 只负责选择和连接具体实现。Review 应用层通过 `AgentRuntimePort` 调用模型，不直接创建 `AsyncOpenAI` 或引用 Agents SDK 类型。

### 4.2 Worker 准备模型输入

`WorkerReviewExecutor.prepare()` 是一次模型运行之前的主要准备入口。它依次执行：

1. 从持久化存储读取 Review 执行记录；
2. 重新验证源仓库身份；
3. 创建或验证任务专属 detached worktree；
4. 解析当前目标适用的仓库规则；
5. 冻结 `ReviewSnapshot`；
6. 按任务语言加载选中 Reviewer 的专属 Prompt；
7. 调用 `ContextBuilder` 为每个 Agent 生成确定性首包。

同一 Review 可以准备多个 `AgentVersion`，每个 Agent 都获得独立的输入字节。当前内置目录只注册了 `correctness:v1`，其不可变元数据定义在 [`reviewer_catalog/infrastructure/builtin_agents.py`](../backend/src/codelens/reviewer_catalog/infrastructure/builtin_agents.py)。

## 5. 首次用户输入如何构造

`ContextBuilder.build()` 不负责生成自然语言 Prompt。它生成一个确定性的 JSON 用户输入，仅包含两个顶层字段：

```json
{
  "review_files": [
    {
      "path": "backend/src/example.py",
      "change_type": "modified",
      "old_ranges": [{"start_line": 10, "end_line": 12}],
      "new_ranges": [{"start_line": 10, "end_line": 16}]
    }
  ],
  "repository_instructions": [
    {
      "path": "AGENTS.md",
      "content": "完整且已经冻结的规则正文",
      "applies_to": ["backend/src/example.py"]
    }
  ]
}
```

### 5.1 `review_files`

`review_files` 来自 Snapshot 的不可变文件级变更元数据。每项描述：

- 规范化仓库相对路径；
- `added`、`modified`、`deleted` 或 `renamed`；
- 重命名前路径（如适用）；
- old/new 侧允许产生 Finding 的变更范围。

模型首轮知道完整审查范围，但不会预先收到全部 diff 或文件正文。模型需要根据调查需要调用 `get_diff`、`read_file` 或 `grep`。

### 5.2 `repository_instructions`

仓库规则由宿主发现和解析，不提供“加载规则”工具给模型。`ContextBuilder` 在序列化前会验证：

- 规则路径位于 Snapshot 内；
- 规则正文哈希没有变化；
- 每个目标都有唯一且完整的规则链；
- 规则适用范围和优先级顺序有效；
- Snapshot Manifest 中的规则条目与解析结果一致。

相同规则正文只注入一次，`applies_to` 精确列出它作用于哪些 Review 文件。内部 precedence 数值、Snapshot ID、内容哈希和规则链标识不会暴露给模型。

### 5.3 确定性序列化

`AgentInput.canonical_bytes()` 使用固定字段、稳定排序和紧凑 JSON 序列化。同一个 Snapshot 和规则集合应产生相同输入字节，这对 checkpoint、转录审计和重启恢复很重要。

## 6. Prompt 的三种来源

阅读 Runtime 时要区分“系统指令”“工具描述”和“首次用户输入”。三者都对模型可见，但来源和职责不同。

### 6.1 平台系统 Prompt

每个语言包固定包含：

```text
prompts/sys/<locale>/
  review-policy.md
  review-workflow.md
  tools.json
```

- `review-policy.md`：平台安全边界和仓库规则优先级；
- `review-workflow.md`：调查流程、输出语言和结束协议；
- `tools.json`：7 个工具的本地化自然语言描述。

`I18nPromptLoader.load()` 在启动时遍历 `prompts/sys`，完整读取所有语言包。它会拒绝：

- 缺失默认语言；
- Markdown 文件不存在或为空；
- `tools.json` 不是对象或无法解析；
- 工具缺少 `description`；
- 工具集合不是固定的 7 个名称。

加载结果使用冻结 dataclass 和 `MappingProxyType` 保存。Runtime 的 `get(locale)` 只读取内存对象；未知 locale 回退到默认语言。

### 6.2 Reviewer 专属 Prompt

Reviewer Prompt 位于：

```text
prompts/<agent_id>/<locale>.md
```

例如 correctness Reviewer 的中文策略在 [`prompts/correctness/zh-CN.md`](../prompts/correctness/zh-CN.md)。它只定义该 Reviewer 关注什么、忽略什么，不重复平台安全边界和通用工作流。

`ReviewerPromptSettingsService` 会先读取仓库内默认 Prompt，再检查数据目录中的用户覆盖。设置页面只能覆盖这一层，不能替换 `review-policy.md`、`review-workflow.md` 或工具说明。

### 6.3 仓库规则

仓库中的 `AGENTS.md`、`REVIEW.md` 和文件级规则属于不可信仓库输入。它们不会拼到系统指令里，而是作为 `repository_instructions` 放在首次用户消息中，并带有精确适用范围。

因此，实际模型输入的层次是：

```text
System instructions
  1. review-policy
  2. review-workflow
  3. Reviewer Policy

Tool definitions
  - tools.json 中的自然语言描述
  - Python 类型生成的 JSON Schema

Initial user input
  - review_files
  - repository_instructions
```

`OpenAIAgentRuntime` 使用上述固定顺序拼接 `Agent.instructions`，避免仓库规则覆盖平台边界。

## 7. 模型 Provider 如何选择

`OpenAIAgentRuntime._invoke()` 每次运行开始时都会从 `ModelProviderConfigPort` 读取当前激活网关，而不是在进程启动时固定模型。这样设置页面切换网关后，后续任务无需重启即可使用新配置。

配置包括：

- vendor、API type、model 和 Base URL；
- 只写 API Key；
- 最大输出 token 和 thinking level；
- Agent 总超时和最大模型回合数；
- 工具调用次数、单次工具超时和重复结果限制。

[`provider_adapters.py`](../backend/src/codelens/review/infrastructure/provider_adapters.py) 把供应商差异转换为统一的 `ProviderRequestBehavior`：

| Vendor | 模型协议 | Thinking 适配 |
| --- | --- | --- |
| OpenAI | Responses 或 Chat Completions | SDK `Reasoning` |
| DeepSeek | Chat Completions | `extra_body.thinking` |
| Zhipu | Chat Completions | `extra_body.thinking` |

Runtime 随后创建 `AsyncOpenAI`。`trust_env=False` 防止环境代理配置隐式改变模型请求路径；Agents SDK 的模型数据和工具数据日志也在导入 SDK 前被关闭，完整模型交换只进入 CodeLens 自己的脱敏 Transcript 边界。

## 8. 工具如何定义和注册

### 8.1 每次 Run 创建独立工具实例

Runtime 不复用全局工具对象。每个 Agent Run 都创建：

```text
FilesystemReviewTools
  - find_files
  - grep
  - read_file
  - get_diff

ReviewCommentCollector
  - comment
  - review_file_done
  - task_done
```

这样 Snapshot 可见范围、Finding 累积状态和完成声明都只属于当前 Run，不会泄漏到其他 Review 或 Agent。

### 8.2 函数工具和 JSON Schema

工具通过 Agents SDK 的 `@function_tool` 暴露。Python 参数注解、Pydantic Model、`Literal` 和 `Annotated[Field(...)]` 被转换成模型可见 JSON Schema。

以 `read_file` 为例，模型看到的是工具名、`tools.json` 中的描述，以及包含以下必填字段的严格 schema：

```json
{
  "path": "backend/src/example.py",
  "start_line": 1,
  "end_line": 120,
  "version": "current"
}
```

`reject_unknown_arguments()` 在本地边界再次检查额外字段。即使供应商对 strict schema 的支持不完整，未声明参数也不会直接进入工具实现。

### 8.3 证据工具

4 个证据工具只读取 `ReviewSnapshot`：

- `find_files` 枚举 Manifest 中可见文件；
- `grep` 搜索经过哈希验证的可见文本；
- `read_file` 读取冻结 current 或固定 base/head revision；
- `get_diff` 比较固定 base 和哈希验证后的 current。

它们不执行任意 Shell、不访问网络、不写文件，也不接受任意 Git ref。详细路径、分页和输出限制见 [`build-in-tool.md`](./build-in-tool.md)。

### 8.4 状态工具

`ReviewCommentCollector` 是任务内有状态对象：

- `comment` 把模型提交的候选问题重新定位到冻结 Snapshot，并累积已接受 Finding；
- `review_file_done` 只记录已经通过模型可见 `get_diff` 或 `read_file` 成功取证的 Review 文件；
- `task_done` 以 Snapshot 的完整 Review 文件集合为基准尝试结束调查，不接受模型自报的文件数量；
- `finding_batch()` 只根据已经接受的评论生成最终信封。

`comment` 不信任模型给出行号、hunk ID 或 hash。模型只提供路径、old/new 侧和 `existing_code`；宿主重新解析代码所在行，要求范围完整位于唯一 changed hunk，再派生可信位置元数据。

### 8.5 统一执行限制

`enforce_tool_execution_limits()` 使用一个 Run 级共享 `ToolExecutionLimiter` 包装全部 7 个工具。每次工具调用都会：

1. 原子消耗一次工具预算；
2. 在配置的超时内执行工具；
3. 对工具名、规范化参数和规范化结果计算哈希；
4. 检测相同调用和相同结果是否重复到达阈值。

因此限制针对整个 Agent Run，而不是每个工具分别计数。达到上限、工具超时或检测到无进展循环时，会抛出项目自己的 provider-neutral Runtime 错误。

## 9. 模型与工具的循环如何运行

### 9.1 循环由 Agents SDK 驱动

CodeLens 没有手写 `while` 循环解析模型响应。`OpenAIAgentRuntime._run_observable()` 调用：

```python
Runner.run_streamed(
    starting_agent=agent,
    input=input_value,
    max_turns=max_turns,
    run_config=run_config,
)
```

Agents SDK 负责维护对话历史、识别工具调用、执行本地函数工具并把结果放入下一轮模型输入。概念上等价于：

```python
messages = [initial_user_input]

for turn in range(max_turns):
    response = await model.generate(
        instructions=system_instructions,
        messages=messages,
        tools=tool_schemas,
    )

    if response.has_tool_calls:
        tool_results = await execute_tools(response.tool_calls)
        messages.extend([response.tool_calls, tool_results])
        continue

    final_response = response
    break
```

这段伪代码只用于解释控制流；实际消息格式、并行工具调用和供应商响应对象由 Agents SDK 管理。

### 9.2 一次典型调查

```text
第 1 轮：模型读取 review_files 和 repository_instructions
         -> 调用 get_diff(path="...")

工具执行：验证 Snapshot -> 返回 bounded diff JSON

第 2 轮：模型读取 diff
         -> 调用 grep(...) 或 read_file(...)

工具执行：读取更多调用方或上下文

第 3 轮：模型确认问题
         -> 调用 comment(comments=[...])

工具执行：重新定位 existing_code -> 返回 accepted/rejected

第 4 轮：模型完成剩余文件调查
         -> 调用 task_done(...)

第 5 轮：模型返回普通最终文本
         -> Runner 结束
```

模型可以在一次响应中发出多个工具调用；具体执行策略由 SDK 决定，但所有工具共享同一个线程安全调用预算。

### 9.3 循环的边界

当前生产路径同时受以下边界约束：

- `max_agent_turns`：Runner 允许的最大模型回合数；
- `agent_timeout`：一次流式 Agent Run 的总时限；
- `max_tool_calls`：整个 Run 的模型可见工具调用总数；
- `tool_timeout_seconds`：每个工具调用的时限；
- `max_identical_tool_results`：无进展重复调用阈值；
- 用户取消：由 Review Orchestrator 在运行边界检查；
- 工具自身的读取字节数、行数、搜索结果数和正则执行时限。

达到模型回合上限时，SDK 抛出 `MaxTurnsExceeded`，Runtime 转换为 `AgentMaxTurnsExceededError`。供应商超时、连接错误、限流、服务端错误、模型拒绝和非法结构也会被转换为稳定的项目错误，供应商异常类型不会进入应用层。

## 10. 流式事件和 Transcript

### 10.1 Runtime 事件映射

流式运行时，`_visible_event()` 把 SDK 事件映射为项目内部 `AgentRuntimeEvent`：

| SDK 事件 | Transcript kind |
| --- | --- |
| 输出文本 delta | `model_output_delta` |
| 输出文本结束 | `model_output_completed` |
| reasoning summary delta | `model_reasoning_delta` |
| reasoning summary 结束 | `model_reasoning_completed` |
| 工具开始 | `tool_call` |
| 工具结果 | `tool_result` |

Runtime 还会在开始和结束时产生 `model_started`、`model_completed`，并在 Runner 返回后保存供应商 raw response。完整模型可见输入则在调用前作为 `prompt` 事件记录，其中包含系统指令、工具 schema、模型设置和首次用户输入。

### 10.2 活动转录和终态持久化

`ReviewOrchestrator` 接收这些事件，附加 Agent 标识后写入 `WorkerTranscriptStore`。活动 Review 的转录保留在进程内内存中；模型运行期间大约每秒把当前缓冲批次追加到内存 Store。

任务到达 completed、partial、failed 或 canceled 后，`WorkerTranscriptStore.finalize()` 才会把完整脱敏转录原子写入任务 Artifact，并清理内存副本。凭证样式内容在进入 Store 时替换为 `[REDACTED_CREDENTIAL]`。

### 10.3 Transcript 轮询和 SSE 的区别

前端执行控制台通过 `GET /api/reviews/{task_id}/transcript` 获取模型文本、reasoning summary、工具调用和工具结果。活动任务读取 Worker 内存，终态任务读取持久化 Artifact。当前前端每秒轮询这一接口。

`GET /api/reviews/{task_id}/events` 的 SSE 负责可恢复的 Review 生命周期事件，例如状态变化和终态通知。它支持 `Last-Event-ID` 和持久化 outbox 重放，但不直接承载每个模型 token delta。前端收到终态 SSE 后会刷新 Findings 和 Transcript。

## 11. 最终输出如何形成

### 11.1 模型最终文本不是报告

Runner 结束后，Runtime 不解析 `final_output` 中的自然语言来生成问题。它直接调用：

```python
comment_collector.finding_batch()
```

得到的结构只包含此前通过 `comment` 工具接受的候选：

```json
{
  "schema_version": "1",
  "findings": []
}
```

这意味着模型在最终文本里重复 Finding、补充新 Finding 或输出其他格式，都不会改变最终 Review 结果。

### 11.2 Runtime 边界校验

`AgentOutputCodec.encode()` 对 FindingBatch 再次执行版本化结构校验并生成规范字节。Runtime 同时从每个 provider raw response 聚合：

- response ID 和 request ID；
- input/output token；
- 模型调用次数；
- 输出 item 数量。

这些信息和 canonical output 被包装为 provider-neutral `UnvalidatedAgentOutput` 返回应用层。

### 11.3 Artifact、Checkpoint 和 FindingValidator

`ReviewOrchestrator._checkpoint_output()` 会：

1. 把 canonical output 写入 Run Artifact；
2. 保存 Artifact reference 和内容哈希；
3. 把 checkpoint 标记为 `output_saved`；
4. 在验证阶段重新读取并校验 Artifact；
5. 使用 `FindingValidator` 执行领域规则、证据和去重校验；
6. 持久化最终 Findings 并推进 Review 状态。

因此“模型运行成功”和“Finding 已持久化”是两个不同 checkpoint。进程在中间崩溃时，可以从持久 Artifact 恢复验证，而不必重复调用模型。

### 11.4 前端输出

最终用户看到两类输出：

- `GET /api/reviews/{task_id}/findings` 返回经过验证的结构化 Findings；
- `GET /api/reviews/{task_id}/transcript` 返回完整脱敏执行过程。

过程报告 `/process-report` 再根据终态 Transcript 确定性聚合模型调用次数、token、工具调用、时长和 Finding 数量，不从模型文本猜测用量。

## 12. 容易误解的当前实现细节

### 12.1 `task_done` 不是 Runner 的硬停止开关

`task_done` 是普通 FunctionTool，CodeLens 没有把它配置成 SDK 的 stop tool。因此无论完成请求被接受还是拒绝，工具结果都会先返回模型；只有模型随后给出不含工具调用的最终响应，Runner 才结束。

但业务完成条件由宿主强制执行，而不是只依赖 Prompt：

1. `get_diff` 或 `read_file` 成功后，宿主记录该 Review 文件已经取得模型可见证据；
2. `review_file_done` 只能记录已经取得上述证据的文件；
3. `task_done` 比较 Snapshot 的完整目标集合与已记录文件，未完成时返回 `accepted: false`，并分别给出尚未取证的 `missing_evidence_files` 和已取证但未声明的 `undeclared_files`；
4. SDK 把拒绝结果送回模型，模型补充调查后可再次调用 `task_done`；
5. Runner 结束后，`OpenAIAgentRuntime` 检查 `comment_collector.is_completed`。如果从未出现一次被接受的 `task_done`，Agent Run 以 `review_completion_not_declared` 失败。

每个 Run 启动时读取 `max_incomplete_review_retries`，默认值为 3。超过打回次数后，宿主会接受下一次不完整的 `task_done`，保留已经验证的 Finding，并把精确的 `incomplete_files` 带到 Runtime 输出；Orchestrator 随后记录 `review_coverage_incomplete` 告警。该降级路径用于避免模型无限循环，不会把未检查文件伪装成完整覆盖。

因此应区分两层退出：SDK 的 Agent Loop 仍由最终文本、最大回合、超时或异常结束；CodeLens 的 Review 成功还要求一次被接受的 `task_done`。`task_done` 不是代码里的 `break`，但它是宿主强制校验的业务完成协议。

### 12.2 当前运行限制来自模型网关配置

`AgentVersion` 中保留了 `max_turns` 和 `timeout_seconds` 等 Reviewer 元数据，但 `OpenAIAgentRuntime._invoke()` 当前传给 Runner 的是 `provider_config.max_agent_turns`，总超时也是 `provider_config.agent_timeout`。也就是说，设置页面中的激活模型网关配置决定实际运行边界。

### 12.3 “流式输出”不等于“最终业务输出”

控制台里看到的模型 delta 和 reasoning summary 只属于可观测 Transcript。最终业务输出仍是 `comment` 累积后产生的 FindingBatch。即使控制台文本看起来完整，也不能据此判断 Finding 已经通过后端验证。

### 12.4 SSE 不传模型 token

模型 delta 被写入活动 Transcript，由前端轮询 `/transcript` 获取；SSE `/events` 传递的是 Review outbox 事件。两条通道职责不同。

### 12.5 工具描述和工具 Schema 来自不同位置

`tools.json` 只提供本地化自然语言描述；参数名、类型、枚举和上下限来自 Python 函数签名与 Pydantic 模型。修改工具契约时通常需要同时检查：

- Python 参数和类型；
- `prompts/sys/en/tools.json`；
- `prompts/sys/zh-CN/tools.json`；
- Runtime 契约测试和工具单元测试。

## 13. 用一段伪代码串起全部机制

下面的伪代码省略持久化和错误映射，只表达当前设计中的主要对象关系：

```python
async def execute_review(task_id: str) -> None:
    record = await review_store.get_execution(task_id)
    instructions = await snapshot_service.resolve_instructions(record)
    snapshot = await snapshot_service.freeze(record, instructions)

    reviewer = await reviewer_catalog.load(record.agent, record.prompt_locale)
    user_input = ContextBuilder().build(snapshot, instructions).canonical_bytes()

    provider = await provider_config_store.load()
    prompts = system_prompt_loader.get(record.prompt_locale)

    evidence_tools = FilesystemReviewTools(snapshot)
    collector = ReviewCommentCollector(snapshot, evidence_tools)
    tools = enforce_tool_execution_limits(
        evidence_tools.as_agent_tools(prompts.tools)
        + collector.as_agent_tools()
    )

    agent = Agent(
        instructions=join(
            prompts.review_policy,
            prompts.review_workflow,
            reviewer.prompt_template,
        ),
        model=create_provider_model(provider),
        tools=tools,
    )

    sdk_result = await Runner.run_streamed(
        starting_agent=agent,
        input=user_input.decode("utf-8"),
        max_turns=provider.max_agent_turns,
    )

    canonical_output = output_codec.encode(collector.finding_batch())
    artifact = await artifact_store.write(canonical_output)
    findings = await finding_validator.validate(artifact)
    await review_store.complete_with_findings(task_id, findings)
```

理解这段对象关系后，再回到各实现文件阅读超时、哈希校验、异常转换、Transcript 和恢复逻辑，会比从 SDK 事件分支开始阅读更容易建立完整心智模型。
